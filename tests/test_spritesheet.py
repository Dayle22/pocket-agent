"""Tests for PocketBot spritesheet generation, slicing, and PetCatalog."""

from pathlib import Path
from PIL import Image

from pocket_agent.engine.catalog import BUILTIN_POCKETBOT_ID, PetCatalog
from pocket_agent.engine.fsm import PetAction
from pocket_agent.engine.spritesheet import (
    ACTION_SPECS,
    CELL_HEIGHT,
    CELL_WIDTH,
    COLUMNS,
    ROWS,
    SpritePlayer,
    SpriteSheet,
    generate_pocketbot_sheet,
)


def test_generate_and_slice_pocketbot_sheet(qapp, tmp_path: Path) -> None:
    out = tmp_path / "pocketbot_sheet.png"
    generate_pocketbot_sheet(out)
    assert out.is_file()

    with Image.open(out) as img:
        assert img.size == (COLUMNS * CELL_WIDTH, ROWS * CELL_HEIGHT)

    sheet = SpriteSheet(out)
    for action, (_, durations) in ACTION_SPECS.items():
        assert sheet.get_frame_count(action) == len(durations)
        frame = sheet.get_frame(action, 0)
        assert frame is not None
        assert frame.width() == CELL_WIDTH
        assert frame.height() == CELL_HEIGHT

    player = SpritePlayer(sheet, static_idle=False)
    assert player.update(300) is True


def test_catalog_discovers_builtin_and_external_packs(qapp, tmp_path: Path) -> None:
    ext_root = tmp_path / "pets"
    sample_pet = ext_root / "custom-buddy"
    sample_pet.mkdir(parents=True)
    generate_pocketbot_sheet(sample_pet / "spritesheet.png")
    (sample_pet / "pet.json").write_text('{"name_en": "Custom Buddy"}', encoding="utf-8")

    catalog = PetCatalog(extra_dirs=[ext_root])
    assert catalog.get(BUILTIN_POCKETBOT_ID).is_builtin is True
    ext = catalog.get("custom-buddy")
    assert ext.display_name == "Custom Buddy"
    assert ext.is_builtin is False
