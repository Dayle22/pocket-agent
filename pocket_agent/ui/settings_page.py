"""Settings index and detail pages inside the chat drawer."""

from __future__ import annotations

from PyQt6.QtCore import Qt, QSignalBlocker, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QCommandLinkButton, QFrame, QHBoxLayout, QLabel,
    QPushButton, QScrollArea, QStackedWidget, QVBoxLayout, QWidget,
)

from pocket_agent import __version__
from pocket_agent.agents import AgentSpec
from pocket_agent.ui.companion_picker import CompanionPicker
from pocket_agent.ui.theme import FONT_UI, get_palette, lucide_icon
from pocket_agent.voice import VOICE_PRESETS


class SettingsPage(QWidget):
    back_requested = pyqtSignal()
    voice_toggled = pyqtSignal(bool)
    voice_selected = pyqtSignal(str)
    test_voice_requested = pyqtSignal()
    stop_speaking_requested = pyqtSignal()
    theme_selected = pyqtSignal(str)
    blur_toggled = pyqtSignal(bool)
    drop_shadow_toggled = pyqtSignal(bool)
    always_on_top_toggled = pyqtSignal(bool)
    startup_toggled = pyqtSignal(bool)
    thoughts_toggled = pyqtSignal(bool)
    start_without_project_toggled = pyqtSignal(bool)
    auto_update_toggled = pyqtSignal(bool)
    check_updates_requested = pyqtSignal()
    apply_update_requested = pyqtSignal()
    manage_orbs_requested = pyqtSignal()
    setup_requested = pyqtSignal()
    reconnect_requested = pyqtSignal()
    log_requested = pyqtSignal()
    default_agent_selected = pyqtSignal(str)
    text_size_changed = pyqtSignal(int)
    reduced_motion_toggled = pyqtSignal(bool)
    idle_animation_toggled = pyqtSignal(bool)

    def __init__(
        self, voice_enabled: bool = True, voice_name: str = "en-ZA-LeahNeural",
        theme_mode: str = "system", backdrop_blur: bool = True,
        always_on_top: bool = True, launch_at_startup: bool = False,
        show_thoughts: bool = True, start_without_project: bool = True,
        check_updates_on_startup: bool = True, parent: QWidget | None = None,
        *, drop_shadow: bool = True,
    ) -> None:
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 8, 12, 10)
        header = QHBoxLayout()
        self._index_btn = QPushButton("‹ Settings")
        self._index_btn.setAccessibleName("Back to settings list")
        self._index_btn.clicked.connect(self.show_index)
        header.addWidget(self._index_btn)
        self._title_lbl = QLabel("Settings")
        self._title_lbl.setObjectName("SettingsTitle")
        header.addWidget(self._title_lbl, 1)
        self._back_btn = QPushButton("Back to chat")
        self._back_btn.clicked.connect(self.back_requested.emit)
        header.addWidget(self._back_btn)
        root.addLayout(header)
        self._stack = QStackedWidget()
        root.addWidget(self._stack, 1)
        self._index, index_layout = self._new_page()
        self._sections: dict[str, QScrollArea] = {}
        self._rows: dict[str, QCommandLinkButton] = {}
        for name in ("Agent & chat", "Companion", "Voice", "Appearance", "General"):
            row = QCommandLinkButton(name.replace("&", "&&"))
            row.setObjectName("SettingsRow")
            row.clicked.connect(lambda _checked=False, section=name: self.open_section(section))
            index_layout.addWidget(row)
            self._rows[name] = row
        index_layout.addStretch()

        chat = self._section("Agent & chat")
        self._agent = self._combo(chat, "Agent at startup", [("Last used agent", "")])
        self._agent.activated.connect(lambda _index: self.default_agent_selected.emit(self._agent.currentData()))
        self._button(chat, "Connect an agent…", self.setup_requested.emit)
        self._button(chat, "Reconnect current agent", self.reconnect_requested.emit)
        self._no_project_default_cb = self._check(chat, "Start in general chat", start_without_project,
                                                  self.start_without_project_toggled)
        self._note(chat, "When off, Pocket Agent opens the last project folder at startup.")
        self._thoughts_cb = self._check(chat, "Show agent thinking", show_thoughts, self.thoughts_toggled)
        chat.addStretch()

        companion = self._section("Companion")
        self.picker = CompanionPicker(self)
        companion.addWidget(self.picker)
        self._button(companion, "Manage custom orbs…", self.manage_orbs_requested.emit)
        self._animate_cb = self._check(companion, "Animate while idle", True, self.idle_animation_toggled)
        self._top_cb = self._check(companion, "Keep companion and chat on top", always_on_top,
                                   self.always_on_top_toggled)
        companion.addStretch()

        voice = self._section("Voice")
        self._voice_enable_cb = self._check(voice, "Read replies aloud automatically", voice_enabled,
                                            self.voice_toggled)
        self._note(voice, "Spoken text is sent to Microsoft for speech synthesis. You can also read an individual reply aloud.")
        self._voice_combo = self._combo(voice, "Voice", VOICE_PRESETS)
        if self._voice_combo.findData(voice_name) < 0:
            self._voice_combo.addItem(voice_name, voice_name)
        self.set_voice_name(voice_name)
        self._voice_combo.currentIndexChanged.connect(lambda: self.voice_selected.emit(self._voice_combo.currentData()))
        self._playing = False
        self._test_voice_btn = self._button(voice, "Preview voice", self._preview_voice)
        voice.addStretch()

        appearance = self._section("Appearance")
        self._theme_combo = self._combo(appearance, "Theme", [("Follow Windows", "system"),
                                                              ("Dark", "dark"), ("Light", "light")])
        self.set_theme_mode(theme_mode)
        self._theme_combo.currentIndexChanged.connect(lambda: self.theme_selected.emit(self._theme_combo.currentData()))
        self._blur_cb = self._check(appearance, "Blur background", backdrop_blur, self.blur_toggled)
        self._drop_shadow_cb = self._check(appearance, "Drop shadow", drop_shadow, self.drop_shadow_toggled)
        self._note(appearance, "Applies to the chat drawer, PocketBot and orbs.")
        self._text_size = self._combo(appearance, "Message text size", [("Standard", 13), ("Large", 15), ("Extra large", 17)])
        self._text_size.currentIndexChanged.connect(lambda: self.text_size_changed.emit(self._text_size.currentData()))
        self._motion_cb = self._check(appearance, "Reduce motion", False, self.reduced_motion_toggled)
        appearance.addStretch()

        general = self._section("General")
        self._shortcut_available = True
        self._shortcut_lbl = self._note(general, "Show or hide chat    Ctrl+Shift+Space")
        self._startup_cb = self._check(general, "Launch at Windows sign-in", launch_at_startup, self.startup_toggled)
        self._auto_update_cb = self._check(general, "Check for updates at startup", check_updates_on_startup,
                                           self.auto_update_toggled)
        self._version_lbl = self._note(general, f"Pocket Agent v{__version__}")
        self._update_status_lbl = self._note(general, "No update check yet")
        self._check_updates_btn = self._button(general, "Check for updates", self.check_updates_requested.emit)
        self._apply_update_btn = self._button(general, "Update & Restart", self.apply_update_requested.emit)
        self._apply_update_btn.hide()
        self._button(general, "Open diagnostic log", self.log_requested.emit)
        general.addStretch()

        for control in (self._agent, self._voice_combo, self._theme_combo):
            control.currentIndexChanged.connect(self._summarise)
        for box in (self._no_project_default_cb, self._voice_enable_cb, self._blur_cb):
            box.toggled.connect(self._summarise)
        self.picker.summary_changed.connect(self._summarise)
        self._summarise()
        self.show_index()
        self.apply_theme()

    def _new_page(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(2, 6, 6, 6)
        layout.setSpacing(9)
        scroll.setWidget(body)
        self._stack.addWidget(scroll)
        return scroll, layout

    def _section(self, name):
        page, layout = self._new_page()
        self._sections[name] = page
        return layout

    @staticmethod
    def _note(layout, text):
        label = QLabel(text)
        label.setWordWrap(True)
        layout.addWidget(label)
        return label

    @staticmethod
    def _button(layout, text, handler):
        button = QPushButton(text)
        button.clicked.connect(lambda _checked=False: handler())
        layout.addWidget(button)
        return button

    @staticmethod
    def _check(layout, text, checked, signal):
        box = QCheckBox(text)
        box.setChecked(checked)
        box.toggled.connect(signal.emit)
        layout.addWidget(box)
        return box

    @staticmethod
    def _combo(layout, title, items):
        label = QLabel(title)
        combo = QComboBox()
        combo.setAccessibleName(title)
        label.setBuddy(combo)
        for name, value in items:
            combo.addItem(name, value)
        layout.addWidget(label)
        layout.addWidget(combo)
        return combo

    def show_index(self):
        previous = self._title_lbl.text()
        self._stack.setCurrentWidget(self._index)
        self._index_btn.hide()
        self._title_lbl.setText("Settings")
        self._rows.get(previous, self._rows["Agent & chat"]).setFocus()

    def open_section(self, name: str):
        self._title_lbl.setText(name)
        self._index_btn.show()
        self._stack.setCurrentWidget(self._sections[name])
        self._index_btn.setFocus()

    def navigate_back(self):
        if self._stack.currentWidget() is self._index:
            self.back_requested.emit()
        else:
            self.show_index()

    def focus_current(self) -> None:
        if self._stack.currentWidget() is self._index:
            self._rows["Agent & chat"].setFocus()
        else:
            self._index_btn.setFocus()

    def _summarise(self):
        project = "General chat" if self._no_project_default_cb.isChecked() else "Last project"
        aloud = "Automatic" if self._voice_enable_cb.isChecked() else "On request"
        blur = "Blur on" if self._blur_cb.isChecked() else "Blur off"
        shortcut = "Ctrl+Shift+Space" if self._shortcut_available else "Shortcut unavailable"
        summaries = {
            "Agent & chat": f"{self._agent.currentText()} · {project}",
            "Companion": self.picker.summary(),
            "Voice": f"{self._voice_combo.currentText()} · {aloud}",
            "Appearance": f"{self._theme_combo.currentText()} · {blur}",
            "General": f"{shortcut} · v{__version__}",
        }
        for name, summary in summaries.items():
            self._rows[name].setDescription(summary)
            self._rows[name].setAccessibleName(f"{name}, {summary}")

    def set_agents(self, agents: list[AgentSpec], default_id: str):
        with QSignalBlocker(self._agent):
            self._agent.clear()
            self._agent.addItem("Last used agent", "")
            for agent in agents:
                self._agent.addItem(agent.name, agent.id)
            self._agent.setCurrentIndex(max(0, self._agent.findData(default_id)))
        self._summarise()

    def set_idle_animation(self, enabled: bool) -> None:
        self._set_checked(self._animate_cb, enabled)

    def set_shortcut_available(self, available: bool) -> None:
        self._shortcut_available = available
        self._shortcut_lbl.setText("Show or hide chat    Ctrl+Shift+Space" if available else
                                   "Ctrl+Shift+Space is unavailable. Click the companion to open chat.")
        self._summarise()

    def set_speaking(self, playing: bool):
        self._playing = playing
        self._test_voice_btn.setText("Stop speaking" if playing else "Preview voice")

    def _preview_voice(self):
        if self._playing:
            self.stop_speaking_requested.emit()
        else:
            self.test_voice_requested.emit()

    def _set_checked(self, box, checked):
        with QSignalBlocker(box):
            box.setChecked(checked)
        if hasattr(self, "_shortcut_lbl"):
            self._summarise()

    def set_voice_enabled(self, enabled):
        self._set_checked(self._voice_enable_cb, enabled)

    def set_voice_name(self, name):
        with QSignalBlocker(self._voice_combo):
            self._voice_combo.setCurrentIndex(self._voice_combo.findData(name))
        if hasattr(self, "_shortcut_lbl"):
            self._summarise()

    def set_theme_mode(self, mode):
        with QSignalBlocker(self._theme_combo):
            self._theme_combo.setCurrentIndex(max(0, self._theme_combo.findData(mode)))
        if hasattr(self, "_shortcut_lbl"):
            self._summarise()

    def set_backdrop_blur(self, enabled):
        self._set_checked(self._blur_cb, enabled)

    def set_drop_shadow(self, enabled: bool) -> None:
        self._set_checked(self._drop_shadow_cb, enabled)

    def set_always_on_top(self, enabled):
        self._set_checked(self._top_cb, enabled)

    def set_launch_at_startup(self, enabled):
        self._set_checked(self._startup_cb, enabled)

    def set_show_thoughts(self, enabled):
        self._set_checked(self._thoughts_cb, enabled)

    def set_start_without_project(self, enabled):
        self._set_checked(self._no_project_default_cb, enabled)

    def set_check_updates_on_startup(self, enabled):
        self._set_checked(self._auto_update_cb, enabled)

    def set_readability(self, size: int, reduced: bool):
        with QSignalBlocker(self._text_size):
            self._text_size.setCurrentIndex(max(0, self._text_size.findData(size)))
        self._set_checked(self._motion_cb, reduced)
        self._animate_cb.setEnabled(not reduced)

    def set_update_status(self, text: str, *, busy=False, update_available=False, is_frozen=False):
        self._update_status_lbl.setText(text)
        self._check_updates_btn.setEnabled(not busy)
        self._apply_update_btn.setEnabled(not busy)
        if update_available:
            self._apply_update_btn.setText("Update & Restart" if is_frozen else "Open Release Page")
        self._apply_update_btn.setVisible(update_available)

    def apply_theme(self):
        pal = get_palette()
        self.setStyleSheet(f"""
            QWidget {{ font-family: {FONT_UI}; color: {pal.text_normal}; }}
            QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; border: none; }}
            QLabel {{ background: transparent; font-size: 13px; }}
            QLabel#SettingsTitle {{ font-size: 16px; font-weight: 600; }}
            QPushButton, QComboBox {{ background: {pal.bg_secondary}; border: 1px solid {pal.border};
                border-radius: 7px; padding: 5px 9px; min-height: 20px; }}
            QCommandLinkButton#SettingsRow {{ background: {pal.bg_secondary}; border: 1px solid {pal.border_subtle};
                border-radius: 8px; padding: 2px 5px; min-height: 45px; text-align: left; }}
            QCheckBox {{ spacing: 8px; min-height: 28px; }}
            QPushButton:hover, QComboBox:hover {{ border-color: {pal.text_muted}; }}
            QPushButton:focus, QCommandLinkButton#SettingsRow:focus, QCheckBox:focus, QSlider:focus,
            QComboBox:focus {{ border: 2px solid {pal.accent}; }}
            QComboBox QAbstractItemView {{ background: {pal.bg_secondary}; color: {pal.text_normal};
                selection-background-color: {pal.accent}; }}
        """)
        for name, icon in zip(self._rows, ("message-square", "smile", "volume-2", "sun", "settings")):
            self._rows[name].setIcon(lucide_icon(icon, pal.text_muted, 18))
