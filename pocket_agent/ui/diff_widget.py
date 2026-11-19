"""GitHub-style unified diff widget with word-level highlights and auto-collapse."""

from __future__ import annotations

import difflib
import html
from dataclasses import dataclass

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from pocket_agent.ui.theme import FONT_MONO, ThemePalette, get_palette, lucide_pixmap


@dataclass(frozen=True)
class DiffLine:
    """Single rendered line in a diff block."""

    kind: str  # "context" | "added" | "removed" | "hunk"
    raw_html: str
    word_parts: tuple[tuple[str, str], ...] = ()  # (kind: "eq"|"add"|"rem", escaped_text)


def _word_diff_parts(old_line: str, new_line: str) -> tuple[tuple[tuple[str, str], ...], tuple[tuple[str, str], ...]]:
    """Compute inline word-level tokens for paired removed/added lines."""
    old_tokens = old_line.split(" ")
    new_tokens = new_line.split(" ")
    sm = difflib.SequenceMatcher(None, old_tokens, new_tokens)
    rem_parts: list[tuple[str, str]] = []
    add_parts: list[tuple[str, str]] = []

    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        old_chunk = html.escape(" ".join(old_tokens[i1:i2]))
        new_chunk = html.escape(" ".join(new_tokens[j1:j2]))
        if tag == "equal":
            if old_chunk:
                rem_parts.append(("eq", old_chunk))
                add_parts.append(("eq", new_chunk))
        elif tag in ("delete", "replace"):
            if old_chunk:
                rem_parts.append(("rem", old_chunk))
            if tag == "replace" and new_chunk:
                add_parts.append(("add", new_chunk))
        elif tag == "insert":
            if new_chunk:
                add_parts.append(("add", new_chunk))

    return tuple(rem_parts), tuple(add_parts)


def _format_parts(parts: tuple[tuple[str, str], ...], pal: ThemePalette) -> str:
    out: list[str] = []
    for kind, txt in parts:
        if kind == "add":
            out.append(f'<span style="background:{pal.diff_add_word};font-weight:600;">{txt}</span>')
        elif kind == "rem":
            out.append(f'<span style="background:{pal.diff_rem_word};font-weight:600;">{txt}</span>')
        else:
            out.append(txt)
    return " ".join(out) or "&nbsp;"


def compute_diff_lines(old_text: str | None, new_text: str) -> list[DiffLine]:
    """Compute diff lines with hunk headers and word-level tokens."""
    if old_text is None:
        return [
            DiffLine("added", html.escape(line) or "&nbsp;")
            for line in (new_text or "").splitlines()
        ]

    old_lines = old_text.splitlines()
    new_lines = (new_text or "").splitlines()
    diff_iter = list(difflib.unified_diff(old_lines, new_lines, lineterm="", n=2))

    body = [line for line in diff_iter if not line.startswith("--- ") and not line.startswith("+++ ")]
    result: list[DiffLine] = []
    i = 0
    while i < len(body):
        line = body[i]
        if line.startswith("@@"):
            result.append(DiffLine("hunk", html.escape(line)))
            i += 1
            continue
        if line.startswith("-") and i + 1 < len(body) and body[i + 1].startswith("+"):
            rem_parts, add_parts = _word_diff_parts(line[1:], body[i + 1][1:])
            result.append(DiffLine("removed", "", rem_parts))
            result.append(DiffLine("added", "", add_parts))
            i += 2
            continue
        if line.startswith("-"):
            result.append(DiffLine("removed", html.escape(line[1:]) or "&nbsp;"))
        elif line.startswith("+"):
            result.append(DiffLine("added", html.escape(line[1:]) or "&nbsp;"))
        else:
            content = line[1:] if line.startswith(" ") else line
            result.append(DiffLine("context", html.escape(content) or "&nbsp;"))
        i += 1
    return result


