"""Tests for Windows console-free packaging, frozen asset resolution, and single-instance IPC."""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path

from PIL import Image

from pocket_agent import __version__
from pocket_agent.acp_bridge import _subprocess_creation_flags
from pocket_agent.app import create_instance_server, try_notify_existing_instance
from pocket_agent.config import _default_workspace, startup_command
from pocket_agent.engine.catalog import BUILTIN_ASSETS_DIR, _resolve_builtin_assets_dir
from pocket_agent.ui.settings_page import SettingsPage


def test_builtin_assets_include_spritesheet_and_icon() -> None:
    with Image.open(BUILTIN_ASSETS_DIR / "pocketbot_sheet.png") as sprite:
        assert sprite.getpixel((96, 186))[3] > 0
    with Image.open(BUILTIN_ASSETS_DIR / "pocket_agent.ico") as icon:
        assert {(16, 16), (32, 32), (48, 48), (256, 256)} <= icon.ico.sizes()
    with Image.open(BUILTIN_ASSETS_DIR / "pocketbot_no_shadow.png") as sprite:
        assert sprite.getpixel((96, 186))[3] == 0


def test_resolve_builtin_assets_dir_uses_meipass_when_frozen(monkeypatch, tmp_path) -> None:
    import pocket_agent.engine.catalog as catalog_mod

    fake_pkg_file = tmp_path / "missing_site" / "pocket_agent" / "engine" / "catalog.py"
    meipass_assets = tmp_path / "meipass" / "pocket_agent" / "assets"
    meipass_assets.mkdir(parents=True)

    monkeypatch.setattr(catalog_mod, "__file__", str(fake_pkg_file))
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "meipass"), raising=False)

    assert _resolve_builtin_assets_dir() == meipass_assets


def test_subprocess_creation_flags_hides_console_on_windows(monkeypatch) -> None:
    if os.name == "nt":
        assert _subprocess_creation_flags() == subprocess.CREATE_NO_WINDOW
    monkeypatch.setattr(os, "name", "posix")
    assert _subprocess_creation_flags() == 0


def test_default_workspace_uses_home_when_frozen(monkeypatch) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert _default_workspace() == str(Path.home())


def test_startup_command_formats_frozen_and_source(monkeypatch) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\Apps\PocketAgent\PocketAgent.exe")
    assert startup_command() == r'"C:\Apps\PocketAgent\PocketAgent.exe"'


def test_single_instance_ipc_notifies_primary(qapp) -> None:
    server_name = f"pocket-agent-test-{uuid.uuid4().hex}"
    activated: list[bool] = []
    server = create_instance_server(lambda: activated.append(True), server_name=server_name)
    try:
        assert try_notify_existing_instance(server_name=server_name, timeout_ms=1000) is True
        for _ in range(20):
            qapp.processEvents()
            if activated:
                break
        assert activated == [True]
    finally:
        server.close()

    assert try_notify_existing_instance(server_name=f"{server_name}-unused", timeout_ms=150) is False


def test_settings_page_startup_toggle_and_version_label(qapp) -> None:
    page = SettingsPage(launch_at_startup=False)
    toggled: list[bool] = []
    page.startup_toggled.connect(toggled.append)

    page._startup_cb.setChecked(True)
    assert toggled == [True]

    page.set_launch_at_startup(False)
    assert page._startup_cb.isChecked() is False
    assert toggled == [True]
    assert f"v{__version__}" in page._version_lbl.text()
    page.deleteLater()
    qapp.processEvents()


def test_config_persists_drop_shadow_and_coerces_non_bool(tmp_path: Path) -> None:
    from pocket_agent.config import AppConfig

    cfg_path = tmp_path / "config.json"
    cfg = AppConfig(drop_shadow=False)
    cfg.save(cfg_path)
    reloaded = AppConfig.load(cfg_path)
    assert reloaded.drop_shadow is False

    cfg_path.write_text('{"drop_shadow": 0}', encoding="utf-8")
    coerced = AppConfig.load(cfg_path)
    assert coerced.drop_shadow is False


def test_smoke_test_cli_exits_zero() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "pocket_agent", "--smoke-test"],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert proc.returncode == 0, proc.stderr
