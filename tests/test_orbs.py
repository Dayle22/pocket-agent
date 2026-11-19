"""Tests for the 4 GPU GLSL VoiceOrbs, 7-state contract, catalog integration, and PetWindow."""

from __future__ import annotations

import time
from pathlib import Path

from PyQt6.QtCore import QPointF, QRect
from PyQt6.QtGui import QColor, QImage, QPainter, QPixmap

from pocket_agent.agents import BUILTIN_AGENTS
from pocket_agent.config import AppConfig
from pocket_agent.engine.catalog import BUILTIN_POCKETBOT_ID, PetCatalog
from pocket_agent.engine.fsm import PetState
from pocket_agent.engine.orb_shaders import SHADER_ORB_IDS, OrbGlRenderer
from pocket_agent.engine.orbs import (
    ORB_REGISTRY,
    ORB_STATES,
    VOICE_ORBS,
    OrbPlayer,
    StateMix,
)
from pocket_agent.engine.spritesheet import CELL_HEIGHT, CELL_WIDTH, SpritePlayer
from pocket_agent.ui.pet_window import PetWindow


def test_only_4_exact_glsl_orbs_registered_and_in_catalog(qapp) -> None:
    expected = ("siri-sheet", "aura-field", "iridescent-flow", "duotone-flow")
    assert len(VOICE_ORBS) == 4
    assert len(ORB_REGISTRY) == 4
    assert tuple(spec.id for spec in VOICE_ORBS) == expected
    assert SHADER_ORB_IDS == expected

    catalog = PetCatalog(extra_dirs=[])
    orbs = catalog.orbs()
    assert len(orbs) == 4
    assert tuple(o.id for o in orbs) == expected
    assert all(o.is_builtin and o.orb_id == o.id for o in orbs)

    chars = catalog.characters()
    assert any(c.id == BUILTIN_POCKETBOT_ID and c.orb_id is None for c in chars)


def test_state_mix_transitions_and_normalizes_all_7_states() -> None:
    mix = StateMix("idle")
    assert mix.weights["idle"] == 1.0
    assert abs(sum(mix.weights.values()) - 1.0) < 1e-6

    for target in ORB_STATES:
        for _ in range(12):
            weights = mix.update(target, 0.05)
            assert abs(sum(weights.values()) - 1.0) < 1e-5
            assert all(0.0 <= v <= 1.0 for v in weights.values())
        assert mix.weights[target] > 0.90


