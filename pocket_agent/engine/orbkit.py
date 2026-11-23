"""Orbkit (`zzzzshawn/orbkit`) data model, TSX/JSON/JSX parsers, remote fetcher, and persistence."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field, replace
import json
import logging
from pathlib import Path
import re
import ssl
from typing import Any
from urllib.parse import parse_qs, urlparse
import urllib.request

import certifi

from pocket_agent import __version__
from pocket_agent.config import CONFIG_DIR

logger = logging.getLogger("pocket_agent.orbkit")

ORBKIT_REPO_URL = "https://github.com/zzzzshawn/orbkit"
ORBKIT_PLAYGROUND_URL = "https://orbkit.zzzzshawn.cloud/playground"
ORBKIT_API_BASE = "https://orbkit.zzzzshawn.cloud/api/v1/components"
ORBKIT_REGISTRY_BASE = "https://orbkit.zzzzshawn.cloud/r"
ORBKIT_RAW_GITHUB_BASE = "https://raw.githubusercontent.com/zzzzshawn/orbkit/main/orbs/orbs"

CUSTOM_ORBS_DIR = CONFIG_DIR / "orbs"

ORBKIT_STATES: tuple[str, ...] = ("idle", "thinking", "speaking")
ORBKIT_WRAPPERS: tuple[str, ...] = (
    "none",
    "glass",
    "ring",
    "dotted",
    "ticks",
    "reticle",
    "grid",
    "halftone",
    "scanlines",
)

BUILTIN_RESERVED_IDS: frozenset[str] = frozenset(
    {"pocketbot", "siri-sheet", "aura-field", "iridescent-flow", "duotone-flow"}
)

ORBKIT_GLSL_PRELUDE = """
#ifdef GL_ES
precision highp float;
#endif
uniform vec2 uRes;
uniform float uTime;   // slow ambient clock (half real-time)
uniform float uAnim;   // flow clock — its speed follows the output volume
uniform float uInput;  // input volume 0..1: user speech energy
uniform float uOutput; // output volume 0..1: agent speech energy

float hash(vec2 p) { return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453123); }
float noise(vec2 p) {
  vec2 i = floor(p);
  vec2 f = fract(p);
  f = f * f * (3.0 - 2.0 * f);
  return mix(
    mix(hash(i), hash(i + vec2(1.0, 0.0)), f.x),
    mix(hash(i + vec2(0.0, 1.0)), hash(i + vec2(1.0, 1.0)), f.x),
    f.y
  );
}
float fbm(vec2 p) {
  float v = 0.0;
  float a = 0.5;
  for (int i = 0; i < 5; i++) {
    v += a * noise(p);
    p = p * 2.03 + vec2(11.7, 7.3);
    a *= 0.5;
  }
  return v;
}
vec2 orbUV() { return (2.0 * gl_FragCoord.xy - uRes) / min(uRes.x, uRes.y); }

