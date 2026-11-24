"""Transparent floating companion window with sprite animations and context menu."""

from __future__ import annotations

import ctypes
import sys
from pathlib import Path

from PyQt6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QContextMenuEvent,
    QIcon,
    QImage,
    QMouseEvent,
    QPainter,
    QRadialGradient,
)
from PyQt6.QtWidgets import (
    QApplication,
    QSystemTrayIcon,
    QWidget,
)

from pocket_agent.agents import AgentSpec
from pocket_agent.config import AppConfig
from pocket_agent.engine.catalog import BUILTIN_ASSETS_DIR, BUILTIN_POCKETBOT_ID, PetCatalog, ensure_pocketbot_sheet
from pocket_agent.engine.fsm import STATE_TO_ACTION, PetAction, PetState
from pocket_agent.engine.orbs import BG_OVERSCAN, PET_STATE_TO_ORB_STATE, OrbPlayer, approach
from pocket_agent.engine.spritesheet import CELL_HEIGHT, CELL_WIDTH, SpritePlayer, SpriteSheet
from pocket_agent.ui.bubble import SpeechBubble
from pocket_agent.ui.menus import PocketMenu
from pocket_agent.ui.theme import get_palette


class PetWindow(QWidget):
    """Frameless transparent animated sprite window floating over Windows."""

    toggle_drawer_requested = pyqtSignal()
    new_session_requested = pyqtSignal()
    reconnect_requested = pyqtSignal()
    setup_guide_requested = pyqtSignal()
    settings_requested = pyqtSignal()
    companion_requested = pyqtSignal()
    show_chat_requested = pyqtSignal()
    check_updates_requested = pyqtSignal()
    always_on_top_changed = pyqtSignal(bool)
    startup_toggled = pyqtSignal(bool)
    voice_toggled = pyqtSignal(bool)
    exit_requested = pyqtSignal()
    moved = pyqtSignal(QRect)

    def __init__(
        self,
        config: AppConfig,
        catalog: PetCatalog,
        agents: list[AgentSpec],
        launch_at_startup: bool = False,
        active_workspace: str = "",
    ) -> None:
        super().__init__(None)
        self.config = config
        self.catalog = catalog
        self._launch_at_startup = launch_at_startup
        self._sheets: dict[Path, SpriteSheet] = {}
        self._player: SpritePlayer | OrbPlayer | None = None
        self._state = PetState.IDLE
        self._orb_state = "idle"
        self._voice_speaking = False
        self._is_hovered = False
        self._hover_progress: float = 0.0
        self._dragging = False
        self._drag_moved = False
        self._press_global = QPoint()
        self._drag_offset = QPoint()
        self._last_drag_x = 0
        self._capture_excluded = False
        self._bg_capture_elapsed_ms = 0
        self._last_capture_pos: QPoint | None = None

        self.bubble = SpeechBubble()
        self._init_window_flags()
        self._load_pet(config.pet_id)
        self._apply_scale(config.pet_scale)
        self._restore_position()

        self.setMouseTracking(True)

        self._frame_timer = QTimer(self)
        self._frame_timer.timeout.connect(self._on_tick)
        if not config.reduced_motion:
            self._frame_timer.start(33)
        self.bubble.set_reduced_motion(config.reduced_motion)

        self._celebrate_timer = QTimer(self)
        self._celebrate_timer.setSingleShot(True)
        self._celebrate_timer.timeout.connect(self._return_to_idle)

        self._init_tray()

    def _init_window_flags(self) -> None:
        self._capture_excluded = False
        flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
        if self.config.always_on_top:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)

    def _ensure_capture_excluded(self) -> bool:
        """Exclude this window from screen captures so desktop refraction never feedback-loops."""
        if sys.platform != "win32":
            self._capture_excluded = False
            return False
        try:
            hwnd = int(self.winId())
            # WDA_EXCLUDEFROMCAPTURE (0x11) hides the window from BitBlt / grabWindow on Windows 10 2004+
            ok = ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, 0x00000011)
            self._capture_excluded = bool(ok)
        except Exception:
            self._capture_excluded = False
        return self._capture_excluded

    def _capture_orb_background(self) -> None:
        """Capture the desktop region behind the orb with overscan for Liquid Glass refraction."""
        if not isinstance(self._player, OrbPlayer) or not self._capture_excluded or not self.isVisible():
            return

        cx, cy, draw_half = self._player.compute_draw_geometry(self.rect())
        g_center = self.mapToGlobal(QPoint(int(round(cx)), int(round(cy))))
        screen = QApplication.screenAt(g_center) or QApplication.primaryScreen()
        if screen is None:
            return

        cap_half = max(32, int(round(draw_half * BG_OVERSCAN)))
        cap_size = cap_half * 2
        cap_rect = QRect(g_center.x() - cap_half, g_center.y() - cap_half, cap_size, cap_size)
        sgeo = screen.geometry()
        clamped = cap_rect.intersected(sgeo)
        if clamped.isEmpty():
            self._player.set_background_image(None)
            return

        pm = screen.grabWindow(
            0,
            clamped.x() - sgeo.x(),
            clamped.y() - sgeo.y(),
            clamped.width(),
            clamped.height(),
        )
        if pm.isNull():
            return

        if clamped == cap_rect:
            img = pm.toImage()
        else:
            dpr = max(1.0, float(pm.devicePixelRatio()))
            px_size = max(32, int(round(cap_size * dpr)))
            canvas = QImage(px_size, px_size, QImage.Format.Format_RGBA8888)
            canvas.fill(QColor(18, 22, 32, 255))
            canvas.setDevicePixelRatio(dpr)
            p = QPainter(canvas)
            p.drawPixmap(clamped.topLeft() - cap_rect.topLeft(), pm)
            p.end()
            canvas.setDevicePixelRatio(1.0)
            img = canvas

        self._player.set_background_image(img)

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        if self._ensure_capture_excluded():
            self._bg_capture_elapsed_ms = 0
            self._last_capture_pos = self.pos()
            self._capture_orb_background()

    def _apply_scale(self, scale: float) -> None:
        scale = max(0.25, min(1.5, scale))
        self.config.pet_scale = scale
        self.resize(int(round(CELL_WIDTH * scale)), int(round(CELL_HEIGHT * scale)))
        self.update()
        self.moved.emit(self.frameGeometry())

    def _restore_position(self) -> None:
        screen = QApplication.primaryScreen()
        geo = screen.availableGeometry() if screen else QRect(0, 0, 1920, 1080)
        if self.config.pet_x is not None and self.config.pet_y is not None:
            x = max(geo.left(), min(self.config.pet_x, geo.right() - self.width()))
            y = max(geo.top(), min(self.config.pet_y, geo.bottom() - self.height()))
            self.move(x, y)
        else:
            self.move(geo.right() - self.width() - 36, geo.bottom() - self.height() - 28)

    def _load_pet(self, pet_id: str) -> None:
        info = self.catalog.get(pet_id)
        if info.orb_id is not None:
            self._player = OrbPlayer(
                info.orb_id,
                static_idle=self.config.static_idle,
                custom_orb=info.custom_orb,
                drop_shadow=self.config.drop_shadow,
            )
            self._player.set_orb_state(self._orb_state)
            self._bg_capture_elapsed_ms = 0
            self._capture_orb_background()
        else:
            path = info.spritesheet_path
            if info.id == BUILTIN_POCKETBOT_ID and not self.config.drop_shadow:
                path = ensure_pocketbot_sheet(drop_shadow=False)
            sheet = self._sheets.get(path)
            if sheet is None:
                sheet = SpriteSheet(path)
                self._sheets[path] = sheet
            self._player = SpritePlayer(sheet, static_idle=self.config.static_idle)
            self._player.set_action(STATE_TO_ACTION[self._state])

        if self.config.pet_id != info.id:
            self.config.pet_id = info.id
            self.config.save()

        self.update()

    def update_drop_shadow(self) -> None:
        if isinstance(self._player, OrbPlayer) or self.config.pet_id == BUILTIN_POCKETBOT_ID:
            self._load_pet(self.config.pet_id)

    def _init_tray(self) -> None:
        self._tray = QSystemTrayIcon(self)
        self._tray.setIcon(QIcon(str(BUILTIN_ASSETS_DIR / "pocket_agent.ico")))
        self._tray.setToolTip("Pocket Agent (Ctrl+Shift+Space to toggle)")
        self._tray.activated.connect(self._on_tray_activated)
        self._build_tray_menu()
        self._tray.show()

    def _build_tray_menu(self) -> None:
        tray_menu = PocketMenu(parent=self)
        tray_menu.add_item("Show / Hide Chat", self.toggle_drawer_requested.emit, icon="message-square")
        tray_menu.add_item("New Chat", self.new_session_requested.emit, icon="plus")
        tray_menu.add_item("Reconnect Agent", self.reconnect_requested.emit, icon="refresh-cw")
        tray_menu.add_item("Connect an AI (Setup Guide)...", self.setup_guide_requested.emit, icon="book-open")
        tray_menu.add_item("Check for Updates...", self.check_updates_requested.emit, icon="refresh-cw")
        tray_menu.add_item("Settings...", self.settings_requested.emit, icon="settings")
        tray_menu.add_check("Launch at Windows Startup", self._launch_at_startup, self.startup_toggled.emit)
        tray_menu.addSeparator()
        tray_menu.add_item("Quit Pocket Agent", self.exit_requested.emit, icon="power")
        old = getattr(self, "_tray_menu", None)
        self._tray.setContextMenu(tray_menu)
        self._tray_menu = tray_menu
        if old is not None:
            old.deleteLater()

    def set_launch_at_startup(self, enabled: bool) -> None:
        """Update the tray menu's Windows Startup checkmark."""
        self._launch_at_startup = enabled
        if hasattr(self, "_tray"):
            self._build_tray_menu()

    def set_idle_animation(self, enabled: bool) -> None:
        self.config.static_idle = not enabled
        self.config.save()
        if self._player is not None:
            self._player.static_idle = not enabled

    def apply_theme(self) -> None:
        """Re-tint theme-dependent chrome (tray menu) after a Dark/Light switch."""
        self._build_tray_menu()
        self.update()

    def set_state(
        self,
        state: PetState,
        bubble_text: str | None = None,
        bubble_ms: int = 2400,
        orb_state: str | None = None,
    ) -> None:
        self._state = state
        self._orb_state = orb_state or PET_STATE_TO_ORB_STATE[state]
        if state == PetState.CELEBRATE:
            self._celebrate_timer.start(2200)
        else:
            self._celebrate_timer.stop()

        if self._player is not None and not self._dragging:
            if isinstance(self._player, OrbPlayer):
                self._player.set_orb_state(self._orb_state)
            else:
                self._player.set_action(STATE_TO_ACTION[state])
            self.update()

        if bubble_text:
            self.say(bubble_text, bubble_ms)

    def pulse_orb_level(self, boost: float = 0.65) -> None:
        """Pulse live amplitude on the active VoiceOrb renderer."""
        if isinstance(self._player, OrbPlayer):
            self._player.pulse_level(boost)

    def set_orb_audio_level(self, level: float | None) -> None:
        """Set or clear live TTS RMS amplitude (0..1) on the active VoiceOrb renderer."""
        if isinstance(self._player, OrbPlayer):
            self._player.set_audio_level(level)

    def set_voice_speaking(self, speaking: bool) -> None:
        """Track active TTS playback so celebrate timeout does not drop out of speaking."""
        self._voice_speaking = speaking
        if not speaking:
            self.set_orb_audio_level(None)

    def say(self, text: str, duration_ms: int = 2600) -> None:
        geo = self.frameGeometry()
        self.bubble.show_text(text, geo.center().x(), geo.top(), duration_ms=duration_ms)

    def show_working_bubble(self, text: str) -> None:
        """Show or update the animated working-status bubble anchored above the pet."""
        geo = self.frameGeometry()
        self.bubble.show_status(text, geo.center().x(), geo.top())

    def hide_working_bubble(self) -> None:
        """Hide the animated working-status bubble if active."""
        self.bubble.hide_status()

    def _return_to_idle(self) -> None:
        if self._state == PetState.CELEBRATE:
            orb_state = "speaking" if self._voice_speaking else "idle"
            self.set_state(PetState.IDLE, orb_state=orb_state)

    def set_reduced_motion(self, enabled: bool) -> None:
        self.bubble.set_reduced_motion(enabled)
        if enabled:
            self._frame_timer.stop()
        else:
            self._frame_timer.start(33)
        self.update()

    def _on_tick(self) -> None:
        if self._player is None:
            return
        prev_hover = self._hover_progress
        target_hover = 1.0 if self._is_hovered else 0.0
        rate = 12.0 if self._is_hovered else 6.0
        self._hover_progress = approach(self._hover_progress, target_hover, rate, 0.033)
        if not self._is_hovered and self._hover_progress < 0.001:
            self._hover_progress = 0.0

        dirty = self._player.update(33, is_hovered=self._is_hovered)

        if isinstance(self._player, OrbPlayer) and self._capture_excluded and self.isVisible():
            cur_pos = self.pos()
            moved = self._last_capture_pos != cur_pos
            self._bg_capture_elapsed_ms += 33
            interval_ms = 0 if (self._dragging or moved) else (100 if dirty else 500)
            if self._bg_capture_elapsed_ms >= interval_ms:
                self._bg_capture_elapsed_ms = 0
                self._last_capture_pos = cur_pos
                self._capture_orb_background()
                if moved:
                    dirty = True

        if dirty or abs(self._hover_progress - prev_hover) > 1e-4:
            self.update()

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self.toggle_drawer_requested.emit()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        if self._player is None:
            return
        if isinstance(self._player, OrbPlayer):
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            self._player.paint(painter, self.rect())
            return
        pix = self._player.current_pixmap()
        if pix is None or pix.isNull():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        rect_f = QRectF(self.rect())
        if self._hover_progress > 0.01:
            pal = get_palette()
            glow_col = QColor(pal.accent)
            glow_col.setAlpha(int(55 * self._hover_progress))
            center = rect_f.center()
            glow_r = min(rect_f.width(), rect_f.height()) * 0.48
            glow = QRadialGradient(center, glow_r)
            glow.setColorAt(0.0, glow_col)
            glow.setColorAt(0.6, QColor(glow_col.red(), glow_col.green(), glow_col.blue(), int(22 * self._hover_progress)))
            glow.setColorAt(1.0, QColor(0, 0, 0, 0))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(glow))
            painter.drawEllipse(center, glow_r, glow_r)

            scale = 1.0 + 0.045 * self._hover_progress
            w = rect_f.width() * scale
            h = rect_f.height() * scale
            rect_f = QRectF(center.x() - w * 0.5, center.y() - h * 0.5, w, h)

        painter.drawPixmap(rect_f, pix, QRectF(pix.rect()))

    def _update_hover_cursor(self, pos: QPointF) -> None:
        if not isinstance(self._player, OrbPlayer):
            return
        cy = self.height() * 0.455
        nx = (pos.x() - self.width() / 2.0) / max(1.0, self.width() / 2.0)
        ny = (pos.y() - cy) / max(1.0, cy)
        self._player.set_hover_cursor(nx, ny)

    def enterEvent(self, event) -> None:  # type: ignore[override]
        self._is_hovered = True
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        if hasattr(event, "position"):
            self._update_hover_cursor(event.position())

    def leaveEvent(self, event) -> None:  # type: ignore[override]
        self._is_hovered = False
        if isinstance(self._player, OrbPlayer):
            self._player.set_hover_cursor(0.0, 0.0)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._drag_moved = False
            self._press_global = event.globalPosition().toPoint()
            self._drag_offset = self._press_global - self.frameGeometry().topLeft()
            self._last_drag_x = self._press_global.x()
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        self._update_hover_cursor(event.position())
        if self._dragging and (event.buttons() & Qt.MouseButton.LeftButton):
            gpos = event.globalPosition().toPoint()
            if (gpos - self._press_global).manhattanLength() > 5:
                self._drag_moved = True
                if self._player is not None:
                    dx = gpos.x() - self._last_drag_x
                    if dx > 2:
                        self._player.set_action(PetAction.RUNNING_RIGHT, reset_index=False)
                    elif dx < -2:
                        self._player.set_action(PetAction.RUNNING_LEFT, reset_index=False)
                self._last_drag_x = gpos.x()
                self.move(gpos - self._drag_offset)
                geo = self.frameGeometry()
                if self.bubble.isVisible():
                    self.bubble.reposition(geo.center().x(), geo.top())
                self.moved.emit(geo)
            event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton and self._dragging:
            self._dragging = False
            if self._player is not None:
                if isinstance(self._player, OrbPlayer):
                    self._player.set_orb_state(self._orb_state)
                else:
                    self._player.set_action(STATE_TO_ACTION[self._state])
            if self._drag_moved:
                pos = self.pos()
                self.config.pet_x = pos.x()
                self.config.pet_y = pos.y()
                self.config.save()
            else:
                self.toggle_drawer_requested.emit()
            event.accept()

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:  # type: ignore[override]
        menu = PocketMenu(parent=self)

        menu.add_item("Open chat", self.show_chat_requested.emit, icon="message-square", shortcut="Ctrl+Shift+Space")
        menu.add_check("Mute automatic replies", not self.config.voice_enabled,
                       lambda muted: self.voice_toggled.emit(not muted))
        menu.add_item("Change companion…", self.companion_requested.emit, icon="smile")
        menu.add_item("Settings…", self.settings_requested.emit, icon="settings")
        menu.addSeparator()
        menu.add_item("Quit Pocket Agent", self.exit_requested.emit, icon="power")
        menu.exec(event.globalPos())

    def _set_scale_and_save(self, scale: float) -> None:
        self._apply_scale(scale)
        self.config.save()

    def _toggle_always_on_top(self, checked: bool) -> None:
        self.config.always_on_top = checked
        self.config.save()
        self._init_window_flags()
        self.show()
        self.raise_()
        self.always_on_top_changed.emit(checked)

    def reload_catalog(self) -> None:
        """Refresh the pet/orb catalog and fall back safely if the active custom orb was deleted."""
        self.catalog.refresh()
        available_ids = {p.id for p in self.catalog.all()}
        if self.config.pet_id not in available_ids:
            self._load_pet("siri-sheet")

    def close_tray(self) -> None:
        self._tray.hide()
