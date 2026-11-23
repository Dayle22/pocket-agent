"""In-drawer Settings page for voice responses, appearance, and workspace preferences."""

from __future__ import annotations

from PyQt6.QtCore import Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from pocket_agent import __version__
from pocket_agent.engine.orbkit import ORBKIT_PLAYGROUND_URL
from pocket_agent.ui.theme import FONT_UI, get_palette, is_blur_enabled, lucide_icon
from pocket_agent.voice import VOICE_PRESETS


class SettingsPage(QWidget):
    """Scrollable settings panel docked inside the chat drawer."""

    back_requested = pyqtSignal()
    voice_toggled = pyqtSignal(bool)
    voice_selected = pyqtSignal(str)
    test_voice_requested = pyqtSignal()
    stop_speaking_requested = pyqtSignal()
    theme_selected = pyqtSignal(str)
    blur_toggled = pyqtSignal(bool)
    always_on_top_toggled = pyqtSignal(bool)
    startup_toggled = pyqtSignal(bool)
    thoughts_toggled = pyqtSignal(bool)
    start_without_project_toggled = pyqtSignal(bool)
    workspace_browse_requested = pyqtSignal()
    workspace_clear_requested = pyqtSignal()
    auto_update_toggled = pyqtSignal(bool)
    check_updates_requested = pyqtSignal()
    apply_update_requested = pyqtSignal()
    manage_orbs_requested = pyqtSignal()

    def __init__(
        self,
        voice_enabled: bool = True,
        voice_name: str = "en-ZA-LeahNeural",
        theme_mode: str = "system",
        backdrop_blur: bool = True,
        always_on_top: bool = True,
        launch_at_startup: bool = False,
        show_thoughts: bool = True,
        workspace: str = "",
        start_without_project: bool = True,
        check_updates_on_startup: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._cards: list[QFrame] = []
        self._section_labels: list[QLabel] = []
        self._field_labels: list[QLabel] = []

        self._build_ui(
            voice_enabled=voice_enabled,
            voice_name=voice_name,
            theme_mode=theme_mode,
            backdrop_blur=backdrop_blur,
            always_on_top=always_on_top,
            launch_at_startup=launch_at_startup,
            show_thoughts=show_thoughts,
            workspace=workspace,
            start_without_project=start_without_project,
            check_updates_on_startup=check_updates_on_startup,
        )
        self.apply_theme()

    def _build_ui(
        self,
        *,
        voice_enabled: bool,
        voice_name: str,
        theme_mode: str,
        backdrop_blur: bool,
        always_on_top: bool,
        launch_at_startup: bool,
        show_thoughts: bool,
        workspace: str,
        start_without_project: bool,
        check_updates_on_startup: bool,
    ) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        container = QWidget()
        container.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(14, 14, 14, 16)
        layout.setSpacing(12)

        # Top row
        top_row = QHBoxLayout()
        top_row.setSpacing(8)
        self._title_lbl = QLabel("Settings", container)
        top_row.addWidget(self._title_lbl)
        top_row.addStretch(1)

        self._back_btn = QPushButton("Back to Chat", container)
        self._back_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._back_btn.clicked.connect(self.back_requested.emit)
        top_row.addWidget(self._back_btn)
        layout.addLayout(top_row)

        # ---- Voice Responses Card ----
        voice_card, v_lay = self._make_card("Voice Responses", container)
        self._voice_enable_cb = QCheckBox("Enable Voice Responses (Edge TTS)", voice_card)
        self._voice_enable_cb.setChecked(voice_enabled)
        self._voice_enable_cb.toggled.connect(self.voice_toggled.emit)
        v_lay.addWidget(self._voice_enable_cb)

        voice_lbl = QLabel("Voice", voice_card)
        self._field_labels.append(voice_lbl)
        v_lay.addWidget(voice_lbl)

        self._voice_combo = QComboBox(voice_card)
        for label, vid in VOICE_PRESETS:
            self._voice_combo.addItem(label, vid)
        if self._voice_combo.findData(voice_name) == -1 and voice_name:
            self._voice_combo.addItem(voice_name, voice_name)
        self.set_voice_name(voice_name)
        self._voice_combo.currentIndexChanged.connect(self._on_voice_combo_changed)
        v_lay.addWidget(self._voice_combo)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self._test_voice_btn = QPushButton("Test Voice", voice_card)
        self._test_voice_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._test_voice_btn.clicked.connect(self.test_voice_requested.emit)
        btn_row.addWidget(self._test_voice_btn)

        self._stop_voice_btn = QPushButton("Stop", voice_card)
        self._stop_voice_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._stop_voice_btn.clicked.connect(self.stop_speaking_requested.emit)
        btn_row.addWidget(self._stop_voice_btn)
        btn_row.addStretch(1)
        v_lay.addLayout(btn_row)
        layout.addWidget(voice_card)

        # ---- Appearance & Window Card ----
        app_card, a_lay = self._make_card("Appearance & Window", container)
        theme_lbl = QLabel("Theme", app_card)
        self._field_labels.append(theme_lbl)
        a_lay.addWidget(theme_lbl)

        self._theme_combo = QComboBox(app_card)
        for label, mode in [("Auto (Windows)", "system"), ("Dark", "dark"), ("Light", "light")]:
            self._theme_combo.addItem(label, mode)
        self.set_theme_mode(theme_mode)
        self._theme_combo.currentIndexChanged.connect(self._on_theme_combo_changed)
        a_lay.addWidget(self._theme_combo)

        self._blur_cb = QCheckBox("Translucent Blur (Acrylic)", app_card)
        self._blur_cb.setChecked(backdrop_blur)
        self._blur_cb.toggled.connect(self.blur_toggled.emit)
        a_lay.addWidget(self._blur_cb)

        self._top_cb = QCheckBox("Always on Top", app_card)
        self._top_cb.setChecked(always_on_top)
        self._top_cb.toggled.connect(self.always_on_top_toggled.emit)
        a_lay.addWidget(self._top_cb)

        self._startup_cb = QCheckBox("Launch at Windows Startup", app_card)
        self._startup_cb.setChecked(launch_at_startup)
        self._startup_cb.toggled.connect(self.startup_toggled.emit)
        a_lay.addWidget(self._startup_cb)

        orbkit_lbl = QLabel("Custom Orbs (Orbkit)", app_card)
        self._field_labels.append(orbkit_lbl)
        a_lay.addWidget(orbkit_lbl)

        orb_row = QHBoxLayout()
        orb_row.setSpacing(8)
        self._manage_orbs_btn = QPushButton("Add / Manage Orbkit Orbs...", app_card)
        self._manage_orbs_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._manage_orbs_btn.clicked.connect(self.manage_orbs_requested.emit)
        orb_row.addWidget(self._manage_orbs_btn)

        self._open_orbkit_btn = QPushButton("Open Playground", app_card)
        self._open_orbkit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._open_orbkit_btn.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(ORBKIT_PLAYGROUND_URL))
        )
        orb_row.addWidget(self._open_orbkit_btn)
        orb_row.addStretch(1)
        a_lay.addLayout(orb_row)

        layout.addWidget(app_card)

        # ---- Chat & Workspace Card ----
        chat_card, c_lay = self._make_card("Chat & Project", container)
        self._thoughts_cb = QCheckBox("Show Thinking Blocks", chat_card)
        self._thoughts_cb.setChecked(show_thoughts)
        self._thoughts_cb.toggled.connect(self.thoughts_toggled.emit)
        c_lay.addWidget(self._thoughts_cb)

        self._no_project_default_cb = QCheckBox("Start without a project by default", chat_card)
        self._no_project_default_cb.setChecked(start_without_project)
        self._no_project_default_cb.toggled.connect(self.start_without_project_toggled.emit)
        c_lay.addWidget(self._no_project_default_cb)

        ws_lbl = QLabel("Active Project", chat_card)
        self._field_labels.append(ws_lbl)
        c_lay.addWidget(ws_lbl)

        ws_row = QHBoxLayout()
        ws_row.setSpacing(8)
        self._workspace_val = QLabel(workspace or "No project (general chat)", chat_card)
        self._workspace_val.setWordWrap(True)
        ws_row.addWidget(self._workspace_val, 1)

        self._clear_ws_btn = QPushButton("No Project", chat_card)
        self._clear_ws_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._clear_ws_btn.setEnabled(bool(workspace))
        self._clear_ws_btn.clicked.connect(self.workspace_clear_requested.emit)
        ws_row.addWidget(self._clear_ws_btn)

        self._browse_ws_btn = QPushButton("Browse...", chat_card)
        self._browse_ws_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._browse_ws_btn.clicked.connect(self.workspace_browse_requested.emit)
        ws_row.addWidget(self._browse_ws_btn)
        c_lay.addLayout(ws_row)
        layout.addWidget(chat_card)

        # ---- Updates Card ----
        updates_card, u_lay = self._make_card("Updates", container)
        self._auto_update_cb = QCheckBox("Check for updates automatically", updates_card)
        self._auto_update_cb.setChecked(check_updates_on_startup)
        self._auto_update_cb.toggled.connect(self.auto_update_toggled.emit)
        u_lay.addWidget(self._auto_update_cb)

        self._update_status_lbl = QLabel("Up to date", updates_card)
        self._update_status_lbl.setWordWrap(True)
        self._field_labels.append(self._update_status_lbl)
        u_lay.addWidget(self._update_status_lbl)

        upd_row = QHBoxLayout()
        upd_row.setSpacing(8)
        self._check_updates_btn = QPushButton("Check for Updates", updates_card)
        self._check_updates_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._check_updates_btn.clicked.connect(self.check_updates_requested.emit)
        upd_row.addWidget(self._check_updates_btn)

        self._apply_update_btn = QPushButton("Update & Restart", updates_card)
        self._apply_update_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._apply_update_btn.clicked.connect(self.apply_update_requested.emit)
        self._apply_update_btn.setVisible(False)
        upd_row.addWidget(self._apply_update_btn)
        upd_row.addStretch(1)
        u_lay.addLayout(upd_row)
        layout.addWidget(updates_card)

        layout.addStretch(1)
        self._version_lbl = QLabel(f"Pocket Agent v{__version__}", container)
        self._version_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._version_lbl)
        self._scroll.setWidget(container)
        root.addWidget(self._scroll)

    def _make_card(self, title: str, parent: QWidget) -> tuple[QFrame, QVBoxLayout]:
        card = QFrame(parent)
        card.setObjectName("SettingsCard")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(12, 10, 12, 12)
        lay.setSpacing(8)
        lbl = QLabel(title, card)
        lay.addWidget(lbl)
        self._cards.append(card)
        self._section_labels.append(lbl)
        return card, lay

    def _on_voice_combo_changed(self, idx: int) -> None:
        voice_id = self._voice_combo.itemData(idx)
        if isinstance(voice_id, str) and voice_id:
            self.voice_selected.emit(voice_id)

    def _on_theme_combo_changed(self, idx: int) -> None:
        mode = self._theme_combo.itemData(idx)
        if isinstance(mode, str) and mode:
            self.theme_selected.emit(mode)

    # ---- Sync setters (do not re-emit signals) ----

    def set_voice_enabled(self, enabled: bool) -> None:
        self._voice_enable_cb.blockSignals(True)
        self._voice_enable_cb.setChecked(enabled)
        self._voice_enable_cb.blockSignals(False)

    def set_voice_name(self, voice_name: str) -> None:
        self._voice_combo.blockSignals(True)
        idx = self._voice_combo.findData(voice_name)
        if idx == -1 and voice_name:
            self._voice_combo.addItem(voice_name, voice_name)
            idx = self._voice_combo.findData(voice_name)
        if idx >= 0:
            self._voice_combo.setCurrentIndex(idx)
        self._voice_combo.blockSignals(False)

    def set_theme_mode(self, mode: str) -> None:
        self._theme_combo.blockSignals(True)
        idx = self._theme_combo.findData(mode)
        if idx >= 0:
            self._theme_combo.setCurrentIndex(idx)
        self._theme_combo.blockSignals(False)

    def set_backdrop_blur(self, enabled: bool) -> None:
        self._blur_cb.blockSignals(True)
        self._blur_cb.setChecked(enabled)
        self._blur_cb.blockSignals(False)

    def set_always_on_top(self, enabled: bool) -> None:
        self._top_cb.blockSignals(True)
        self._top_cb.setChecked(enabled)
        self._top_cb.blockSignals(False)

    def set_launch_at_startup(self, enabled: bool) -> None:
        self._startup_cb.blockSignals(True)
        self._startup_cb.setChecked(enabled)
        self._startup_cb.blockSignals(False)

    def set_show_thoughts(self, enabled: bool) -> None:
        self._thoughts_cb.blockSignals(True)
        self._thoughts_cb.setChecked(enabled)
        self._thoughts_cb.blockSignals(False)

    def set_workspace(self, folder: str) -> None:
        clean = folder.strip()
        self._workspace_val.setText(clean or "No project (general chat)")
        self._clear_ws_btn.setEnabled(bool(clean))

    def set_start_without_project(self, enabled: bool) -> None:
        self._no_project_default_cb.blockSignals(True)
        self._no_project_default_cb.setChecked(enabled)
        self._no_project_default_cb.blockSignals(False)

    def set_check_updates_on_startup(self, enabled: bool) -> None:
        self._auto_update_cb.blockSignals(True)
        self._auto_update_cb.setChecked(enabled)
        self._auto_update_cb.blockSignals(False)

    def set_update_status(
        self,
        text: str,
        *,
        busy: bool = False,
        update_available: bool = False,
        is_frozen: bool = False,
    ) -> None:
        """Update the status label and action buttons in the Updates card."""
        self._update_status_lbl.setText(text)
        self._check_updates_btn.setEnabled(not busy)
        self._apply_update_btn.setEnabled(not busy)
        if update_available:
            self._apply_update_btn.setText("Update & Restart" if is_frozen else "Open Release Page")
            self._apply_update_btn.setVisible(True)
        elif not busy:
            self._apply_update_btn.setVisible(False)

    def apply_theme(self) -> None:
        pal = get_palette()
        self._title_lbl.setStyleSheet(
            f"color: {pal.heading_color}; font-size: 16px; font-weight: 700; font-family: {FONT_UI}; background: transparent;"
        )
        for lbl in self._section_labels:
            lbl.setStyleSheet(
                f"color: {pal.heading_color}; font-size: 12px; font-weight: 600; font-family: {FONT_UI}; background: transparent;"
            )
        for lbl in self._field_labels:
            lbl.setStyleSheet(
                f"color: {pal.text_muted}; font-size: 11px; font-family: {FONT_UI}; background: transparent;"
            )
        self._workspace_val.setStyleSheet(
            f"color: {pal.text_normal}; font-size: 11px; font-family: {FONT_UI}; background: transparent;"
        )
        self._version_lbl.setStyleSheet(
            f"color: {pal.text_faint}; font-size: 10px; font-family: {FONT_UI}; background: transparent; padding-top: 4px;"
        )

        card_bg = pal.bg_card_blur if is_blur_enabled() else pal.bg_card
        card_qss = (
            f"#SettingsCard {{ background: {card_bg}; border: 1px solid {pal.border_subtle}; border-radius: 10px; }}"
        )
        for card in self._cards:
            card.setStyleSheet(card_qss)

        cb_qss = (
            f"QCheckBox {{ color: {pal.text_normal}; font-size: 12px; font-family: {FONT_UI}; spacing: 8px; background: transparent; }}"
        )
        for cb in (
            self._voice_enable_cb,
            self._blur_cb,
            self._top_cb,
            self._startup_cb,
            self._thoughts_cb,
            self._no_project_default_cb,
            self._auto_update_cb,
        ):
            cb.setStyleSheet(cb_qss)

        combo_qss = f"""
            QComboBox {{
                background: {pal.bg_secondary};
                color: {pal.text_normal};
                border: 1px solid {pal.border};
                border-radius: 7px;
                padding: 5px 10px;
                font-size: 12px;
                font-family: {FONT_UI};
            }}
            QComboBox::drop-down {{ border: none; width: 20px; }}
            QComboBox QAbstractItemView {{
                background: {pal.bg_secondary};
                color: {pal.text_normal};
                border: 1px solid {pal.border};
                selection-background-color: {pal.accent};
                selection-color: #ffffff;
            }}
        """
        self._voice_combo.setStyleSheet(combo_qss)
        self._theme_combo.setStyleSheet(combo_qss)

        btn_qss = f"""
            QPushButton {{
                background: {pal.bg_secondary};
                color: {pal.text_normal};
                border: 1px solid {pal.border};
                border-radius: 7px;
                padding: 5px 12px;
                font-size: 11px;
                font-family: {FONT_UI};
            }}
            QPushButton:hover {{
                background: {pal.bg_hover};
                border-color: {pal.accent};
            }}
        """
        for btn in (
            self._back_btn,
            self._test_voice_btn,
            self._stop_voice_btn,
            self._manage_orbs_btn,
            self._open_orbkit_btn,
            self._clear_ws_btn,
            self._browse_ws_btn,
            self._check_updates_btn,
            self._apply_update_btn,
        ):
            btn.setStyleSheet(btn_qss)
        self._test_voice_btn.setIcon(lucide_icon("volume-2", pal.text_normal, 13))
        self._manage_orbs_btn.setIcon(lucide_icon("sparkles", pal.text_normal, 13))
        self._open_orbkit_btn.setIcon(lucide_icon("globe", pal.text_normal, 13))
        self._check_updates_btn.setIcon(lucide_icon("refresh-cw", pal.text_normal, 13))

        self._scroll.setStyleSheet(
            f"""
            QScrollArea {{ background: transparent; border: none; }}
            QScrollBar:vertical {{ background: transparent; width: 6px; margin: 6px 2px; }}
            QScrollBar::handle:vertical {{ background: {pal.border}; border-radius: 3px; min-height: 24px; }}
            QScrollBar::handle:vertical:hover {{ background: {pal.text_faint}; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
            """
        )