def test_all_glsl_orb_shaders_compile_and_render(qapp) -> None:
    renderer = OrbGlRenderer.instance()
    assert renderer.is_available() is True
    errors = renderer.compile_all()
    assert errors == {oid: "" for oid in SHADER_ORB_IDS}

    for spec in VOICE_ORBS:
        player = OrbPlayer(spec.id)
        for state in ORB_STATES:
            player.set_orb_state(state)
            player.pulse_level(0.75)
            assert player.update(33, is_hovered=True) is True

        pm = player.current_pixmap()
        assert not pm.isNull()
        assert pm.width() == CELL_WIDTH
        assert pm.height() == CELL_HEIGHT

        # Verify non-transparent pixels exist in rendered center region
        img = pm.toImage()
        center_alpha = img.pixelColor(CELL_WIDTH // 2, CELL_HEIGHT // 2).alpha()
        ring_alpha = img.pixelColor(CELL_WIDTH // 2 + 30, CELL_HEIGHT // 2).alpha()
        assert max(center_alpha, ring_alpha) > 0, f"{spec.id} rendered blank pixels"


def test_orb_player_render_performance(qapp) -> None:
    # Warm up shader compilation once
    for spec in VOICE_ORBS:
        OrbPlayer(spec.id).current_pixmap()

    t0 = time.perf_counter()
    for spec in VOICE_ORBS:
        player = OrbPlayer(spec.id)
        player.set_orb_state("speaking")
        player.pulse_level(0.8)
        player.update(33)
        _ = player.current_pixmap()
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    avg_ms = elapsed_ms / len(VOICE_ORBS)
    assert avg_ms < 25.0, f"Average orb frame render took {avg_ms:.2f}ms"


def test_pet_window_switches_between_sprite_and_orbs(qapp, monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(AppConfig, "save", lambda self, path=None: None)
    cfg = AppConfig(pet_id="pocketbot", workspace=str(tmp_path), pets_dirs=[])
    catalog = PetCatalog(extra_dirs=[])
    win = PetWindow(cfg, catalog, list(BUILTIN_AGENTS))
    try:
        assert isinstance(win._player, SpritePlayer)

        for orb_id in ("siri-sheet", "aura-field", "iridescent-flow", "duotone-flow"):
            win._load_pet(orb_id)
            assert isinstance(win._player, OrbPlayer)
            assert win._player.orb_id == orb_id
            assert win.config.pet_id == orb_id

            for pet_state, orb_state in (
                (PetState.THINKING, "connecting"),
                (PetState.IDLE, "idle"),
                (PetState.THINKING, "thinking"),
                (PetState.CODING, "speaking"),
                (PetState.PERMISSION, "listening"),
                (PetState.ERROR, "error"),
                (PetState.IDLE, "disabled"),
            ):
                win.set_state(pet_state, orb_state=orb_state)
                assert win._player.orb_state == orb_state
                win.pulse_orb_level(0.85)
                win._on_tick()

        win._load_pet("pocketbot")
        assert isinstance(win._player, SpritePlayer)
    finally:
        win.close_tray()
        win.close()


def test_orb_hover_effects_and_cursor_parallax(qapp, monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(AppConfig, "save", lambda self, path=None: None)

    player = OrbPlayer("siri-sheet")
    assert player.hover == 0.0
    assert player.level == 0.0

    # Entering hover wakes up the orb and advances cursor parallax
    player.set_hover_cursor(0.6, -0.4)
    for _ in range(10):
        assert player.update(33, is_hovered=True) is True
    assert player.hover > 0.85
    assert player.level > 0.1
    assert player.pointer_x > 0.45
    assert player.pointer_y < -0.25

    for orb_id in SHADER_ORB_IDS:
        p = OrbPlayer(orb_id)
        p.set_hover_cursor(0.6, -0.4)
        p.update(33, is_hovered=True)
        pm = p.current_pixmap()
        assert not pm.isNull()
        assert pm.width() == CELL_WIDTH
        assert pm.height() == CELL_HEIGHT

    # Leaving hover decays hover weight and cursor parallax back toward zero
    for _ in range(40):
        player.update(33, is_hovered=False)
    assert player.hover < 0.02
    assert abs(player.pointer_x) < 0.02
    assert abs(player.pointer_y) < 0.02

    # PetWindow tracks cursor parallax and sprite hover lift cleanly
    cfg = AppConfig(pet_id="iridescent-flow", workspace=str(tmp_path), pets_dirs=[])
    win = PetWindow(cfg, PetCatalog(extra_dirs=[]), list(BUILTIN_AGENTS))
    try:
        win._is_hovered = True
        win._update_hover_cursor(QPointF(win.width() * 0.85, win.height() * 0.2))
        win._on_tick()
        assert isinstance(win._player, OrbPlayer)
        assert win._player.target_pointer_x > 0.5
        assert win._player.target_pointer_y < -0.4
        assert not win.grab().isNull()

        win._load_pet("pocketbot")
        win._on_tick()
        assert win._hover_progress > 0.0
        assert not win.grab().isNull()
    finally:
        win.close_tray()
        win.close()


def test_speaking_amplitude_boost_and_live_audio_level(qapp, monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(AppConfig, "save", lambda self, path=None: None)
    player = OrbPlayer("duotone-flow")
    player.set_orb_state("speaking")
    for _ in range(15):
        player.update(33)
    assert 0.0 <= player.level <= 1.0
    assert player.anim_level > player.level * 1.4

    # Live audio level lets speech pauses drop cleanly below procedural energy
    player.set_audio_level(0.02)
    for _ in range(25):
        player.set_audio_level(0.02)
        player.update(33)
    assert player.level < 0.1

    player.set_audio_level(0.95)
    for _ in range(10):
        player.set_audio_level(0.95)
        player.update(33)
    assert player.level > 0.85
    assert player.anim_level > 1.3
    assert player._build_uniforms(192)["uOut"] > 1.15

    # All 4 GLSL orbs react strongly in uniforms and draw geometry during loud speech vs pauses
    rect = QRect(0, 0, CELL_WIDTH, CELL_HEIGHT)
    for oid in SHADER_ORB_IDS:
        orb = OrbPlayer(oid)
        orb.set_orb_state("speaking")
        for _ in range(25):
            orb.set_audio_level(0.02)
            orb.update(33)
        quiet_u = orb._build_uniforms(192)
        _, _, quiet_half = orb.compute_draw_geometry(rect)

        for _ in range(12):
            orb.set_audio_level(0.95)
            orb.update(33)
        loud_u = orb._build_uniforms(192)
        _, _, loud_half = orb.compute_draw_geometry(rect)

        assert loud_half > quiet_half * 1.04
        if oid == "siri-sheet":
            assert loud_u["uVoice"] > 1.3
            assert loud_u["uWarp"] > quiet_u["uWarp"] + 1.0
            assert loud_u["uRidge"] > quiet_u["uRidge"] + 0.4
        elif oid == "aura-field":
            assert loud_u["uRadius"] > quiet_u["uRadius"] + 0.2
            assert loud_u["uThick"] > quiet_u["uThick"] + 0.04
            assert loud_u["uBright"] > quiet_u["uBright"] + 0.2
        elif oid == "iridescent-flow":
            assert loud_u["uLevel"] > 1.3
        elif oid == "duotone-flow":
            assert loud_u["uOut"] > quiet_u["uOut"] + 1.0

    # Celebrate timeout keeps orb in speaking while voice playback is still active
    cfg = AppConfig(pet_id="aura-field", workspace=str(tmp_path), pets_dirs=[])
    win = PetWindow(cfg, PetCatalog(extra_dirs=[]), list(BUILTIN_AGENTS))
    try:
        win.set_voice_speaking(True)
        win.set_state(PetState.CELEBRATE, orb_state="speaking")
        win._return_to_idle()
        assert win._orb_state == "speaking"

        win.set_voice_speaking(False)
        win.set_state(PetState.CELEBRATE, orb_state="speaking")
        win._return_to_idle()
        assert win._orb_state == "idle"
    finally:
        win.close_tray()
        win.close()


def test_orb_3d_glass_uniforms_specular_and_contact_shadow(qapp) -> None:
    renderer = OrbGlRenderer.instance()

    # All 4 orbs pass uHover and uPointer into GPU uniforms
    for orb_id in SHADER_ORB_IDS:
        p = OrbPlayer(orb_id)
        p.set_hover_cursor(0.75, -0.5)
        for _ in range(15):
            p.update(33, is_hovered=True)
        uniforms = p._build_uniforms(192)
        assert uniforms["uHover"] > 0.9
        px_u, py_u = uniforms["uPointer"]
        assert px_u > 0.6
        assert py_u < -0.4

    # Cursor-tracking 3D specular highlight shifts brightness toward the hovered quadrant
    duo = OrbPlayer("duotone-flow")
    duo.set_hover_cursor(0.8, -0.6)
    for _ in range(20):
        duo.update(33, is_hovered=True)
    right_hover_gl = renderer.render("duotone-flow", 192, duo._build_uniforms(192))
    assert right_hover_gl is not None

    duo_left = OrbPlayer("duotone-flow")
    duo_left.set_hover_cursor(-0.8, -0.6)
    for _ in range(20):
        duo_left.update(33, is_hovered=True)
    left_hover_gl = renderer.render("duotone-flow", 192, duo_left._build_uniforms(192))
    assert left_hover_gl is not None

    # Upper-right sample should be brighter when cursor is upper-right than when upper-left
    ur_when_right = right_hover_gl.pixelColor(124, 66).lightness()
    ur_when_left = left_hover_gl.pixelColor(124, 66).lightness()
    assert ur_when_right > ur_when_left

    # Aura Field stays ring-shaped and airy (center much more transparent than ring)
    aura = OrbPlayer("aura-field")
    aura.update(33, is_hovered=False)
    aura_img = aura.current_pixmap().toImage()
    center_a = aura_img.pixelColor(CELL_WIDTH // 2, int(CELL_HEIGHT * 0.455)).alpha()
    ring_a = max(
        aura_img.pixelColor(CELL_WIDTH // 2 + dx, int(CELL_HEIGHT * 0.455)).alpha()
        for dx in range(28, 56, 4)
    )
    assert center_a < 60
    assert ring_a > center_a + 60

    # Floating contact shadow renders below the orb and shifts opposite the cursor
    siri_r = OrbPlayer("siri-sheet")
    siri_r.set_hover_cursor(0.9, 0.0)
    for _ in range(20):
        siri_r.update(33, is_hovered=True)
    shadow_img_r = siri_r.current_pixmap().toImage()

    siri_l = OrbPlayer("siri-sheet")
    siri_l.set_hover_cursor(-0.9, 0.0)
    for _ in range(20):
        siri_l.update(33, is_hovered=True)
    shadow_img_l = siri_l.current_pixmap().toImage()

    # Measure alpha-weighted x-centroid in the shadow band (y=180..190) below the sphere
    def shadow_centroid_x(img) -> float:
        w_sum = 0.0
        wx_sum = 0.0
        for y in range(180, 191):
            for x in range(30, CELL_WIDTH - 30):
                a = img.pixelColor(x, y).alpha()
                if a > 0:
                    w_sum += a
                    wx_sum += a * x
        assert w_sum > 0, "Contact shadow band rendered no pixels"
        return wx_sum / w_sum

    assert shadow_centroid_x(shadow_img_r) < shadow_centroid_x(shadow_img_l)


def test_liquid_glass_background_refraction_and_capture_guard(qapp, monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(AppConfig, "save", lambda self, path=None: None)
    renderer = OrbGlRenderer.instance()

    # High-contrast split desktop patch (vivid cyan top-left, warm orange bottom-right)
    bg = QImage(220, 220, QImage.Format.Format_RGBA8888)
    p_bg = QPainter(bg)
    p_bg.fillRect(0, 0, 110, 220, QColor(0, 210, 255))
    p_bg.fillRect(110, 0, 110, 220, QColor(255, 110, 20))
    p_bg.end()

    for orb_id in ("siri-sheet", "iridescent-flow", "duotone-flow"):
        player = OrbPlayer(orb_id)
        player.update(33, is_hovered=False)
        uniforms = player._build_uniforms(192)
        no_bg = renderer.render(orb_id, 192, uniforms, bg_image=None)
        with_bg = renderer.render(orb_id, 192, uniforms, bg_image=bg)
        assert no_bg is not None and with_bg is not None

        # Refracted left/right halves should differ measurably from unrefracted and from each other
        left_px = with_bg.pixelColor(66, 96)
        right_px = with_bg.pixelColor(126, 96)
        no_bg_left = no_bg.pixelColor(66, 96)
        assert (
            abs(left_px.red() - no_bg_left.red())
            + abs(left_px.green() - no_bg_left.green())
            + abs(left_px.blue() - no_bg_left.blue())
        ) > 15
        assert left_px.blue() - left_px.red() != right_px.blue() - right_px.red()

    # Aura Field stays hollow/transparent at center even with a bright background texture
    aura = OrbPlayer("aura-field")
    aura.update(33, is_hovered=False)
    aura_pm = QPixmap(CELL_WIDTH, CELL_HEIGHT)
    aura_pm.fill(QColor(0, 0, 0, 0))
    painter = QPainter(aura_pm)
    aura.paint(painter, QRect(0, 0, CELL_WIDTH, CELL_HEIGHT), bg_image=bg)
    painter.end()
    assert aura_pm.toImage().pixelColor(CELL_WIDTH // 2, int(CELL_HEIGHT * 0.455)).alpha() < 60

    # PetWindow only captures desktop when capture exclusion is confirmed active
    cfg = AppConfig(pet_id="siri-sheet", workspace=str(tmp_path), pets_dirs=[])
    win = PetWindow(cfg, PetCatalog(extra_dirs=[]), list(BUILTIN_AGENTS))
    try:
        win._capture_excluded = False
        win._capture_orb_background()
        assert isinstance(win._player, OrbPlayer)
        assert win._player._bg_image is None
    finally:
        win.close_tray()
        win.close()

