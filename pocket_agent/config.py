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
    workspace: str = field(default_factory=_default_workspace)
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
        # Ensure discovered local pets dirs remain available if user kept empty list unintentionally
        if not merged.get("pets_dirs"):
            merged["pets_dirs"] = _default_pets_dirs()
        return cls(**merged)

    def save(self, path: Path = CONFIG_FILE) -> None:
        """Persist configuration atomically."""
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
