"""Theme-aware message bubbles, collapsible thoughts, and tool call blocks."""

from __future__ import annotations

import html
from dataclasses import dataclass, field

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QEnterEvent
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from pocket_agent.ui.animations import SpinningIcon
from pocket_agent.ui.diff_widget import DiffWidget
from pocket_agent.ui.theme import (
    FONT_MONO,
    FONT_UI,
    get_palette,
    is_blur_enabled,
    kind_icon_name,
    lucide_icon,
    lucide_pixmap,
    render_markdown_html,
)


@dataclass
class DiffEntry:
    """Represents a file diff inside a tool call or permission review."""

    path: str
    old_text: str | None
    new_text: str


@dataclass
class ToolEntry:
    """Tracks a single tool call displayed inside an assistant turn."""

    tool_call_id: str
    title: str
    kind: str
    status: str = "in_progress"
    command: str = ""
    locations: list[str] = field(default_factory=list)
    diffs: list[DiffEntry] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)


@dataclass
class ChatTurn:
    """One visual message card in the drawer transcript."""

    role: str  # "user" | "assistant" | "system" | "error"
    text: str = ""
    thoughts: str = ""
    tools: list[ToolEntry] = field(default_factory=list)


def _elide(text: str, max_chars: int = 58) -> str:
    clean = " ".join(text.split())
    if len(clean) <= max_chars:
        return clean
    return clean[: max_chars - 1] + "…"


def _rich_text_label(parent: QWidget | None = None, font_size: int = 13) -> QLabel:
    lbl = QLabel(parent)
    lbl.setTextFormat(Qt.TextFormat.RichText)
    lbl.setWordWrap(True)
    lbl.setOpenExternalLinks(True)
    lbl.setTextInteractionFlags(
        Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.LinksAccessibleByMouse
    )
    lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
    lbl._base_font_size = font_size  # type: ignore[attr-defined]
    return lbl


class CollapsibleThoughtWidget(QWidget):
    """Minimal Obsidian-style collapsible thinking block."""

    def __init__(self, expanded_by_default: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._expanded = expanded_by_default
        self._text = ""

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 6)
        root.setSpacing(6)

        header = QWidget(self)
        header.setCursor(Qt.CursorShape.PointingHandCursor)
        hl = QHBoxLayout(header)
        hl.setContentsMargins(0, 2, 0, 2)
        hl.setSpacing(5)

        self._bulb = QLabel(header)
        hl.addWidget(self._bulb)

        self._title = QLabel("Thinking", header)
        hl.addWidget(self._title)
        hl.addStretch(1)

        self._chevron = QLabel(header)
        hl.addWidget(self._chevron)

        header.mousePressEvent = lambda _ev: self.set_expanded(not self._expanded)  # type: ignore[method-assign]
        root.addWidget(header)

        self._body = _rich_text_label(self, font_size=12)
        self._body.setVisible(self._expanded)
        root.addWidget(self._body)
        self.apply_theme()

    def set_text(self, text: str) -> None:
        self._text = text
        self._body.setText(render_markdown_html(text.strip()))

    def set_expanded(self, expanded: bool) -> None:
        self._expanded = expanded
        self._body.setVisible(expanded)
        pal = get_palette()
        self._chevron.setPixmap(lucide_pixmap("chevron-down" if expanded else "chevron-right", pal.text_muted, 13))

    def apply_theme(self) -> None:
        pal = get_palette()
        self._bulb.setPixmap(lucide_pixmap("lightbulb", pal.text_muted, 13))
        self._title.setStyleSheet(f"color: {pal.text_muted}; font-size: 12px; background: transparent;")
        self._chevron.setPixmap(lucide_pixmap("chevron-down" if self._expanded else "chevron-right", pal.text_muted, 13))
        self._body.setStyleSheet(
            f"background: transparent; color: {pal.text_muted}; font-family: {FONT_UI}; "
            f"font-size: 12px; padding-left: 16px;"
        )
        if self._text:
            self._body.setText(render_markdown_html(self._text.strip()))


