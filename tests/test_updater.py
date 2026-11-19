"""Tests for GitHub Releases update checking, SHA256 staging, self-update script, and UI."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
from pathlib import Path
import subprocess
import zipfile

from aiohttp import web
import pytest

from pocket_agent.agents import BUILTIN_AGENTS
from pocket_agent.config import AppConfig
from pocket_agent.ui.drawer import ChatDrawer
from pocket_agent.ui.overlays import ErrorBannerWidget
from pocket_agent.ui.settings_page import SettingsPage
from pocket_agent.updater import (
    ReleaseInfo,
    UpdateCheckResult,
    UpdateError,
    UpdateManager,
    build_apply_update_script,
    check_for_updates,
    compare_versions,
    download_and_stage_update,
    is_newer_version,
    parse_release_payload,
    parse_sha256_text,
    spawn_apply_update_script,
)


def test_version_comparison_edge_cases() -> None:
    assert is_newer_version("v0.2.0", "0.1.0") is True
    assert is_newer_version("V1.0.0", "0.9.9") is True
    assert is_newer_version("0.1.0", "v0.1.0") is False
    assert is_newer_version("0.0.9", "0.1.0") is False
    assert compare_versions("1.0", "1.0.0") == 0
    assert compare_versions("1.2.3+build.45", "1.2.3") == 0

    # Pre-release precedence
    assert is_newer_version("0.2.0", "0.2.0-rc.1") is True
    assert is_newer_version("0.2.0-rc.1", "0.2.0") is False
    assert compare_versions("0.2.0-alpha.1", "0.2.0-beta.1") == -1
    assert compare_versions("0.2.0-beta.2", "0.2.0-beta.10") == -1
    assert is_newer_version("0.2.0-rc.1", "0.1.0") is True

    # Invalid strings fail safely
    assert is_newer_version("not-a-version", "0.1.0") is False
    assert is_newer_version("0.2.0", "") is False


def test_parse_release_payload_extracts_win64_zip_and_sha256() -> None:
    payload = {
        "tag_name": "v0.2.0",
        "name": "Pocket Agent v0.2.0",
        "html_url": "https://github.com/Dayle22/pocket-agent/releases/tag/v0.2.0",
        "body": "## What's New\n- Auto updater",
        "assets": [
            {
                "name": "pocket_agent-0.2.0-py3-none-any.whl",
                "browser_download_url": "https://example.com/pkg.whl",
                "size": 1000,
            },
            {
                "name": "PocketAgent-v0.2.0-win64.zip",
                "browser_download_url": "https://example.com/PocketAgent-v0.2.0-win64.zip",
                "size": 42000000,
            },
            {
                "name": "PocketAgent-v0.2.0-win64.zip.sha256",
                "browser_download_url": "https://example.com/PocketAgent-v0.2.0-win64.zip.sha256",
                "size": 96,
            },
        ],
    }
    rel = parse_release_payload(payload)
    assert rel.tag_name == "v0.2.0"
    assert rel.version == "0.2.0"
    assert rel.zip_name == "PocketAgent-v0.2.0-win64.zip"
    assert rel.zip_url == "https://example.com/PocketAgent-v0.2.0-win64.zip"
    assert rel.sha256_url == "https://example.com/PocketAgent-v0.2.0-win64.zip.sha256"
    assert rel.zip_size == 42000000


def test_parse_sha256_text() -> None:
    digest = "a" * 64
    assert parse_sha256_text(f"{digest}  PocketAgent-v0.2.0-win64.zip\n") == digest
    with pytest.raises(UpdateError):
        parse_sha256_text("invalid-hash")


def test_config_check_updates_on_startup_default_and_persistence(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({"agent_id": "agy"}), encoding="utf-8")
    loaded = AppConfig.load(cfg_path)
    assert loaded.check_updates_on_startup is True

    loaded.check_updates_on_startup = False
    loaded.save(cfg_path)
    reloaded = AppConfig.load(cfg_path)
    assert reloaded.check_updates_on_startup is False


def test_check_for_updates_available_and_silent_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    rel = ReleaseInfo(
        tag_name="v0.2.0",
        version="0.2.0",
        name="v0.2.0",
        html_url="https://github.com/Dayle22/pocket-agent/releases/tag/v0.2.0",
    )

    async def _fake_fetch_ok(**_kwargs):
        return rel

    monkeypatch.setattr("pocket_agent.updater.fetch_latest_release", _fake_fetch_ok)
    res = asyncio.run(check_for_updates(current_version="0.1.0", silent=True))
    assert res.update_available is True
    assert res.latest_version == "0.2.0"
    assert res.error is None

    res_same = asyncio.run(check_for_updates(current_version="0.2.0", silent=False))
    assert res_same.update_available is False
    assert res_same.error is None

    async def _fake_fetch_fail(**_kwargs):
        raise UpdateError("GitHub API rate limit exceeded (HTTP 403).")

    monkeypatch.setattr("pocket_agent.updater.fetch_latest_release", _fake_fetch_fail)
    silent_res = asyncio.run(check_for_updates(current_version="0.1.0", silent=True))
    assert silent_res.update_available is False
    assert silent_res.error is None
    assert silent_res.silent is True

    loud_res = asyncio.run(check_for_updates(current_version="0.1.0", silent=False))
    assert loud_res.update_available is False
    assert "rate limit" in (loud_res.error or "").lower()


def _make_zip_bytes(include_exe: bool = True) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        if include_exe:
            zf.writestr("PocketAgent.exe", b"MZ-fake-exe")
        zf.writestr("_internal/readme.txt", b"hello")
    return buf.getvalue()


def test_download_and_stage_update_sha256_pass_and_fail(tmp_path: Path) -> None:
    good_zip = _make_zip_bytes(include_exe=True)
    good_hash = hashlib.sha256(good_zip).hexdigest()
    bad_hash = "0" * 64
    no_exe_zip = _make_zip_bytes(include_exe=False)
    no_exe_hash = hashlib.sha256(no_exe_zip).hexdigest()

    async def _run_server_tests() -> None:
        async def _ok_zip(_req):
            return web.Response(body=good_zip)

        async def _ok_sha(_req):
            return web.Response(text=f"{good_hash}  good.zip\n")

        async def _bad_sha(_req):
            return web.Response(text=f"{bad_hash}  good.zip\n")

        async def _noexe_zip(_req):
            return web.Response(body=no_exe_zip)

        async def _noexe_sha(_req):
            return web.Response(text=f"{no_exe_hash}  noexe.zip\n")

        app = web.Application()
        app.router.add_get("/good.zip", _ok_zip)
        app.router.add_get("/good.sha256", _ok_sha)
        app.router.add_get("/bad.sha256", _bad_sha)
        app.router.add_get("/noexe.zip", _noexe_zip)
        app.router.add_get("/noexe.sha256", _noexe_sha)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
        base = f"http://127.0.0.1:{port}"

        try:
            # 1. Valid download + checksum + extraction
            progress_events: list[tuple[int, int]] = []
            rel_ok = ReleaseInfo(
                tag_name="v0.2.0",
                version="0.2.0",
                name="v0.2.0",
                html_url="https://example.com/rel",
                zip_name="PocketAgent-v0.2.0-win64.zip",
                zip_url=f"{base}/good.zip",
                sha256_url=f"{base}/good.sha256",
                zip_size=len(good_zip),
            )
            extract_dir = await download_and_stage_update(
                rel_ok,
                staging_root=tmp_path / "stage_ok",
                progress_cb=lambda d, t: progress_events.append((d, t)),
            )
            assert (extract_dir / "PocketAgent.exe").is_file()
            assert progress_events and progress_events[-1][0] == len(good_zip)

            # 2. Checksum mismatch aborts
            rel_bad = ReleaseInfo(
                tag_name="v0.2.0",
                version="0.2.0",
                name="v0.2.0",
                html_url="https://example.com/rel",
                zip_name="PocketAgent-v0.2.0-win64.zip",
                zip_url=f"{base}/good.zip",
                sha256_url=f"{base}/bad.sha256",
            )
            with pytest.raises(UpdateError, match="SHA256 checksum verification failed"):
                await download_and_stage_update(rel_bad, staging_root=tmp_path / "stage_bad")
            assert not (tmp_path / "stage_bad" / "v0.2.0" / "PocketAgent-v0.2.0-win64.zip").exists()

            # 3. Missing PocketAgent.exe aborts
            rel_noexe = ReleaseInfo(
                tag_name="v0.2.0",
                version="0.2.0",
                name="v0.2.0",
                html_url="https://example.com/rel",
                zip_name="PocketAgent-v0.2.0-win64.zip",
                zip_url=f"{base}/noexe.zip",
                sha256_url=f"{base}/noexe.sha256",
            )
            with pytest.raises(UpdateError, match="PocketAgent.exe"):
                await download_and_stage_update(rel_noexe, staging_root=tmp_path / "stage_noexe")
        finally:
            await runner.cleanup()

    asyncio.run(_run_server_tests())


def test_build_and_spawn_apply_update_script(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    extract_dir = tmp_path / "updates" / "v0.2.0" / "extracted"
    extract_dir.mkdir(parents=True)
    app_dir = tmp_path / "PocketAgent"
    app_dir.mkdir()

    script = build_apply_update_script(
        pid=4321,
        source_dir=extract_dir,
        target_dir=app_dir,
        staging_dir=extract_dir.parent,
    )
    assert "$TargetPid = 4321" in script
    assert "Get-Process -Id $TargetPid" in script
    assert "Copy-Item -Path (Join-Path $SourceDir '*') -Destination $TargetDir -Recurse -Force" in script
    assert "Start-Process -FilePath $ExePath" in script

    launched: list[tuple[list[str], dict]] = []

    def _fake_popen(cmd, **kwargs):
        launched.append((cmd, kwargs))
        return None

    monkeypatch.setattr(subprocess, "Popen", _fake_popen)
    script_path = spawn_apply_update_script(extract_dir, app_dir=app_dir, pid=4321)
    assert script_path.is_file()
    assert len(launched) == 1
    cmd, kwargs = launched[0]
    assert cmd[0] == "powershell.exe"
    assert str(script_path) in cmd
    assert kwargs["creationflags"] == getattr(subprocess, "CREATE_NO_WINDOW", 0)


def test_ui_update_banner_and_settings_page(qapp) -> None:
    banner = ErrorBannerWidget()
    actions: list[str] = []
    banner.update_action_requested.connect(lambda: actions.append("update"))
    banner.release_notes_requested.connect(lambda: actions.append("notes"))

    banner.show_update_banner("Update Available: v0.2.0", "Ready", primary_label="Update & Restart")
    assert banner._variant == "info"
    assert banner._update_actions_row.isHidden() is False
    assert banner._actions_row.isHidden() is True
    assert banner._update_btn.text() == "Update & Restart"

    banner._update_btn.click()
    banner._release_notes_btn.click()
    assert actions == ["update", "notes"]

    # Source vs frozen drawer banner text
    drawer = ChatDrawer(agents=list(BUILTIN_AGENTS), current_agent_id="agy", workspace=".")
    drawer.show_update_banner("0.2.0", frozen=False)
    assert drawer._error_banner._update_btn.text() == "Open Release"
    assert "git pull" in drawer._error_banner._msg_lbl.text()

    drawer.show_update_banner("0.2.0", frozen=True)
    assert drawer._error_banner._update_btn.text() == "Update & Restart"

    # Settings page controls
    settings = SettingsPage(check_updates_on_startup=True)
    toggled: list[bool] = []
    checks: list[bool] = []
    settings.auto_update_toggled.connect(toggled.append)
    settings.check_updates_requested.connect(lambda: checks.append(True))

    settings._auto_update_cb.setChecked(False)
    settings._check_updates_btn.click()
    assert toggled == [False]
    assert checks == [True]

    settings.set_update_status("Update available: v0.2.0", update_available=True, is_frozen=False)
    assert settings._apply_update_btn.isHidden() is False
    assert settings._apply_update_btn.text() == "Open Release Page"

    settings.set_update_status("Update available: v0.2.0", update_available=True, is_frozen=True)
    assert settings._apply_update_btn.text() == "Update & Restart"

    banner.deleteLater()
    drawer.deleteLater()
    settings.deleteLater()
    qapp.processEvents()


def test_update_manager_emits_signals(qapp, monkeypatch: pytest.MonkeyPatch) -> None:
    rel = ReleaseInfo(
        tag_name="v0.2.0",
        version="0.2.0",
        name="v0.2.0",
        html_url="https://example.com/rel",
    )

    async def _fake_check(**_kwargs):
        return UpdateCheckResult(
            current_version="0.1.0",
            latest_version="0.2.0",
            update_available=True,
            release=rel,
        )

    monkeypatch.setattr("pocket_agent.updater.check_for_updates", _fake_check)

    mgr = UpdateManager()
    started: list[bool] = []
    results: list[UpdateCheckResult] = []
    mgr.check_started.connect(lambda: started.append(True))
    mgr.check_finished.connect(results.append)

    mgr.check_for_updates_async(silent=False)
    for _ in range(50):
        qapp.processEvents()
        if results:
            break
        import time
        time.sleep(0.02)

    assert started == [True]
    assert len(results) == 1
    assert results[0].update_available is True
    assert results[0].latest_version == "0.2.0"

    mgr.deleteLater()
    qapp.processEvents()
