"""VoiceOrbs 7-state contract, catalog registry, and GPU GLSL shader simulators.

Includes the 4 raw-WebGL VoiceOrbs from amunozdev/voiceorbs whose GLSL fragment
shaders execute verbatim via PyQt6 OpenGL:
- siri-sheet
- aura-field
- iridescent-flow
- duotone-flow
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from PyQt6.QtCore import QPointF, QRect, QRectF, Qt
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QImage,
    QPainter,
    QPen,
    QPixmap,
    QRadialGradient,
)

from pocket_agent.engine.fsm import PetAction, PetState
from pocket_agent.engine.orb_shaders import OrbGlRenderer
from pocket_agent.engine.orbkit import CustomOrbDefinition
from pocket_agent.engine.spritesheet import CELL_HEIGHT, CELL_WIDTH

Rgb = tuple[int, int, int]
Vec3 = tuple[float, float, float]

ORB_STATES: tuple[str, ...] = (
    "idle",
    "connecting",
    "listening",
    "thinking",
    "speaking",
    "error",
    "disabled",
)

ERROR_COLOR_FROM = "#fb7185"
ERROR_COLOR_TO = "#f43f5e"

ENTER_RATE = 14.0
SETTLE_RATE = 5.0
ERROR_RATE = 10.0
BG_OVERSCAN = 1.35

PET_STATE_TO_ORB_STATE: dict[PetState, str] = {
    PetState.IDLE: "idle",
    PetState.THINKING: "thinking",
    PetState.CODING: "speaking",
    PetState.PERMISSION: "listening",
    PetState.CELEBRATE: "speaking",
    PetState.ERROR: "error",
}

ACTION_TO_ORB_STATE: dict[PetAction, str] = {
    PetAction.IDLE: "idle",
    PetAction.WAITING: "thinking",
    PetAction.REVIEW: "speaking",
    PetAction.WAVING: "listening",
    PetAction.JUMPING: "speaking",
    PetAction.FAILED: "error",
    PetAction.RUNNING: "speaking",
    PetAction.RUNNING_LEFT: "listening",
    PetAction.RUNNING_RIGHT: "listening",
}


def clamp01(value: float) -> float:
    """Clamp a floating-point number to the closed unit interval [0.0, 1.0]."""
    return min(1.0, max(0.0, value))


def hex_to_rgb(hex_str: str) -> Rgb:
    """Parse a 3- or 6-digit hex color string into an (r, g, b) tuple."""
    clean = hex_str.strip().lstrip("#")
    if len(clean) == 3:
        clean = "".join(c * 2 for c in clean)
    n = int(clean[:6], 16)
    return ((n >> 16) & 255, (n >> 8) & 255, n & 255)


def _to_linear(channel: int) -> float:
    v = channel / 255.0
    return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4


def _to_srgb(linear: float) -> int:
    c = linear * 12.92 if linear <= 0.0031308 else 1.055 * (max(0.0, linear) ** (1.0 / 2.4)) - 0.055
    return int(round(min(255.0, max(0.0, c * 255.0))))


def mix_rgb(a: Rgb, b: Rgb, t: float) -> Rgb:
    """Perceptually linear-light blend between two sRGB color tuples."""
    t = clamp01(t)
    return (
        _to_srgb(_to_linear(a[0]) + (_to_linear(b[0]) - _to_linear(a[0])) * t),
        _to_srgb(_to_linear(a[1]) + (_to_linear(b[1]) - _to_linear(a[1])) * t),
        _to_srgb(_to_linear(a[2]) + (_to_linear(b[2]) - _to_linear(a[2])) * t),
    )


def tint_rgb(rgb: Rgb, t: float) -> Rgb:
    """Blend an RGB color toward pure white."""
    return mix_rgb(rgb, (255, 255, 255), t)


def shade_rgb(rgb: Rgb, t: float) -> Rgb:
    """Blend an RGB color toward pure black."""
    return mix_rgb(rgb, (0, 0, 0), t)


def _to_unit(rgb: Rgb) -> Vec3:
    return (rgb[0] / 255.0, rgb[1] / 255.0, rgb[2] / 255.0)


def _mix3(a: Vec3, b: Vec3, t: float) -> Vec3:
    t = clamp01(t)
    return (
        a[0] + (b[0] - a[0]) * t,
        a[1] + (b[1] - a[1]) * t,
        a[2] + (b[2] - a[2]) * t,
    )


def _rotate_hue(rgb: Rgb, degrees: float) -> Rgb:
    a = math.radians(degrees)
    c = math.cos(a)
    s = math.sin(a)
    k = 1.0 / 3.0
    q = math.sqrt(k)
    m0 = c + (1.0 - c) * k
    m1 = k * (1.0 - c) - q * s
    m2 = k * (1.0 - c) + q * s
    r, g, b = rgb
    return (
        int(round(min(255.0, max(0.0, r * m0 + g * m1 + b * m2)))),
        int(round(min(255.0, max(0.0, r * m2 + g * m0 + b * m1)))),
        int(round(min(255.0, max(0.0, r * m1 + g * m2 + b * m0)))),
    )


def _mute_rgb(rgb: Rgb, amount: float) -> Rgb:
    lum = int(round(rgb[0] * 0.2126 + rgb[1] * 0.7152 + rgb[2] * 0.0722))
    return mix_rgb(rgb, (lum, lum, lum), amount)


def qcolor(rgb: Rgb, alpha: float = 1.0) -> QColor:
    """Convert an RGB tuple and normalized alpha (0..1) into a QColor."""
    a = int(round(clamp01(alpha) * 255.0))
    return QColor(rgb[0], rgb[1], rgb[2], a)


def wave(x: float) -> float:
    """Raised cosine wave oscillating smoothly in [0.0, 1.0]."""
    return 0.5 - 0.5 * math.cos(x)


def state_energy(state: str, t: float) -> float:
    """Return procedural audio energy for an OrbState at time t (seconds)."""
    if state == "listening":
        return 0.4 + 0.32 * wave(t * 17.0) + 0.18 * wave(t * 8.2 + 3.0)
    if state == "speaking":
        return 0.26 + 0.44 * wave(t * 13.4) + 0.28 * wave(t * 6.2 + 1.2)
    if state == "thinking":
        return 0.24 + 0.2 * wave(t * 4.8)
    if state == "connecting":
        return 0.12 + 0.1 * wave(t * 3.2)
    if state == "error":
        return 0.2
    return 0.0


def approach(current: float, target: float, rate: float, dt: float) -> float:
    """Exponentially approach target at the given rate over step dt."""
    return current + (target - current) * (1.0 - math.exp(-rate * max(0.0, dt)))


def state_rate(state: str) -> float:
    """Return transition speed for entering a target orb state."""
    if state in ("idle", "disabled"):
        return SETTLE_RATE
    if state == "error":
        return ERROR_RATE
    return ENTER_RATE


class StateMix:
    """Tracks normalized per-state blend weights across all 7 VoiceOrb states."""

    def __init__(self, initial: str = "idle") -> None:
        if initial not in ORB_STATES:
            initial = "idle"
        self.weights: dict[str, float] = {s: (1.0 if s == initial else 0.0) for s in ORB_STATES}

    def update(self, state: str, dt: float, rate: float | None = None) -> dict[str, float]:
        """Advance state weights exponentially toward `state` and normalize."""
        if state not in ORB_STATES:
            state = "idle"
        r = state_rate(state) if rate is None else rate
        total = 0.0
        for key in ORB_STATES:
            target = 1.0 if key == state else 0.0
            nxt = approach(self.weights[key], target, r, dt)
            self.weights[key] = 0.0 if target == 0.0 and nxt < 0.001 else nxt
            total += self.weights[key]
        if total > 0.0:
            inv = 1.0 / total
            for key in ORB_STATES:
                self.weights[key] *= inv
        return self.weights


def blend_states(weights: dict[str, float], table: dict[str, dict[str, float]]) -> dict[str, float]:
    """Blend per-state numeric parameter rows according to normalized weights."""
    out: dict[str, float] = {}
    for key, w in weights.items():
        if w <= 0.0:
            continue
        row = table.get(key)
        if not row:
            continue
        for param, val in row.items():
            out[param] = out.get(param, 0.0) + val * w
    return out


def blend_energy(weights: dict[str, float], t: float) -> float:
    """Compute weighted procedural energy across active states at time t."""
    energy = 0.0
    for key, w in weights.items():
        if w > 0.0:
            energy += w * state_energy(key, t)
    return energy


@dataclass(frozen=True)
class OrbSpec:
    """Metadata and palette definition for a VoiceOrb preset."""

    id: str
    name: str
    tagline: str
    color_from: str
    color_to: str
    tech: str = "Shader (GLSL)"


VOICE_ORBS: tuple[OrbSpec, ...] = (
    OrbSpec(
        id="siri-sheet",
        name="Siri Sheet",
        tagline="Glass sphere with a luminous sheet of light inside; the rim refracts and splits color.",
        color_from="#82f4ff",
        color_to="#8e6cff",
    ),
    OrbSpec(
        id="aura-field",
        name="Aura Field",
        tagline="Undulating ring of energy that springs open when it starts listening.",
        color_from="#34d399",
        color_to="#60a5fa",
    ),
    OrbSpec(
        id="iridescent-flow",
        name="Iridescent Flow",
        tagline="Single-pass fragment shader with flowing iridescent hues.",
        color_from="#c084fc",
        color_to="#67e8f9",
    ),
    OrbSpec(
        id="duotone-flow",
        name="Duotone Flow",
        tagline="Two-color flowing disc: your voice ripples the rings, the agent voice stirs the core.",
        color_from="#38bdf8",
        color_to="#f472b6",
    ),
)

ORB_REGISTRY: dict[str, OrbSpec] = {orb.id: orb for orb in VOICE_ORBS}

# ---------------------------------------------------------------------------
# Exact Upstream State Tables & Math Helpers from amunozdev/voiceorbs
# ---------------------------------------------------------------------------

_ERROR_FROM_RGB = hex_to_rgb(ERROR_COLOR_FROM)
_ERROR_TO_RGB = hex_to_rgb(ERROR_COLOR_TO)
_ERROR_FROM_V = _to_unit(_ERROR_FROM_RGB)
_ERROR_TO_V = _to_unit(_ERROR_TO_RGB)

# siri-sheet.tsx
_SIRI_STATE_TABLE: dict[str, dict[str, float]] = {
    "idle": {"speed": 0.3, "warp": 0.52, "ridge": 0.48, "sharp": 0.9, "zoom": 0.94, "exposure": 0.68, "mute": 0.4, "glow": 0.12, "rim": 0.55, "hear": 0.0, "voice": 0.0, "fade": 1.0},
    "connecting": {"speed": 0.5, "warp": 0.78, "ridge": 0.72, "sharp": 0.95, "zoom": 0.97, "exposure": 0.84, "mute": 0.16, "glow": 0.22, "rim": 0.7, "hear": 0.0, "voice": 0.0, "fade": 1.0},
    "listening": {"speed": 0.55, "warp": 0.66, "ridge": 0.7, "sharp": 1.0, "zoom": 1.0, "exposure": 1.04, "mute": 0.0, "glow": 0.3, "rim": 0.85, "hear": 1.0, "voice": 0.0, "fade": 1.0},
    "thinking": {"speed": 1.0, "warp": 1.0, "ridge": 1.0, "sharp": 1.0, "zoom": 1.0, "exposure": 1.0, "mute": 0.0, "glow": 0.28, "rim": 0.8, "hear": 0.0, "voice": 0.0, "fade": 1.0},
    "speaking": {"speed": 0.78, "warp": 0.72, "ridge": 0.9, "sharp": 1.0, "zoom": 1.0, "exposure": 0.96, "mute": 0.0, "glow": 0.34, "rim": 0.8, "hear": 0.0, "voice": 1.0, "fade": 1.0},
    "error": {"speed": 0.5, "warp": 0.7, "ridge": 0.8, "sharp": 1.0, "zoom": 0.98, "exposure": 0.94, "mute": 0.0, "glow": 0.3, "rim": 0.8, "hear": 0.0, "voice": 0.0, "fade": 1.0},
    "disabled": {"speed": 0.0, "warp": 0.42, "ridge": 0.36, "sharp": 0.88, "zoom": 0.93, "exposure": 0.42, "mute": 0.92, "glow": 0.0, "rim": 0.35, "hear": 0.0, "voice": 0.0, "fade": 0.62},
}

# aura-field.tsx
_AURA_TABLE: dict[str, dict[str, float]] = {
    "idle": {"speed": 10.0, "scale": 0.24, "thick": 0.05, "warp": 0.055, "brightLo": 1.0, "brightHi": 1.0, "shimmer": 0.0, "sweepRate": 0.6, "desat": 0.0},
    "connecting": {"speed": 14.0, "scale": 0.22, "thick": 0.045, "warp": 0.04, "brightLo": 0.9, "brightHi": 0.9, "shimmer": 1.0, "sweepRate": 1.3, "desat": 0.0},
    "listening": {"speed": 20.0, "scale": 0.3, "thick": 0.058, "warp": 0.1, "brightLo": 1.5, "brightHi": 2.0, "shimmer": 0.0, "sweepRate": 0.8, "desat": 0.0},
    "thinking": {"speed": 30.0, "scale": 0.27, "thick": 0.05, "warp": 0.14, "brightLo": 0.5, "brightHi": 2.5, "shimmer": 0.6, "sweepRate": 2.8, "desat": 0.0},
    "speaking": {"speed": 70.0, "scale": 0.2, "thick": 0.06, "warp": 0.12, "brightLo": 1.6, "brightHi": 1.6, "shimmer": 0.0, "sweepRate": 1.0, "desat": 0.0},
    "error": {"speed": 25.0, "scale": 0.24, "thick": 0.058, "warp": 0.18, "brightLo": 1.4, "brightHi": 1.4, "shimmer": 0.0, "sweepRate": 0.6, "desat": 0.0},
    "disabled": {"speed": 0.0, "scale": 0.2, "thick": 0.04, "warp": 0.05, "brightLo": 0.5, "brightHi": 0.5, "shimmer": 0.0, "sweepRate": 0.0, "desat": 0.85},
}
_AURA_SPEED_UNIT = 0.02
_AURA_PULSE_HALF_PERIOD = 0.35
_AURA_RADIUS_BASE = 0.26
_AURA_RADIUS_GAIN = 1.05
_AURA_SPEAK_SCALE_GAIN = 0.2
_BOUNCY_SPRING = (math.tau / 1.0, 1.0 - 0.35)
_SMOOTH_SPRING = (math.tau / 0.8, 1.0)

# iridescent-flow.tsx
_IRID_FLOW_RATE: dict[str, float] = {
    "idle": 0.22,
    "connecting": 0.55,
    "listening": 0.72,
    "thinking": 0.46,
    "speaking": 1.35,
    "error": 0.4,
    "disabled": 0.02,
}
_IRID_TIME_OFFSET = 4.7

# duotone-flow.tsx
_DUO_CHANNEL_ATTACK = 10.0
_DUO_CHANNEL_RELEASE = 4.0
_DUO_FLOW_SCALE = 1.0

# ---------------------------------------------------------------------------
# Orbkit (`zzzzshawn/orbkit`) Runtime Helpers
# ---------------------------------------------------------------------------

_ORBKIT_PARAM_EASE = 4.0


def _orbkit_spring_step(
    x: float,
    v: float,
    target: float,
    dt: float,
    omega: float = _ORBKIT_PARAM_EASE,
) -> tuple[float, float]:
    """Implicit critically damped spring step matching `orbkit-core.tsx`."""
    if dt <= 0.0:
        return x, v
    f = 1.0 + 2.0 * dt * omega
    oo = omega * omega
    hoo = dt * oo
    hhoo = dt * hoo
    det_inv = 1.0 / (f + hhoo)
    nx = (f * x + dt * v + hhoo * target) * det_inv
    nv = (v + hoo * (target - x)) * det_inv
    return nx, nv


def _orbkit_target_volumes(state: str, t: float) -> tuple[float, float]:
    """Synthesized `(input, output)` volume channels matching `orbkit-core.tsx`."""
    if state == "speaking":
        return (
            clamp01(0.65 + math.sin(t * 4.8) * 0.22),
            clamp01(0.75 + math.sin(t * 3.6) * 0.22),
        )
    if state == "thinking":
        base = 0.38 + 0.07 * math.sin(t * 0.7)
        wander = 0.05 * math.sin(t * 2.1) * math.sin(t * 0.37 + 1.2)
        return (
            clamp01(base + wander),
            clamp01(0.48 + 0.12 * math.sin(t * 1.05 + 0.6)),
        )
    return (0.0, 0.3)


def _map_to_orbkit_state(orb_state: str) -> str:
    """Map Pocket Agent's 7-state orb lifecycle onto Orbkit's 3-state contract."""
    if orb_state == "speaking":
        return "speaking"
    if orb_state in ("thinking", "connecting", "listening", "error"):
        return "thinking"
    return "idle"


def _orbkit_palette_pair(custom_orb: CustomOrbDefinition) -> tuple[str, str]:
    """Pick representative gradient colors for contact shadow / fallback rendering."""
    idle_cols = custom_orb.state_colors.get("idle", {})
    chosen: list[str] = []
    for c in custom_orb.colors:
        hex_col = custom_orb.color_overrides.get(c.key) or idle_cols.get(c.key) or c.default
        if hex_col:
            chosen.append(hex_col)
        if len(chosen) == 2:
            break
    if len(chosen) == 0:
        return ("#82f4ff", "#8e6cff")
    if len(chosen) == 1:
        return (chosen[0], chosen[0])
    return (chosen[0], chosen[1])


def _step_spring(x: float, v: float, target: float, omega: float, zeta: float, dt: float) -> tuple[float, float]:
    if dt <= 0.0:
        return x, v
    x0 = x - target
    v0 = v
    if zeta < 1.0:
        wd = omega * math.sqrt(1.0 - zeta * zeta)
        decay = math.exp(-zeta * omega * dt)
        c = math.cos(wd * dt)
        s = math.sin(wd * dt)
        b = (v0 + zeta * omega * x0) / wd
        nx = decay * (x0 * c + b * s)
        return target + nx, -zeta * omega * nx + decay * wd * (b * c - x0 * s)
    decay = math.exp(-omega * dt)
    k = v0 + omega * x0
    return target + (x0 + k * dt) * decay, (v0 - omega * dt * k) * decay


def _ping_pong(t: float) -> float:
    p = t / _AURA_PULSE_HALF_PERIOD
    leg = math.floor(p)
    f = p - leg
    eased = 1.0 - (1.0 - f) * (1.0 - f)
    return eased if leg % 2 == 0 else 1.0 - eased


def _duotone_flow_speed(output: float) -> float:
    clamped = clamp01(output)
    return 0.1 + (1.0 - (clamped - 1.0) ** 2) * 0.9


def _duotone_state_rows(t: float, level: float) -> dict[str, dict[str, float]]:
    return {
        "idle": {
            "input": 0.05 + 0.03 * math.sin(t * 0.9),
            "output": 0.12 + 0.05 * math.sin(t * 0.63 + 1.1),
            "spin": 0.05,
            "motion": 1.0,
            "saturation": 1.0,
            "brightness": 0.96,
        },
        "connecting": {
            "input": 0.14 + 0.07 * math.sin(t * 0.8),
            "output": 0.22 + 0.14 * math.sin(t * 1.05 + 0.4),
            "spin": 0.18,
            "motion": 1.0,
            "saturation": 0.92,
            "brightness": 0.94,
        },
        "listening": {
            "input": level,
            "output": 0.12,
            "spin": 0.06,
            "motion": 1.0,
            "saturation": 1.0,
            "brightness": 1.0,
        },
        "thinking": {
            "input": 0.2 + 0.1 * math.sin(t * 1.6),
            "output": 0.35 + 0.25 * math.sin(t * 2.1),
            "spin": 0.38,
            "motion": 1.0,
            "saturation": 1.0,
            "brightness": 1.0,
        },
        "speaking": {
            "input": 0.06,
            "output": level,
            "spin": 0.08,
            "motion": 1.0,
            "saturation": 1.04,
            "brightness": 1.04,
        },
        "error": {
            "input": 0.32,
            "output": 0.42,
            "spin": 0.1,
            "motion": 1.0,
            "saturation": 1.0,
            "brightness": 1.0,
        },
        "disabled": {
            "input": 0.0,
            "output": 0.0,
            "spin": 0.0,
            "motion": 0.0,
            "saturation": 0.12,
            "brightness": 0.62,
        },
    }


def _siri_build_palette(from_rgb: Rgb, to_rgb: Rgb, mute: float) -> dict[str, Vec3]:
    a = _mute_rgb(from_rgb, mute)
    b = _mute_rgb(to_rgb, mute)
    c = _mute_rgb(_rotate_hue(mix_rgb(from_rgb, to_rgb, 0.35), -38.0), mute)
    d = _mute_rgb(_rotate_hue(mix_rgb(from_rgb, to_rgb, 0.65), 42.0), mute)
    mid = mix_rgb(a, b, 0.5)
    white: Rgb = (255, 255, 255)
    return {
        "a": _to_unit(a),
        "b": _to_unit(b),
        "c": _to_unit(c),
        "d": _to_unit(d),
        "hi": _to_unit(mix_rgb(mid, white, 0.82)),
        "cool": _to_unit(mix_rgb(a, white, 0.15)),
        "warm": _to_unit(mix_rgb(d, white, 0.10)),
        "glow": _to_unit(mid),
    }


class OrbPlayer:
    """Real-time GPU shader VoiceOrb animation engine compatible with SpritePlayer."""

    def __init__(
        self,
        orb_id: str,
        static_idle: bool = False,
        custom_orb: CustomOrbDefinition | None = None,
        drop_shadow: bool = True,
    ) -> None:
        self.custom_orb: CustomOrbDefinition | None = custom_orb
        if custom_orb is not None:
            c_from, c_to = _orbkit_palette_pair(custom_orb)
            self.spec = OrbSpec(
                id=custom_orb.id,
                name=custom_orb.name,
                tagline=custom_orb.note,
                color_from=c_from,
                color_to=c_to,
                tech="Orbkit (GLSL)",
            )
            OrbGlRenderer.instance().register_shader(
                custom_orb.id,
                custom_orb.build_fragment_shader(),
            )
        else:
            self.spec = ORB_REGISTRY.get(orb_id) or ORB_REGISTRY["siri-sheet"]
        self.orb_id: str = self.spec.id
        self.static_idle: bool = static_idle
        self.drop_shadow = drop_shadow

        self.orb_state: str = "idle"
        self.mix: StateMix = StateMix("idle")
        self.time_s: float = 0.0
        self.phase_s: float = 0.0
        self.level: float = 0.0
        self._impulse: float = 0.0
        self._live_audio_level: float = 0.0
        self._live_audio_ttl: float = 0.0
        self.hover: float = 0.0
        self._was_hovered: bool = False
        self.target_pointer_x: float = 0.0
        self.target_pointer_y: float = 0.0
        self.pointer_x: float = 0.0
        self.pointer_y: float = 0.0
        self._bg_image: QImage | None = None
        self._cached_pixmap: QPixmap | None = None

        # Per-orb exact simulator state
        self._siri_sheet: float = 1.7

        self._aura_phase: float = 0.0
        self._aura_sweep: float = 0.0
        self._aura_pulse: float = 0.0
        self._aura_spring_x: float = _AURA_TABLE["idle"]["scale"]
        self._aura_spring_v: float = 0.0

        self._irid_flow: float = 0.0
        self._irid_flow_rate: float = _IRID_FLOW_RATE["idle"]

        self._duo_input: float = 0.05
        self._duo_output: float = 0.12
        self._duo_flow: float = 3.7
        self._duo_spin: float = 0.0

        # Orbkit driver state snapped to initial idle preset
        self._ok_anim: float = 12.0
        self._ok_speed: float = 0.55
        self._ok_speed_vel: float = 0.0
        self._ok_in: float = 0.0
        self._ok_out: float = 0.3
        self._ok_param_cur: dict[str, float] = {}
        self._ok_param_vel: dict[str, float] = {}
        self._ok_param_clocks: dict[str, float] = {}
        self._ok_color_cur: dict[str, Vec3] = {}
        self._ok_color_vel: dict[str, list[float]] = {}
        if custom_orb is not None:
            self._init_orbkit_state(custom_orb)

    def set_hover_cursor(self, nx: float, ny: float) -> None:
        """Update normalized cursor coordinates (-1..1) for magnetic hover parallax."""
        self.target_pointer_x = max(-1.0, min(1.0, float(nx)))
        self.target_pointer_y = max(-1.0, min(1.0, float(ny)))

    def set_background_image(self, image: QImage | None) -> None:
        """Store an overscanned desktop background image for Liquid Glass refraction."""
        self._bg_image = image if (image is not None and not image.isNull()) else None
        self._cached_pixmap = None

    def set_orb_state(self, state: str) -> None:
        """Set the target 7-state VoiceOrb lifecycle state."""
        if state in ORB_STATES and state != self.orb_state:
            self.orb_state = state
            self._cached_pixmap = None

    def set_action(self, action: PetAction, reset_index: bool = True) -> None:
        """Map a PetAction (e.g. from drag or sprite FSM) to the 7-state orb contract."""
        del reset_index
        mapped = ACTION_TO_ORB_STATE.get(action, "idle")
        self.set_orb_state(mapped)

    def pulse_level(self, boost: float = 0.65) -> None:
        """Inject a live voice/streaming amplitude pulse into the orb level."""
        self._impulse = max(self._impulse, clamp01(boost))
        self._cached_pixmap = None

    def set_audio_level(self, level: float | None) -> None:
        """Feed real-time TTS audio RMS amplitude (0..1), or clear when playback stops."""
        if level is None:
            self._live_audio_level = 0.0
            self._live_audio_ttl = 0.0
        else:
            self._live_audio_level = clamp01(level)
            self._live_audio_ttl = 0.35
        self._cached_pixmap = None

    @property
    def anim_level(self) -> float:
        """Effective animation level, amplified during speaking for higher dynamic range."""
        return self.level * (1.0 + 0.65 * self.mix.weights["speaking"])

    def update(self, delta_ms: int, is_hovered: bool = False) -> bool:
        """Advance state weights, hover transitions, audio level, and orb simulator."""
        dt = max(0.001, min(0.1, delta_ms / 1000.0))
        weights = self.mix.update(self.orb_state, dt)

        if is_hovered and not self._was_hovered:
            self.pulse_level(0.45)
        self._was_hovered = is_hovered

        hover_rate = 12.0 if is_hovered else 6.0
        self.hover = approach(self.hover, 1.0 if is_hovered else 0.0, hover_rate, dt)
        if not is_hovered:
            if self.hover < 0.001:
                self.hover = 0.0
            self.target_pointer_x = 0.0
            self.target_pointer_y = 0.0

        self.pointer_x = approach(self.pointer_x, self.target_pointer_x, 14.0, dt)
        self.pointer_y = approach(self.pointer_y, self.target_pointer_y, 14.0, dt)

        if (
            self.static_idle
            and self.orb_state == "idle"
            and not is_hovered
            and weights["idle"] > 0.999
            and self.hover < 0.001
            and abs(self.pointer_x) < 0.001
            and abs(self.pointer_y) < 0.001
        ):
            return False

        speed_mul = 1.0 + 0.35 * self.hover + 0.45 * weights["speaking"] * self.level
        d_phase = dt * speed_mul
        self.time_s += dt
        self.phase_s += d_phase

        proc_energy = blend_energy(weights, self.time_s)
        hover_energy = self.hover * (0.24 + 0.14 * wave(self.time_s * 6.5))
        self._impulse = approach(self._impulse, 0.0, 6.0, dt)
        if self._live_audio_ttl > 0.0:
            self._live_audio_ttl = max(0.0, self._live_audio_ttl - dt)
            base_energy = self._live_audio_level
            decay_rate = 14.0
        else:
            base_energy = proc_energy
            decay_rate = 7.0
        target_level = clamp01(max(base_energy, self._impulse, hover_energy))
        rate = 22.0 if target_level > self.level else decay_rate
        self.level = approach(self.level, target_level, rate, dt)

        self._step_simulator(dt, d_phase, weights)
        self._cached_pixmap = None
        return True

    def _init_orbkit_state(self, orb: CustomOrbDefinition) -> None:
        idle_presets = orb.state_presets.get("idle", {})
        for p in orb.params:
            val = orb.param_overrides.get(p.key, idle_presets.get(p.key, p.default))
            self._ok_param_cur[p.key] = float(val)
            self._ok_param_vel[p.key] = 0.0
            if p.integrate:
                self._ok_param_clocks[p.key] = 1.5
        idle_colors = orb.state_colors.get("idle", {})
        for c in orb.colors:
            hex_val = orb.color_overrides.get(c.key, idle_colors.get(c.key, c.default))
            self._ok_color_cur[c.key] = _to_unit(hex_to_rgb(hex_val))
            self._ok_color_vel[c.key] = [0.0, 0.0, 0.0]

    def _step_orbkit_simulator(self, dt: float) -> None:
        orb = self.custom_orb
        if orb is None:
            return
        ok_state = _map_to_orbkit_state(self.orb_state)
        tin, tout = _orbkit_target_volumes(ok_state, self.time_s)
        vol_ovr = orb.state_volumes.get(ok_state)
        if vol_ovr:
            if "input" in vol_ovr:
                tin = clamp01(vol_ovr["input"])
            if "output" in vol_ovr:
                tout = clamp01(vol_ovr["output"])

        if self._live_audio_ttl > 0.0 and self.orb_state == "speaking":
            tout = max(tout * 0.25, clamp01(self.anim_level))
        elif self.orb_state == "speaking":
            tout = max(tout, clamp01(self.anim_level * 0.65))
        if self.orb_state == "listening" or self.hover > 0.01:
            tin = clamp01(max(tin, self.level * 0.85, self.hover * 0.35))

        k_vol = 1.0 - math.exp(-dt * 12.0)
        self._ok_in += (tin - self._ok_in) * k_vol
        self._ok_out += (tout - self._ok_out) * k_vol

        target_speed = 0.1 + (1.0 - (self._ok_out - 1.0) ** 2) * 0.9
        self._ok_speed, self._ok_speed_vel = _orbkit_spring_step(
            self._ok_speed, self._ok_speed_vel, target_speed, dt
        )
        self._ok_anim += dt * self._ok_speed

        state_preset = orb.state_presets.get(ok_state, {})
        for p in orb.params:
            target = orb.param_overrides.get(p.key, state_preset.get(p.key, p.default))
            cur = self._ok_param_cur.get(p.key, target)
            vel = self._ok_param_vel.get(p.key, 0.0)
            nxt, nvel = _orbkit_spring_step(cur, vel, float(target), dt)
            self._ok_param_cur[p.key] = nxt
            self._ok_param_vel[p.key] = nvel
            if p.integrate:
                clk = self._ok_param_clocks.get(p.key, 1.5) + dt * self._ok_speed * nxt
                self._ok_param_clocks[p.key] = clk

        state_col = orb.state_colors.get(ok_state, {})
        for c in orb.colors:
            hex_val = orb.color_overrides.get(c.key, state_col.get(c.key, c.default))
            target_rgb = _to_unit(hex_to_rgb(hex_val))
            cur_rgb = list(self._ok_color_cur.get(c.key, target_rgb))
            vel_rgb = self._ok_color_vel.setdefault(c.key, [0.0, 0.0, 0.0])
            for i in range(3):
                cur_rgb[i], vel_rgb[i] = _orbkit_spring_step(cur_rgb[i], vel_rgb[i], target_rgb[i], dt)
            self._ok_color_cur[c.key] = (cur_rgb[0], cur_rgb[1], cur_rgb[2])

    def _step_simulator(self, dt: float, d_phase: float, weights: dict[str, float]) -> None:
        if self.custom_orb is not None:
            self._step_orbkit_simulator(dt)
            return
        level = clamp01(self.level)
        anim = self.anim_level

        if self.orb_id == "siri-sheet":
            p = blend_states(weights, _SIRI_STATE_TABLE)
            voice = p["voice"] * anim
            self._siri_sheet += d_phase * p["speed"] * (1.0 + 0.7 * voice)
        elif self.orb_id == "aura-field":
            p = blend_states(weights, _AURA_TABLE)
            target = _AURA_TABLE[self.orb_state]["scale"]
            omega, zeta = _BOUNCY_SPRING if self.orb_state == "listening" else _SMOOTH_SPRING
            self._aura_spring_x, self._aura_spring_v = _step_spring(
                self._aura_spring_x, self._aura_spring_v, target, omega, zeta, dt
            )
            rate = 1.0 + 0.35 * self.hover + 0.45 * weights["speaking"] * level
            self._aura_phase += dt * rate * p["speed"] * _AURA_SPEED_UNIT
            self._aura_sweep += dt * rate * p["sweepRate"]
            self._aura_pulse += dt * rate
        elif self.orb_id == "iridescent-flow":
            rate_target = sum(weights[k] * _IRID_FLOW_RATE[k] for k in ORB_STATES)
            self._irid_flow_rate = approach(self._irid_flow_rate, rate_target, 4.0, dt)
            self._irid_flow += d_phase * self._irid_flow_rate
        elif self.orb_id == "duotone-flow":
            row = blend_states(weights, _duotone_state_rows(self.time_s, anim))
            in_t = clamp01(row["input"])
            out_t = max(0.0, min(1.65, row["output"]))
            atk = _DUO_CHANNEL_ATTACK + 12.0 * weights["speaking"]
            rel = _DUO_CHANNEL_RELEASE + 8.0 * weights["speaking"]
            self._duo_input = approach(self._duo_input, in_t, atk if in_t > self._duo_input else rel, dt)
            self._duo_output = approach(self._duo_output, out_t, atk if out_t > self._duo_output else rel, dt)
            step = dt * (1.0 + 0.35 * self.hover + 0.45 * weights["speaking"] * level) * row["motion"]
            self._duo_flow += step * _DUO_FLOW_SCALE * _duotone_flow_speed(self._duo_output)
            self._duo_spin += step * row["spin"]

    def _build_uniforms(self, px: int) -> dict[str, Any]:
        if self.custom_orb is not None:
            uniforms: dict[str, Any] = {
                "uRes": (float(px), float(px)),
                "uTime": self.time_s * 0.5,
                "uAnim": self._ok_anim,
                "uInput": self._ok_in,
                "uOutput": self._ok_out,
            }
            for p in self.custom_orb.params:
                val = (
                    self._ok_param_clocks.get(p.key, 1.5)
                    if p.integrate
                    else self._ok_param_cur.get(p.key, p.default)
                )
                uniforms[f"uP_{p.key}"] = float(val)
            for c in self.custom_orb.colors:
                uniforms[f"uC_{c.key}"] = self._ok_color_cur.get(
                    c.key, _to_unit(hex_to_rgb(c.default))
                )
            return uniforms

        w = self.mix.weights
        level = clamp01(self.level)
        anim = self.anim_level
        hover = clamp01(self.hover)
        pointer = (self.pointer_x, self.pointer_y)
        base_from = hex_to_rgb(self.spec.color_from)
        base_to = hex_to_rgb(self.spec.color_to)

        if self.orb_id == "siri-sheet":
            p = blend_states(w, _SIRI_STATE_TABLE)
            voice = p["voice"] * anim
            f_rgb = mix_rgb(base_from, _ERROR_FROM_RGB, w["error"])
            t_rgb = mix_rgb(base_to, _ERROR_TO_RGB, w["error"])
            pal = _siri_build_palette(f_rgb, t_rgb, clamp01(p["mute"]))
            return {
                "uSize": float(px),
                "uPhase": self._siri_sheet,
                "uLevel": anim,
                "uWarp": p["warp"] * 3.2 + 1.15 * voice,
                "uRidge": p["ridge"] + 0.48 * voice + 0.25 * p["hear"] * level,
                "uSharp": p["sharp"] * 2.2,
                "uZoom": p["zoom"],
                "uExposure": p["exposure"] * 1.9 * (1.0 + 0.16 * voice + 0.1 * p["hear"] * level),
                "uGlow": p["glow"] * 0.45 * (1.0 + 0.65 * voice),
                "uRim": p["rim"],
                "uHear": p["hear"],
                "uVoice": voice,
                "uFade": p["fade"],
                "uHover": hover,
                "uPointer": pointer,
                "uColA": pal["a"],
                "uColB": pal["b"],
                "uColC": pal["c"],
                "uColD": pal["d"],
                "uHi": pal["hi"],
                "uCool": pal["cool"],
                "uWarm": pal["warm"],
                "uGlowCol": pal["glow"],
            }

        if self.orb_id == "aura-field":
            p = blend_states(w, _AURA_TABLE)
            pulse = _ping_pong(self._aura_pulse)
            bright = p["brightLo"] + (p["brightHi"] - p["brightLo"]) * pulse
            speak = w["speaking"] * anim
            scale = self._aura_spring_x + _AURA_SPEAK_SCALE_GAIN * speak
            from_v = _mix3(_to_unit(base_from), _ERROR_FROM_V, w["error"])
            to_v = _mix3(_to_unit(base_to), _ERROR_TO_V, w["error"])
            return {
                "uRes": float(px),
                "uPhase": self._aura_phase,
                "uSweep": self._aura_sweep,
                "uRadius": _AURA_RADIUS_BASE + _AURA_RADIUS_GAIN * scale,
                "uThick": p["thick"] + 0.045 * speak,
                "uWarp": p["warp"] + 0.18 * speak + 0.04 * w["listening"] * level,
                "uBright": 0.5 + 0.42 * bright + 0.24 * speak,
                "uShimmer": p["shimmer"],
                "uDesat": p["desat"],
                "uHover": hover,
                "uPointer": pointer,
                "uFrom": from_v,
                "uTo": to_v,
            }

        if self.orb_id == "iridescent-flow":
            from_v = _mix3(_to_unit(base_from), _ERROR_FROM_V, w["error"])
            to_v = _mix3(_to_unit(base_to), _ERROR_TO_V, w["error"])
            return {
                "uSize": float(px),
                "uTime": self.phase_s + _IRID_TIME_OFFSET,
                "uFlow": self._irid_flow,
                "uLevel": anim,
                "uColorFrom": from_v,
                "uColorTo": to_v,
                "uConnect": w["connecting"],
                "uListen": w["listening"],
                "uThink": w["thinking"],
                "uSpeak": w["speaking"],
                "uError": w["error"],
                "uDisabled": w["disabled"],
                "uHover": hover,
                "uPointer": pointer,
            }

        # duotone-flow
        row = blend_states(w, _duotone_state_rows(self.time_s, anim))
        from_v = _mix3(_to_unit(base_from), _ERROR_FROM_V, w["error"])
        to_v = _mix3(_to_unit(base_to), _ERROR_TO_V, w["error"])
        return {
            "uSize": float(px),
            "uFlow": self._duo_flow,
            "uSpin": self._duo_spin,
            "uIn": self._duo_input,
            "uOut": self._duo_output,
            "uSat": row["saturation"],
            "uBright": row["brightness"],
            "uHover": hover,
            "uPointer": pointer,
            "uFrom": from_v,
            "uTo": to_v,
        }

    def current_pixmap(self) -> QPixmap:
        """Render and cache the current orb frame as a CELL_WIDTH x CELL_HEIGHT QPixmap."""
        if self._cached_pixmap is None:
            pm = QPixmap(CELL_WIDTH, CELL_HEIGHT)
            pm.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pm)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            self.paint(painter, QRect(0, 0, CELL_WIDTH, CELL_HEIGHT))
            painter.end()
            self._cached_pixmap = pm
        return self._cached_pixmap

    def compute_draw_geometry(self, rect: QRect) -> tuple[float, float, float]:
        """Return `(cx, cy, draw_half)` for the orb within `rect`."""
        w = self.mix.weights
        side = float(min(rect.width(), rect.height()))
        hover = clamp01(self.hover)
        hover_scale = 1.0 + 0.065 * hover
        speak_scale = 1.0 + (0.048 * self.anim_level) * w["speaking"] * (1.0 - w["disabled"])
        shake_x = w["error"] * math.sin(self.time_s * 42.0) * 2.5

        cx_base = float(rect.center().x())
        cy_base = float(rect.top()) + float(rect.height()) * 0.455
        lift_y = -hover * side * 0.025
        cx = cx_base + shake_x + self.pointer_x * side * 0.028 * hover
        cy = cy_base + lift_y + self.pointer_y * side * 0.022 * hover
        draw_half = side * 0.415 * hover_scale * speak_scale
        return cx, cy, draw_half

    def paint(self, painter: QPainter, rect: QRect, bg_image: QImage | None = None) -> None:
        """Paint the floating contact shadow and GPU GLSL VoiceOrb onto `painter` within `rect`."""
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        w = self.mix.weights
        side = float(min(rect.width(), rect.height()))
        shake_x = w["error"] * math.sin(self.time_s * 42.0) * 2.5
        cx_base = float(rect.center().x())
        cy_base = float(rect.top()) + float(rect.height()) * 0.455
        cx, cy, draw_half = self.compute_draw_geometry(rect)
        target_rect = QRectF(cx - draw_half, cy - draw_half, draw_half * 2.0, draw_half * 2.0)

        if self.drop_shadow:
            self._draw_contact_shadow(painter, cx_base, cy_base, side, draw_half, shake_x, w)

        dev = painter.device()
        dpr = max(1.0, float(dev.devicePixelRatioF())) if dev is not None else 1.0
        render_px = max(168, min(512, int(round(draw_half * 2.0 * dpr))))
        active_bg = bg_image if (bg_image is not None and not bg_image.isNull()) else self._bg_image
        uniforms = self._build_uniforms(render_px)
        uniforms["uBgOverscan"] = BG_OVERSCAN
        img = OrbGlRenderer.instance().render(self.orb_id, render_px, uniforms, bg_image=active_bg)
        if img is not None and not img.isNull():
            if self.custom_orb is not None and w["disabled"] > 0.01:
                painter.setOpacity(max(0.25, 1.0 - 0.5 * w["disabled"]))
            painter.drawImage(target_rect, img, QRectF(img.rect()))
            if self.custom_orb is not None and self.custom_orb.wrapper != "none":
                self._draw_orbkit_wrapper(painter, target_rect, self.custom_orb.wrapper)
        else:
            self._draw_fallback(painter, QPointF(cx, cy), draw_half * 0.78, w)

        painter.restore()

    def _draw_orbkit_wrapper(self, painter: QPainter, target_rect: QRectF, wrapper: str) -> None:
        """Render lightweight vector bezel/glass wrappers matching Orbkit's wrapper presets."""
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        cx = target_rect.center().x()
        cy = target_rect.center().y()
        r = min(target_rect.width(), target_rect.height()) * 0.48
        fg = QColor(220, 232, 255)

        if wrapper == "ring":
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(qcolor((220, 232, 255), 0.28), 1.2))
            painter.drawEllipse(QPointF(cx, cy), r, r)
            painter.setPen(QPen(qcolor((220, 232, 255), 0.14), 1.0))
            painter.drawEllipse(QPointF(cx, cy), r * 0.94, r * 0.94)
        elif wrapper == "dotted":
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(qcolor((220, 232, 255), 0.42)))
            count = 36
            spin = self.time_s * 0.13
            for i in range(count):
                ang = (math.tau * i / count) + spin
                painter.drawEllipse(
                    QPointF(cx + math.cos(ang) * r, cy + math.sin(ang) * r),
                    1.3,
                    1.3,
                )
        elif wrapper == "ticks":
            for i in range(60):
                ang = math.tau * i / 60.0
                major = i % 5 == 0
                r_in = r * (0.88 if major else 0.93)
                painter.setPen(QPen(qcolor((220, 232, 255), 0.55 if major else 0.25), 1.4 if major else 0.9))
                painter.drawLine(
                    QPointF(cx + math.cos(ang) * r_in, cy + math.sin(ang) * r_in),
                    QPointF(cx + math.cos(ang) * r, cy + math.sin(ang) * r),
                )
        elif wrapper == "reticle":
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(qcolor((220, 232, 255), 0.25), 0.9))
            painter.drawEllipse(QPointF(cx, cy), r * 0.96, r * 0.96)
            painter.setPen(QPen(qcolor((220, 232, 255), 0.5), 1.2))
            left, top = cx - r, cy - r
            right, bot = cx + r, cy + r
            arm = r * 0.22
            for sx, sy, dx, dy in (
                (left, top, 1, 1),
                (right, top, -1, 1),
                (right, bot, -1, -1),
                (left, bot, 1, -1),
            ):
                painter.drawLine(QPointF(sx, sy), QPointF(sx + arm * dx, sy))
                painter.drawLine(QPointF(sx, sy), QPointF(sx, sy + arm * dy))
        elif wrapper == "glass":
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(255, 255, 255, 58), 1.3))
            painter.drawEllipse(QPointF(cx, cy), r, r)
            hi = QRadialGradient(QPointF(cx - r * 0.34, cy - r * 0.42), r * 0.48)
            hi.setColorAt(0.0, QColor(255, 255, 255, 82))
            hi.setColorAt(0.55, QColor(255, 255, 255, 20))
            hi.setColorAt(1.0, QColor(255, 255, 255, 0))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(hi))
            painter.drawEllipse(QPointF(cx, cy), r, r)
        else:
            # grid / halftone / scanlines: subtle outer hairline frame
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(fg.red(), fg.green(), fg.blue(), 46), 1.0))
            painter.drawEllipse(QPointF(cx, cy), r, r)

        painter.restore()

    def _draw_contact_shadow(
        self,
        painter: QPainter,
        cx_base: float,
        cy_base: float,
        side: float,
        draw_half: float,
        shake_x: float,
        w: dict[str, float],
    ) -> None:
        """Draw a soft grounded drop shadow beneath the orb that shifts opposite the cursor and softens on hover."""
        hover = clamp01(self.hover)
        alpha_scale = 1.0 - 0.45 * w["disabled"]
        if self.orb_id == "aura-field":
            alpha_scale *= 0.62

        # Shadow sits beneath the orb's resting base and shifts subtly opposite the cursor
        sx = cx_base + shake_x * 0.5 - self.pointer_x * side * 0.032 * hover
        sy = cy_base + side * 0.415 * 0.93 + hover * side * 0.015 - self.pointer_y * side * 0.014 * hover

        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)

        def draw_ellipse_grad(rx: float, ry: float, center_alpha: int, mid_alpha: int, rgb: Rgb) -> None:
            if rx <= 0.5 or ry <= 0.5 or center_alpha <= 0:
                return
            painter.save()
            painter.translate(sx, sy)
            painter.scale(1.0, ry / rx)
            grad = QRadialGradient(QPointF(0.0, 0.0), rx)
            grad.setColorAt(0.0, qcolor(rgb, center_alpha / 255.0))
            grad.setColorAt(0.55, qcolor(rgb, mid_alpha / 255.0))
            grad.setColorAt(1.0, qcolor(rgb, 0.0))
            painter.setBrush(QBrush(grad))
            painter.drawEllipse(QPointF(0.0, 0.0), rx, rx)
            painter.restore()

        # Wide ambient penumbra (spreads and softens as the orb lifts on hover)
        rx_outer = draw_half * (0.76 + 0.14 * hover)
        ry_outer = side * (0.072 + 0.022 * hover)
        draw_ellipse_grad(
            rx_outer,
            ry_outer,
            int(round((56 - 16 * hover) * alpha_scale)),
            int(round((20 - 6 * hover) * alpha_scale)),
            (8, 10, 20),
        )

        # Tighter contact occlusion core right beneath the sphere
        rx_inner = draw_half * (0.48 + 0.09 * hover)
        ry_inner = side * (0.040 + 0.014 * hover)
        draw_ellipse_grad(
            rx_inner,
            ry_inner,
            int(round((84 - 34 * hover) * alpha_scale)),
            int(round((28 - 12 * hover) * alpha_scale)),
            (6, 8, 16),
        )

        # Subtle tinted glass caustic bounce in the center of the shadow
        c_from = mix_rgb(hex_to_rgb(self.spec.color_from), _ERROR_FROM_RGB, w["error"])
        c_to = mix_rgb(hex_to_rgb(self.spec.color_to), _ERROR_TO_RGB, w["error"])
        caustic_rgb = mix_rgb(c_from, c_to, 0.5)
        caustic_alpha = min(255, int(round((30 + 18 * self.anim_level) * alpha_scale * (1.0 - 0.25 * hover))))
        draw_ellipse_grad(
            rx_inner * 0.68,
            ry_inner * 0.68,
            caustic_alpha,
            int(caustic_alpha * 0.35),
            caustic_rgb,
        )

        painter.restore()

    def _draw_fallback(self, painter: QPainter, center: QPointF, radius: float, w: dict[str, float]) -> None:
        """Minimal gradient fallback used only when OpenGL is unavailable."""
        c_from = mix_rgb(hex_to_rgb(self.spec.color_from), _ERROR_FROM_RGB, w["error"])
        c_to = mix_rgb(hex_to_rgb(self.spec.color_to), _ERROR_TO_RGB, w["error"])
        alpha = 1.0 - 0.5 * w["disabled"]
        grad = QRadialGradient(center, radius)
        grad.setColorAt(0.0, qcolor(tint_rgb(c_from, 0.35), alpha))
        grad.setColorAt(0.7, qcolor(c_to, 0.9 * alpha))
        grad.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(grad))
        painter.drawEllipse(center, radius, radius)