class DiffWidget(QFrame):
    """Collapsible diff viewer matching .agent-client-tool-call-diff."""

    def __init__(
        self,
        path: str,
        old_text: str | None,
        new_text: str,
        auto_collapse: bool = True,
        collapse_threshold: int = 10,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._lines = compute_diff_lines(old_text, new_text)
        self._collapse_threshold = collapse_threshold
        self._should_collapse = auto_collapse and len(self._lines) > collapse_threshold
        self._is_collapsed = self._should_collapse

        self.setObjectName("DiffWidget")
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._info_label: QLabel | None = None
        if path or old_text is None:
            info_text = f"New file · {path}" if old_text is None and path else ("New file" if old_text is None else path)
            info = QLabel(info_text, self)
            info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self._info_label = info
            root.addWidget(info)

        self._content_label = QLabel(self)
        self._content_label.setTextFormat(Qt.TextFormat.RichText)
        self._content_label.setWordWrap(True)
        self._content_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        root.addWidget(self._content_label)

        self._expand_bar: QWidget | None = None
        if self._should_collapse:
            self._expand_bar = self._build_expand_bar()
            root.addWidget(self._expand_bar)

        self.apply_theme()

    def _build_expand_bar(self) -> QWidget:
        bar = QWidget(self)
        bar.setCursor(Qt.CursorShape.PointingHandCursor)
        hl = QHBoxLayout(bar)
        hl.setContentsMargins(10, 4, 10, 4)
        hl.setSpacing(6)
        hl.addStretch(1)

        self._expand_text = QLabel(bar)
        hl.addWidget(self._expand_text)

        self._expand_icon = QLabel(bar)
        self._expand_icon.setStyleSheet("border: none; background: transparent;")
        hl.addWidget(self._expand_icon)
        hl.addStretch(1)

        bar.mousePressEvent = lambda _ev: self._toggle_collapse()  # type: ignore[method-assign]
        return bar

    def _toggle_collapse(self) -> None:
        self._is_collapsed = not self._is_collapsed
        self._refresh()

    def apply_theme(self) -> None:
        pal = get_palette()
        self.setStyleSheet(
            f"#DiffWidget {{ background: {pal.code_block_bg}; border: 1px solid {pal.border}; border-radius: 6px; }}"
        )
        if self._info_label is not None:
            self._info_label.setStyleSheet(
                f"background: {pal.bg_secondary_alt}; color: {pal.text_muted}; font-family: {FONT_MONO}; "
                f"font-size: 11px; padding: 4px 10px; border-bottom: 1px solid {pal.border};"
            )
        self._content_label.setStyleSheet(
            f"background: transparent; color: {pal.text_normal}; font-family: {FONT_MONO}; "
            f"font-size: 11px; padding: 0; border: none;"
        )
        if self._expand_bar is not None:
            self._expand_bar.setStyleSheet(
                f"background: {pal.bg_secondary_alt}; border-top: 1px solid {pal.border};"
            )
            self._expand_text.setStyleSheet(
                f"color: {pal.text_muted}; font-family: {FONT_MONO}; font-size: 11px; border: none; background: transparent;"
            )
        self._refresh()

    def _refresh(self) -> None:
        pal = get_palette()
        visible = self._lines[: self._collapse_threshold] if self._is_collapsed else self._lines
        rows: list[str] = []
        for line in visible:
            body_html = _format_parts(line.word_parts, pal) if line.word_parts else line.raw_html
            if line.kind == "hunk":
                rows.append(
                    f'<div style="background:{pal.bg_secondary_alt};color:{pal.text_muted};'
                    f'padding:3px 10px;border-top:1px solid {pal.border};border-bottom:1px solid {pal.border};'
                    f'font-weight:500;">{body_html}</div>'
                )
            elif line.kind == "added":
                rows.append(
                    f'<div style="background:{pal.diff_add_bg};padding:1px 10px;white-space:pre-wrap;">'
                    f'<span style="color:{pal.green};">+ </span>{body_html}</div>'
                )
            elif line.kind == "removed":
                rows.append(
                    f'<div style="background:{pal.diff_rem_bg};padding:1px 10px;white-space:pre-wrap;">'
                    f'<span style="color:{pal.red};">- </span>{body_html}</div>'
                )
            else:
                rows.append(
                    f'<div style="padding:1px 10px;white-space:pre-wrap;">'
                    f'<span style="color:{pal.text_faint};">&nbsp; </span>{body_html}</div>'
                )
        self._content_label.setText("".join(rows))

        if self._expand_bar is not None:
            remaining = len(self._lines) - self._collapse_threshold
            self._expand_text.setText(f"{remaining} more lines" if self._is_collapsed else "Collapse")
            icon_name = "chevron-right" if self._is_collapsed else "chevron-up"
            self._expand_icon.setPixmap(lucide_pixmap(icon_name, pal.text_muted, 13))
