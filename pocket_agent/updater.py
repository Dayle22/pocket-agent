"""GitHub Releases update checker and self-updater for frozen Windows builds."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
import hashlib
import logging
import os
from pathlib import Path
import re
import shutil
import ssl
import subprocess
import sys
import threading
from typing import Any
import zipfile

import aiohttp
import certifi
from PyQt6.QtCore import QObject, pyqtSignal

from pocket_agent import __version__
from pocket_agent.config import CONFIG_DIR

logger = logging.getLogger("pocket_agent.updater")

GITHUB_REPO = "Dayle22/pocket-agent"
LATEST_RELEASE_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
RELEASES_PAGE_URL = f"https://github.com/{GITHUB_REPO}/releases"
UPDATES_DIR = CONFIG_DIR / "updates"

_SHA256_RE = re.compile(r"([0-9a-fA-F]{64})")


class UpdateError(RuntimeError):
    """Raised when an update check, download, or verification step fails."""


@dataclass(frozen=True)
class ReleaseInfo:
    """Normalized GitHub Release metadata and Windows asset URLs."""

    tag_name: str
    version: str
    name: str
    html_url: str
    body: str = ""
    zip_name: str | None = None
    zip_url: str | None = None
    sha256_url: str | None = None
    zip_size: int = 0


@dataclass(frozen=True)
class UpdateCheckResult:
    """Outcome of checking GitHub Releases for a newer version."""

    current_version: str
    latest_version: str | None
    update_available: bool
    release: ReleaseInfo | None = None
    error: str | None = None
    silent: bool = False


def is_frozen() -> bool:
    """Return True when running from a PyInstaller-frozen executable."""
    return bool(getattr(sys, "frozen", False))


def _ssl_context() -> ssl.SSLContext:
    """Build an SSL context backed by certifi's CA bundle."""
    return ssl.create_default_context(cafile=certifi.where())


def parse_version(version_str: str) -> tuple[tuple[int, ...], tuple[tuple[int, int | str], ...]]:
    """Parse a semantic version string into comparable (core, prerelease) tuples.

    Strips a leading ``v``/``V`` and ignores ``+build`` metadata. Pre-release
    identifiers follow SemVer 2.0.0 precedence (numeric < alphanumeric, and any
    pre-release < final release).
    """
    cleaned = version_str.strip()
    if cleaned.startswith(("v", "V")):
        cleaned = cleaned[1:]
    cleaned = cleaned.split("+", 1)[0].strip()
    if not cleaned:
        raise ValueError(f"Empty version string: {version_str!r}")

    if "-" in cleaned:
        core_str, pre_str = cleaned.split("-", 1)
    else:
        core_str, pre_str = cleaned, ""

    core_parts = core_str.split(".")
    if not core_parts or any(not p.isdigit() for p in core_parts):
        raise ValueError(f"Invalid version core: {version_str!r}")
    core = tuple(int(p) for p in core_parts)

    pre_tokens: list[tuple[int, int | str]] = []
    if pre_str:
        for ident in pre_str.split("."):
            if not ident:
                raise ValueError(f"Empty pre-release identifier in {version_str!r}")
            if ident.isdigit():
                pre_tokens.append((0, int(ident)))
            else:
                pre_tokens.append((1, ident.lower()))

    return core, tuple(pre_tokens)


def compare_versions(a: str, b: str) -> int:
    """Compare two semantic versions, returning -1, 0, or 1."""
    core_a, pre_a = parse_version(a)
    core_b, pre_b = parse_version(b)

    width = max(len(core_a), len(core_b))
    padded_a = core_a + (0,) * (width - len(core_a))
    padded_b = core_b + (0,) * (width - len(core_b))

    if padded_a != padded_b:
        return -1 if padded_a < padded_b else 1

    # Same core version: no pre-release > any pre-release
    if not pre_a and not pre_b:
        return 0
    if not pre_a:
        return 1
    if not pre_b:
        return -1

    if pre_a == pre_b:
        return 0
    return -1 if pre_a < pre_b else 1


def is_newer_version(latest: str, current: str) -> bool:
    """Return True if *latest* is strictly newer than *current*."""
    try:
        return compare_versions(latest, current) > 0
    except ValueError:
        return False


