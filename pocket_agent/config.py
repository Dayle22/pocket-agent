"""Persistent user configuration for Pocket Agent."""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


CONFIG_DIR = Path.home() / ".pocket-agent"
CONFIG_FILE = CONFIG_DIR / "config.json"


def _default_pets_dirs() -> list[str]:
    """Return sensible default locations to scan for sprite packs."""
    candidates = [
        Path.home() / "source" / "repos" / "awesome-antigravity-pet" / "pets",
    ]
    return [str(p) for p in candidates if p.exists()]


def _default_workspace() -> str:
    """Return a clean default working directory for ACP sessions."""
    return str(Path.cwd())


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