vec3 tanh3(vec3 x) {
  x = clamp(x, -10.0, 10.0);
  vec3 e = exp(2.0 * x);
  return (e - 1.0) / (e + 1.0);
}
"""

_PRECISION_LINE_RE = re.compile(
    r"^(?P<indent>[ \t]*)(?P<stmt>precision\s+(?:lowp|mediump|highp)\s+(?:float|int)\s*;)",
    re.MULTILINE,
)
_CONST_TEMPLATE_RE = re.compile(
    r"(?:export\s+)?(?:const|let)\s+([A-Za-z_]\w*)(?:\s*:\s*string)?\s*=\s*`([\s\S]*?)`\s*;?"
)
_VARIANT_DECL_RE = re.compile(r":\s*OrbVariant\s*=\s*\{")
_SHDR_SLUG_RE = re.compile(r"^[Ss][Hh][Dd][Rr]-?(\d+)$")
_SLUG_TOKEN_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")
_CREDIT_COMMENT_RE = re.compile(
    r"Shader by\s+([^\n(]+?)(?:\s*\((https?://[^\s)]+)\))?\s*(?:—|-|\n|$)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class OrbkitParamDef:
    """Single numeric uniform parameter declaration from an Orbkit variant."""

    key: str
    label: str
    min: float
    max: float
    step: float
    default: float
    integrate: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "min": self.min,
            "max": self.max,
            "step": self.step,
            "default": self.default,
            "integrate": self.integrate,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OrbkitParamDef:
        key = str(data["key"]).strip()
        default = float(data.get("default", 0.0))
        return cls(
            key=key,
            label=str(data.get("label") or key),
            min=float(data.get("min", default)),
            max=float(data.get("max", default)),
            step=float(data.get("step", 0.01)),
            default=default,
            integrate=bool(data.get("integrate", False)),
        )


@dataclass(frozen=True)
class OrbkitColorDef:
    """Single RGB hex color uniform declaration from an Orbkit variant."""

    key: str
    label: str
    default: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "default": self.default,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OrbkitColorDef:
        key = str(data["key"]).strip()
        return cls(
            key=key,
            label=str(data.get("label") or key),
            default=str(data.get("default") or "#ffffff"),
        )


@dataclass(frozen=True)
class OrbkitCatalogEntry:
    """Summary metadata for one orb returned by the Orbkit components API."""

    slug: str
    title: str
    name: str
    note: str
    description: str
    docs: str
    registry: str
    license: str = ""
    credit: dict[str, str] | None = None


@dataclass(frozen=True)
class PlaygroundSnippet:
    """Customized props parsed from an Orbkit Playground JSX snippet."""

    slug: str
    wrapper: str = "none"
    params: dict[str, float] = field(default_factory=dict)
    colors: dict[str, str] = field(default_factory=dict)
    state_presets: dict[str, dict[str, float]] = field(default_factory=dict)
    state_colors: dict[str, dict[str, str]] = field(default_factory=dict)
    state_volumes: dict[str, dict[str, float]] = field(default_factory=dict)


@dataclass(frozen=True)
class OrbkitReference:
    """Classified user input referring to an Orbkit orb or snippet."""

    kind: str  # "snippet" | "tsx" | "json" | "url" | "slug"
    value: str
    snippet: PlaygroundSnippet | None = None


@dataclass(frozen=True)
class CustomOrbDefinition:
    """Complete self-contained Orbkit orb definition persisted in Pocket Agent."""

    id: str
    name: str
    note: str
    frag: str
    params: tuple[OrbkitParamDef, ...] = ()
    colors: tuple[OrbkitColorDef, ...] = ()
    state_presets: dict[str, dict[str, float]] = field(default_factory=dict)
    state_colors: dict[str, dict[str, str]] = field(default_factory=dict)
    state_volumes: dict[str, dict[str, float]] = field(default_factory=dict)
    param_overrides: dict[str, float] = field(default_factory=dict)
    color_overrides: dict[str, str] = field(default_factory=dict)
    wrapper: str = "none"
    source_slug: str = ""
    source_url: str = ""
    license: str = ""
    credit: dict[str, str] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": 1,
            "kind": "orbkit",
            "id": self.id,
            "name": self.name,
            "note": self.note,
            "frag": self.frag,
            "params": [p.to_dict() for p in self.params],
            "colors": [c.to_dict() for c in self.colors],
            "state_presets": self.state_presets,
            "state_colors": self.state_colors,
            "state_volumes": self.state_volumes,
            "param_overrides": self.param_overrides,
            "color_overrides": self.color_overrides,
            "wrapper": self.wrapper,
            "source_slug": self.source_slug,
            "source_url": self.source_url,
            "license": self.license,
            "credit": self.credit,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CustomOrbDefinition:
        frag = str(data.get("frag") or "").strip()
        if not frag:
            raise ValueError("Orb definition is missing a fragment shader (`frag`).")
        raw_id = str(data.get("id") or data.get("source_slug") or "custom-orb")
        name = str(data.get("name") or raw_id).strip() or raw_id
        wrapper = str(data.get("wrapper") or "none").strip().lower()
        if wrapper not in ORBKIT_WRAPPERS:
            wrapper = "none"
        raw_credit = data.get("credit")
        credit = (
            {str(k): str(v) for k, v in raw_credit.items()}
            if isinstance(raw_credit, dict)
            else None
        )
        return cls(
            id=slugify_orb_id(raw_id, prefix="orbkit"),
            name=name,
            note=str(data.get("note") or ""),
            frag=frag,
            params=tuple(
                OrbkitParamDef.from_dict(p)
                for p in data.get("params", [])
                if isinstance(p, dict) and p.get("key")
            ),
            colors=tuple(
                OrbkitColorDef.from_dict(c)
                for c in data.get("colors", [])
                if isinstance(c, dict) and c.get("key")
            ),
            state_presets=_normalize_nested_float_map(data.get("state_presets")),
            state_colors=_normalize_nested_str_map(data.get("state_colors")),
            state_volumes=_normalize_nested_float_map(data.get("state_volumes")),
            param_overrides={
                str(k): float(v)
                for k, v in (data.get("param_overrides") or {}).items()
                if _is_number(v)
            },
            color_overrides={
                str(k): str(v)
                for k, v in (data.get("color_overrides") or {}).items()
                if isinstance(v, str)
            },
            wrapper=wrapper,
            source_slug=str(data.get("source_slug") or ""),
            source_url=str(data.get("source_url") or ""),
            license=str(data.get("license") or ""),
            credit=credit,
        )

    def build_fragment_shader(self) -> str:
        """Assemble the full Desktop-GL/WebGL-compatible GLSL fragment shader."""
        decls = [f"uniform float uP_{p.key};" for p in self.params] + [
            f"uniform vec3 uC_{c.key};" for c in self.colors
        ]
        sanitized_frag = _sanitize_glsl_fragment(self.frag)
        return "\n".join([ORBKIT_GLSL_PRELUDE, *decls, sanitized_frag])


def _is_number(val: Any) -> bool:
    return isinstance(val, (int, float)) and not isinstance(val, bool)


def _normalize_nested_float_map(raw: Any) -> dict[str, dict[str, float]]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict[str, float]] = {}
    for state, row in raw.items():
        if not isinstance(row, dict):
            continue
        cleaned = {str(k): float(v) for k, v in row.items() if _is_number(v)}
        if cleaned:
            out[str(state)] = cleaned
    return out


def _normalize_nested_str_map(raw: Any) -> dict[str, dict[str, str]]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict[str, str]] = {}
    for state, row in raw.items():
        if not isinstance(row, dict):
            continue
        cleaned = {str(k): str(v) for k, v in row.items() if isinstance(v, str)}
        if cleaned:
            out[str(state)] = cleaned
    return out


def _sanitize_glsl_fragment(frag: str) -> str:
    """Guard any bare GLSL ES precision statements so Desktop OpenGL 2.0 compiles cleanly."""
    return _PRECISION_LINE_RE.sub(
        r"\g<indent>#ifdef GL_ES\n\g<indent>\g<stmt>\n\g<indent>#endif",
        frag,
    )


# ---------------------------------------------------------------------------
# Pure-Python JS/TS Object Literal Parser
# ---------------------------------------------------------------------------

_SIMPLE_ESCAPES: dict[str, str] = {
    "n": "\n",
    "r": "\r",
    "t": "\t",
    "b": "\b",
    "f": "\f",
    "\\": "\\",
    "'": "'",
    '"': '"',
    "`": "`",
}


def _skip_ws_and_comments(text: str, pos: int) -> int:
    n = len(text)
    while pos < n:
        ch = text[pos]
        if ch.isspace():
            pos += 1
            continue
        if ch == "/" and pos + 1 < n:
            nxt = text[pos + 1]
            if nxt == "/":
                nl = text.find("\n", pos + 2)
                pos = n if nl == -1 else nl + 1
                continue
            if nxt == "*":
                end = text.find("*/", pos + 2)
                if end == -1:
                    return n
                pos = end + 2
                continue
        break
    return pos


def _read_js_string(text: str, pos: int) -> tuple[str, int]:
    quote = text[pos]
    pos += 1
    n = len(text)
    chars: list[str] = []
    while pos < n:
        ch = text[pos]
        if ch == quote:
            return "".join(chars), pos + 1
        if ch == "\\" and pos + 1 < n:
            esc = text[pos + 1]
            if esc in _SIMPLE_ESCAPES:
                chars.append(_SIMPLE_ESCAPES[esc])
                pos += 2
                continue
            if esc == "\n":
                pos += 2
                continue
            if esc == "\r":
                pos += 3 if pos + 2 < n and text[pos + 2] == "\n" else 2
                continue
            if esc == "x" and pos + 3 < n:
                try:
                    chars.append(chr(int(text[pos + 2 : pos + 4], 16)))
                    pos += 4
                    continue
                except ValueError:
                    pass
            if esc == "u" and pos + 5 < n:
                try:
                    chars.append(chr(int(text[pos + 2 : pos + 6], 16)))
                    pos += 6
                    continue
                except ValueError:
                    pass
            chars.append(esc)
            pos += 2
            continue
        chars.append(ch)
        pos += 1
    raise ValueError("Unterminated string literal in Orbkit source.")


def _consume_as_const(text: str, pos: int) -> int:
    pos2 = _skip_ws_and_comments(text, pos)
    if text[pos2 : pos2 + 2] == "as" and (
        pos2 + 2 == len(text) or text[pos2 + 2].isspace()
    ):
        pos3 = _skip_ws_and_comments(text, pos2 + 2)
        if text[pos3 : pos3 + 5] == "const" and (
            pos3 + 5 == len(text) or not (text[pos3 + 5].isalnum() or text[pos3 + 5] == "_")
        ):
            return pos3 + 5
    return pos


_NUMBER_RE = re.compile(r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")
_IDENT_RE = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*")


def _parse_js_value(
    text: str,
    pos: int,
    const_strings: dict[str, str] | None = None,
) -> tuple[Any, int]:
    pos = _skip_ws_and_comments(text, pos)
    n = len(text)
    if pos >= n:
        raise ValueError("Unexpected end of input while parsing Orbkit literal.")

    ch = text[pos]
    if ch in ('"', "'", "`"):
        val, pos = _read_js_string(text, pos)
        return val, _consume_as_const(text, pos)

    if ch == "{":
        obj: dict[str, Any] = {}
        pos = _skip_ws_and_comments(text, pos + 1)
        while pos < n and text[pos] != "}":
            if text[pos] in ('"', "'", "`"):
                key, pos = _read_js_string(text, pos)
            else:
                m = _IDENT_RE.match(text, pos)
                if not m:
                    raise ValueError(f"Expected object key near position {pos}.")
                key = m.group(0)
                pos = m.end()
            pos = _skip_ws_and_comments(text, pos)
            if pos >= n or text[pos] != ":":
                raise ValueError(f"Expected ':' after key {key!r} near position {pos}.")
            val, pos = _parse_js_value(text, pos + 1, const_strings)
            obj[key] = val
            pos = _skip_ws_and_comments(text, pos)
            if pos < n and text[pos] == ",":
                pos = _skip_ws_and_comments(text, pos + 1)
                continue
            break
        if pos >= n or text[pos] != "}":
            raise ValueError(f"Expected '}}' closing object near position {pos}.")
        return obj, _consume_as_const(text, pos + 1)

    if ch == "[":
        arr: list[Any] = []
        pos = _skip_ws_and_comments(text, pos + 1)
        while pos < n and text[pos] != "]":
            val, pos = _parse_js_value(text, pos, const_strings)
            arr.append(val)
            pos = _skip_ws_and_comments(text, pos)
            if pos < n and text[pos] == ",":
                pos = _skip_ws_and_comments(text, pos + 1)
                continue
            break
        if pos >= n or text[pos] != "]":
            raise ValueError(f"Expected ']' closing array near position {pos}.")
        return arr, _consume_as_const(text, pos + 1)

    m_num = _NUMBER_RE.match(text, pos)
    if m_num:
        raw_num = m_num.group(0)
        num = float(raw_num)
        return num, _consume_as_const(text, m_num.end())

    m_id = _IDENT_RE.match(text, pos)
    if m_id:
        ident = m_id.group(0)
        end = _consume_as_const(text, m_id.end())
        if ident == "true":
            return True, end
        if ident == "false":
            return False, end
        if ident in ("null", "undefined"):
            return None, end
        if const_strings and ident in const_strings:
            return const_strings[ident], end
        raise ValueError(f"Unknown identifier {ident!r} in Orbkit literal.")

    raise ValueError(f"Unexpected token {text[pos:pos + 16]!r} at position {pos}.")


# ---------------------------------------------------------------------------
# TSX, Registry JSON, Playground JSX, and Reference Parsers
# ---------------------------------------------------------------------------


def _extract_tsx_credit(source: str) -> dict[str, str] | None:
    m = _CREDIT_COMMENT_RE.search(source[:1200])
    if not m:
        return None
    author = m.group(1).strip().lstrip("@")
    url = (m.group(2) or "").strip()
    if not author:
        return None
    credit: dict[str, str] = {"author": author, "handle": author}
    if url:
        credit["url"] = url
    if "non-commercial" in source[:1200].lower():
        credit["license"] = "Non-commercial use only with attribution."
    return credit


def parse_orbkit_tsx(
    source: str,
    fallback_slug: str = "",
    source_url: str = "",
) -> CustomOrbDefinition:
    """Parse an Orbkit `OrbVariant` `.tsx` source file into a `CustomOrbDefinition`."""
    if not source or not source.strip():
        raise ValueError("Empty TSX source.")

    const_strings: dict[str, str] = {}
    for m in _CONST_TEMPLATE_RE.finditer(source):
        const_strings[m.group(1)] = m.group(2)

    variant_obj: dict[str, Any] | None = None
    m_decl = _VARIANT_DECL_RE.search(source)
    if m_decl:
        brace_idx = source.find("{", m_decl.start())
        val, _ = _parse_js_value(source, brace_idx, const_strings)
        if isinstance(val, dict):
            variant_obj = val

    if variant_obj is None:
        # Fallback: locate an object literal containing both `frag:` and `params:`
        for m_brace in re.finditer(r"\{", source):
            snippet = source[m_brace.start() : m_brace.start() + 600]
            if re.search(r"\bfrag\s*:", snippet) and re.search(r"\bparams\s*:", snippet):
                try:
                    val, _ = _parse_js_value(source, m_brace.start(), const_strings)
                    if isinstance(val, dict) and "frag" in val:
                        variant_obj = val
                        break
                except ValueError:
                    continue

    if variant_obj is None:
        raise ValueError("Could not find an OrbVariant definition with `frag` in the TSX source.")

    frag = variant_obj.get("frag")
    if not isinstance(frag, str) or not frag.strip():
        raise ValueError("OrbVariant `frag` shader string is missing or empty.")

    key = str(variant_obj.get("key") or fallback_slug or "custom-orb").strip()
    label = str(variant_obj.get("label") or key).strip() or key
    note = str(variant_obj.get("note") or "").strip()

    params = tuple(
        OrbkitParamDef.from_dict(p)
        for p in (variant_obj.get("params") or [])
        if isinstance(p, dict) and p.get("key")
    )
    colors = tuple(
        OrbkitColorDef.from_dict(c)
        for c in (variant_obj.get("colors") or [])
        if isinstance(c, dict) and c.get("key")
    )
    state_presets = _normalize_nested_float_map(variant_obj.get("statePresets"))
    state_colors = _normalize_nested_str_map(variant_obj.get("stateColors"))
    credit = _extract_tsx_credit(source)

    return CustomOrbDefinition(
        id=slugify_orb_id(key, prefix="orbkit"),
        name=label,
        note=note,
        frag=frag.strip(),
        params=params,
        colors=colors,
        state_presets=state_presets,
        state_colors=state_colors,
        source_slug=key,
        source_url=source_url,
        license=credit.get("license", "MIT") if credit else "MIT",
        credit=credit,
    )


def parse_orbkit_registry_json(
    data: dict[str, Any],
    source_url: str = "",
) -> CustomOrbDefinition:
    """Parse either a saved Pocket Agent orb JSON or an Orbkit shadcn registry JSON."""
    if data.get("kind") == "orbkit" and isinstance(data.get("frag"), str):
        orb = CustomOrbDefinition.from_dict(data)
        if source_url and not orb.source_url:
            return replace(orb, source_url=source_url)
        return orb

    files = data.get("files")
    if not isinstance(files, list):
        raise ValueError("Registry JSON does not contain a `files` array or `frag` shader.")

    tsx_content = ""
    tsx_path = ""
    for item in files:
        if not isinstance(item, dict):
            continue
        p = str(item.get("path") or "")
        if p.endswith(".tsx") and not p.endswith("orbkit-core.tsx"):
            tsx_content = str(item.get("content") or "")
            tsx_path = p
            break

    if not tsx_content:
        raise ValueError("Registry JSON does not contain an orb `.tsx` component file.")

    fallback_slug = str(data.get("name") or Path(tsx_path).stem or "custom-orb")
    orb = parse_orbkit_tsx(tsx_content, fallback_slug=fallback_slug, source_url=source_url)

    title = str(data.get("title") or "").strip()
    desc = str(data.get("description") or "").strip()
    meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
    meta_credit = meta.get("credit") if isinstance(meta.get("credit"), dict) else None

    updates: dict[str, Any] = {}
    if title and orb.name == orb.source_slug:
        updates["name"] = title
    if desc and not orb.note:
        updates["note"] = desc
    if meta_credit:
        updates["credit"] = {str(k): str(v) for k, v in meta_credit.items()}
        if "license" in meta_credit:
            updates["license"] = str(meta_credit["license"])
    return replace(orb, **updates) if updates else orb


def _component_name_to_slug(comp_name: str, source_text: str = "") -> str:
    m_imp = re.search(
        rf"import\s+\{{[^}}]*\b{re.escape(comp_name)}\b[^}}]*\}}\s+from\s+[\"']([^\"']+)[\"']",
        source_text,
    )
    if m_imp:
        tail = m_imp.group(1).rstrip("/").rsplit("/", 1)[-1]
        tail = re.sub(r"\.(tsx|ts|jsx|js)$", "", tail)
        if tail:
            return tail.lower()

    m_shdr = _SHDR_SLUG_RE.match(comp_name)
    if m_shdr:
        return f"shdr-{m_shdr.group(1).zfill(2)}" if len(m_shdr.group(1)) == 1 else f"shdr-{m_shdr.group(1)}"

    # Generic PascalCase -> kebab-case fallback
    kebab = re.sub(r"([a-z0-9])([A-Z])", r"\1-\2", comp_name)
    kebab = re.sub(r"([A-Za-z])(\d)", r"\1-\2", kebab)
    return kebab.lower()


def _find_matching_brace(text: str, open_idx: int) -> int:
    depth = 0
    pos = open_idx
    n = len(text)
    while pos < n:
        ch = text[pos]
        if ch in ('"', "'", "`"):
            _, pos = _read_js_string(text, pos)
            continue
        if ch == "/" and pos + 1 < n and text[pos + 1] in ("/", "*"):
            pos = _skip_ws_and_comments(text, pos)
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return pos
        pos += 1
    raise ValueError("Unbalanced '{' in JSX prop.")


def parse_playground_jsx(text: str) -> PlaygroundSnippet | None:
    """Parse an Orbkit Playground JSX snippet such as `<Shdr32 size={420} ... />`."""
    m_tag = re.search(r"<([A-Z][A-Za-z0-9_]*)\b([\s\S]*?)/>", text)
    if not m_tag:
        return None
    comp_name = m_tag.group(1)
    attrs = m_tag.group(2)
    slug = _component_name_to_slug(comp_name, text)

    wrapper = "none"
    params: dict[str, float] = {}
    colors: dict[str, str] = {}
    state_presets: dict[str, dict[str, float]] = {}
    state_colors: dict[str, dict[str, str]] = {}
    state_volumes: dict[str, dict[str, float]] = {}

    m_wrap = re.search(r"\bwrapper\s*=\s*([\"'])([a-zA-Z0-9_-]+)\1", attrs)
    if m_wrap:
        cand = m_wrap.group(2).lower()
        if cand in ORBKIT_WRAPPERS:
            wrapper = cand

    for prop_name in ("params", "colors", "statePresets", "stateColors", "stateVolumes"):
        m_prop = re.search(rf"\b{prop_name}\s*=\s*\{{", attrs)
        if not m_prop:
            continue
        outer_open = m_prop.end() - 1
        outer_close = _find_matching_brace(attrs, outer_open)
        inner = attrs[outer_open + 1 : outer_close].strip()
        val, _ = _parse_js_value(inner, 0)
        if not isinstance(val, dict):
            continue
        if prop_name == "params":
            params = {str(k): float(v) for k, v in val.items() if _is_number(v)}
        elif prop_name == "colors":
            colors = {str(k): str(v) for k, v in val.items() if isinstance(v, str)}
        elif prop_name == "statePresets":
            state_presets = _normalize_nested_float_map(val)
        elif prop_name == "stateColors":
            state_colors = _normalize_nested_str_map(val)
        elif prop_name == "stateVolumes":
            state_volumes = _normalize_nested_float_map(val)

    return PlaygroundSnippet(
        slug=slug,
        wrapper=wrapper,
        params=params,
        colors=colors,
        state_presets=state_presets,
        state_colors=state_colors,
        state_volumes=state_volumes,
    )


def apply_playground_snippet(
    base: CustomOrbDefinition,
    snippet: PlaygroundSnippet,
) -> CustomOrbDefinition:
    """Merge Playground JSX overrides onto a fetched base `CustomOrbDefinition`."""
    merged_presets = copy.deepcopy(base.state_presets)
    for st, row in snippet.state_presets.items():
        merged_presets.setdefault(st, {}).update(row)

    merged_colors = copy.deepcopy(base.state_colors)
    for st, row in snippet.state_colors.items():
        merged_colors.setdefault(st, {}).update(row)

    merged_volumes = copy.deepcopy(base.state_volumes)
    for st, row in snippet.state_volumes.items():
        merged_volumes.setdefault(st, {}).update(row)

    merged_param_ovr = dict(base.param_overrides)
    merged_param_ovr.update(snippet.params)

    merged_color_ovr = dict(base.color_overrides)
    merged_color_ovr.update(snippet.colors)

    wrapper = snippet.wrapper if snippet.wrapper in ORBKIT_WRAPPERS else base.wrapper
    return replace(
        base,
        state_presets=merged_presets,
        state_colors=merged_colors,
        state_volumes=merged_volumes,
        param_overrides=merged_param_ovr,
        color_overrides=merged_color_ovr,
        wrapper=wrapper,
    )


def normalize_orbkit_url(url: str) -> str:
    """Convert GitHub `blob` URLs to `raw.githubusercontent.com` URLs."""
    parsed = urlparse(url.strip())
    if parsed.netloc.lower() in ("github.com", "www.github.com"):
        parts = [p for p in parsed.path.split("/") if p]
        if len(parts) >= 5 and parts[2] == "blob":
            owner, repo, _blob, ref, *rest = parts
            return f"https://raw.githubusercontent.com/{owner}/{repo}/{ref}/{'/'.join(rest)}"
    return url.strip()


def _normalize_slug(token: str) -> str:
    clean = token.strip().strip("\"'`.,;")
    clean = clean.removeprefix("@orbkit/")
    m_shdr = _SHDR_SLUG_RE.match(clean)
    if m_shdr:
        digits = m_shdr.group(1)
        return f"shdr-{digits.zfill(2)}" if len(digits) == 1 else f"shdr-{digits}"
    return clean.lower()


def parse_orbkit_reference(raw_text: str) -> OrbkitReference:
    """Classify pasted user input (JSON, TSX, Playground JSX, URL, CLI cmd, or slug)."""
    text = raw_text.strip()
    if not text:
        raise ValueError("Please paste an Orbkit snippet, TSX file, URL, or slug (e.g. shdr-11).")

    if text.startswith("{"):
        return OrbkitReference(kind="json", value=text)

    if "OrbVariant" in text or "gl_FragColor" in text or "orbUV()" in text:
        return OrbkitReference(kind="tsx", value=text)

    snippet = parse_playground_jsx(text)
    if snippet is not None:
        return OrbkitReference(kind="snippet", value=snippet.slug, snippet=snippet)

    # Check CLI commands like:
    #   npx shadcn@latest add zzzzshawn/orbkit/shdr-11
    #   pnpm dlx shadcn@latest add @orbkit/shdr-11
    #   npx shadcn@latest add https://orbkit.zzzzshawn.cloud/r/shdr-11.json
    if "shadcn" in text and " add " in text:
        for tok in reversed(text.split()):
            tok_clean = tok.strip("\"'`")
            if tok_clean.startswith(("http://", "https://")):
                return OrbkitReference(kind="url", value=normalize_orbkit_url(tok_clean))
            if "zzzzshawn/orbkit/" in tok_clean:
                slug = _normalize_slug(tok_clean.rsplit("/", 1)[-1])
                return OrbkitReference(kind="slug", value=slug)
            if tok_clean.startswith("@orbkit/"):
                slug = _normalize_slug(tok_clean)
                return OrbkitReference(kind="slug", value=slug)

    if text.startswith(("http://", "https://")):
        parsed = urlparse(text)
        host = parsed.netloc.lower()
        if "orbkit.zzzzshawn.cloud" in host and parsed.path.rstrip("/") == "/playground":
            qs = parse_qs(parsed.query)
            orb_q = (qs.get("orb") or [""])[0]
            if orb_q:
                return OrbkitReference(kind="slug", value=_normalize_slug(orb_q))
        return OrbkitReference(kind="url", value=normalize_orbkit_url(text))

    slug = _normalize_slug(text)
    if _SLUG_TOKEN_RE.match(slug):
        return OrbkitReference(kind="slug", value=slug)

    raise ValueError(
        "Unrecognised Orbkit input. Paste a Playground JSX snippet, a `.tsx` orb file, "
        "a registry/GitHub URL, or a slug like `shdr-11`."
    )


# ---------------------------------------------------------------------------
# Remote Fetchers & Storage
# ---------------------------------------------------------------------------


def _ssl_context() -> ssl.SSLContext:
    return ssl.create_default_context(cafile=certifi.where())


def _http_get_text(url: str, timeout: float = 15.0) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": f"PocketAgent/{__version__}",
            "Accept": "application/json, text/plain, */*",
        },
    )
    with urllib.request.urlopen(req, context=_ssl_context(), timeout=timeout) as resp:
        return resp.read().decode("utf-8")


def fetch_orbkit_catalog(timeout: float = 12.0) -> list[OrbkitCatalogEntry]:
    """Fetch the live list of available orbs from the Orbkit components API."""
    payload = json.loads(_http_get_text(ORBKIT_API_BASE, timeout=timeout))
    raw_items = payload.get("components") if isinstance(payload, dict) else None
    if not isinstance(raw_items, list):
        raise ValueError("Unexpected response from Orbkit catalog API.")
    entries: list[OrbkitCatalogEntry] = []
    for item in raw_items:
        if not isinstance(item, dict) or not item.get("slug"):
            continue
        raw_credit = item.get("credit")
        credit = (
            {str(k): str(v) for k, v in raw_credit.items()}
            if isinstance(raw_credit, dict)
            else None
        )
        entries.append(
            OrbkitCatalogEntry(
                slug=str(item["slug"]),
                title=str(item.get("title") or item["slug"]),
                name=str(item.get("name") or item["slug"]),
                note=str(item.get("note") or ""),
                description=str(item.get("description") or ""),
                docs=str(item.get("docs") or ""),
                registry=str(item.get("registry") or f"{ORBKIT_REGISTRY_BASE}/{item['slug']}.json"),
                license=str(item.get("license") or ""),
                credit=credit,
            )
        )
    return entries


def fetch_orbkit_orb(slug_or_url: str, timeout: float = 15.0) -> CustomOrbDefinition:
    """Fetch and parse a single Orbkit orb by slug or direct URL."""
    target = slug_or_url.strip()
    if target.startswith(("http://", "https://")):
        url = normalize_orbkit_url(target)
        body = _http_get_text(url, timeout=timeout)
        if body.lstrip().startswith("{"):
            return parse_orbkit_registry_json(json.loads(body), source_url=url)
        fallback_slug = Path(urlparse(url).path).stem
        return parse_orbkit_tsx(body, fallback_slug=fallback_slug, source_url=url)

    slug = _normalize_slug(target)
    reg_url = f"{ORBKIT_REGISTRY_BASE}/{slug}.json"
    try:
        body = _http_get_text(reg_url, timeout=timeout)
        return parse_orbkit_registry_json(json.loads(body), source_url=reg_url)
    except Exception as reg_exc:
        raw_url = f"{ORBKIT_RAW_GITHUB_BASE}/{slug}.tsx"
        try:
            tsx = _http_get_text(raw_url, timeout=timeout)
            return parse_orbkit_tsx(tsx, fallback_slug=slug, source_url=raw_url)
        except Exception:
            raise reg_exc


def resolve_orbkit_input(raw_text: str, timeout: float = 15.0) -> CustomOrbDefinition:
    """Resolve any supported Orbkit input (TSX, JSON, Playground JSX, URL, CLI cmd, or slug)."""
    ref = parse_orbkit_reference(raw_text)
    if ref.kind == "json":
        return parse_orbkit_registry_json(json.loads(ref.value))
    if ref.kind == "tsx":
        return parse_orbkit_tsx(ref.value)
    if ref.kind == "snippet":
        assert ref.snippet is not None
        base = fetch_orbkit_orb(ref.snippet.slug, timeout=timeout)
        return apply_playground_snippet(base, ref.snippet)
    return fetch_orbkit_orb(ref.value, timeout=timeout)


# ---------------------------------------------------------------------------
# ID Helpers & Local JSON Persistence
# ---------------------------------------------------------------------------


def slugify_orb_id(name: str, prefix: str = "orbkit") -> str:
    """Sanitize an arbitrary orb name or key into a safe filesystem/catalog ID."""
    cleaned = re.sub(r"[^a-z0-9_-]+", "-", name.strip().lower()).strip("-_")
    if not cleaned:
        cleaned = f"{prefix}-orb"
    return cleaned[:64]


def unique_orb_id(preferred_name: str, existing_ids: set[str]) -> str:
    """Return a unique orb ID that never collides with built-ins or `existing_ids`."""
    base = slugify_orb_id(preferred_name)
    taken = set(existing_ids) | BUILTIN_RESERVED_IDS
    if base not in taken:
        return base
    idx = 2
    while True:
        cand = f"{base}-{idx}"
        if cand not in taken:
            return cand
        idx += 1


def load_custom_orbs(orbs_dir: Path = CUSTOM_ORBS_DIR) -> list[CustomOrbDefinition]:
    """Load all persisted Orbkit orbs from `orbs_dir`, skipping invalid files."""
    if not orbs_dir.is_dir():
        return []
    orbs: list[CustomOrbDefinition] = []
    for path in sorted(orbs_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                continue
            orb = CustomOrbDefinition.from_dict(data)
            if orb.id in BUILTIN_RESERVED_IDS:
                continue
            orbs.append(orb)
        except Exception as exc:
            logger.warning("Skipping invalid custom orb %s: %s", path, exc)
    return orbs


def save_custom_orb(orb: CustomOrbDefinition, orbs_dir: Path = CUSTOM_ORBS_DIR) -> Path:
    """Persist `orb` atomically as `<id>.json` inside `orbs_dir`."""
    if orb.id in BUILTIN_RESERVED_IDS:
        raise ValueError(f"Cannot overwrite reserved built-in ID {orb.id!r}.")
    orbs_dir.mkdir(parents=True, exist_ok=True)
    target = orbs_dir / f"{orb.id}.json"
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(orb.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(target)
    return target


def delete_custom_orb(orb_id: str, orbs_dir: Path = CUSTOM_ORBS_DIR) -> bool:
    """Delete a persisted custom orb JSON file by ID; return True if removed."""
    if orb_id in BUILTIN_RESERVED_IDS:
        return False
    target = orbs_dir / f"{slugify_orb_id(orb_id)}.json"
    if target.is_file():
        target.unlink()
        return True
    return False