def parse_release_payload(payload: dict[str, Any]) -> ReleaseInfo:
    """Extract ReleaseInfo and Windows zip/sha256 assets from GitHub API JSON."""
    tag_name = str(payload.get("tag_name") or "").strip()
    if not tag_name:
        raise UpdateError("GitHub release payload is missing 'tag_name'.")

    version = tag_name[1:] if tag_name.startswith(("v", "V")) else tag_name
    name = str(payload.get("name") or tag_name).strip()
    html_url = str(payload.get("html_url") or RELEASES_PAGE_URL).strip()
    body = str(payload.get("body") or "").strip()

    assets = payload.get("assets") or []
    if not isinstance(assets, list):
        assets = []

    expected_zip = f"PocketAgent-{tag_name}-win64.zip"
    alt_zip = f"PocketAgent-v{version}-win64.zip"

    zip_asset: dict[str, Any] | None = None
    for candidate_name in (expected_zip, alt_zip):
        for asset in assets:
            if isinstance(asset, dict) and str(asset.get("name") or "") == candidate_name:
                zip_asset = asset
                break
        if zip_asset is not None:
            break

    if zip_asset is None:
        for asset in assets:
            if isinstance(asset, dict):
                aname = str(asset.get("name") or "")
                if aname.startswith("PocketAgent-") and aname.endswith("-win64.zip"):
                    zip_asset = asset
                    break

    zip_name = str(zip_asset.get("name")) if zip_asset else None
    zip_url = str(zip_asset.get("browser_download_url") or "") or None if zip_asset else None
    zip_size = int(zip_asset.get("size") or 0) if zip_asset else 0

    sha256_url: str | None = None
    if zip_name:
        expected_sha = f"{zip_name}.sha256"
        for asset in assets:
            if isinstance(asset, dict) and str(asset.get("name") or "") == expected_sha:
                sha256_url = str(asset.get("browser_download_url") or "") or None
                break

    return ReleaseInfo(
        tag_name=tag_name,
        version=version,
        name=name,
        html_url=html_url,
        body=body,
        zip_name=zip_name,
        zip_url=zip_url,
        sha256_url=sha256_url,
        zip_size=zip_size,
    )


def parse_sha256_text(text: str) -> str:
    """Extract the 64-hex-character SHA256 digest from a .sha256 file."""
    match = _SHA256_RE.search(text.strip())
    if not match:
        raise UpdateError("SHA256 checksum file does not contain a valid digest.")
    return match.group(1).lower()


def compute_file_sha256(path: Path) -> str:
    """Compute the lowercase SHA256 hex digest of *path*."""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest().lower()


def verify_file_sha256(file_path: Path, expected_hash: str) -> bool:
    """Return True if *file_path* matches *expected_hash* (case-insensitive)."""
    return compute_file_sha256(file_path) == expected_hash.strip().lower()


async def fetch_latest_release(
    api_url: str = LATEST_RELEASE_API_URL,
    timeout_s: float = 10.0,
) -> ReleaseInfo:
    """Fetch the latest release metadata from GitHub Releases asynchronously."""
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": f"PocketAgent/{__version__}",
    }
    timeout = aiohttp.ClientTimeout(total=timeout_s)
    connector = aiohttp.TCPConnector(ssl=_ssl_context())
    async with aiohttp.ClientSession(connector=connector, timeout=timeout, headers=headers) as session:
        async with session.get(api_url) as resp:
            if resp.status in (403, 429):
                raise UpdateError(f"GitHub API rate limit exceeded (HTTP {resp.status}).")
            if resp.status != 200:
                raise UpdateError(f"GitHub API returned HTTP {resp.status}.")
            payload = await resp.json()
            if not isinstance(payload, dict):
                raise UpdateError("Unexpected GitHub API response format.")
            return parse_release_payload(payload)