class ToolCallWidget(QWidget):
    """Single-line collapsible tool call row with kind icon, spinner, locations, and diffs."""

    def __init__(self, entry: ToolEntry, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._entry = entry
        self._expanded = False
        self._diff_widgets: list[DiffWidget] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 8)
        root.setSpacing(6)

        header = QWidget(self)
        header.setCursor(Qt.CursorShape.PointingHandCursor)
        hl = QHBoxLayout(header)
        hl.setContentsMargins(0, 1, 0, 1)
        hl.setSpacing(5)

        self._kind_icon = QLabel(header)
        hl.addWidget(self._kind_icon)

        self._title_lbl = QLabel(header)
        self._title_lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        hl.addWidget(self._title_lbl, 1)

        self._status_icon = SpinningIcon("loader", get_palette().accent_hover, 13, header)
        hl.addWidget(self._status_icon)

        self._chevron = QLabel(header)
        hl.addWidget(self._chevron)

        header.mousePressEvent = lambda _ev: self.set_expanded(not self._expanded)  # type: ignore[method-assign]
        root.addWidget(header)

        self._body = QWidget(self)
        self._body.setVisible(False)
        self._body_layout = QVBoxLayout(self._body)
        self._body_layout.setContentsMargins(16, 2, 0, 2)
        self._body_layout.setSpacing(6)
        root.addWidget(self._body)

        self.update_entry(entry)

    def set_expanded(self, expanded: bool) -> None:
        self._expanded = expanded
        self._body.setVisible(expanded)
        pal = get_palette()
        self._chevron.setPixmap(lucide_pixmap("chevron-down" if expanded else "chevron-right", pal.text_muted, 13))

    def apply_theme(self) -> None:
        self.update_entry(self._entry)

    def update_entry(self, entry: ToolEntry) -> None:
        pal = get_palette()
        self._entry = entry
        self._kind_icon.setPixmap(lucide_pixmap(kind_icon_name(entry.kind), pal.text_muted, 13))
        self._title_lbl.setStyleSheet(f"color: {pal.text_muted}; font-size: 12px; background: transparent;")
        self._title_lbl.setText(_elide(entry.title or "Tool Call"))
        self._title_lbl.setToolTip(entry.title)

        if entry.status == "completed":
            self._status_icon.set_icon("check", pal.text_muted, False)
            self._status_icon.setVisible(False)
        elif entry.status == "failed":
            self._status_icon.set_icon("x", pal.red, False)
            self._status_icon.setVisible(True)
        else:
            self._status_icon.set_icon("loader", pal.accent_hover, True)
            self._status_icon.setVisible(True)

        self._chevron.setPixmap(lucide_pixmap("chevron-down" if self._expanded else "chevron-right", pal.text_muted, 13))
        self._rebuild_body()

    def _rebuild_body(self) -> None:
        pal = get_palette()
        self._diff_widgets.clear()
        while self._body_layout.count():
            item = self._body_layout.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()

        has_details = False
        if self._entry.command:
            has_details = True
            cmd = QLabel(f"<code>{html.escape(self._entry.command)}</code>", self._body)
            cmd.setTextFormat(Qt.TextFormat.RichText)
            cmd.setWordWrap(True)
            cmd.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            cmd.setStyleSheet(
                f"color: {pal.text_muted}; font-family: {FONT_MONO}; font-size: 11px; background: transparent;"
            )
            self._body_layout.addWidget(cmd)

        if self._entry.locations:
            has_details = True
            locs = QLabel("\n".join(self._entry.locations), self._body)
            locs.setWordWrap(True)
            locs.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            locs.setStyleSheet(
                f"color: {pal.text_muted}; font-family: {FONT_MONO}; font-size: 11px; background: transparent;"
            )
            self._body_layout.addWidget(locs)

        for d in self._entry.diffs:
            has_details = True
            dw = DiffWidget(d.path, d.old_text, d.new_text, auto_collapse=True, parent=self._body)
            self._diff_widgets.append(dw)
            self._body_layout.addWidget(dw)

        for out in self._entry.outputs:
            trimmed = out.strip()
            if not trimmed:
                continue
            has_details = True
            preview = trimmed if len(trimmed) <= 1800 else trimmed[:1800] + "\n…"
            lbl = QLabel(preview, self._body)
            lbl.setWordWrap(True)
            lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            lbl.setStyleSheet(
                f"background: {pal.code_block_bg}; color: {pal.text_normal}; border: 1px solid {pal.border}; "
                f"border-radius: 6px; padding: 6px 8px; font-family: {FONT_MONO}; font-size: 11px;"
            )
            self._body_layout.addWidget(lbl)

        if not has_details:
            empty = QLabel(f"[{self._entry.kind}] {self._entry.status}", self._body)
            empty.setStyleSheet(f"color: {pal.text_faint}; font-size: 11px; background: transparent;")
            self._body_layout.addWidget(empty)


