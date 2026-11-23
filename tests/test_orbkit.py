"""Tests for Orbkit (`zzzzshawn/orbkit`) parsing, persistence, OpenGL rendering, catalog, and UI."""

from __future__ import annotations

import json
from pathlib import Path

from PyQt6.QtCore import QRect
from PyQt6.QtGui import QColor, QPainter, QPixmap

from pocket_agent.agents import BUILTIN_AGENTS
from pocket_agent.config import AppConfig
from pocket_agent.engine.catalog import PetCatalog
from pocket_agent.engine.fsm import PetState
from pocket_agent.engine.orb_shaders import OrbGlRenderer
from pocket_agent.engine.orbkit import (
    ORBKIT_WRAPPERS,
    CustomOrbDefinition,
    OrbkitColorDef,
    OrbkitParamDef,
    apply_playground_snippet,
    delete_custom_orb,
    load_custom_orbs,
    normalize_orbkit_url,
    parse_orbkit_reference,
    parse_orbkit_registry_json,
    parse_orbkit_tsx,
    parse_playground_jsx,
    save_custom_orb,
    unique_orb_id,
)
from pocket_agent.engine.orbs import OrbPlayer
from pocket_agent.engine.spritesheet import CELL_HEIGHT, CELL_WIDTH
from pocket_agent.ui.orbkit_dialog import OrbkitManagerDialog
from pocket_agent.ui.pet_window import PetWindow

_SAMPLE_TSX = """
/*
 * Shader by @XorDev (https://x.com/XorDev) — non-commercial use only with attribution.
 */
import { ShaderOrb, type OrbVariant, type ShaderOrbProps } from "../core/orbkit-core";

const TEST_FRAG = `
precision highp float;
void main() {
  vec2 uv = orbUV();
  float r = length(uv);
  float R = uP_radius;
  float mask = smoothstep(0.02, -0.02, r - R);
  float pulse = 0.5 + 0.5 * sin(uP_speed + uAnim * 2.0 + r * 6.0 - uOutput);
  // keep // inside comments safe from stripping
  vec3 col = mix(uC_core, uC_rim, clamp(r / max(R, 0.01), 0.0, 1.0));
  col = tanh3(col * (0.7 + pulse * uP_gain + uInput * 0.4));
  float a = mask;
  gl_FragColor = vec4(col * a, a);
}
`;

export const shdr99Orb: OrbVariant = {
  key: "shdr-99",
  label: "SHDR-99",
  note: "test procedural orb // with slash in string",
  frag: TEST_FRAG,
  params: [
    { key: "speed", label: "Speed", min: 0.1, max: 5, step: 0.05, default: 1.2, integrate: true },
    { key: "radius", label: "Radius", min: 0.2, max: 1.2, step: .02, default: .82 },
    { key: "gain", label: "Gain", min: 0.1, max: 3, step: 0.1, default: 1.1 },
  ],
  colors: [
    { key: "core", label: "Core", default: "#38bdf8" },
    { key: "rim", label: "Rim", default: "#c084fc" },
  ],
  statePresets: {
    idle: { gain: 1.0, speed: 0.8 },
    thinking: { gain: 1.6, speed: 1.5 },
    speaking: { gain: 2.2, speed: 2.6 },
  },
  stateColors: {
    idle: { core: "#38bdf8", rim: "#c084fc" },
    speaking: { core: "#fbbf24", rim: "#f43f5e" },
  },
} as const;
"""


def test_parse_orbkit_tsx_and_inline_frag() -> None:
    orb = parse_orbkit_tsx(_SAMPLE_TSX)
    assert orb.id == "shdr-99"
    assert orb.name == "SHDR-99"
    assert orb.note == "test procedural orb // with slash in string"
    assert [p.key for p in orb.params] == ["speed", "radius", "gain"]
    assert orb.params[0].integrate is True
    assert orb.params[1].step == 0.02
    assert orb.params[1].default == 0.82
    assert [c.key for c in orb.colors] == ["core", "rim"]
    assert orb.state_presets["speaking"]["gain"] == 2.2
    assert orb.state_colors["speaking"]["core"] == "#fbbf24"
    assert orb.credit is not None
    assert orb.credit["author"] == "XorDev"

    # Inline frag variant without const reference
    inline_tsx = """
    export const customOrb: OrbVariant = {
      key: "my-inline",
      label: "Inline Orb",
      note: "inline shader",
      frag: `void main() { vec2 uv = orbUV(); gl_FragColor = vec4(uC_tint, step(length(uv), uP_r)); }`,
      params: [{ key: "r", label: "R", min: 0.1, max: 1, step: 0.1, default: 0.7 }],
      colors: [{ key: "tint", label: "Tint", default: "#ff00aa" }]
    };
    """
    inline = parse_orbkit_tsx(inline_tsx)
    assert inline.id == "my-inline"
    assert inline.name == "Inline Orb"
    assert "orbUV()" in inline.frag
    assert inline.colors[0].default == "#ff00aa"