async def check_for_updates(
    current_version: str = __version__,
    *,
    silent: bool = False,
    api_url: str = LATEST_RELEASE_API_URL,
    timeout_s: float = 10.0,
) -> UpdateCheckResult:
    """Check GitHub Releases for an update, suppressing errors when *silent* is True."""
    try:
        release = await fetch_latest_release(api_url=api_url, timeout_s=timeout_s)
    except Exception as exc:
        if silent:
            logger.debug("Silent update check skipped: %s", exc)
            return UpdateCheckResult(
                current_version=current_version,
                latest_version=None,
                update_available=False,
                release=None,
                error=None,
                silent=True,
            )
        logger.warning("Update check failed: %s", exc)
        return UpdateCheckResult(
            current_version=current_version,
            latest_version=None,
            update_available=False,
            release=None,
            error=str(exc),
            silent=False,
        )

    available = is_newer_version(release.version, current_version)
    return UpdateCheckResult(
        current_version=current_version,
        latest_version=release.version,
        update_available=available,
        release=release,
        error=None,
        silent=silent,
    )


def _safe_extract_zip(zip_path: Path, dest_dir: Path) -> None:
    """Extract *zip_path* into *dest_dir* while guarding against path traversal."""
    dest_resolved = dest_dir.resolve()
    with zipfile.ZipFile(zip_path, "r") as zf:
        for member in zf.infolist():
            target = (dest_resolved / member.filename).resolve()
            if target != dest_resolved and dest_resolved not in target.parents:
                raise UpdateError(f"Unsafe path in update archive: {member.filename}")
        zf.extractall(dest_resolved)


async def download_and_stage_update(
    release: ReleaseInfo,
    staging_root: Path = UPDATES_DIR,
    progress_cb: Callable[[int, int], None] | None = None,
    timeout_s: float = 120.0,
) -> Path:
    """Download release zip + sha256, verify checksum, extract, and validate PocketAgent.exe."""
    if not release.zip_url or not release.sha256_url:
        raise UpdateError("Release is missing Windows zip or SHA256 checksum asset.")

    tag_dir = staging_root / release.tag_name
    if tag_dir.exists():
        shutil.rmtree(tag_dir, ignore_errors=True)
    tag_dir.mkdir(parents=True, exist_ok=True)

    zip_filename = release.zip_name or f"PocketAgent-{release.tag_name}-win64.zip"
    zip_path = tag_dir / zip_filename
    extract_dir = tag_dir / "extracted"

    headers = {"User-Agent": f"PocketAgent/{__version__}"}
    timeout = aiohttp.ClientTimeout(total=timeout_s)
    connector = aiohttp.TCPConnector(ssl=_ssl_context())

    async with aiohttp.ClientSession(connector=connector, timeout=timeout, headers=headers) as session:
        # 1. Fetch expected SHA256 checksum first
        async with session.get(release.sha256_url) as sha_resp:
            if sha_resp.status != 200:
                raise UpdateError(f"Failed to download SHA256 checksum (HTTP {sha_resp.status}).")
            expected_hash = parse_sha256_text(await sha_resp.text())

        # 2. Stream release zip to staging directory
        async with session.get(release.zip_url) as zip_resp:
            if zip_resp.status != 200:
                raise UpdateError(f"Failed to download update archive (HTTP {zip_resp.status}).")
            total = int(zip_resp.headers.get("Content-Length") or release.zip_size or 0)
            downloaded = 0
            with zip_path.open("wb") as fh:
                async for chunk in zip_resp.content.iter_chunked(65536):
                    fh.write(chunk)
                    downloaded += len(chunk)
                    if progress_cb is not None:
                        progress_cb(downloaded, total)

    # 3. Verify SHA256 before extraction
    if not verify_file_sha256(zip_path, expected_hash):
        zip_path.unlink(missing_ok=True)
        raise UpdateError("SHA256 checksum verification failed. Update aborted.")

    # 4. Extract and validate PocketAgent.exe
    extract_dir.mkdir(parents=True, exist_ok=True)
    _safe_extract_zip(zip_path, extract_dir)
    if not (extract_dir / "PocketAgent.exe").is_file():
        raise UpdateError("Extracted update archive does not contain PocketAgent.exe.")

    return extract_dir


def _ps_quote(value: str | Path) -> str:
    """Single-quote a string safely for PowerShell."""
    return "'" + str(value).replace("'", "''") + "'"


