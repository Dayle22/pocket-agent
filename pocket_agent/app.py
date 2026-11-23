"""Main application wiring PetWindow, ChatDrawer, AcpBridge, and global hotkey."""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import logging
from logging.handlers import RotatingFileHandler
import os
import sys
from pathlib import Path

from PyQt6.QtCore import QAbstractNativeEventFilter, QByteArray, QObject, QTimer, QUrl
from PyQt6.QtGui import QDesktopServices, QIcon
from PyQt6.QtNetwork import QLocalServer, QLocalSocket
from PyQt6.QtWidgets import QApplication, QFileDialog

from pocket_agent.acp_bridge import AcpBridge, ModeInfo
from pocket_agent.agents import AgentRegistry
from pocket_agent.config import (
    CONFIG_DIR,
    AppConfig,
    get_launch_at_startup,
    set_launch_at_startup,
)
from pocket_agent.engine.catalog import BUILTIN_ASSETS_DIR, PetCatalog
from pocket_agent.engine.fsm import PetState
from pocket_agent.engine.orbkit import CUSTOM_ORBS_DIR
from pocket_agent.ui.drawer import ChatDrawer
from pocket_agent.ui.orbkit_dialog import OrbkitManagerDialog
from pocket_agent.ui.pet_window import PetWindow
from pocket_agent.ui.setup_guide import AgentSetupDialog
from pocket_agent.ui.theme import set_active_theme
from pocket_agent.updater import (
    RELEASES_PAGE_URL,
    ReleaseInfo,
    UpdateCheckResult,
    UpdateManager,
    is_frozen,
    spawn_apply_update_script,
)
from pocket_agent.voice import VoiceEngine

SINGLE_INSTANCE_SERVER_NAME = "pocket-agent-single-instance"
_LOG_FILE = CONFIG_DIR / "pocket-agent.log"
logger = logging.getLogger("pocket_agent")

_WORKING_PHRASES: dict[str, tuple[str, ...]] = {
    "thinking": ("Thinking...", "Pondering...", "Reasoning...", "Connecting dots..."),
    "reading": ("Reading files...", "Searching...", "Inspecting code...", "Gathering context..."),
    "editing": ("Editing files...", "Writing code...", "Applying changes...", "Refactoring..."),
    "running": ("Running command...", "Testing changes...", "Checking output...", "Working on it..."),
    "tools": ("Using tools...", "Working on it...", "Crunching away...", "Making progress..."),
    "responding": ("Writing reply...", "Drafting response...", "Putting it together..."),
}


def _tool_phase_for(kind: str, title: str) -> str:
    """Map an ACP tool call kind/title to a concise working-bubble phase."""
    haystack = f"{kind} {title}".lower()
    if any(k in haystack for k in ("read", "search", "find", "grep", "glob", "list", "inspect", "fetch", "think")):
        return "reading"
    if any(k in haystack for k in ("edit", "write", "create", "patch", "update", "delete", "move")):
        return "editing"
    if any(k in haystack for k in ("execute", "run", "bash", "shell", "terminal", "command", "test", "build")):
        return "running"
    return "tools"


class _WindowsHotkeyFilter(QAbstractNativeEventFilter):
    """Listens for WM_HOTKEY (Ctrl+Shift+Space) on Windows."""

    WM_HOTKEY = 0x0312
    MOD_CONTROL = 0x0002
    MOD_SHIFT = 0x0004
    VK_SPACE = 0x20
    HOTKEY_ID = 0x5041  # 'PA'

    def __init__(self, on_triggered) -> None:
        super().__init__()
        self._on_triggered = on_triggered
        self._registered = False
        if os.name == "nt":
            try:
                user32 = ctypes.windll.user32
                ok = user32.RegisterHotKey(
                    None,
                    self.HOTKEY_ID,
                    self.MOD_CONTROL | self.MOD_SHIFT,
                    self.VK_SPACE,
                )
                self._registered = bool(ok)
            except Exception:
                self._registered = False

    def unregister(self) -> None:
        if os.name == "nt" and self._registered:
            try:
                ctypes.windll.user32.UnregisterHotKey(None, self.HOTKEY_ID)
            except Exception:
                pass
            self._registered = False

    def nativeEventFilter(self, eventType: QByteArray, message) -> tuple[bool, int]:  # type: ignore[override]
        if os.name == "nt" and eventType == b"windows_generic_MSG" and message is not None:
            try:
                msg = ctypes.wintypes.MSG.from_address(int(message))
                if msg.message == self.WM_HOTKEY and msg.wParam == self.HOTKEY_ID:
                    self._on_triggered()
                    return True, 0
            except Exception:
                pass
        return False, 0