def test_parse_orbkit_registry_json_and_roundtrip() -> None:
    reg = {
        "name": "shdr-99",
        "title": "Test Shader 99",
        "description": "Custom registry orb",
        "meta": {"renderer": "webgl", "credit": {"handle": "XorDev", "license": "Non-commercial"}},
        "files": [
            {"path": "components/ui/orbkit-core.tsx", "content": "// core runtime"},
            {"path": "components/ui/shdr-99.tsx", "content": _SAMPLE_TSX},
        ],
    }
    orb = parse_orbkit_registry_json(reg, source_url="https://orbkit.zzzzshawn.cloud/r/shdr-99.json")
    assert orb.id == "shdr-99"
    assert orb.note == "test procedural orb // with slash in string"
    assert orb.credit == {"handle": "XorDev", "license": "Non-commercial"}
    assert orb.license == "Non-commercial"

    roundtrip = CustomOrbDefinition.from_dict(orb.to_dict())
    assert roundtrip == orb


def test_parse_playground_jsx_and_apply_overrides() -> None:
    jsx = """
    import { Shdr99 } from "@/components/ui/shdr-99";
    <Shdr99
      size={420}
      state="speaking"
      wrapper="glass"
      params={{ gain: 2.5 }}
      colors={{ core: "#10b981" }}
      statePresets={{
        thinking: { gain: 1.9 }
      }}
      stateColors={{
        speaking: { rim: "#ec4899" }
      }}
      stateVolumes={{
        idle: { input: 0.15, output: 0.45 }
      }}
    />
    """
    snippet = parse_playground_jsx(jsx)
    assert snippet is not None
    assert snippet.slug == "shdr-99"
    assert snippet.wrapper == "glass"
    assert snippet.params == {"gain": 2.5}
    assert snippet.colors == {"core": "#10b981"}
    assert snippet.state_presets == {"thinking": {"gain": 1.9}}
    assert snippet.state_colors == {"speaking": {"rim": "#ec4899"}}
    assert snippet.state_volumes == {"idle": {"input": 0.15, "output": 0.45}}

    base = parse_orbkit_tsx(_SAMPLE_TSX)
    merged = apply_playground_snippet(base, snippet)
    assert merged.wrapper == "glass"
    assert merged.param_overrides == {"gain": 2.5}
    assert merged.color_overrides == {"core": "#10b981"}
    # Key-by-key merge preserves existing keys in thinking/speaking
    assert merged.state_presets["thinking"] == {"gain": 1.9, "speed": 1.5}
    assert merged.state_colors["speaking"] == {"core": "#fbbf24", "rim": "#ec4899"}
    assert merged.state_volumes["idle"] == {"input": 0.15, "output": 0.45}


def test_parse_orbkit_reference_variants() -> None:
    assert parse_orbkit_reference("shdr-11").kind == "slug"
    assert parse_orbkit_reference("SHDR-11").value == "shdr-11"
    assert parse_orbkit_reference("Shdr5").value == "shdr-05"
    assert parse_orbkit_reference("npx shadcn@latest add zzzzshawn/orbkit/shdr-11").value == "shdr-11"
    assert parse_orbkit_reference("pnpm dlx shadcn@latest add @orbkit/shdr-32").value == "shdr-32"
    assert parse_orbkit_reference("https://orbkit.zzzzshawn.cloud/playground?orb=shdr-21&state=thinking").value == "shdr-21"

    raw_url = normalize_orbkit_url("https://github.com/zzzzshawn/orbkit/blob/main/orbs/orbs/shdr-11.tsx")
    assert raw_url == "https://raw.githubusercontent.com/zzzzshawn/orbkit/main/orbs/orbs/shdr-11.tsx"
    url_ref = parse_orbkit_reference("https://github.com/zzzzshawn/orbkit/blob/main/orbs/orbs/shdr-11.tsx")
    assert url_ref.kind == "url"
    assert url_ref.value == raw_url


def test_custom_orb_storage_and_unique_ids(tmp_path: Path) -> None:
    base = parse_orbkit_tsx(_SAMPLE_TSX)
    assert unique_orb_id("siri-sheet", set()) == "siri-sheet-2"
    assert unique_orb_id("pocketbot", set()) == "pocketbot-2"

    id1 = unique_orb_id("My Cool Orb", set())
    assert id1 == "my-cool-orb"
    orb1 = CustomOrbDefinition.from_dict({**base.to_dict(), "id": id1, "name": "My Cool Orb"})
    save_custom_orb(orb1, tmp_path)

    id2 = unique_orb_id("My Cool Orb", {id1})
    assert id2 == "my-cool-orb-2"

    loaded = load_custom_orbs(tmp_path)
    assert len(loaded) == 1
    assert loaded[0].id == "my-cool-orb"

    # Corrupt JSON files are ignored safely
    (tmp_path / "broken.json").write_text("{not valid json", encoding="utf-8")
    assert len(load_custom_orbs(tmp_path)) == 1

    assert delete_custom_orb("my-cool-orb", tmp_path) is True
    assert load_custom_orbs(tmp_path) == []