def build_apply_update_script(
    *,
    pid: int,
    source_dir: Path,
    target_dir: Path,
    staging_dir: Path,
    exe_name: str = "PocketAgent.exe",
) -> str:
    """Generate a PowerShell script that waits for PID exit, swaps files, relaunches, and cleans up."""
    return "\n".join(
        [
            "$ErrorActionPreference = 'Stop'",
            f"$TargetPid = {int(pid)}",
            f"$SourceDir = {_ps_quote(source_dir)}",
            f"$TargetDir = {_ps_quote(target_dir)}",
            f"$StagingDir = {_ps_quote(staging_dir)}",
            f"$ExePath = Join-Path $TargetDir {_ps_quote(exe_name)}",
            "",
            "for ($i = 0; $i -lt 120; $i++) {",
            "    if (-not (Get-Process -Id $TargetPid -ErrorAction SilentlyContinue)) { break }",
            "    Start-Sleep -Milliseconds 250",
            "}",
            "",
            "for ($attempt = 0; $attempt -lt 5; $attempt++) {",
            "    try {",
            "        Copy-Item -Path (Join-Path $SourceDir '*') -Destination $TargetDir -Recurse -Force",
            "        break",
            "    } catch {",
            "        if ($attempt -eq 4) { throw }",
            "        Start-Sleep -Milliseconds 500",
            "    }",
            "}",
            "",
            "Start-Process -FilePath $ExePath -WorkingDirectory $TargetDir",
            "Remove-Item -Path $StagingDir -Recurse -Force -ErrorAction SilentlyContinue",
            "Remove-Item -Path $PSCommandPath -Force -ErrorAction SilentlyContinue",
            "",
        ]
    )


def spawn_apply_update_script(
    extract_dir: Path,
    *,
    app_dir: Path | None = None,
    pid: int | None = None,
    staging_dir: Path | None = None,
) -> Path:
    """Write and launch the console-free PowerShell updater script."""
    target_dir = app_dir or Path(sys.executable).resolve().parent
    target_pid = os.getpid() if pid is None else pid
    stage_dir = staging_dir or extract_dir.parent
    script_path = stage_dir / "apply_update.ps1"

    script_text = build_apply_update_script(
        pid=target_pid,
        source_dir=extract_dir,
        target_dir=target_dir,
        staging_dir=stage_dir,
    )
    script_path.write_text(script_text, encoding="utf-8")

    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    subprocess.Popen(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-WindowStyle",
            "Hidden",
            "-File",
            str(script_path),
        ],
        creationflags=creationflags,
        close_fds=True,
        start_new_session=True,
    )
    return script_path


class UpdateManager(QObject):
    """Qt-friendly background coordinator for update checks and staged downloads."""

    check_started = pyqtSignal()
    check_finished = pyqtSignal(object)  # UpdateCheckResult
    download_progress = pyqtSignal(int, int)  # downloaded_bytes, total_bytes
    update_ready = pyqtSignal(object)  # Path (extract_dir)
    update_failed = pyqtSignal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._checking = False
        self._downloading = False

    @property
    def is_busy(self) -> bool:
        return self._checking or self._downloading

    def check_for_updates_async(self, *, silent: bool = False) -> None:
        """Start an asynchronous update check off the Qt UI thread."""
        if self._checking or self._downloading:
            return
        self._checking = True
        self.check_started.emit()

        def _worker() -> None:
            try:
                result = asyncio.run(check_for_updates(current_version=__version__, silent=silent))
            except Exception as exc:
                result = UpdateCheckResult(
                    current_version=__version__,
                    latest_version=None,
                    update_available=False,
                    release=None,
                    error=None if silent else str(exc),
                    silent=silent,
                )
            finally:
                self._checking = False
            self.check_finished.emit(result)

        threading.Thread(target=_worker, name="pocket-agent-update-check", daemon=True).start()

    def download_update_async(self, release: ReleaseInfo) -> None:
        """Download, verify, and stage *release* off the Qt UI thread."""
        if self._downloading:
            return
        self._downloading = True

        def _worker() -> None:
            try:
                extract_dir = asyncio.run(
                    download_and_stage_update(
                        release,
                        progress_cb=lambda done, total: self.download_progress.emit(done, total),
                    )
                )
            except Exception as exc:
                self._downloading = False
                self.update_failed.emit(str(exc))
                return
            self._downloading = False
            self.update_ready.emit(extract_dir)

        threading.Thread(target=_worker, name="pocket-agent-update-download", daemon=True).start()
