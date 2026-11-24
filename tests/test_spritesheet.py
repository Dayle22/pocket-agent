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


def test_anime_pack_is_curated_and_keeps_the_active_sprite(tmp_path: Path) -> None:
    root = tmp_path / "awesome-antigravity-pet" / "pets"
    for pet_id in ("frieren--lingxiaotian", "bocchi--lingxiaotian", "saved-pet", "extra-pet"):
        folder = root / pet_id
        folder.mkdir(parents=True)
        (folder / "spritesheet.png").touch()

    catalog = PetCatalog([root], active_pet_id="saved-pet")
    assert {pet.id for pet in catalog.characters()} == {
        BUILTIN_POCKETBOT_ID, "frieren--lingxiaotian", "bocchi--lingxiaotian", "saved-pet",
    }


def test_ensure_pocketbot_sheet_generates_and_falls_back_on_oserror(tmp_path: Path, monkeypatch) -> None:
    from pocket_agent.engine import catalog as catalog_mod

    generated = catalog_mod.ensure_pocketbot_sheet(drop_shadow=False, assets_dir=tmp_path)
    assert generated == tmp_path / "pocketbot_no_shadow.png"
    assert generated.is_file()

    (tmp_path / "pocketbot_no_shadow.png").unlink()
    shadow_sheet = catalog_mod.ensure_pocketbot_sheet(drop_shadow=True, assets_dir=tmp_path)

    def _fail_generate(*_args, **_kwargs):
        raise OSError("Read-only assets directory")

    monkeypatch.setattr(catalog_mod, "generate_pocketbot_sheet", _fail_generate)
    fallback = catalog_mod.ensure_pocketbot_sheet(drop_shadow=False, assets_dir=tmp_path)
    assert fallback == shadow_sheet
