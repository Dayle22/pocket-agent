"""Persistent user configuration for Pocket Agent."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


CONFIG_DIR = Path.home() / ".pocket-agent"
CONFIG_FILE = CONFIG_DIR / "config.json"
NO_PROJECT_DIR = CONFIG_DIR / "scratch"
_STARTUP_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_STARTUP_VALUE_NAME = "PocketAgent"


def _default_pets_dirs() -> list[str]:
    """Return sensible default locations to scan for sprite packs."""
    candidates = [
        Path.home() / "source" / "repos" / "awesome-antigravity-pet" / "pets",
    ]
    return [str(p) for p in candidates if p.exists()]


def _default_workspace() -> str:
    """Return a clean default working directory for ACP sessions."""
    cwd = Path.cwd()
    if getattr(sys, "frozen", False):
        return str(Path.home())
    try:
        exe_dir = Path(sys.executable).resolve().parent
        if cwd.resolve() == exe_dir or cwd.name.lower() == "system32":
            return str(Path.home())
    except OSError:
        pass
    return str(cwd)


def resolve_session_cwd(workspace: str, no_project_dir: Path = NO_PROJECT_DIR) -> str:
    """Return a valid working directory for ACP, using an isolated scratch folder when no project is active."""
    cleaned = workspace.strip()
    if cleaned:
        candidate = Path(cleaned)
        if candidate.is_dir():
            return str(candidate)
    no_project_dir.mkdir(parents=True, exist_ok=True)
    return str(no_project_dir)


def startup_command() -> str:
    """Return the Windows Run command used to launch Pocket Agent without a console."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    runner = pythonw if pythonw.is_file() else exe
    return f'"{runner}" -m pocket_agent'


def get_launch_at_startup() -> bool:
    """Return True if Pocket Agent is registered to launch at Windows sign-in."""
    if os.name != "nt":
        return False
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _STARTUP_RUN_KEY, 0, winreg.KEY_READ) as key:
            val, _ = winreg.QueryValueEx(key, _STARTUP_VALUE_NAME)
            return bool(str(val).strip())
    except OSError:
        return False


def set_launch_at_startup(enabled: bool) -> bool:
    """Enable or disable launching Pocket Agent at Windows sign-in via HKCU Run."""
    if os.name != "nt":
        return False
    try:
        import winreg

        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER,
            _STARTUP_RUN_KEY,
            0,
            winreg.KEY_SET_VALUE,
        ) as key:
            if enabled:
                winreg.SetValueEx(key, _STARTUP_VALUE_NAME, 0, winreg.REG_SZ, startup_command())
            else:
                try:
                    winreg.DeleteValue(key, _STARTUP_VALUE_NAME)
                except FileNotFoundError:
                    pass
        return True
    except OSError:
        return False


@dataclass
class CustomAgentConfig:
    """User-defined ACP agent preset stored in config.json."""

    id: str
    name: str
    command: str
    args: list[str] = field(default_factory=list)
    description: str = ""


@dataclass
class AppConfig:
    """Typed application settings."""

    agent_id: str = "agy"
    pet_id: str = "pocketbot"
    workspace: str = ""
    start_without_project: bool = True
    recent_workspaces: list[str] = field(default_factory=list)
    pets_dirs: list[str] = field(default_factory=_default_pets_dirs)
    always_on_top: bool = True
    static_idle: bool = False
    theme: str = "system"  # "dark" | "light" | "system"
    backdrop_blur: bool = True
    pet_scale: float = 0.4
    drawer_width: int = 440
    drawer_height: int = 560
    pet_x: int | None = None
    pet_y: int | None = None
    voice_enabled: bool = True
    voice_name: str = "en-ZA-LeahNeural"
    check_updates_on_startup: bool = True
    custom_agents: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path = CONFIG_FILE) -> "AppConfig":
        """Load configuration from JSON, falling back to defaults on missing/corrupt data."""
        if not path.exists():
            cfg = cls()
            cfg.save(path)
            return cfg

        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return cls()

        defaults = asdict(cls())
        merged = {k: raw.get(k, v) for k, v in defaults.items()}
        merged["workspace"] = str(merged.get("workspace") or "").strip()
        merged["start_without_project"] = bool(merged.get("start_without_project", True))
        raw_recent = merged.get("recent_workspaces")
        merged["recent_workspaces"] = (
            [str(p).strip() for p in raw_recent if isinstance(p, str) and str(p).strip()]
            if isinstance(raw_recent, list)
            else []
        )
        # Ensure discovered local pets dirs remain available if user kept empty list unintentionally
        if not merged.get("pets_dirs"):
            merged["pets_dirs"] = _default_pets_dirs()
        cfg = cls(**merged)
        if cfg.workspace:
            cfg.record_recent_workspace(cfg.workspace)
        return cfg

    def record_recent_workspace(self, folder: str, limit: int = 5) -> None:
        """Keep the most recently used project folders deduplicated and bounded."""
        clean = folder.strip()
        if not clean:
            return
        try:
            if Path(clean).resolve() == NO_PROJECT_DIR.resolve():
                return
        except OSError:
            pass
        deduped = [clean] + [p for p in self.recent_workspaces if os.path.normcase(p) != os.path.normcase(clean)]
        self.recent_workspaces = deduped[:limit]

    def save(self, path: Path = CONFIG_FILE) -> None:
        """Persist configuration atomically."""
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