class PocketAgentApp(QObject):
    """Coordinates pet animations, ACP bridge events, and chat drawer UI."""

    def __init__(self, qt_app: QApplication) -> None:
        super().__init__()
        self.qt_app = qt_app
        self.config = AppConfig.load()
        self.registry = AgentRegistry(self.config.custom_agents)
        self.catalog = PetCatalog(self.config.pets_dirs, custom_orbs_dir=CUSTOM_ORBS_DIR)
        self.bridge = AcpBridge(self)
        self.updater = UpdateManager(self)
        self._latest_release: ReleaseInfo | None = None

        # Pick saved agent if available, else first available preset
        current_spec = self.registry.get(self.config.agent_id)
        if current_spec is None or not current_spec.is_available():
            current_spec = self.registry.default_available()
            self.config.agent_id = current_spec.id
            self.config.save()

        set_active_theme(self.config.theme, self.config.backdrop_blur)
        self.voice = VoiceEngine(
            voice_name=self.config.voice_name,
            enabled=self.config.voice_enabled,
            parent=self,
        )
        self._turn_active = False
        self._waiting_permission = False
        self._working_phase = "thinking"
        self._working_phrase_idx = 0
        self._working_phrase_timer = QTimer(self)
        self._working_phrase_timer.setInterval(3200)
        self._working_phrase_timer.timeout.connect(self._advance_working_phrase)
        launch_at_startup = get_launch_at_startup()
        self.pet = PetWindow(
            self.config,
            self.catalog,
            self.registry.all(),
            launch_at_startup=launch_at_startup,
        )
        self.drawer = ChatDrawer(
            agents=self.registry.all(),
            current_agent_id=self.config.agent_id,
            workspace=self.config.workspace,
            always_on_top=self.config.always_on_top,
            launch_at_startup=launch_at_startup,
            theme_mode=self.config.theme,
            backdrop_blur=self.config.backdrop_blur,
            voice_enabled=self.config.voice_enabled,
            voice_name=self.config.voice_name,
            check_updates_on_startup=self.config.check_updates_on_startup,
            width=self.config.drawer_width,
            height=self.config.drawer_height,
        )

        self._wire_signals()

        self._hotkey_filter = _WindowsHotkeyFilter(self._toggle_drawer)
        self.qt_app.installNativeEventFilter(self._hotkey_filter)

        self.pet.show()
        self.drawer.dock_near(self.pet.frameGeometry(), force=True)
        self.pet.say("Click me or press Ctrl+Shift+Space to chat!", 3200)

        self._connect_current_agent()
        if self.config.check_updates_on_startup:
            QTimer.singleShot(1500, lambda: self.updater.check_for_updates_async(silent=True))

    def _wire_signals(self) -> None:
        # Pet interactions
        self.pet.toggle_drawer_requested.connect(self._toggle_drawer)
        self.pet.bubble.clicked.connect(self._open_drawer_from_bubble)
        self.pet.moved.connect(lambda rect: self.drawer.dock_near(rect))
        self.pet.agent_selected.connect(self._switch_agent)
        self.pet.new_session_requested.connect(self._new_session)
        self.pet.reconnect_requested.connect(self._reconnect_agent)
        self.pet.setup_guide_requested.connect(self._open_setup_guide)
        self.pet.settings_requested.connect(self._open_settings)
        self.pet.manage_orbs_requested.connect(self._open_orbkit_manager)
        self.pet.check_updates_requested.connect(self._check_updates_manual)
        self.pet.workspace_change_requested.connect(self._prompt_workspace)
        self.pet.always_on_top_changed.connect(self.drawer.set_always_on_top)
        self.pet.startup_toggled.connect(self._set_launch_at_startup)
        self.pet.theme_selected.connect(self._set_theme_mode)
        self.pet.blur_toggled.connect(self._set_backdrop_blur)
        self.pet.voice_toggled.connect(self._set_voice_enabled)
        self.pet.voice_selected.connect(self._set_voice_name)
        self.pet.stop_speaking_requested.connect(self.voice.stop)
        self.pet.exit_requested.connect(self.quit)

        # Drawer interactions
        self.drawer.prompt_submitted.connect(lambda _t: self.voice.stop())
        self.drawer.prompt_submitted.connect(self.bridge.send_prompt)
        self.drawer.cancel_requested.connect(self.voice.stop)
        self.drawer.cancel_requested.connect(self.bridge.cancel_turn)
        self.drawer.agent_selected.connect(self._switch_agent)
        self.drawer.mode_selected.connect(self.bridge.set_mode)
        self.drawer.config_option_selected.connect(self.bridge.set_config_option)
        self.drawer.workspace_selected.connect(self._switch_workspace)
        self.drawer.new_session_requested.connect(self._new_session)
        self.drawer.reconnect_requested.connect(self._reconnect_agent)
        self.drawer.setup_guide_requested.connect(self._open_setup_guide)
        self.drawer.permission_decided.connect(self._on_permission_resolved)
        self.drawer.opened.connect(self._sync_working_bubble)
        self.drawer.closed.connect(self._sync_working_bubble)
        self.drawer.theme_selected.connect(self._set_theme_mode)
        self.drawer.blur_toggled.connect(self._set_backdrop_blur)
        self.drawer.always_on_top_toggled.connect(self._set_always_on_top)
        self.drawer.startup_toggled.connect(self._set_launch_at_startup)
        self.drawer.voice_toggled.connect(self._set_voice_enabled)
        self.drawer.voice_selected.connect(self._set_voice_name)
        self.drawer.test_voice_requested.connect(self.voice.speak_sample)
        self.drawer.stop_speaking_requested.connect(self.voice.stop)
        self.drawer.check_updates_requested.connect(self._check_updates_manual)
        self.drawer.auto_update_toggled.connect(self._set_check_updates_on_startup)
        self.drawer.update_action_requested.connect(self._on_update_action_requested)
        self.drawer.release_notes_requested.connect(self._on_release_notes_requested)
        self.drawer.manage_orbs_requested.connect(self._open_orbkit_manager)

        # Updater -> Drawer
        self.updater.check_started.connect(self._on_update_check_started)
        self.updater.check_finished.connect(self._on_update_check_finished)
        self.updater.download_progress.connect(self._on_update_download_progress)
        self.updater.update_ready.connect(self._on_update_ready)
        self.updater.update_failed.connect(self._on_update_failed)

        # ACP Bridge -> Pet + Drawer
        self.bridge.connecting.connect(self._on_connecting)
        self.bridge.connected.connect(self._on_connected)
        self.bridge.disconnected.connect(self._on_disconnected)
        self.bridge.connection_failed.connect(self._on_connection_failed)

        self.bridge.turn_started.connect(self._on_turn_started)
        self.bridge.thought_chunk.connect(self._on_thought_chunk)
        self.bridge.message_chunk.connect(self._on_message_chunk)
        self.bridge.tool_started.connect(self._on_tool_started)
        self.bridge.tool_updated.connect(self._on_tool_updated)
        self.bridge.turn_finished.connect(self._on_turn_finished)
        self.bridge.turn_failed.connect(self._on_turn_failed)
        self.bridge.mode_changed.connect(self.drawer.set_current_mode)
        self.bridge.config_options_updated.connect(self.drawer.set_config_options)
        self.bridge.plan_updated.connect(self.drawer.set_plan)
        self.bridge.commands_updated.connect(self.drawer.set_commands)
        self.bridge.usage_updated.connect(self.drawer.set_usage)
        self.bridge.permission_requested.connect(self._on_permission_requested)

        # Voice engine -> Pet orb
        self.voice.speaking_started.connect(self._on_voice_started)
        self.voice.speaking_stopped.connect(self._on_voice_stopped)
        self.voice.pulse_requested.connect(self.pet.pulse_orb_level)
        self.voice.audio_level_updated.connect(self.pet.set_orb_audio_level)

    def _toggle_drawer(self) -> None:
        if self.drawer.isVisible():
            self.drawer.hide_animated()
        else:
            self.drawer.set_always_on_top(self.config.always_on_top)
            self.drawer.dock_near(self.pet.frameGeometry())
            self.drawer.focus_input()

    def _open_drawer_from_bubble(self) -> None:
        if not self.drawer.isVisible():
            self._toggle_drawer()

    def _set_working_phase(self, phase: str) -> None:
        if phase != self._working_phase:
            self._working_phase = phase
            self._working_phrase_idx = 0
        self._sync_working_bubble()

    def _advance_working_phrase(self) -> None:
        phrases = _WORKING_PHRASES.get(self._working_phase, _WORKING_PHRASES["thinking"])
        self._working_phrase_idx = (self._working_phrase_idx + 1) % len(phrases)
        self._sync_working_bubble()

    def _sync_working_bubble(self) -> None:
        should_show = (
            self._turn_active
            and not self._waiting_permission
            and not self.drawer.isVisible()
        )
        if should_show:
            phrases = _WORKING_PHRASES.get(self._working_phase, _WORKING_PHRASES["thinking"])
            phrase = phrases[self._working_phrase_idx % len(phrases)]
            if not self._working_phrase_timer.isActive():
                self._working_phrase_timer.start()
            self.pet.show_working_bubble(phrase)
        else:
            self._working_phrase_timer.stop()
            self.pet.hide_working_bubble()

    def _on_permission_resolved(self, request_id: str, option_id: object) -> None:
        self.bridge.resolve_permission(request_id, option_id)
        self._waiting_permission = False
        self._sync_working_bubble()

    def _connect_current_agent(self) -> None:
        spec = self.registry.get(self.config.agent_id) or self.registry.default_available()
        cwd = self.config.workspace if Path(self.config.workspace).is_dir() else str(Path.home())
        if not spec.is_available():
            self.drawer.add_system_message(
                "No ACP agent CLI found on PATH. Install one (for example `agy`, `gemini`, "
                "`codex-acp`, `opencode`, or `claude-agent-acp`) or add a custom preset in "
                "~/.pocket-agent/config.json."
            )
        self.bridge.connect_agent(spec, cwd)

    def _switch_agent(self, agent_id: str) -> None:
        spec = self.registry.get(agent_id)
        if spec is None:
            return
        self.config.agent_id = spec.id
        self.config.save()
        self.pet.current_agent_id = spec.id
        self.drawer.select_agent_in_combo(spec.id)
        self.drawer.set_config_options([])
        self.drawer.clear_chat()
        self._connect_current_agent()

    def _switch_workspace(self, folder: str) -> None:
        self.config.workspace = folder
        self.config.save()
        self.drawer.add_system_message(f"Workspace switched to {folder}")
        self._new_session()

    def _prompt_workspace(self) -> None:
        folder = QFileDialog.getExistingDirectory(self.drawer, "Choose Workspace Folder", self.config.workspace)
        if folder:
            self.drawer.set_workspace(folder)
            self._switch_workspace(folder)

    def _new_session(self) -> None:
        self.drawer.clear_chat()
        cwd = self.config.workspace if Path(self.config.workspace).is_dir() else str(Path.home())
        self.bridge.new_session(cwd)

    def _reconnect_agent(self) -> None:
        self.drawer._error_banner.setVisible(False)
        self._connect_current_agent()

    def _open_setup_guide(self) -> None:
        dlg = AgentSetupDialog(
            agents=self.registry.all(),
            current_agent_id=self.config.agent_id,
            log_file=_LOG_FILE,
            parent=self.drawer if self.drawer.isVisible() else self.pet,
        )
        dlg.connect_agent_requested.connect(self._switch_agent)
        dlg.exec()

    def _open_settings(self) -> None:
        if not self.drawer.isVisible():
            self._toggle_drawer()
        self.drawer.open_settings()

    def _open_orbkit_manager(self) -> None:
        dlg = OrbkitManagerDialog(
            custom_orbs_dir=CUSTOM_ORBS_DIR,
            current_pet_id=self.config.pet_id,
            parent=self.drawer if self.drawer.isVisible() else self.pet,
        )
        dlg.orbs_changed.connect(self.pet.reload_catalog)
        dlg.orb_selected.connect(self._select_pet_orb)
        dlg.exec()

    def _select_pet_orb(self, orb_id: str) -> None:
        self.pet.reload_catalog()
        self.pet._load_pet(orb_id)

    def _set_theme_mode(self, mode: str) -> None:
        self.config.theme = mode
        self.config.save()
        self.drawer.set_theme(mode, self.config.backdrop_blur)
        self.pet.bubble.apply_theme()
        self.pet.apply_theme()

    def _set_backdrop_blur(self, enabled: bool) -> None:
        self.config.backdrop_blur = enabled
        self.config.save()
        self.drawer.set_theme(self.config.theme, enabled)
        self.pet.bubble.apply_theme()
        self.pet.apply_theme()

    def _set_voice_enabled(self, enabled: bool) -> None:
        self.config.voice_enabled = enabled
        self.config.save()
        self.voice.set_enabled(enabled)
        self.drawer.set_voice_enabled(enabled)

    def _set_voice_name(self, voice_name: str) -> None:
        self.config.voice_name = voice_name
        self.config.save()
        self.voice.set_voice(voice_name)
        self.drawer.set_voice_name(voice_name)

    def _set_always_on_top(self, enabled: bool) -> None:
        self.pet._toggle_always_on_top(enabled)

    def _set_launch_at_startup(self, enabled: bool) -> None:
        set_launch_at_startup(enabled)
        actual = get_launch_at_startup() if os.name == "nt" else enabled
        self.pet.set_launch_at_startup(actual)
        self.drawer.set_launch_at_startup(actual)

    def _set_check_updates_on_startup(self, enabled: bool) -> None:
        self.config.check_updates_on_startup = enabled
        self.config.save()

    def _check_updates_manual(self) -> None:
        self.updater.check_for_updates_async(silent=False)

    def _on_update_check_started(self) -> None:
        self.drawer.set_update_status("Checking for updates...", busy=True)

    def _on_update_check_finished(self, result: UpdateCheckResult) -> None:
        if result.error:
            self.drawer.set_update_status(f"Check failed: {result.error}", busy=False)
            if not result.silent:
                self.drawer.show_error_banner("Update Check Failed", result.error)
            return

        if result.update_available and result.release is not None:
            self._latest_release = result.release
            frozen = is_frozen()
            self.drawer.set_update_status(
                f"Update available: v{result.release.version}",
                busy=False,
                update_available=True,
                is_frozen=frozen,
            )
            self.drawer.show_update_banner(result.release.version, frozen=frozen)
            return

        self._latest_release = None
        self.drawer.set_update_status("Up to date", busy=False, update_available=False)
        if not result.silent and result.latest_version:
            self.drawer.show_info_banner(
                "Up to Date",
                f"Pocket Agent v{result.current_version} is the latest version.",
            )

    def _on_update_action_requested(self) -> None:
        if self._latest_release is None:
            return
        if is_frozen():
            self.drawer.set_update_status(
                "Downloading update...",
                busy=True,
                update_available=True,
                is_frozen=True,
            )
            self.updater.download_update_async(self._latest_release)
        else:
            QDesktopServices.openUrl(QUrl(self._latest_release.html_url or RELEASES_PAGE_URL))

    def _on_release_notes_requested(self) -> None:
        url = self._latest_release.html_url if self._latest_release else RELEASES_PAGE_URL
        QDesktopServices.openUrl(QUrl(url))

    def _on_update_download_progress(self, downloaded: int, total: int) -> None:
        if total > 0:
            pct = min(100, int(downloaded * 100 / total))
            msg = f"Downloading update... {pct}%"
        else:
            mb = downloaded / (1024 * 1024)
            msg = f"Downloading update... {mb:.1f} MB"
        self.drawer.set_update_status(msg, busy=True, update_available=True, is_frozen=True)

    def _on_update_ready(self, extract_dir: Path) -> None:
        self.drawer.set_update_status(
            "Applying update and restarting...",
            busy=True,
            update_available=True,
            is_frozen=True,
        )
        try:
            spawn_apply_update_script(extract_dir)
        except Exception as exc:
            self._on_update_failed(str(exc))
            return
        self.quit()

    def _on_update_failed(self, error: str) -> None:
        frozen = is_frozen()
        self.drawer.set_update_status(
            f"Update failed: {error}",
            busy=False,
            update_available=self._latest_release is not None,
            is_frozen=frozen,
        )
        self.drawer.show_error_banner("Update Failed", error)

    def show_drawer(self) -> None:
        """Ensure the chat drawer is visible and focused."""
        if not self.drawer.isVisible():
            self._toggle_drawer()
        else:
            self.drawer.raise_()
            self.drawer.activateWindow()
            self.drawer.focus_input()

    def _on_voice_started(self) -> None:
        self.pet.set_voice_speaking(True)
        if not self._turn_active:
            self.pet.set_state(PetState.IDLE, orb_state="speaking")

    def _on_voice_stopped(self) -> None:
        self.pet.set_voice_speaking(False)
        if not self._turn_active:
            self.pet.set_state(PetState.IDLE, orb_state="idle")

    def _on_connecting(self, agent_name: str) -> None:
        self.drawer.set_status(f"Connecting to {agent_name}...", "#f59e0b")
        self.pet.set_state(PetState.THINKING, orb_state="connecting")

    def _on_connected(
        self,
        agent_id: str,
        agent_name: str,
        agent_version: str,
        session_id: str,
        modes: list[ModeInfo],
        current_mode: str,
    ) -> None:
        ver = f" {agent_version}" if agent_version else ""
        self.drawer._error_banner.setVisible(False)
        self.drawer.set_status(f"Connected · {agent_name}{ver}", "#34d399")
        self.drawer.set_modes(modes, current_mode)
        self.drawer.add_system_message(f"Connected to {agent_name}{ver}")
        self.pet.set_state(PetState.IDLE, orb_state="idle")

    def _on_disconnected(self, reason: str) -> None:
        self._turn_active = False
        self._waiting_permission = False
        self._sync_working_bubble()
        self.drawer.set_busy(False)
        self.drawer.set_status(f"Disconnected: {reason}", "#f87171")
        if reason and reason != "Session closed":
            self.drawer.show_connection_error("Agent Disconnected", reason)
        self.pet.set_state(PetState.IDLE, orb_state="disabled")

    def _on_connection_failed(self, error: str) -> None:
        self.drawer.set_busy(False)
        self.drawer.set_status("Connection failed", "#f87171")
        self.drawer.show_connection_error("Connection Failed", error)
        self.pet.set_state(PetState.ERROR, "Couldn't reach agent!", 3200, orb_state="error")

    def _on_turn_started(self) -> None:
        self._turn_active = True
        self._waiting_permission = False
        self.voice.stop()
        self.drawer.set_busy(True)
        self.drawer.set_status("Thinking...", "#fbbf24")
        self.pet.set_state(PetState.THINKING, orb_state="thinking")
        self._set_working_phase("thinking")

    def _on_thought_chunk(self, text: str) -> None:
        self.drawer.append_thought_chunk(text)
        self.pet.set_state(PetState.THINKING, orb_state="thinking")
        self.pet.pulse_orb_level(0.45)
        if self._waiting_permission:
            self._waiting_permission = False
        self._set_working_phase("thinking")

    def _on_message_chunk(self, text: str) -> None:
        self.drawer.append_message_chunk(text)
        self.voice.feed_chunk(text)
        self.pet.set_state(PetState.CODING, orb_state="speaking")
        if not self.voice.is_speaking:
            self.pet.pulse_orb_level(0.85)
        if self._waiting_permission:
            self._waiting_permission = False
        self._set_working_phase("responding")

    def _on_tool_started(self, tool_call_id: str, title: str, kind: str, status: str, details: object) -> None:
        self.drawer.start_tool_call(
            tool_call_id,
            title,
            kind,
            status,
            details=details if isinstance(details, dict) else None,
        )
        self.drawer.set_status(f"Running: {title}", "#7f6df2")
        self.pet.set_state(PetState.CODING, orb_state="speaking")
        self.pet.pulse_orb_level(0.7)
        if self._waiting_permission:
            self._waiting_permission = False
        self._set_working_phase(_tool_phase_for(kind, title))

    def _on_tool_updated(self, tool_call_id: str, title: str, kind: str, status: str, details: object) -> None:
        self.drawer.update_tool_call(
            tool_call_id,
            title,
            kind,
            status,
            details=details if isinstance(details, dict) else None,
        )

    def _on_turn_finished(self, stop_reason: str) -> None:
        self._turn_active = False
        self._waiting_permission = False
        self._sync_working_bubble()
        self.voice.flush()
        self.drawer.set_busy(False)
        self.drawer.clear_permission()
        self.drawer.set_status("Ready", "#4ade80")
        bubble = "Done!" if not self.drawer.isVisible() else None
        self.pet.set_state(PetState.CELEBRATE, bubble, 2000, orb_state="speaking")

    def _on_turn_failed(self, error: str) -> None:
        self._turn_active = False
        self._waiting_permission = False
        self._sync_working_bubble()
        self.voice.stop()
        self.drawer.set_busy(False)
        self.drawer.clear_permission()
        self.drawer.set_status("Error", "#f85149")
        self.drawer.add_error_message(error)
        self.pet.set_state(PetState.ERROR, "Hit a snag!", 2600, orb_state="error")

    def _on_permission_requested(self, request_id: str, title: str, kind: str, options, details: object = None) -> None:
        self._waiting_permission = True
        self._sync_working_bubble()
        self.drawer.set_status("Waiting for permission...", "#e2b93d")
        self.drawer.show_permission_request(
            request_id,
            title,
            kind,
            options,
            details=details if isinstance(details, dict) else None,
        )
        self.pet.set_state(PetState.PERMISSION, "Need your OK!", 3500, orb_state="listening")

    def quit(self) -> None:
        self._hotkey_filter.unregister()
        self.voice.shutdown()
        self.pet.close_tray()
        self.bridge.shutdown()
        self.qt_app.quit()


