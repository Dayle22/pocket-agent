"""Companion character catalog with built-in PocketBot and external sprite pack discovery."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from pocket_agent.engine.orbs import VOICE_ORBS
from pocket_agent.engine.spritesheet import generate_pocketbot_sheet

BUILTIN_ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"
BUILTIN_POCKETBOT_ID = "pocketbot"

FEATURED_EXTERNAL_IDS = [
    "firefly--lingxiaotian",
    "acheron--lingxiaotian",
    "furina--lingxiaotian",
    "frieren--lingxiaotian",
    "arlecchino--lingxiaotian",
    "sparkle--lingxiaotian",
    "nahida--lingxiaotian",
    "paimon--lingxiaotian",
    "raiden-shogun--lingxiaotian",
    "bocchi--lingxiaotian",
]


@dataclass(frozen=True)
class PetInfo:
    """Metadata and spritesheet location for an animated companion."""

    id: str
    display_name: str
    spritesheet_path: Path
    description: str = ""
    is_builtin: bool = False
    orb_id: str | None = None


class PetCatalog:
    """Scans built-in and configured external pet directories."""

    def __init__(self, extra_dirs: Iterable[str | Path] = ()) -> None:
        self._pets: dict[str, PetInfo] = {}
        self.extra_dirs = [Path(d) for d in extra_dirs]
        self.refresh()

    def refresh(self) -> None:
        self._pets.clear()

        pocketbot_sheet = BUILTIN_ASSETS_DIR / "pocketbot_sheet.png"
        if not pocketbot_sheet.exists():
            generate_pocketbot_sheet(pocketbot_sheet)

        self._pets[BUILTIN_POCKETBOT_ID] = PetInfo(
            id=BUILTIN_POCKETBOT_ID,
            display_name="PocketBot (Built-in)",
            spritesheet_path=pocketbot_sheet,
            description="Built-in floating ACP companion",
            is_builtin=True,
        )

        for spec in VOICE_ORBS:
            self._pets[spec.id] = PetInfo(
                id=spec.id,
                display_name=f"{spec.name} (Orb)",
                spritesheet_path=pocketbot_sheet,
                description=spec.tagline,
                is_builtin=True,
                orb_id=spec.id,
            )

        for root in self.extra_dirs:
            if not root.is_dir():
                continue
            for folder in sorted(root.iterdir()):
                if not folder.is_dir():
                    continue
                sheet = _find_spritesheet(folder)
                if sheet is None:
                    continue
                display_name, desc = _read_pet_meta(folder)
                pet_id = folder.name
                self._pets[pet_id] = PetInfo(
                    id=pet_id,
                    display_name=display_name,
                    spritesheet_path=sheet,
                    description=desc,
                    is_builtin=False,
                )

    def get(self, pet_id: str) -> PetInfo:
        return self._pets.get(pet_id) or self._pets[BUILTIN_POCKETBOT_ID]

    def all(self) -> list[PetInfo]:
        return list(self._pets.values())

    def orbs(self) -> list[PetInfo]:
        """Return all built-in procedural VoiceOrb companions."""
        return [p for p in self._pets.values() if p.orb_id is not None]

    def characters(self) -> list[PetInfo]:
        """Return all sprite-based companions (built-in and external)."""
        return [p for p in self._pets.values() if p.orb_id is None]

    def featured(self) -> list[PetInfo]:
        items: list[PetInfo] = [self._pets[BUILTIN_POCKETBOT_ID]]
        for pid in FEATURED_EXTERNAL_IDS:
            if pid in self._pets:
                items.append(self._pets[pid])
        return items


def _find_spritesheet(folder: Path) -> Path | None:
    for name in ("spritesheet.webp", "spritesheet.png"):
        candidate = folder / name
        if candidate.is_file():
            return candidate
    return None


def _read_pet_meta(folder: Path) -> tuple[str, str]:
    json_path = folder / "pet.json"
    if not json_path.is_file():
        return folder.name, ""
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
        name = data.get("name_en") or data.get("name") or data.get("name_zh") or folder.name
        if data.get("name_zh") and data.get("name_en") and data["name_zh"] != data["name_en"]:
            name = f"{data['name_en']} ({data['name_zh']})"
        desc = str(data.get("description", ""))
        return str(name), desc
    except Exception:
        return folder.name, ""