def test_orbkit_shader_compiles_and_renders_all_states_and_wrappers(qapp) -> None:
    orb = parse_orbkit_tsx(_SAMPLE_TSX)
    renderer = OrbGlRenderer.instance()
    assert renderer.is_available() is True
    err = renderer.compile_shader(orb.id, orb.build_fragment_shader())
    assert err == ""

    player = OrbPlayer(orb.id, custom_orb=orb)
    clock_start = player._ok_param_clocks["speed"]

    for st in ("idle", "connecting", "listening", "thinking", "speaking", "error", "disabled"):
        player.set_orb_state(st)
        player.pulse_level(0.8)
        assert player.update(33, is_hovered=True) is True

    assert player._ok_param_clocks["speed"] > clock_start

    player.set_orb_state("speaking")
    player.set_audio_level(0.95)
    for _ in range(8):
        player.update(33)
    uniforms = player._build_uniforms(192)
    assert uniforms["uOutput"] > 0.85
    assert "uP_speed" in uniforms and "uC_core" in uniforms

    pm = player.current_pixmap()
    assert not pm.isNull()
    assert pm.width() == CELL_WIDTH
    assert pm.height() == CELL_HEIGHT
    assert pm.toImage().pixelColor(CELL_WIDTH // 2, CELL_HEIGHT // 2).alpha() > 0

    # Ensure every Orbkit wrapper paints cleanly
    for wrapper in ORBKIT_WRAPPERS:
        wrapped_orb = CustomOrbDefinition.from_dict({**orb.to_dict(), "wrapper": wrapper})
        wp = OrbPlayer(wrapped_orb.id, custom_orb=wrapped_orb)
        wp.update(33)
        canvas = QPixmap(CELL_WIDTH, CELL_HEIGHT)
        canvas.fill(QColor(0, 0, 0, 0))
        painter = QPainter(canvas)
        wp.paint(painter, QRect(0, 0, CELL_WIDTH, CELL_HEIGHT))
        painter.end()
        assert not canvas.isNull()

    renderer.unregister_shader(orb.id)


def test_catalog_and_pet_window_custom_orb_lifecycle(qapp, monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(AppConfig, "save", lambda self, path=None: None)
    orb = parse_orbkit_tsx(_SAMPLE_TSX)
    save_custom_orb(orb, tmp_path)

    # Default catalog without custom_orbs_dir stays strictly at the 4 built-in orbs
    assert len(PetCatalog(extra_dirs=[]).orbs()) == 4

    catalog = PetCatalog(extra_dirs=[], custom_orbs_dir=tmp_path)
    custom = catalog.custom_orbs()
    assert len(custom) == 1
    assert custom[0].id == "shdr-99"
    assert custom[0].custom_orb is not None

    cfg = AppConfig(pet_id="shdr-99", workspace=str(tmp_path), pets_dirs=[])
    win = PetWindow(cfg, catalog, list(BUILTIN_AGENTS))
    try:
        assert isinstance(win._player, OrbPlayer)
        assert win._player.custom_orb is not None
        assert win._player.orb_id == "shdr-99"

        win.set_state(PetState.CODING, orb_state="speaking")
        win._on_tick()

        # Deleting the active orb and reloading falls back safely to siri-sheet
        delete_custom_orb("shdr-99", tmp_path)
        win.reload_catalog()
        assert win.config.pet_id == "siri-sheet"
        assert win._player.custom_orb is None
    finally:
        win.close_tray()
        win.close()


def test_orbkit_manager_dialog_preview_save_and_remove(qapp, tmp_path: Path) -> None:
    dlg = OrbkitManagerDialog(custom_orbs_dir=tmp_path, auto_fetch_catalog=False)
    changed: list[bool] = []
    selected: list[str] = []
    dlg.orbs_changed.connect(lambda: changed.append(True))
    dlg.orb_selected.connect(selected.append)

    try:
        orb = parse_orbkit_tsx(_SAMPLE_TSX)
        dlg._source_input.setPlainText(_SAMPLE_TSX)
        dlg._on_orb_loaded(orb)

        assert dlg._add_use_btn.isEnabled() is True
        assert "XorDev" in dlg._meta_lbl.text()

        dlg._name_edit.setText("Aurora Custom")
        dlg._save_candidate(use_now=False)

        assert changed == [True]
        assert (tmp_path / "aurora-custom.json").is_file()
        assert dlg._installed_list.count() == 1

        dlg._installed_list.setCurrentRow(0)
        dlg._on_use_installed()
        assert selected == ["aurora-custom"]

        dlg._on_remove_installed()
        assert not (tmp_path / "aurora-custom.json").is_file()
        assert len(changed) == 2
    finally:
        dlg.close()
        qapp.processEvents()