def _configure_logging() -> None:
    """Write diagnostics to ~/.pocket-agent/pocket-agent.log for console-free runs."""
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(_LOG_FILE, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
        root = logging.getLogger()
        if not any(isinstance(h, RotatingFileHandler) for h in root.handlers):
            root.addHandler(handler)
            root.setLevel(logging.INFO)
    except Exception:
        pass

    prev_hook = sys.excepthook

    def _excepthook(exc_type, exc_value, exc_tb) -> None:
        logger.exception("Unhandled exception", exc_info=(exc_type, exc_value, exc_tb))
        if sys.stderr is not None and prev_hook is not None:
            prev_hook(exc_type, exc_value, exc_tb)

    sys.excepthook = _excepthook


def try_notify_existing_instance(server_name: str = SINGLE_INSTANCE_SERVER_NAME, timeout_ms: int = 350) -> bool:
    """Ping a running Pocket Agent instance to reveal its drawer; return True if handled."""
    sock = QLocalSocket()
    sock.connectToServer(server_name)
    if not sock.waitForConnected(timeout_ms):
        return False
    sock.write(b"show\n")
    sock.flush()
    sock.waitForBytesWritten(timeout_ms)
    sock.disconnectFromServer()
    return True


def create_instance_server(
    on_activate,
    server_name: str = SINGLE_INSTANCE_SERVER_NAME,
    parent: QObject | None = None,
) -> QLocalServer:
    """Listen on a local IPC socket so duplicate launches wake the running instance."""
    server = QLocalServer(parent)
    if not server.listen(server_name):
        QLocalServer.removeServer(server_name)
        server.listen(server_name)

    def _handle_connection() -> None:
        while server.hasPendingConnections():
            conn = server.nextPendingConnection()
            if conn is not None:
                conn.readyRead.connect(conn.readAll)
                conn.disconnected.connect(conn.deleteLater)
                conn.close()
        on_activate()

    server.newConnection.connect(_handle_connection)
    return server


def main() -> int:
    """Launch Pocket Agent desktop application."""
    _configure_logging()
    qt_app = QApplication(sys.argv)
    qt_app.setQuitOnLastWindowClosed(False)
    qt_app.setApplicationName("Pocket Agent")

    ico_path = BUILTIN_ASSETS_DIR / "pocket_agent.ico"
    if ico_path.is_file():
        qt_app.setWindowIcon(QIcon(str(ico_path)))

    if try_notify_existing_instance():
        return 0

    app = PocketAgentApp(qt_app)
    server = create_instance_server(app.show_drawer, parent=app)
    qt_app.aboutToQuit.connect(server.close)
    qt_app.aboutToQuit.connect(app.bridge.shutdown)
    return qt_app.exec()
