"""Modern minimal floating ACP chat drawer with Light/Dark modes and Windows 11 Acrylic blur."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QEvent, QPoint, QRect, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QActionGroup,
    QColor,
    QFont,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QResizeEvent,
    QShowEvent,
)
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizeGrip,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from pocket_agent.acp_bridge import (
    AvailableCommandInfo,
    ConfigOptionInfo,
    ModeInfo,
    PermissionOptionInfo,
    PlanEntryInfo,
    UsageInfo,
)
from pocket_agent.agents import AgentSpec
from pocket_agent.ui.animations import LoadingIndicator, SpinningIcon, WindowAnimator
from pocket_agent.ui.menus import PocketMenu
from pocket_agent.ui.message_widgets import ChatTurn, DiffEntry, MessageBubbleWidget, ToolEntry
from pocket_agent.ui.overlays import (
    ErrorBannerWidget,
    PermissionDialogWidget,
    PermissionRequestData,
    PlanStripWidget,
    SuggestionPopupWidget,
)
from pocket_agent.ui.settings_page import SettingsPage
from pocket_agent.ui.theme import (
    FONT_UI,
    apply_windows_backdrop,
    get_palette,
    is_blur_enabled,
    lucide_icon,
    lucide_pixmap,
    render_markdown_html,
    set_active_theme,
)

__all__ = ["ChatDrawer", "ChatTurn", "ToolEntry", "render_markdown_html"]


def _elide_label(text: str, max_len: int = 18) -> str:
    clean = " ".join(text.split())
    if len(clean) <= max_len:
        return clean
    return clean[: max_len - 1] + "…"


class _PromptInput(QPlainTextEdit):
    """Auto-expanding multiline prompt box with slash-command & history navigation."""

    submitted = pyqtSignal(str)
    key_navigation = pyqtSignal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._min_h = 40
        self._max_h = 136
        self._history: list[str] = []
        self._history_idx: int = -1
        self._draft_before_history: str = ""
        self._popup_active = False

        self.setPlaceholderText("Ask anything...")
        self.setFixedHeight(self._min_h)
        self.setFont(QFont("Segoe UI", 10))
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.textChanged.connect(self._adjust_height)
        self.apply_theme()

    def apply_theme(self) -> None:
        pal = get_palette()
        self.setStyleSheet(
            f"""
            QPlainTextEdit {{
                background: transparent;
                color: {pal.text_normal};
                border: none;
                padding: 10px 12px 2px 12px;
                selection-background-color: {pal.accent};
            }}
            QScrollBar:vertical {{
                background: transparent;
                width: 5px;
            }}
            QScrollBar::handle:vertical {{
                background: {pal.border};
                border-radius: 2px;
            }}
            """
        )

    def set_popup_active(self, active: bool) -> None:
        self._popup_active = active

    def push_history(self, text: str) -> None:
        clean = text.strip()
        if clean and (not self._history or self._history[-1] != clean):
            self._history.append(clean)
        self._history_idx = -1
        self._draft_before_history = ""

    def _adjust_height(self) -> None:
        fm = self.fontMetrics()
        lines = max(1, self.document().blockCount())
        approx_h = lines * (fm.lineSpacing() + 1) + 14
        doc_layout = self.document().documentLayout()
        if doc_layout is not None:
            layout_h = int(doc_layout.documentSize().height())
            if layout_h > 0:
                approx_h = layout_h + 12
        target = max(self._min_h, min(approx_h, self._max_h))
        if self.height() != target:
            self.setFixedHeight(target)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # type: ignore[override]
        key = event.key()
        if self._popup_active and key in (
            Qt.Key.Key_Up,
            Qt.Key.Key_Down,
            Qt.Key.Key_Return,
            Qt.Key.Key_Enter,
            Qt.Key.Key_Tab,
            Qt.Key.Key_Escape,
        ):
            self.key_navigation.emit(event)
            event.accept()
            return

        if key == Qt.Key.Key_Up and not self.toPlainText().strip():
            self._navigate_history(-1)
            event.accept()
            return
        if key == Qt.Key.Key_Down and self._history_idx != -1:
            self._navigate_history(1)
            event.accept()
            return

        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not (
            event.modifiers() & Qt.KeyboardModifier.ShiftModifier
        ):
            text = self.toPlainText().strip()
            if text:
                self.push_history(text)
                self.submitted.emit(text)
            event.accept()
            return

        super().keyPressEvent(event)

    def _navigate_history(self, direction: int) -> None:
        if not self._history:
            return
        if self._history_idx == -1 and direction < 0:
            self._draft_before_history = self.toPlainText()
            self._history_idx = len(self._history) - 1
        elif self._history_idx != -1:
            nxt = self._history_idx + direction
            if nxt < 0:
                self._history_idx = 0
            elif nxt >= len(self._history):
                self._history_idx = -1
                self.setPlainText(self._draft_before_history)
                return
            else:
                self._history_idx = nxt
        if 0 <= self._history_idx < len(self._history):
            self.setPlainText(self._history[self._history_idx])
            cursor = self.textCursor()
            cursor.movePosition(cursor.MoveOperation.End)
            self.setTextCursor(cursor)


class _ToolbarMenuPill(QPushButton):
    """Minimal ghost pill dropdown matching obsidian-agent-client's ToolbarDropdown."""

    selected = pyqtSignal(str)

    def __init__(
        self,
        icon_name: str | None = None,
        use_primary_text: bool = False,
        bold: bool = False,
        open_above: bool = True,
        max_label_chars: int = 18,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._icon_name = icon_name
        self._use_primary_text = use_primary_text
        self._bold = bold
        self._open_above = open_above
        self._max_chars = max_label_chars
        self._items: list[tuple[str, str, str, str, bool]] = []
        self._current_value = ""

        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(22)
        self.clicked.connect(self._open_menu)
        self.apply_theme()

    def apply_theme(self) -> None:
        pal = get_palette()
        fg = pal.text_normal if self._use_primary_text else pal.text_muted
        weight = "font-weight: 600;" if self._bold else ""
        size = "12px" if self._bold else "11px"
        self.setStyleSheet(
            f"""
            QPushButton {{
                background: transparent;
                color: {fg};
                border: none;
                border-radius: 6px;
                padding: 2px 16px 2px 6px;
                font-size: {size};
                {weight}
                font-family: {FONT_UI};
            }}
            QPushButton:hover {{
                background: {pal.bg_hover};
                color: {pal.text_normal};
            }}
            """
        )
        self._chevron = lucide_pixmap("chevron-down", pal.text_faint, 11)
        self._refresh_display()

    def set_items(
        self,
        items: list[tuple[str, str, str, str, bool]],
        current_value: str,
        title: str = "",
    ) -> None:
        self._items = items
        self._current_value = current_value
        if title:
            self.setToolTip(title)
            self.setAccessibleName(title)
        self._refresh_display()

    def set_current_value(self, value: str) -> None:
        self._current_value = value
        self._refresh_display()

    def _refresh_display(self) -> None:
        pal = get_palette()
        match = next((lbl for val, lbl, _d, _g, _en in self._items if val == self._current_value), None)
        label = match or (self._items[0][1] if self._items else "")
        self.setText(_elide_label(label, self._max_chars))
        if self._icon_name:
            fg = pal.text_normal if self._use_primary_text else pal.text_muted
            self.setIcon(lucide_icon(self._icon_name, fg, 12))

    def _open_menu(self) -> None:
        if not self._items:
            return
        menu = PocketMenu(parent=self)
        last_group = ""
        for val, lbl, desc, group, enabled in self._items:
            if group and group != last_group and last_group:
                menu.addSeparator()
            last_group = group
            menu.add_check(
                lbl,
                val == self._current_value,
                lambda _c=False, v=val: self._pick(v),
                enabled=enabled,
                tooltip=desc,
            )

        if self._open_above:
            hint = menu.sizeHint()
            pos = self.mapToGlobal(QPoint(0, -hint.height() - 4))
        else:
            pos = self.mapToGlobal(QPoint(0, self.height() + 4))
        menu.exec(pos)

    def _pick(self, value: str) -> None:
        self.set_current_value(value)
        self.selected.emit(value)

    def paintEvent(self, event) -> None:  # type: ignore[override]
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.drawPixmap(self.width() - 14, (self.height() - 11) // 2, self._chevron)


class _HeaderIconButton(QPushButton):
    """Borderless Lucide header action button."""

    def __init__(self, icon_name: str, tooltip: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._icon_name = icon_name
        self.setFixedSize(32, 32)
        self.setAccessibleName(tooltip)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(tooltip)
        self.apply_theme()

    def apply_theme(self) -> None:
        pal = get_palette()
        self.setIcon(lucide_icon(self._icon_name, pal.text_muted, 14))
        self.setStyleSheet(
            f"""
            QPushButton {{
                background: transparent;
                border: none;
                border-radius: 6px;
            }}
            QPushButton:hover {{
                background: {pal.bg_hover};
            }}
            """
        )


def _config_option_sort_key(opt: ConfigOptionInfo) -> tuple[int, str]:
    cat = (opt.category or opt.id).lower()
    if cat == "model":
        return (0, opt.name)
    if cat in ("thought_level", "reasoning", "reasoning_effort", "thinking"):
        return (1, opt.name)
    if cat == "mode":
        return (2, opt.name)
    return (3, opt.name)


def _config_option_icon(opt: ConfigOptionInfo) -> str | None:
    cat = (opt.category or opt.id).lower()
    if cat == "model":
        return "sparkles"
    if cat in ("thought_level", "reasoning", "reasoning_effort", "thinking"):
        return "brain"
    if cat == "mode":
        return "shield"
    return None


class ChatDrawer(QWidget):
    """Modern minimal floating ACP chat drawer with Dark/Light & Windows 11 Acrylic blur."""

    prompt_submitted = pyqtSignal(str)
    cancel_requested = pyqtSignal()
    agent_selected = pyqtSignal(str)
    mode_selected = pyqtSignal(str)
    config_option_selected = pyqtSignal(str, str)  # config_id, value
    workspace_selected = pyqtSignal(str)
    start_without_project_toggled = pyqtSignal(bool)
    new_session_requested = pyqtSignal()
    reconnect_requested = pyqtSignal()
    setup_guide_requested = pyqtSignal()
    permission_decided = pyqtSignal(str, object)  # request_id, option_id | None
    theme_selected = pyqtSignal(str)  # "dark" | "light" | "system"
    blur_toggled = pyqtSignal(bool)
    always_on_top_toggled = pyqtSignal(bool)
    startup_toggled = pyqtSignal(bool)
    voice_toggled = pyqtSignal(bool)
    voice_selected = pyqtSignal(str)
    test_voice_requested = pyqtSignal()
    stop_speaking_requested = pyqtSignal()
    check_updates_requested = pyqtSignal()
    auto_update_toggled = pyqtSignal(bool)
    update_action_requested = pyqtSignal()
    release_notes_requested = pyqtSignal()
    manage_orbs_requested = pyqtSignal()
    thoughts_toggled = pyqtSignal(bool)
    read_reply_requested = pyqtSignal(str)
    opened = pyqtSignal()
    closed = pyqtSignal()

    def __init__(
        self,
        agents: list[AgentSpec],
        current_agent_id: str,
        workspace: str,
        always_on_top: bool = True,
        launch_at_startup: bool = False,
        theme_mode: str = "system",
        backdrop_blur: bool = True,
        voice_enabled: bool = True,
        voice_name: str = "en-ZA-LeahNeural",
        check_updates_on_startup: bool = True,
        start_without_project: bool = True,
        recent_workspaces: list[str] | None = None,
        width: int = 440,
        height: int = 560,
        show_thoughts: bool = True,
        text_size: int = 13,
        reduced_motion: bool = False,
        drop_shadow: bool = True,
    ) -> None:
        super().__init__(None)
        self._always_on_top = always_on_top
        self._launch_at_startup = launch_at_startup
        self._theme_mode = theme_mode
        self._backdrop_blur = backdrop_blur
        self._drop_shadow = drop_shadow
        self._voice_enabled = voice_enabled
        self._voice_name = voice_name
        self._check_updates_on_startup = check_updates_on_startup
        self._start_without_project = start_without_project
        self._recent_workspaces: list[str] = list(recent_workspaces or [])
        set_active_theme(theme_mode, backdrop_blur)

        self._turns: list[ChatTurn] = []
        self._turn_widgets: list[MessageBubbleWidget] = []
        self._busy = False
        self._session_ready = False
        self._status_text = "Connecting..."
        self._show_thoughts = show_thoughts
        self._text_size = text_size
        self._reduced_motion = reduced_motion
        self._connection_state = "connecting"
        self._review_from_settings = False
        self._settings_focus: QWidget | None = None
        self._drag_offset: QPoint | None = None
        self._manually_moved = False
        self._is_at_bottom = True
        self._commands: list[AvailableCommandInfo] = []
        self._agents = list(agents)
        self._current_agent_id = current_agent_id
        self._workspace = workspace
        self._config_options: list[ConfigOptionInfo] = []
        self._modes: list[ModeInfo] = []
        self._current_mode = ""
        self._selector_pills: list[_ToolbarMenuPill] = []
        self._input_focused = False
        self._last_usage: UsageInfo | None = None

        self._init_window_flags()
        self.setMinimumSize(340, 460)
        self.resize(width, height)

        self._animator = WindowAnimator(self)
        self._build_ui(agents, current_agent_id, workspace)
        self.apply_theme()
        self.set_reduced_motion(reduced_motion)

        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(35)
        self._render_timer.timeout.connect(self._render_transcript_now)
        self._auto_hide_timer = QTimer(self)
        self._auto_hide_timer.setSingleShot(True)
        self._auto_hide_timer.setInterval(4000)
        self._auto_hide_timer.timeout.connect(self._hide_if_inactive)

    def _use_native_blur(self) -> bool:
        return self._backdrop_blur and os.name == "nt"

    def _init_window_flags(self) -> None:
        flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
        if not self._drop_shadow:
            flags |= Qt.WindowType.NoDropShadowWindowHint
        if self._always_on_top:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        if self._use_native_blur():
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
            self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        else:
            self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, False)
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

    def set_always_on_top(self, enabled: bool) -> None:
        was_visible = self.isVisible()
        self._always_on_top = enabled
        if hasattr(self, "_settings_page"):
            self._settings_page.set_always_on_top(enabled)
        self._init_window_flags()
        if was_visible:
            self.show()
            self.raise_()
            self.activateWindow()
            self._apply_backdrop_now()
            if self.isActiveWindow():
                self._auto_hide_timer.stop()

    def set_theme(self, mode: str, blur: bool) -> None:
        """Switch Dark/Light/System theme and Acrylic blur dynamically."""
        blur_changed = blur != self._backdrop_blur
        self._theme_mode = mode
        self._backdrop_blur = blur
        if hasattr(self, "_settings_page"):
            self._settings_page.set_theme_mode(mode)
            self._settings_page.set_backdrop_blur(blur)
        set_active_theme(mode, blur)
        if blur_changed:
            was_visible = self.isVisible()
            self._init_window_flags()
            if was_visible:
                self.show()
                self.raise_()
                self.activateWindow()
                self._apply_backdrop_now()
                if self.isActiveWindow():
                    self._auto_hide_timer.stop()
        self.apply_theme()

    def set_drop_shadow(self, enabled: bool) -> None:
        if enabled == self._drop_shadow:
            return
        self._drop_shadow = enabled
        if hasattr(self, "_settings_page"):
            self._settings_page.set_drop_shadow(enabled)
        focused = self.focusWidget()
        was_visible = self.isVisible()
        self._init_window_flags()
        if was_visible:
            self.show()
            self.raise_()
            self.activateWindow()
            self._apply_backdrop_now()
            if self.isActiveWindow():
                self._auto_hide_timer.stop()
            if focused is not None:
                focused.setFocus()

    def set_voice_enabled(self, enabled: bool) -> None:
        """Sync voice-enabled toggle on the settings page."""
        self._voice_enabled = enabled
        if hasattr(self, "_settings_page"):
            self._settings_page.set_voice_enabled(enabled)

    def set_voice_name(self, voice_name: str) -> None:
        """Sync selected voice on the settings page."""
        self._voice_name = voice_name
        if hasattr(self, "_settings_page"):
            self._settings_page.set_voice_name(voice_name)

    def set_launch_at_startup(self, enabled: bool) -> None:
        """Sync Windows startup toggle on the settings page."""
        self._launch_at_startup = enabled
        if hasattr(self, "_settings_page"):
            self._settings_page.set_launch_at_startup(enabled)

    def _apply_backdrop_now(self) -> None:
        if os.name == "nt":
            pal = get_palette()
            apply_windows_backdrop(
                self,
                is_dark=pal.is_dark,
                blur=self._backdrop_blur,
                tint_rgba=pal.bg_tint_rgba,
                drop_shadow=self._drop_shadow,
            )

    def _build_ui(self, agents: list[AgentSpec], current_agent_id: str, workspace: str) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(1, 1, 1, 1)
        root.setSpacing(0)

        # ---- Single Unified Minimal Top Bar ----
        self._header_bar = QFrame(self)
        self._header_bar.setObjectName("FloatingHeader")
        self._header_bar.setFixedHeight(40)
        hl = QHBoxLayout(self._header_bar)
        hl.setContentsMargins(12, 4, 10, 4)
        hl.setSpacing(6)

        self._status_spinner = SpinningIcon("loader", get_palette().yellow, 12, self._header_bar)
        hl.addWidget(self._status_spinner)

        self._agent_pill = _ToolbarMenuPill(
            icon_name=None,
            use_primary_text=True,
            bold=True,
            open_above=False,
            max_label_chars=22,
            parent=self._header_bar,
        )
        agent_items = [
            (
                spec.id,
                spec.name if spec.is_available() else f"{spec.name} (missing)",
                spec.description,
                "",
                spec.is_available(),
            )
            for spec in agents
        ]
        self._agent_pill.set_items(agent_items, current_agent_id, "Switch AI Agent")
        self._agent_pill.selected.connect(self._on_agent_selected)
        hl.addWidget(self._agent_pill)

        self._cwd_btn = QPushButton(self._header_bar)
        self._cwd_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._cwd_btn.setFixedHeight(22)
        self._cwd_btn.clicked.connect(self._show_project_menu)
        self.set_workspace(workspace)
        hl.addWidget(self._cwd_btn)

        hl.addStretch(1)

        self._new_btn = _HeaderIconButton("plus", "New chat", self._header_bar)
        self._new_btn.clicked.connect(self._on_new_chat_clicked)
        hl.addWidget(self._new_btn)

        self._more_btn = _HeaderIconButton("more-vertical", "More options", self._header_bar)
        self._more_btn.clicked.connect(self._show_more_menu)
        hl.addWidget(self._more_btn)

        self._close_btn = _HeaderIconButton("x", "Hide drawer", self._header_bar)
        self._close_btn.clicked.connect(self._on_close_clicked)
        hl.addWidget(self._close_btn)

        root.addWidget(self._header_bar)

        self._notice = QFrame()
        nl = QHBoxLayout(self._notice)
        nl.setContentsMargins(12, 3, 10, 3)
        self._notice_text = QLabel()
        self._notice_text.setTextFormat(Qt.TextFormat.PlainText)
        self._notice_text.setWordWrap(True)
        nl.addWidget(self._notice_text, 1)
        self._notice_action = QPushButton()
        self._notice_action.clicked.connect(self._act_on_notice)
        nl.addWidget(self._notice_action)
        root.addWidget(self._notice)

        # ---- Stacked Pages (0 = Chat, 1 = Settings) ----
        self._pages = QStackedWidget(self)
        self._pages.setStyleSheet("background: transparent;")
        root.addWidget(self._pages, 1)

        self._chat_page = QWidget(self._pages)
        self._chat_page.setStyleSheet("background: transparent;")
        chat_layout = QVBoxLayout(self._chat_page)
        chat_layout.setContentsMargins(0, 0, 0, 0)
        chat_layout.setSpacing(0)

        # ---- Ambient Plan Strip ----
        self._plan_strip = PlanStripWidget(self._chat_page)
        chat_layout.addWidget(self._plan_strip)

        # ---- Open Transcript Area ----
        self._transcript_host = QWidget(self._chat_page)
        self._transcript_host.setStyleSheet("background: transparent;")
        host_layout = QVBoxLayout(self._transcript_host)
        host_layout.setContentsMargins(0, 0, 0, 0)
        host_layout.setSpacing(0)

        self._scroll = QScrollArea(self._transcript_host)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._messages_container = QWidget()
        self._messages_container.setStyleSheet("background: transparent;")
        self._messages_layout = QVBoxLayout(self._messages_container)
        self._messages_layout.setContentsMargins(10, 14, 10, 8)
        self._messages_layout.setSpacing(10)

        self._empty_label = QLabel(self._messages_container)
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setWordWrap(True)
        self._messages_layout.addWidget(self._empty_label)

        self._loading_indicator = LoadingIndicator(self._messages_container)
        self._messages_layout.addWidget(self._loading_indicator)
        self._messages_layout.addStretch(1)

        self._scroll.setWidget(self._messages_container)
        self._scroll.verticalScrollBar().valueChanged.connect(self._on_scroll_changed)
        self._scroll.verticalScrollBar().rangeChanged.connect(self._on_scroll_range_changed)
        host_layout.addWidget(self._scroll, 1)
        chat_layout.addWidget(self._transcript_host, 1)

        self._scroll_bottom_btn = QPushButton(self._transcript_host)
        self._scroll_bottom_btn.setFixedSize(28, 28)
        self._scroll_bottom_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._scroll_bottom_btn.setToolTip("Scroll to bottom")
        self._scroll_bottom_btn.clicked.connect(self._scroll_to_bottom)
        self._scroll_bottom_btn.setVisible(False)

        # ---- Input Card + Floating Overlays ----
        input_outer = QWidget(self._chat_page)
        input_outer.setStyleSheet("background: transparent;")
        iol = QVBoxLayout(input_outer)
        iol.setContentsMargins(10, 2, 10, 10)
        iol.setSpacing(6)

        self._error_banner = ErrorBannerWidget(input_outer)
        self._error_banner.reconnect_requested.connect(self.reconnect_requested.emit)
        self._error_banner.setup_guide_requested.connect(self.setup_guide_requested.emit)
        self._error_banner.update_action_requested.connect(self.update_action_requested.emit)
        self._error_banner.release_notes_requested.connect(self.release_notes_requested.emit)
        iol.addWidget(self._error_banner)

        self._perm_dialog = PermissionDialogWidget(input_outer)
        self._perm_dialog.permission_decided.connect(self._on_permission_widget_decided)
        self._perm_bar = self._perm_dialog
        iol.addWidget(self._perm_dialog)

        self._suggestion_popup = SuggestionPopupWidget(input_outer)
        self._suggestion_popup.command_selected.connect(self._apply_slash_command)
        iol.addWidget(self._suggestion_popup)

        self._input_box = QFrame(input_outer)
        self._input_box.setObjectName("ChatInputBox")
        ibl = QVBoxLayout(self._input_box)
        ibl.setContentsMargins(0, 0, 0, 6)
        ibl.setSpacing(4)

        self._input = _PromptInput(self._input_box)
        self._input.submitted.connect(self._submit_prompt)
        self._input.textChanged.connect(self._on_input_text_changed)
        self._input.key_navigation.connect(self._on_popup_key_nav)
        self._input.installEventFilter(self)
        ibl.addWidget(self._input)

        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(8, 0, 8, 0)
        toolbar.setSpacing(4)

        self._selectors_host = QWidget(self._input_box)
        self._selectors_host.setStyleSheet("background: transparent;")
        self._selectors_layout = QHBoxLayout(self._selectors_host)
        self._selectors_layout.setContentsMargins(0, 0, 0, 0)
        self._selectors_layout.setSpacing(2)
        toolbar.addWidget(self._selectors_host)

        toolbar.addStretch(1)

        self._usage_label = QLabel("", self._input_box)
        self._usage_label.setVisible(False)
        toolbar.addWidget(self._usage_label)

        self._send_btn = QPushButton(self._input_box)
        self._send_btn.setFixedSize(32, 32)
        self._send_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._send_btn.clicked.connect(self._on_send_or_stop_clicked)
        toolbar.addWidget(self._send_btn)

        ibl.addLayout(toolbar)
        iol.addWidget(self._input_box)
        chat_layout.addWidget(input_outer)

        self._pages.addWidget(self._chat_page)

        self._settings_page = SettingsPage(
            voice_enabled=self._voice_enabled,
            voice_name=self._voice_name,
            theme_mode=self._theme_mode,
            backdrop_blur=self._backdrop_blur,
            drop_shadow=self._drop_shadow,
            always_on_top=self._always_on_top,
            launch_at_startup=self._launch_at_startup,
            show_thoughts=self._show_thoughts,
            start_without_project=self._start_without_project,
            check_updates_on_startup=self._check_updates_on_startup,
            parent=self._pages,
        )
        self._settings_page.back_requested.connect(self.close_settings)
        self._settings_page.voice_toggled.connect(self._on_settings_voice_toggled)
        self._settings_page.voice_selected.connect(self._on_settings_voice_selected)
        self._settings_page.test_voice_requested.connect(self.test_voice_requested.emit)
        self._settings_page.stop_speaking_requested.connect(self.stop_speaking_requested.emit)
        self._settings_page.theme_selected.connect(self._emit_theme_mode)
        self._settings_page.blur_toggled.connect(self._emit_blur_toggle)
        self._settings_page.always_on_top_toggled.connect(self._on_settings_always_on_top)
        self._settings_page.startup_toggled.connect(self.startup_toggled.emit)
        self._settings_page.thoughts_toggled.connect(self._on_toggle_thoughts)
        self._settings_page.start_without_project_toggled.connect(self._on_start_without_project_toggled)
        self._settings_page.setup_requested.connect(self.setup_guide_requested)
        self._settings_page.reconnect_requested.connect(self.reconnect_requested)
        self._settings_page.set_agents(agents, "")
        self._settings_page.set_readability(self._text_size, self._reduced_motion)
        self._settings_page.auto_update_toggled.connect(self.auto_update_toggled.emit)
        self._settings_page.check_updates_requested.connect(self.check_updates_requested.emit)
        self._settings_page.apply_update_requested.connect(self.update_action_requested.emit)
        self._settings_page.manage_orbs_requested.connect(self.manage_orbs_requested.emit)
        self._pages.addWidget(self._settings_page)

        self._size_grip = QSizeGrip(self)
        self._size_grip.setFixedSize(12, 12)
        self._size_grip.setStyleSheet("background: transparent;")
        self._size_grip.raise_()

        self._update_empty_state()

    def apply_theme(self) -> None:
        """Apply current Dark/Light palette and blur styling to every child widget."""
        pal = get_palette()
        self._apply_backdrop_now()

        self._header_bar.setStyleSheet(
            f"#FloatingHeader {{ background: transparent; border-bottom: 1px solid {pal.border_subtle}; }}"
        )
        self._agent_pill.apply_theme()
        self._cwd_btn.setIcon(lucide_icon("folder" if self._workspace else "message-square", pal.text_faint, 12))
        self._cwd_btn.setStyleSheet(
            f"""
            QPushButton {{
                background: transparent;
                color: {pal.text_faint};
                border: none;
                border-radius: 6px;
                padding: 2px 7px;
                font-size: 11px;
                font-family: {FONT_UI};
            }}
            QPushButton:hover {{
                background: {pal.bg_hover};
                color: {pal.text_muted};
            }}
            """
        )

        self.setStyleSheet(f"QPushButton:focus {{ border: 2px solid {pal.accent}; }}")
        self._notice.setStyleSheet(f"QFrame {{ background: {pal.bg_secondary}; color: {pal.text_normal}; }}")
        self._notice_action.setStyleSheet(
            f"QPushButton {{ background: {pal.bg_secondary}; color: {pal.text_normal}; "
            f"border: 1px solid {pal.border}; border-radius: 5px; padding: 5px 9px; }}"
            f"QPushButton:focus {{ border: 2px solid {pal.accent}; }}"
        )
        for btn in (self._new_btn, self._more_btn, self._close_btn):
            btn.apply_theme()
        self._settings_page.apply_theme()

        self._plan_strip.apply_theme()
        self._scroll.setStyleSheet(
            f"""
            QScrollArea {{
                background: transparent;
                border: none;
            }}
            QScrollBar:vertical {{
                background: transparent;
                width: 6px;
                margin: 6px 2px;
            }}
            QScrollBar::handle:vertical {{
                background: {pal.border};
                border-radius: 3px;
                min-height: 24px;
            }}
            QScrollBar::handle:vertical:hover {{
                background: {pal.text_faint};
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0px;
            }}
            """
        )
        self._empty_label.setStyleSheet(
            f"color: {pal.text_faint}; font-size: 13px; padding: 36px 16px; background: transparent;"
        )
        self._loading_indicator.apply_theme()
        self._scroll_bottom_btn.setIcon(lucide_icon("arrow-down", pal.text_muted, 14))
        self._scroll_bottom_btn.setStyleSheet(
            f"""
            QPushButton {{
                background: {pal.bg_secondary};
                border: 1px solid {pal.border};
                border-radius: 14px;
            }}
            QPushButton:hover {{
                border-color: {pal.accent};
                background: {pal.bg_hover};
            }}
            """
        )

        self._error_banner.apply_theme()
        self._perm_dialog.apply_theme()
        self._suggestion_popup.apply_theme()
        self._input.apply_theme()
        self._input_box.setStyleSheet(self._input_box_qss(focused=self._input_focused))

        for pill in self._selector_pills:
            pill.apply_theme()
        for w in self._turn_widgets:
            w.apply_theme()
        if self._last_usage is not None:
            self.set_usage(self._last_usage)

        self.set_status(self._status_text)
        self._update_send_button()
        self.update()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_workspace(self, workspace: str, recent_workspaces: list[str] | None = None) -> None:
        clean = workspace.strip()
        self._workspace = clean
        if recent_workspaces is not None:
            self._recent_workspaces = list(recent_workspaces)
        pal = get_palette()
        if clean:
            p = Path(clean)
            name = p.name or str(p)
            self._cwd_btn.setText(_elide_label(name, 16))
            self._cwd_btn.setIcon(lucide_icon("folder", pal.text_faint, 12))
            self._cwd_btn.setToolTip(f"Project: {clean}\nClick to switch or clear")
        else:
            self._cwd_btn.setText("No Project")
            self._cwd_btn.setIcon(lucide_icon("message-square", pal.text_faint, 12))
            self._cwd_btn.setToolTip("No project selected (general chat)\nClick to choose a project folder")
        self._cwd_btn.setAccessibleName(f"Project: {clean or 'General chat'}")

    def set_start_without_project(self, enabled: bool) -> None:
        """Sync the start-without-project default toggle on the settings page."""
        self._start_without_project = enabled
        if hasattr(self, "_settings_page"):
            self._settings_page.set_start_without_project(enabled)

    def select_agent_in_combo(self, agent_id: str) -> None:
        self._current_agent_id = agent_id
        self._agent_pill.set_current_value(agent_id)
        self._update_empty_state()

    def set_config_options(self, options: list[ConfigOptionInfo]) -> None:
        self._config_options = list(options)
        self._rebuild_toolbar_selectors()

    def set_modes(self, modes: list[ModeInfo], current_mode_id: str) -> None:
        self._modes = list(modes)
        self._current_mode = current_mode_id
        if not self._config_options:
            self._rebuild_toolbar_selectors()

    def set_current_mode(self, mode_id: str) -> None:
        self._current_mode = mode_id
        if not self._config_options and self._selector_pills:
            self._selector_pills[0].set_current_value(mode_id)

    def set_commands(self, commands: list[AvailableCommandInfo]) -> None:
        self._commands = list(commands)
        agent_name = self._current_agent_name()
        hint = " (/ for commands)" if self._commands else ""
        self._input.setPlaceholderText(f"Message {agent_name}{hint}...")

    def set_plan(self, entries: list[PlanEntryInfo]) -> None:
        self._plan_strip.set_entries(entries)

    def set_usage(self, usage: UsageInfo) -> None:
        self._last_usage = usage
        if usage.size <= 0:
            self._usage_label.setVisible(False)
            return
        pal = get_palette()
        pct = max(0, min(100, round((usage.used / usage.size) * 100)))
        if pct >= 90:
            col = pal.red
            weight = "font-weight: 600;"
        elif pct >= 80:
            col = pal.orange
            weight = ""
        elif pct >= 70:
            col = pal.yellow
            weight = ""
        else:
            col = pal.text_faint
            weight = ""
        self._usage_label.setText(f"{pct}%")
        self._usage_label.setStyleSheet(
            f"color: {col}; font-size: 11px; {weight} background: transparent; padding-right: 4px;"
        )
        used_k = f"{usage.used / 1000:.1f}K" if usage.used >= 1000 else str(usage.used)
        size_k = f"{round(usage.size / 1000)}K" if usage.size >= 100000 else f"{usage.size / 1000:.1f}K"
        tip = f"{used_k} / {size_k} tokens"
        if usage.cost_amount is not None:
            tip += f"\n${usage.cost_amount:.2f}"
        self._usage_label.setToolTip(tip)
        self._usage_label.setVisible(True)

    def set_status(self, text: str, color: str = "") -> None:
        pal = get_palette()
        self._status_text = text
        self._status_spinner.setToolTip(text)
        lower = text.lower()
        is_ready = self._session_ready
        spinning = self._busy or "connecting" in lower
        if spinning:
            self._status_spinner.set_icon("loader", pal.accent if self._busy else pal.yellow, not self._reduced_motion)
        elif "error" in lower or "failed" in lower or "disconnected" in lower:
            self._status_spinner.set_icon("circle-alert", pal.red, False)
        elif "permission" in lower:
            self._status_spinner.set_icon("circle-alert", pal.yellow, False)
        else:
            self._status_spinner.set_icon("circle", pal.green if is_ready else (color or pal.text_muted), False)

        if self._busy:
            loading_note = "Waiting for permission..." if "permission" in lower else ""
            self._loading_indicator.set_loading(True, loading_note)
        self._refresh_notice()
        self._update_empty_state()

    @property
    def settings(self) -> SettingsPage:
        return self._settings_page

    def set_connection_state(self, state: str) -> None:
        self._connection_state = state
        self._session_ready = state == "connected"
        if not self._session_ready:
            self.set_busy(False)
            self.clear_permission()
        self._refresh_notice()
        self._update_send_button()
        self._update_empty_state()

    @property
    def permission_pending(self) -> bool:
        return self._perm_dialog.current_request is not None

    def _refresh_notice(self) -> None:
        request = self._perm_dialog.current_request
        if request:
            message = f"Approval needed · {request.title}"
            action = "Review" if self._pages.currentWidget() is self._settings_page else ""
        elif self._connection_state == "disconnected":
            message, action = "Disconnected", "Reconnect"
        elif self._connection_state == "connecting":
            message, action = "Connecting…", ""
        else:
            self._notice.hide()
            return
        self._notice_text.setAccessibleName(message)
        self._notice_text.setToolTip(message)
        extent = max(120, self.width() - (120 if action else 36))
        self._notice_text.setText(self._notice_text.fontMetrics().elidedText(message, Qt.TextElideMode.ElideRight, extent))
        self._notice_action.setText(action)
        self._notice_action.setVisible(bool(action))
        self._notice.show()

    def _act_on_notice(self) -> None:
        if self.permission_pending:
            self._settings_focus = self._settings_page.focusWidget()
            self._review_from_settings = self._pages.currentWidget() is self._settings_page
            self._pages.setCurrentWidget(self._chat_page)
            self._perm_dialog.setFocus(Qt.FocusReason.OtherFocusReason)
            self._refresh_notice()
        else:
            self.reconnect_requested.emit()

    def _after_permission_change(self) -> None:
        if not self.permission_pending and self._review_from_settings:
            self._review_from_settings = False
            self._pages.setCurrentWidget(self._settings_page)
            if self._settings_focus and self._settings_page.isAncestorOf(self._settings_focus):
                self._settings_focus.setFocus()
            else:
                self._settings_page.focus_current()
            self._settings_focus = None
        self._refresh_notice()
        if not self.permission_pending and self.isVisible() and not self.isActiveWindow():
            self._auto_hide_timer.start()

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._loading_indicator.set_loading(busy, "")
        self._update_send_button()

    def clear_chat(self) -> None:
        self._turns.clear()
        for w in self._turn_widgets:
            self._messages_layout.removeWidget(w)
            w.deleteLater()
        self._turn_widgets.clear()
        self._plan_strip.clear()
        self._last_usage = None
        self._usage_label.setVisible(False)
        self._error_banner.setVisible(False)
        self.clear_permission()
        self._update_empty_state()

    def add_system_message(self, text: str) -> None:
        turn = ChatTurn(role="system", text=text)
        self._turns.append(turn)
        self._append_turn_widget(turn)

    def add_error_message(self, text: str) -> None:
        turn = ChatTurn(role="error", text=text)
        self._turns.append(turn)
        self._append_turn_widget(turn)
        self._error_banner.show_banner("Agent Error", text, variant="error")

    def show_connection_error(self, title: str, text: str) -> None:
        """Display a connection failure/disconnect in the transcript and banner with Retry actions."""
        turn = ChatTurn(role="error", text=text)
        self._turns.append(turn)
        self._append_turn_widget(turn)
        self._error_banner.show_banner(title, text, variant="error", show_actions=True)

    def show_update_banner(self, version: str, *, frozen: bool) -> None:
        """Display an info banner notifying the user of an available release."""
        if frozen:
            msg = f"Pocket Agent v{version} is ready to download and install."
            primary_label = "Update & Restart"
        else:
            msg = f"Pocket Agent v{version} is out. Running from source: run `git pull` to update."
            primary_label = "Open Release"
        self._error_banner.show_update_banner(
            f"Update Available: v{version}",
            msg,
            primary_label=primary_label,
        )

    def show_info_banner(self, title: str, message: str) -> None:
        """Display a dismissible info banner above the input box."""
        self._error_banner.show_banner(title, message, variant="info")

    def show_error_banner(self, title: str, message: str) -> None:
        """Display a dismissible error banner above the input box."""
        self._error_banner.show_banner(title, message, variant="error")

    def set_update_status(
        self,
        text: str,
        *,
        busy: bool = False,
        update_available: bool = False,
        is_frozen: bool = False,
    ) -> None:
        """Forward update status text and state to the Settings page."""
        self._settings_page.set_update_status(
            text,
            busy=busy,
            update_available=update_available,
            is_frozen=is_frozen,
        )

    def append_user_message(self, text: str) -> None:
        user_turn = ChatTurn(role="user", text=text)
        self._turns.append(user_turn)
        self._append_turn_widget(user_turn)

        assistant_turn = ChatTurn(role="assistant")
        self._turns.append(assistant_turn)
        self._append_turn_widget(assistant_turn)
        self._is_at_bottom = True
        self._scroll_to_bottom()

    def append_thought_chunk(self, chunk: str) -> None:
        turn = self._ensure_assistant_turn()
        turn.thoughts += chunk
        self._schedule_render()

    def append_message_chunk(self, chunk: str) -> None:
        turn = self._ensure_assistant_turn()
        turn.text += chunk
        self._schedule_render()

    def start_tool_call(
        self,
        tool_call_id: str,
        title: str,
        kind: str,
        status: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        turn = self._ensure_assistant_turn()
        for existing in turn.tools:
            if existing.tool_call_id == tool_call_id:
                existing.title = title or existing.title
                existing.kind = kind or existing.kind
                existing.status = status or existing.status
                self._merge_tool_details(existing, details)
                self._schedule_render()
                return
        entry = ToolEntry(tool_call_id=tool_call_id, title=title, kind=kind, status=status)
        self._merge_tool_details(entry, details)
        turn.tools.append(entry)
        self._schedule_render()

    def update_tool_call(
        self,
        tool_call_id: str,
        title: str,
        kind: str,
        status: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        turn = self._ensure_assistant_turn()
        for existing in turn.tools:
            if existing.tool_call_id == tool_call_id:
                if title:
                    existing.title = title
                if kind:
                    existing.kind = kind
                if status:
                    existing.status = status
                self._merge_tool_details(existing, details)
                self._schedule_render()
                return
        self.start_tool_call(
            tool_call_id,
            title or "Tool Call",
            kind or "other",
            status or "in_progress",
            details=details,
        )

    def show_permission_request(
        self,
        request_id: str,
        title: str,
        kind: str,
        options: list[PermissionOptionInfo],
        details: dict[str, Any] | None = None,
    ) -> None:
        d = details or {}
        diffs = [
            DiffEntry(path=str(x.get("path", "")), old_text=x.get("old_text"), new_text=str(x.get("new_text", "")))
            for x in d.get("diffs", [])
        ]
        req = PermissionRequestData(
            request_id=request_id,
            title=title,
            kind=kind,
            options=list(options),
            command=str(d.get("command", "")),
            locations=list(d.get("locations", [])),
            diffs=diffs,
        )
        self._perm_dialog.enqueue(req)
        self._auto_hide_timer.stop()
        self._animator.cancel_hide()
        self._refresh_notice()
        self.show()
        self.raise_()

    def clear_permission(self) -> None:
        self._perm_dialog.clear()
        self._after_permission_change()

    def _decide_permission(self, option_id: str | None) -> None:
        self._perm_dialog.decide_current(option_id)

    def dock_near(self, pet_rect: QRect, force: bool = False) -> None:
        if self._manually_moved and not force:
            return
        screen = QApplication.screenAt(pet_rect.center()) or QApplication.primaryScreen()
        geo = screen.availableGeometry() if screen else QRect(0, 0, 1920, 1080)

        gap = 12
        left_x = pet_rect.left() - self.width() - gap
        right_x = pet_rect.right() + gap
        if left_x >= geo.left() + 8:
            x = left_x
        elif right_x + self.width() <= geo.right() - 8:
            x = right_x
        else:
            x = max(geo.left() + 8, geo.right() - self.width() - 8)

        y = pet_rect.bottom() - self.height()
        y = max(geo.top() + 8, min(y, geo.bottom() - self.height() - 8))
        self.move(x, y)

    def focus_input(self) -> None:
        if not self.isVisible():
            self._animator.show_animated(slide_px=10, duration_ms=170, on_done=self._apply_backdrop_now)
        else:
            self.show()
            self.raise_()
        self._apply_backdrop_now()
        self.activateWindow()
        self._input.setFocus()

    def hide_animated(self) -> None:
        self._auto_hide_timer.stop()
        self._animator.hide_animated(duration_ms=130, on_done=self.closed.emit)

    def _hide_if_inactive(self) -> None:
        if not self.isVisible() or self.isActiveWindow() or self.permission_pending:
            return
        if QApplication.activePopupWidget() or QApplication.activeModalWidget():
            self._auto_hide_timer.start()
            return
        self.hide_animated()

    # ------------------------------------------------------------------
    # Toolbar Selectors (Model / Reasoning / Mode)
    # ------------------------------------------------------------------

    def _rebuild_toolbar_selectors(self) -> None:
        while self._selectors_layout.count():
            item = self._selectors_layout.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()
        self._selector_pills.clear()

        if self._config_options:
            ordered = sorted(self._config_options, key=_config_option_sort_key)
            for opt in ordered:
                if len(opt.choices) <= 1:
                    continue
                max_chars = 18 if opt.category == "model" else 11
                pill = _ToolbarMenuPill(
                    icon_name=_config_option_icon(opt),
                    use_primary_text=False,
                    bold=False,
                    open_above=True,
                    max_label_chars=max_chars,
                    parent=self._selectors_host,
                )
                items = [
                    (c.value, c.name, c.description, c.group, True)
                    for c in opt.choices
                ]
                pill.set_items(items, opt.current_value, opt.description or opt.name)
                pill.selected.connect(
                    lambda val, cid=opt.id: self.config_option_selected.emit(cid, val)
                )
                self._selectors_layout.addWidget(pill)
                self._selector_pills.append(pill)
        elif len(self._modes) > 1:
            pill = _ToolbarMenuPill(
                icon_name="shield",
                use_primary_text=False,
                bold=False,
                open_above=True,
                max_label_chars=14,
                parent=self._selectors_host,
            )
            items = [(m.id, m.name, m.description, "", True) for m in self._modes]
            pill.set_items(items, self._current_mode, "Mode")
            pill.selected.connect(self.mode_selected.emit)
            self._selectors_layout.addWidget(pill)
            self._selector_pills.append(pill)

    # ------------------------------------------------------------------
    # Internal Rendering & Scroll Management
    # ------------------------------------------------------------------

    @staticmethod
    def _merge_tool_details(entry: ToolEntry, details: dict[str, Any] | None) -> None:
        if not details:
            return
        if details.get("command"):
            entry.command = str(details["command"])
        if details.get("locations"):
            entry.locations = [str(x) for x in details["locations"]]
        if details.get("diffs"):
            entry.diffs = [
                DiffEntry(
                    path=str(x.get("path", "")),
                    old_text=x.get("old_text"),
                    new_text=str(x.get("new_text", "")),
                )
                for x in details["diffs"]
            ]
        if details.get("outputs"):
            for txt in details["outputs"]:
                if txt and txt not in entry.outputs:
                    entry.outputs.append(str(txt))

    def _current_agent_name(self) -> str:
        spec = next((a for a in self._agents if a.id == self._current_agent_id), None)
        return spec.name if spec else "Agent"

    def _update_empty_state(self) -> None:
        has_turns = len(self._turns) > 0
        self._empty_label.setVisible(not has_turns)
        if not has_turns:
            agent_name = self._current_agent_name()
            if self._connection_state == "disconnected":
                self._empty_label.setText(f"Reconnect to chat with {agent_name}.")
            elif not self._session_ready:
                self._empty_label.setText(f"Connecting to {agent_name}...")
            else:
                self._empty_label.setText(f"Start a conversation with {agent_name}...")

    def _append_turn_widget(self, turn: ChatTurn) -> None:
        widget = MessageBubbleWidget(turn, show_thoughts=self._show_thoughts, parent=self._messages_container)
        widget.set_text_size(self._text_size)
        widget.read_requested.connect(self.read_reply_requested)
        self._turn_widgets.append(widget)
        insert_idx = max(0, self._messages_layout.count() - 2)
        self._messages_layout.insertWidget(insert_idx, widget)
        self._update_empty_state()

    def _ensure_assistant_turn(self) -> ChatTurn:
        if not self._turns or self._turns[-1].role != "assistant":
            turn = ChatTurn(role="assistant")
            self._turns.append(turn)
            self._append_turn_widget(turn)
        return self._turns[-1]

    def _schedule_render(self) -> None:
        if not self._render_timer.isActive():
            self._render_timer.start()

    def _render_transcript_now(self) -> None:
        if self._turn_widgets:
            self._turn_widgets[-1].refresh_assistant()
        self._update_empty_state()
        if self._is_at_bottom:
            QTimer.singleShot(0, self._scroll_to_bottom)

    def _on_scroll_changed(self, value: int) -> None:
        sb = self._scroll.verticalScrollBar()
        self._is_at_bottom = value >= sb.maximum() - 28
        self._scroll_bottom_btn.setVisible(not self._is_at_bottom and len(self._turns) > 0)
        self._position_scroll_btn()

    def _on_scroll_range_changed(self, _min: int, _max: int) -> None:
        if self._is_at_bottom:
            self._scroll_to_bottom()
        self._position_scroll_btn()

    def _scroll_to_bottom(self) -> None:
        sb = self._scroll.verticalScrollBar()
        sb.setValue(sb.maximum())
        self._is_at_bottom = True
        self._scroll_bottom_btn.setVisible(False)

    def _position_scroll_btn(self) -> None:
        x = (self._transcript_host.width() - self._scroll_bottom_btn.width()) // 2
        y = self._transcript_host.height() - self._scroll_bottom_btn.height() - 10
        self._scroll_bottom_btn.move(max(0, x), max(0, y))

    # ------------------------------------------------------------------
    # Input, Toolbar & Menu Interactions
    # ------------------------------------------------------------------

    def set_text_size(self, size: int) -> None:
        self._text_size = size
        for widget in self._turn_widgets:
            widget.set_text_size(size)

    def set_reduced_motion(self, enabled: bool) -> None:
        self._reduced_motion = enabled
        self._animator.reduced_motion = enabled
        self._loading_indicator.set_reduced_motion(enabled)
        self.set_status(self._status_text)

    def _on_input_text_changed(self) -> None:
        text = self._input.toPlainText()
        self._suggestion_popup.update_suggestions(self._commands, text)
        self._input.set_popup_active(self._suggestion_popup.isVisible())
        self._update_send_button()

    def _on_popup_key_nav(self, event: QKeyEvent) -> None:
        key = event.key()
        if key == Qt.Key.Key_Down:
            self._suggestion_popup.move_selection(1)
        elif key == Qt.Key.Key_Up:
            self._suggestion_popup.move_selection(-1)
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Tab):
            cmd = self._suggestion_popup.accept_current()
            if cmd is not None:
                self._apply_slash_command(cmd)
        elif key == Qt.Key.Key_Escape:
            self._suggestion_popup.setVisible(False)
            self._input.set_popup_active(False)

    def _apply_slash_command(self, cmd: AvailableCommandInfo) -> None:
        self._input.setPlainText(f"/{cmd.name} ")
        cursor = self._input.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self._input.setTextCursor(cursor)
        self._input.set_popup_active(False)
        self._input.setFocus()

    def _update_send_button(self) -> None:
        pal = get_palette()
        has_text = bool(self._input.toPlainText().strip())
        self._send_btn.setEnabled(self._busy or (self._session_ready and has_text))
        self._send_btn.setAccessibleName("Stop generation" if self._busy else "Send message")
        if self._busy:
            self._send_btn.setIcon(lucide_icon("square", "#ffffff", 11))
            self._send_btn.setToolTip("Stop generation")
            self._send_btn.setStyleSheet(
                f"QPushButton {{ background: {pal.red}; border: none; border-radius: 13px; }}"
                "QPushButton:hover { background: #ef4444; }"
            )
        elif has_text and self._session_ready:
            self._send_btn.setIcon(lucide_icon("arrow-up", "#ffffff", 14))
            self._send_btn.setToolTip("Send message (Enter)")
            self._send_btn.setStyleSheet(
                f"QPushButton {{ background: {pal.accent}; border: none; border-radius: 13px; }}"
                f"QPushButton:hover {{ background: {pal.accent_hover}; }}"
            )
        else:
            self._send_btn.setIcon(lucide_icon("arrow-up", pal.text_faint, 14))
            self._send_btn.setToolTip("Type a message" if self._session_ready else "Connect an agent to send")
            self._send_btn.setStyleSheet(
                f"QPushButton {{ background: {pal.bg_hover}; border: none; border-radius: 13px; }}"
            )

    def _on_agent_selected(self, agent_id: str) -> None:
        if not agent_id:
            return
        if agent_id != self._current_agent_id:
            self._current_agent_id = agent_id
            self._update_empty_state()
            self.agent_selected.emit(agent_id)
        elif not self._session_ready:
            self.reconnect_requested.emit()

    def _pick_workspace(self) -> None:
        start_dir = (
            self._workspace
            or (self._recent_workspaces[0] if self._recent_workspaces else "")
            or str(Path.home())
        )
        folder = QFileDialog.getExistingDirectory(self, "Choose Project Folder", start_dir)
        if folder:
            self.set_workspace(folder)
            self.workspace_selected.emit(folder)

    def _select_workspace_direct(self, folder: str) -> None:
        if folder == self._workspace:
            return
        self.set_workspace(folder)
        self.workspace_selected.emit(folder)

    def _on_start_without_project_toggled(self, enabled: bool) -> None:
        self._start_without_project = enabled
        if hasattr(self, "_settings_page"):
            self._settings_page.set_start_without_project(enabled)
        self.start_without_project_toggled.emit(enabled)

    def _populate_project_menu(self, menu: PocketMenu) -> None:
        project_group = QActionGroup(menu)
        menu.add_check(
            "No Project (General Chat)",
            not self._workspace,
            lambda _c=False: self._select_workspace_direct(""),
            group=project_group,
        )
        if self._recent_workspaces:
            menu.addSeparator()
            for folder in self._recent_workspaces:
                label = Path(folder).name or folder
                menu.add_check(
                    label,
                    bool(self._workspace) and os.path.normcase(self._workspace) == os.path.normcase(folder),
                    lambda _c=False, f=folder: self._select_workspace_direct(f),
                    group=project_group,
                    tooltip=folder,
                )
        menu.addSeparator()
        menu.add_item("Choose Project Folder...", self._pick_workspace, icon="folder-open")

    def _show_project_menu(self) -> None:
        menu = PocketMenu(parent=self)
        self._populate_project_menu(menu)
        pos = self._cwd_btn.mapToGlobal(QPoint(0, self._cwd_btn.height() + 4))
        menu.exec(pos)

    def _export_markdown(self) -> None:
        if not self._turns:
            return
        lines: list[str] = []
        for turn in self._turns:
            if turn.role == "user":
                lines.append(f"## You\n\n{turn.text}\n")
            elif turn.role == "assistant":
                body = turn.text.strip()
                if body:
                    lines.append(f"## {self._current_agent_name()}\n\n{body}\n")
        md = "\n".join(lines).strip()
        cb = QApplication.clipboard()
        if cb is not None and md:
            cb.setText(md)
            self._error_banner.show_banner("Copied", "Conversation copied as Markdown.", variant="info")

    def _show_more_menu(self) -> None:
        menu = PocketMenu(parent=self)

        menu.add_item("Settings…", self.open_settings, icon="settings")
        menu.add_item("Copy chat as Markdown", self._export_markdown, icon="copy", enabled=bool(self._turns))

        hint = menu.sizeHint()
        anchor = self._more_btn.mapToGlobal(QPoint(self._more_btn.width() - hint.width(), self._more_btn.height() + 4))
        menu.exec(anchor)

    def toggle_settings(self) -> None:
        """Toggle between the chat transcript and the settings page."""
        if self._pages.currentWidget() is self._settings_page:
            self.close_settings()
        else:
            self.open_settings()

    def open_settings(self) -> None:
        """Show the settings page inside the drawer."""
        self._review_from_settings = False
        self._pages.setCurrentWidget(self._settings_page)
        self._settings_page.show_index()
        self._refresh_notice()

    def close_settings(self) -> None:
        """Return from settings to the chat transcript."""
        self._review_from_settings = False
        self._pages.setCurrentWidget(self._chat_page)
        self._refresh_notice()
        self._input.setFocus()

    def _on_new_chat_clicked(self) -> None:
        self.close_settings()
        self.new_session_requested.emit()

    def _on_settings_voice_toggled(self, enabled: bool) -> None:
        self._voice_enabled = enabled
        self.voice_toggled.emit(enabled)

    def _on_settings_voice_selected(self, voice_name: str) -> None:
        self._voice_name = voice_name
        self.voice_selected.emit(voice_name)

    def _on_settings_always_on_top(self, enabled: bool) -> None:
        self.set_always_on_top(enabled)
        self.always_on_top_toggled.emit(enabled)

    def _emit_theme_mode(self, mode: str) -> None:
        self.set_theme(mode, self._backdrop_blur)
        self.theme_selected.emit(mode)

    def _emit_blur_toggle(self, enabled: bool) -> None:
        self.set_theme(self._theme_mode, enabled)
        self.blur_toggled.emit(enabled)

    def _on_toggle_thoughts(self, checked: bool) -> None:
        self._show_thoughts = checked
        self.thoughts_toggled.emit(checked)
        if hasattr(self, "_settings_page"):
            self._settings_page.set_show_thoughts(checked)
        for w in self._turn_widgets:
            w.set_show_thoughts(checked)

    def _submit_prompt(self, text: str) -> None:
        if self._busy or not self._session_ready:
            return
        self._input.clear()
        self._suggestion_popup.setVisible(False)
        self._input.set_popup_active(False)
        self.append_user_message(text)
        self.prompt_submitted.emit(text)

    def _on_send_or_stop_clicked(self) -> None:
        if self._busy:
            self.cancel_requested.emit()
        else:
            text = self._input.toPlainText().strip()
            if text:
                self._input.push_history(text)
                self._submit_prompt(text)

    def _on_permission_widget_decided(self, request_id: str, option_id: object) -> None:
        self._after_permission_change()
        self.permission_decided.emit(request_id, option_id)

    def _on_close_clicked(self) -> None:
        self.hide_animated()

    # ------------------------------------------------------------------
    # Qt Events
    # ------------------------------------------------------------------

    def showEvent(self, event: QShowEvent) -> None:  # type: ignore[override]
        super().showEvent(event)
        self._apply_backdrop_now()
        if not self.permission_pending and not self.isActiveWindow():
            self._auto_hide_timer.start()
        self.opened.emit()

    def hideEvent(self, event) -> None:
        self._auto_hide_timer.stop()
        super().hideEvent(event)

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.ActivationChange and self.isVisible():
            if self.isActiveWindow():
                self._auto_hide_timer.stop()
                self._animator.cancel_hide()
            elif not self.permission_pending:
                self._auto_hide_timer.start()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # type: ignore[override]
        if event.key() == Qt.Key.Key_Escape and self._pages.currentWidget() is self._settings_page:
            self._settings_page.navigate_back()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Escape and self._error_banner.isVisible():
            self._error_banner.dismiss()
            event.accept()
            return
        super().keyPressEvent(event)

    def eventFilter(self, obj, event) -> bool:  # type: ignore[override]
        if obj is self._input:
            if event.type() == event.Type.FocusIn:
                self._input_focused = True
                self._input_box.setStyleSheet(self._input_box_qss(focused=True))
            elif event.type() == event.Type.FocusOut:
                self._input_focused = False
                self._input_box.setStyleSheet(self._input_box_qss(focused=False))
        return super().eventFilter(obj, event)

    def resizeEvent(self, event: QResizeEvent) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._position_scroll_btn()
        self._size_grip.move(self.width() - self._size_grip.width() - 3, self.height() - self._size_grip.height() - 3)

    def paintEvent(self, event) -> None:  # type: ignore[override]
        pal = get_palette()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        if self._use_native_blur():
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
            # Acrylic tint is applied natively by SetWindowCompositionAttribute; keep client alpha at 1/255
            # so DWM composites the backdrop without double-darkening while preserving mouse hit-testing.
            painter.fillRect(self.rect(), QColor(0, 0, 0, 1))
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            rect = self.rect().adjusted(0, 0, -1, -1)
            path = QPainterPath()
            path.addRoundedRect(rect.x(), rect.y(), rect.width(), rect.height(), 8, 8)
            painter.setPen(QColor(pal.border))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path)
        else:
            rect = self.rect().adjusted(1, 1, -1, -1)
            path = QPainterPath()
            path.addRoundedRect(rect.x(), rect.y(), rect.width(), rect.height(), 14, 14)
            painter.setPen(QColor(pal.border))
            painter.setBrush(QColor(pal.bg_primary))
            painter.drawPath(path)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton and event.position().y() < 42:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if self._drag_offset is not None and (event.buttons() & Qt.MouseButton.LeftButton):
            self._manually_moved = True
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        self._drag_offset = None
        super().mouseReleaseEvent(event)

    @staticmethod
    def _input_box_qss(focused: bool) -> str:
        pal = get_palette()
        border_col = pal.accent if focused else pal.border
        bg_col = pal.bg_card_blur if is_blur_enabled() else pal.bg_card
        return f"#ChatInputBox {{ background: {bg_col}; border: 1px solid {border_col}; border-radius: 12px; }}"