class _CopyActionButton(QPushButton):
    """Hover-revealed copy button with 2-second checkmark confirmation."""

    def __init__(self, get_text_fn, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._get_text = get_text_fn
        self.setFixedSize(22, 22)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Copy message")
        self.apply_theme()
        self.clicked.connect(self._on_click)
        self._reset_timer = QTimer(self)
        self._reset_timer.setSingleShot(True)
        self._reset_timer.setInterval(2000)
        self._reset_timer.timeout.connect(self._reset_icon)

    def apply_theme(self) -> None:
        pal = get_palette()
        self.setIcon(lucide_icon("copy", pal.text_muted, 13))
        self.setStyleSheet(
            f"QPushButton {{ background: {pal.bg_secondary}; border: 1px solid {pal.border}; border-radius: 5px; }}"
            f"QPushButton:hover {{ border-color: {pal.text_muted}; }}"
        )

    def _on_click(self) -> None:
        text = self._get_text()
        if not text:
            return
        cb = QApplication.clipboard()
        if cb is not None:
            cb.setText(text)
        self.setIcon(lucide_icon("check", get_palette().green, 13))
        self._reset_timer.start()

    def _reset_icon(self) -> None:
        self.setIcon(lucide_icon("copy", get_palette().text_muted, 13))


class MessageBubbleWidget(QFrame):
    """User card or borderless assistant message matching obsidian-agent-client."""

    read_requested = pyqtSignal(str)

    def __init__(self, turn: ChatTurn, show_thoughts: bool = True, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.turn = turn
        self._text_size = 13
        self._speak_btn: QPushButton | None = None
        self._show_thoughts = show_thoughts
        self._tool_widgets: dict[str, ToolCallWidget] = {}
        self._thought_widget: CollapsibleThoughtWidget | None = None
        self._text_label: QLabel | None = None
        self._system_label: QLabel | None = None
        self._error_label: QLabel | None = None

        self.setObjectName("MessageBubble")
        self._root = QVBoxLayout(self)

        if turn.role == "user":
            self._root.setContentsMargins(13, 9, 13, 9)
            self._root.setSpacing(4)
            lbl = _rich_text_label(self, font_size=13)
            lbl.setText(html.escape(turn.text).replace("\n", "<br>"))
            self._text_label = lbl
            self._root.addWidget(lbl)
        elif turn.role == "system":
            self._root.setContentsMargins(16, 4, 16, 4)
            lbl = QLabel(turn.text, self)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setWordWrap(True)
            self._system_label = lbl
            self._root.addWidget(lbl)
        elif turn.role == "error":
            self._root.setContentsMargins(12, 10, 12, 10)
            lbl = QLabel(turn.text, self)
            lbl.setWordWrap(True)
            lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self._error_label = lbl
            self._root.addWidget(lbl)
        else:
            self._root.setContentsMargins(16, 4, 16, 8)
            self._root.setSpacing(4)
            self._sync_assistant()

        if turn.role == "assistant":
            self._speak_btn = QPushButton("Read aloud", self)
            self._speak_btn.setAccessibleName("Read this reply aloud")
            self._speak_btn.setToolTip("Read this reply using Microsoft online speech")
            self._speak_btn.clicked.connect(lambda: self.read_requested.emit(self.turn.text))
            self._speak_btn.setVisible(bool(turn.text.strip()))
            self._root.addWidget(self._speak_btn, 0, Qt.AlignmentFlag.AlignLeft)

        self._copy_btn: _CopyActionButton | None = None
        if turn.role in ("user", "assistant"):
            self._copy_btn = _CopyActionButton(lambda: self.turn.text, self)
            self._copy_btn.setVisible(False)

        self.apply_theme()

    def apply_theme(self) -> None:
        pal = get_palette()
        card_bg = pal.bg_card_blur if is_blur_enabled() else pal.bg_card
        if self.turn.role == "user":
            self.setStyleSheet(
                f"#MessageBubble {{ background: {card_bg}; border: 1px solid {pal.border}; border-radius: 10px; }}"
            )
            if self._text_label is not None:
                self._text_label.setStyleSheet(
                    f"background: transparent; color: {pal.text_normal}; font-family: {FONT_UI}; font-size: {self._text_size}px;"
                )
        elif self.turn.role == "system":
            self.setStyleSheet("#MessageBubble { background: transparent; border: none; }")
            if self._system_label is not None:
                self._system_label.setStyleSheet(f"color: {pal.text_faint}; font-size: 11px; background: transparent;")
        elif self.turn.role == "error":
            self.setStyleSheet(
                f"#MessageBubble {{ background: {card_bg}; border: 1px solid {pal.red}; border-radius: 8px; }}"
            )
            if self._error_label is not None:
                self._error_label.setStyleSheet(f"color: {pal.text_normal}; font-size: 12px; background: transparent;")
        else:
            self.setStyleSheet("#MessageBubble { background: transparent; border: none; }")
            if self._thought_widget is not None:
                self._thought_widget.apply_theme()
            for tw in self._tool_widgets.values():
                tw.apply_theme()
            if self._text_label is not None:
                self._text_label.setStyleSheet(
                    f"background: transparent; color: {pal.text_normal}; font-family: {FONT_UI}; font-size: {self._text_size}px;"
                )
                self._text_label.setText(render_markdown_html(self.turn.text))

        if self._speak_btn is not None:
            self._speak_btn.setIcon(lucide_icon("volume-2", pal.text_muted, 13))
            self._speak_btn.setStyleSheet(f"QPushButton {{ color: {pal.text_muted}; background: transparent; "
                                          f"border: 1px solid {pal.border_subtle}; border-radius: 5px; padding: 3px 6px; }}"
                                          f"QPushButton:focus {{ border-color: {pal.accent}; }}")
        if self._copy_btn is not None:
            self._copy_btn.apply_theme()

    def set_text_size(self, size: int) -> None:
        if size != self._text_size:
            self._text_size = size
            self.apply_theme()

    def set_show_thoughts(self, show: bool) -> None:
        self._show_thoughts = show
        if self._thought_widget is not None:
            self._thought_widget.setVisible(show and bool(self.turn.thoughts.strip()))

    def refresh_assistant(self) -> None:
        if self.turn.role == "assistant":
            self._sync_assistant()

    def _sync_assistant(self) -> None:
        pal = get_palette()
        if self.turn.thoughts.strip():
            if self._thought_widget is None:
                self._thought_widget = CollapsibleThoughtWidget(expanded_by_default=False, parent=self)
                self._root.insertWidget(0, self._thought_widget)
            self._thought_widget.set_text(self.turn.thoughts)
            self._thought_widget.setVisible(self._show_thoughts)

        for entry in self.turn.tools:
            widget = self._tool_widgets.get(entry.tool_call_id)
            if widget is None:
                widget = ToolCallWidget(entry, self)
                self._tool_widgets[entry.tool_call_id] = widget
                insert_idx = self._root.count() - int(self._text_label is not None) - int(self._speak_btn is not None)
                self._root.insertWidget(max(0, insert_idx), widget)
            else:
                widget.update_entry(entry)

        if self.turn.text:
            if self._text_label is None:
                self._text_label = _rich_text_label(self, font_size=13)
                self._text_label.setStyleSheet(
                    f"background: transparent; color: {pal.text_normal}; font-family: {FONT_UI}; font-size: {self._text_size}px;"
                )
                before = self._root.indexOf(self._speak_btn) if self._speak_btn else -1
                self._root.insertWidget(before, self._text_label)
            self._text_label.setText(render_markdown_html(self.turn.text))
            if self._speak_btn is not None:
                self._speak_btn.show()

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        if self._copy_btn is not None:
            self._copy_btn.move(self.width() - self._copy_btn.width() - 6, self.height() - self._copy_btn.height() - 4)

    def enterEvent(self, event: QEnterEvent) -> None:  # type: ignore[override]
        if self._copy_btn is not None and bool(self.turn.text.strip()):
            self._copy_btn.move(self.width() - self._copy_btn.width() - 6, self.height() - self._copy_btn.height() - 4)
            self._copy_btn.setVisible(True)
            self._copy_btn.raise_()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # type: ignore[override]
        if self._copy_btn is not None:
            self._copy_btn.setVisible(False)
        super().leaveEvent(event)
