"""Theme-aware overlay UI components: PlanStrip, PermissionDialog, ErrorBanner, SuggestionPopup."""

from __future__ import annotations

import html
from dataclasses import dataclass, field

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from pocket_agent.acp_bridge import AvailableCommandInfo, PermissionOptionInfo, PlanEntryInfo
from pocket_agent.ui.animations import SpinningIcon
from pocket_agent.ui.diff_widget import DiffWidget
from pocket_agent.ui.message_widgets import DiffEntry
from pocket_agent.ui.theme import (
    FONT_MONO,
    get_palette,
    kind_icon_name,
    lucide_icon,
    lucide_pixmap,
)


@dataclass
class PermissionRequestData:
    """Normalized permission request queued in PermissionDialogWidget."""

    request_id: str
    title: str
    kind: str
    options: list[PermissionOptionInfo]
    command: str = ""
    locations: list[str] = field(default_factory=list)
    diffs: list[DiffEntry] = field(default_factory=list)


class PlanStripWidget(QFrame):
    """Ambient plan bar under the header with expandable step list."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._entries: list[PlanEntryInfo] = []
        self._expanded = False

        self.setObjectName("PlanStrip")
        self.setStyleSheet("#PlanStrip { background: transparent; border: none; }")
        self.setVisible(False)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 2, 14, 4)
        root.setSpacing(4)

        summary = QWidget(self)
        summary.setCursor(Qt.CursorShape.PointingHandCursor)
        sl = QHBoxLayout(summary)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(6)

        self._icon = QLabel(summary)
        sl.addWidget(self._icon)

        self._count_lbl = QLabel(summary)
        sl.addWidget(self._count_lbl)

        self._current_lbl = QLabel(summary)
        sl.addWidget(self._current_lbl, 1)

        self._chevron = QLabel(summary)
        sl.addWidget(self._chevron)

        summary.mousePressEvent = lambda _ev: self.set_expanded(not self._expanded)  # type: ignore[method-assign]
        root.addWidget(summary)

        self._entries_container = QWidget(self)
        self._entries_container.setVisible(False)
        self._entries_layout = QVBoxLayout(self._entries_container)
        self._entries_layout.setContentsMargins(0, 2, 0, 2)
        self._entries_layout.setSpacing(3)
        root.addWidget(self._entries_container)

        self.apply_theme()

    def apply_theme(self) -> None:
        pal = get_palette()
        self._icon.setPixmap(lucide_pixmap("list-checks", pal.text_muted, 13))
        self._count_lbl.setStyleSheet(
            f"color: {pal.text_muted}; font-size: 11px; font-weight: 600; background: transparent;"
        )
        self._current_lbl.setStyleSheet(f"color: {pal.text_muted}; font-size: 11px; background: transparent;")
        self._chevron.setPixmap(
            lucide_pixmap("chevron-down" if self._expanded else "chevron-right", pal.text_muted, 13)
        )
        if self._entries:
            self._rebuild_rows()

    def set_entries(self, entries: list[PlanEntryInfo]) -> None:
        self._entries = entries
        if not entries:
            self.setVisible(False)
            return

        total = len(entries)
        completed = sum(1 for e in entries if e.status == "completed")
        current = next((e for e in entries if e.status == "in_progress"), None) or next(
            (e for e in entries if e.status == "pending"), None
        )

        self._count_lbl.setText(f"Plan {completed}/{total}")
        if current:
            clean = " ".join(current.content.split())
            if len(clean) > 48:
                clean = clean[:47] + "…"
            self._current_lbl.setText(f"· {clean}")
            self._current_lbl.setVisible(True)
        else:
            self._current_lbl.clear()
            self._current_lbl.setVisible(False)

        self._rebuild_rows()
        self.setVisible(True)

    def clear(self) -> None:
        self._entries = []
        self._expanded = False
        self._entries_container.setVisible(False)
        self.setVisible(False)

    def set_expanded(self, expanded: bool) -> None:
        self._expanded = expanded
        self._entries_container.setVisible(expanded)
        pal = get_palette()
        self._chevron.setPixmap(lucide_pixmap("chevron-down" if expanded else "chevron-right", pal.text_muted, 13))

    def _rebuild_rows(self) -> None:
        pal = get_palette()
        while self._entries_layout.count():
            item = self._entries_layout.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()

        for entry in self._entries:
            row = QFrame(self._entries_container)
            row.setObjectName("PlanRow")
            row.setStyleSheet(
                f"#PlanRow {{ border-left: 2px solid {pal.accent if entry.status == 'in_progress' else pal.border}; "
                f"padding-left: 6px; background: transparent; }}"
            )
            rl = QHBoxLayout(row)
            rl.setContentsMargins(6, 1, 2, 1)
            rl.setSpacing(6)

            if entry.status == "in_progress":
                rl.addWidget(SpinningIcon("loader", pal.accent, 12, row))
            else:
                ico = QLabel(row)
                name = "check" if entry.status == "completed" else "circle"
                col = pal.text_muted if entry.status == "completed" else pal.text_faint
                ico.setPixmap(lucide_pixmap(name, col, 12))
                rl.addWidget(ico)

            txt = QLabel(entry.content, row)
            txt.setWordWrap(True)
            if entry.status == "completed":
                txt.setStyleSheet(
                    f"color: {pal.text_muted}; font-size: 11px; text-decoration: line-through; background: transparent;"
                )
            elif entry.status == "in_progress":
                txt.setStyleSheet(
                    f"color: {pal.text_normal}; font-size: 11px; font-weight: 600; background: transparent;"
                )
            else:
                txt.setStyleSheet(f"color: {pal.text_muted}; font-size: 11px; background: transparent;")
            rl.addWidget(txt, 1)
            self._entries_layout.addWidget(row)


class PermissionDialogWidget(QFrame):
    """Floating permission review card above the input box with queue support."""

    permission_decided = pyqtSignal(str, object)  # request_id, option_id | None

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._queue: list[PermissionRequestData] = []

        self.setObjectName("PermissionDialog")
        self.setAccessibleName("Review permission request")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setVisible(False)

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        header = QHBoxLayout()
        header.setSpacing(6)
        self._kind_icon = QLabel(self)
        header.addWidget(self._kind_icon)

        self._title_lbl = QLabel(self)
        self._title_lbl.setTextFormat(Qt.TextFormat.PlainText)
        self._title_lbl.setWordWrap(True)
        header.addWidget(self._title_lbl, 1)

        self._queue_badge = QLabel(self)
        self._queue_badge.setVisible(False)
        header.addWidget(self._queue_badge)
        root.addLayout(header)

        self._review_scroll = QScrollArea(self)
        self._review_scroll.setWidgetResizable(True)
        self._review_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._review_scroll.setMaximumHeight(160)
        self._review_scroll.setStyleSheet("background: transparent;")
        self._review_host = QWidget()
        self._review_host.setStyleSheet("background: transparent;")
        self._review_layout = QVBoxLayout(self._review_host)
        self._review_layout.setContentsMargins(0, 0, 0, 0)
        self._review_layout.setSpacing(6)
        self._review_scroll.setWidget(self._review_host)
        self._review_scroll.setVisible(False)
        root.addWidget(self._review_scroll)

        self._options_frame = QFrame(self)
        self._options_frame.setObjectName("PermissionOptions")
        self._options_layout = QVBoxLayout(self._options_frame)
        self._options_layout.setContentsMargins(0, 0, 0, 0)
        self._options_layout.setSpacing(0)
        root.addWidget(self._options_frame)

        self.apply_theme()

    def apply_theme(self) -> None:
        pal = get_palette()
        self.setStyleSheet(
            f"#PermissionDialog {{ background: {pal.bg_secondary}; border: 1px solid {pal.border}; border-radius: 10px; }}"
        )
        self._title_lbl.setStyleSheet(
            f"color: {pal.text_normal}; font-size: 12px; font-weight: 600; background: transparent;"
        )
        self._queue_badge.setStyleSheet(
            f"background: {pal.border}; color: {pal.text_muted}; border-radius: 8px; padding: 1px 6px; font-size: 11px;"
        )
        self._options_frame.setStyleSheet(
            f"#PermissionOptions {{ background: {pal.bg_card}; border: 1px solid {pal.border}; border-radius: 6px; }}"
        )
        if self._queue:
            self._render_current()

    @property
    def current_request(self) -> PermissionRequestData | None:
        return self._queue[0] if self._queue else None

    def enqueue(self, req: PermissionRequestData) -> None:
        self._queue.append(req)
        self._render_current()

    def decide_current(self, option_id: str | None) -> None:
        if not self._queue:
            self.setVisible(False)
            return
        current = self._queue.pop(0)
        self._render_current()
        self.permission_decided.emit(current.request_id, option_id)

    def clear(self) -> None:
        self._queue.clear()
        self.setVisible(False)

    def _render_current(self) -> None:
        if not self._queue:
            self.setVisible(False)
            return

        pal = get_palette()
        req = self._queue[0]
        queued = len(self._queue) - 1
        self._kind_icon.setPixmap(lucide_pixmap(kind_icon_name(req.kind), pal.text_muted, 14))
        self._title_lbl.setText(req.title or "Permission required")
        if queued > 0:
            self._queue_badge.setText(f"+{queued}")
            self._queue_badge.setVisible(True)
        else:
            self._queue_badge.setVisible(False)

        while self._review_layout.count():
            item = self._review_layout.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()

        has_review = False
        if req.command:
            has_review = True
            cmd = QLabel(f"<code>{html.escape(req.command)}</code>", self._review_host)
            cmd.setTextFormat(Qt.TextFormat.RichText)
            cmd.setWordWrap(True)
            cmd.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            cmd.setStyleSheet(
                f"color: {pal.text_muted}; font-family: {FONT_MONO}; font-size: 11px; "
                f"background: {pal.code_block_bg}; border: 1px solid {pal.border}; border-radius: 4px; padding: 6px 8px;"
            )
            self._review_layout.addWidget(cmd)

        if req.locations:
            has_review = True
            locs = QLabel("\n".join(req.locations), self._review_host)
            locs.setWordWrap(True)
            locs.setStyleSheet(
                f"color: {pal.text_muted}; font-family: {FONT_MONO}; font-size: 11px; background: transparent;"
            )
            self._review_layout.addWidget(locs)

        for d in req.diffs:
            has_review = True
            self._review_layout.addWidget(
                DiffWidget(d.path, d.old_text, d.new_text, auto_collapse=False, parent=self._review_host)
            )

        self._review_scroll.setVisible(has_review)

        while self._options_layout.count():
            item = self._options_layout.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()

        options = list(req.options)
        for idx, opt in enumerate(options):
            btn = self._make_option_button(opt.name, opt.kind, idx > 0)
            btn.clicked.connect(lambda _c=False, oid=opt.option_id: self.decide_current(oid))
            self._options_layout.addWidget(btn)

        if not any("reject" in o.kind for o in options):
            deny_btn = self._make_option_button("Deny", "reject_once", len(options) > 0)
            deny_btn.clicked.connect(lambda _c=False: self.decide_current(None))
            self._options_layout.addWidget(deny_btn)

        self.setVisible(True)

    def _make_option_button(self, label: str, kind: str, top_border: bool) -> QPushButton:
        pal = get_palette()
        if kind == "allow_always":
            icon_name, icon_color = "check", pal.green
        elif "allow" in kind:
            icon_name, icon_color = "check", pal.orange
        else:
            icon_name, icon_color = "x", pal.red

        btn = QPushButton(f"  {label}", self._options_frame)
        btn.setIcon(lucide_icon(icon_name, icon_color, 13))
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        border_rule = f"border-top: 1px solid {pal.border};" if top_border else "border: none;"
        btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {pal.text_normal}; {border_rule} "
            f"border-radius: 0px; padding: 7px 10px; text-align: left; font-size: 12px; font-weight: 500; }}"
            f"QPushButton:hover {{ background: {pal.bg_hover}; }}"
        )
        return btn


class ErrorBannerWidget(QFrame):
    """Dismissible error or info overlay positioned above the input box."""

    closed = pyqtSignal()
    reconnect_requested = pyqtSignal()
    setup_guide_requested = pyqtSignal()
    update_action_requested = pyqtSignal()
    release_notes_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ErrorBanner")
        self._variant = "error"
        self.setVisible(False)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(6)

        header = QHBoxLayout()
        self._title_lbl = QLabel(self)
        header.addWidget(self._title_lbl, 1)

        self._close_btn = QPushButton(self)
        self._close_btn.setFixedSize(18, 18)
        self._close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._close_btn.setStyleSheet("QPushButton { background: transparent; border: none; }")
        self._close_btn.clicked.connect(self.dismiss)
        header.addWidget(self._close_btn)
        root.addLayout(header)

        self._msg_lbl = QLabel(self)
        self._msg_lbl.setWordWrap(True)
        self._msg_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        root.addWidget(self._msg_lbl)

        self._actions_row = QWidget(self)
        self._actions_row.setStyleSheet("background: transparent;")
        act_lay = QHBoxLayout(self._actions_row)
        act_lay.setContentsMargins(0, 2, 0, 0)
        act_lay.setSpacing(8)

        self._reconnect_btn = QPushButton("Reconnect", self._actions_row)
        self._reconnect_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._reconnect_btn.clicked.connect(self._on_reconnect_clicked)
        act_lay.addWidget(self._reconnect_btn)

        self._guide_btn = QPushButton("Setup Guide", self._actions_row)
        self._guide_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._guide_btn.clicked.connect(self.setup_guide_requested.emit)
        act_lay.addWidget(self._guide_btn)

        act_lay.addStretch(1)
        self._actions_row.setVisible(False)
        root.addWidget(self._actions_row)

        self._update_actions_row = QWidget(self)
        self._update_actions_row.setStyleSheet("background: transparent;")
        upd_lay = QHBoxLayout(self._update_actions_row)
        upd_lay.setContentsMargins(0, 2, 0, 0)
        upd_lay.setSpacing(8)

        self._update_btn = QPushButton("Update & Restart", self._update_actions_row)
        self._update_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._update_btn.clicked.connect(self.update_action_requested.emit)
        upd_lay.addWidget(self._update_btn)

        self._release_notes_btn = QPushButton("Release Notes", self._update_actions_row)
        self._release_notes_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._release_notes_btn.clicked.connect(self.release_notes_requested.emit)
        upd_lay.addWidget(self._release_notes_btn)

        upd_lay.addStretch(1)
        self._update_actions_row.setVisible(False)
        root.addWidget(self._update_actions_row)

        self.apply_theme()

    def apply_theme(self) -> None:
        pal = get_palette()
        border_col = pal.red if self._variant == "error" else pal.border
        title_col = pal.red if self._variant == "error" else pal.text_normal
        self.setStyleSheet(
            f"#ErrorBanner {{ background: {pal.bg_secondary}; border: 1px solid {border_col}; border-radius: 10px; }}"
        )
        self._title_lbl.setStyleSheet(
            f"color: {title_col}; font-size: 13px; font-weight: 600; background: transparent;"
        )
        self._msg_lbl.setStyleSheet(f"color: {pal.text_normal}; font-size: 12px; background: transparent;")
        self._close_btn.setIcon(lucide_icon("x", pal.text_muted, 13))
        primary_btn_qss = (
            f"QPushButton {{ background: {pal.accent}; color: #ffffff; border: none; border-radius: 6px; padding: 4px 10px; font-size: 11px; font-weight: 600; }}"
            f"QPushButton:hover {{ background: {pal.accent_hover}; }}"
        )
        secondary_btn_qss = (
            f"QPushButton {{ background: transparent; color: {pal.text_normal}; border: 1px solid {pal.border}; border-radius: 6px; padding: 4px 10px; font-size: 11px; }}"
            f"QPushButton:hover {{ background: {pal.bg_hover}; border-color: {pal.accent}; }}"
        )
        self._reconnect_btn.setStyleSheet(primary_btn_qss)
        self._guide_btn.setStyleSheet(secondary_btn_qss)
        self._update_btn.setStyleSheet(primary_btn_qss)
        self._release_notes_btn.setStyleSheet(secondary_btn_qss)

    def show_banner(
        self,
        title: str,
        message: str,
        variant: str = "error",
        show_actions: bool = False,
    ) -> None:
        self._variant = variant
        self._title_lbl.setText(title)
        self._msg_lbl.setText(message)
        self._actions_row.setVisible(show_actions)
        self._update_actions_row.setVisible(False)
        self.apply_theme()
        self.setVisible(True)

    def show_update_banner(
        self,
        title: str,
        message: str,
        *,
        primary_label: str = "Update & Restart",
    ) -> None:
        """Display an info banner with Update & Restart / Open Release and Release Notes buttons."""
        self._variant = "info"
        self._title_lbl.setText(title)
        self._msg_lbl.setText(message)
        self._update_btn.setText(primary_label)
        self._actions_row.setVisible(False)
        self._update_actions_row.setVisible(True)
        self.apply_theme()
        self.setVisible(True)

    def _on_reconnect_clicked(self) -> None:
        self.setVisible(False)
        self.reconnect_requested.emit()

    def dismiss(self) -> None:
        self.setVisible(False)
        self.closed.emit()


class SuggestionPopupWidget(QFrame):
    """Slash command suggestion popup positioned above the input box."""

    command_selected = pyqtSignal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("SuggestionPopup")
        self.setVisible(False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(0)

        self._list = QListWidget(self)
        self._list.setMaximumHeight(180)
        self._list.setFrameShape(QFrame.Shape.NoFrame)
        self._list.itemClicked.connect(self._on_item_clicked)
        layout.addWidget(self._list)

        self.apply_theme()

    def apply_theme(self) -> None:
        pal = get_palette()
        self.setStyleSheet(
            f"#SuggestionPopup {{ background: {pal.bg_secondary}; border: 1px solid {pal.border}; border-radius: 10px; }}"
        )
        self._list.setStyleSheet(
            f"QListWidget {{ background: transparent; color: {pal.text_normal}; outline: none; font-size: 12px; }}"
            f"QListWidget::item {{ padding: 6px 10px; border-radius: 5px; }}"
            f"QListWidget::item:selected {{ background: {pal.bg_hover}; color: {pal.heading_color}; }}"
        )

    def update_suggestions(self, commands: list[AvailableCommandInfo], query: str) -> None:
        if not commands or not query.startswith("/") or "\n" in query or " " in query:
            self.setVisible(False)
            return
        needle = query[1:].lower()
        matches = [c for c in commands if needle in c.name.lower() or needle in c.description.lower()]
        if not matches:
            self.setVisible(False)
            return

        self._list.clear()
        for cmd in matches:
            label = f"/{cmd.name}  ·  {cmd.description}" if cmd.description else f"/{cmd.name}"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, cmd)
            self._list.addItem(item)
        self._list.setCurrentRow(0)
        row_h = min(180, max(36, len(matches) * 30 + 6))
        self._list.setFixedHeight(row_h)
        self.setVisible(True)

    def move_selection(self, delta: int) -> None:
        count = self._list.count()
        if count == 0:
            return
        cur = self._list.currentRow()
        self._list.setCurrentRow((cur + delta) % count)

    def accept_current(self) -> AvailableCommandInfo | None:
        item = self._list.currentItem()
        if item is None:
            return None
        cmd = item.data(Qt.ItemDataRole.UserRole)
        self.setVisible(False)
        return cmd

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        cmd = item.data(Qt.ItemDataRole.UserRole)
        self.setVisible(False)
        if cmd is not None:
            self.command_selected.emit(cmd)
