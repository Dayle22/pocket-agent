"""Head-anchored floating speech bubble for compact companion status messages."""

from __future__ import annotations

from PyQt6.QtCore import QPoint, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QMouseEvent, QPainter, QPainterPath
from PyQt6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget


from pocket_agent.ui.animations import ShimmerWaveLabel, WindowAnimator, _lerp_color
from pocket_agent.ui.theme import get_palette


class SpeechBubble(QWidget):
    """Frameless translucent speech bubble positioned above the pet."""

    clicked = pyqtSignal()

    def __init__(self) -> None:
        super().__init__(None)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.ToolTip
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(14, 10, 14, 16)

        self._label = QLabel(self)
        self._label.setWordWrap(True)
        self._label.setMaximumWidth(240)
        self._label.setFont(QFont("Segoe UI", 9))
        self._layout.addWidget(self._label)

        self._status_label = ShimmerWaveLabel("Thinking...", self)
        self._status_label.setVisible(False)
        self._layout.addWidget(self._status_label)

        self._status_mode = False
        self._animator = WindowAnimator(self)
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(lambda: self._animator.hide_animated(duration_ms=140))
        self._anchor_center_x = 0
        self._anchor_top_y = 0
        self.apply_theme()

    def is_status_active(self) -> bool:
        return self._status_mode and self.isVisible()

    def status_text(self) -> str:
        return self._status_label.text() if self._status_mode else ""

    def apply_theme(self) -> None:
        pal = get_palette()
        self._label.setStyleSheet(f"color: {pal.text_normal}; background: transparent;")
        self._status_label.apply_theme()
        self.update()

    def _set_status_mode(self, enabled: bool) -> None:
        self._status_mode = enabled
        self._label.setVisible(not enabled)
        self._status_label.setVisible(enabled)
        if not enabled:
            self._status_label.set_active(False)
            self._layout.setContentsMargins(14, 10, 14, 16)
            self.unsetCursor()
        else:
            self._layout.setContentsMargins(12, 7, 12, 15)
            self.setCursor(Qt.CursorShape.PointingHandCursor)

    def show_text(self, text: str, anchor_center_x: int, anchor_top_y: int, duration_ms: int = 2800) -> None:
        if not text.strip():
            return
        self._set_status_mode(False)
        self._label.setText(text.strip())
        self._label.adjustSize()
        self.adjustSize()
        self.reposition(anchor_center_x, anchor_top_y)
        self._animator.show_animated(slide_px=6, duration_ms=140)
        if duration_ms > 0:
            self._hide_timer.start(duration_ms)
        else:
            self._hide_timer.stop()

    def show_status(self, text: str, anchor_center_x: int, anchor_top_y: int) -> None:
        """Show or update a compact animated working-status bubble without re-sliding."""
        clean = " ".join(text.split())
        if not clean:
            return
        already_showing = self._status_mode and self.isVisible()
        self._hide_timer.stop()
        if already_showing and clean == self._status_label.text():
            self._status_label.set_active(True)
            return
        self._set_status_mode(True)
        self._status_label.set_text(clean)
        self._status_label.set_active(True)
        self._status_label.adjustSize()
        self.adjustSize()
        self.reposition(anchor_center_x, anchor_top_y)
        if not already_showing:
            self._animator.show_animated(slide_px=6, duration_ms=140)
        else:
            self.update()

    def hide_status(self) -> None:
        """Hide the working-status bubble if it is currently active."""
        if not self._status_mode:
            return
        self._set_status_mode(False)
        self._hide_timer.stop()
        if self.isVisible():
            self._animator.hide_animated(duration_ms=140)

    def hideEvent(self, event) -> None:  # type: ignore[override]
        self._status_label.set_active(False)
        super().hideEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            event.accept()
            return
        super().mousePressEvent(event)

    def reposition(self, anchor_center_x: int, anchor_top_y: int) -> None:
        self._anchor_center_x = anchor_center_x
        self._anchor_top_y = anchor_top_y
        screen = QApplication.screenAt(QPoint(anchor_center_x, anchor_top_y)) or QApplication.primaryScreen()
        geo = screen.availableGeometry() if screen else None

        x = anchor_center_x - self.width() // 2
        y = anchor_top_y - self.height() - 4
        if geo is not None:
            x = max(geo.left() + 8, min(x, geo.right() - self.width() - 8))
            if y < geo.top() + 8:
                y = geo.top() + 8
        self.move(x, y)

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = self.rect().adjusted(1, 1, -1, -9)
        path = QPainterPath()
        path.addRoundedRect(rect.x(), rect.y(), rect.width(), rect.height(), 12, 12)

        # Pointer triangle
        mid_x = min(max(self.width() // 2, 18), self.width() - 18)
        base_y = rect.bottom()
        path.moveTo(mid_x - 6, base_y)
        path.lineTo(mid_x, base_y + 7)
        path.lineTo(mid_x + 6, base_y)

        pal = get_palette()
        border_color = QColor(pal.border)
        if self._status_mode:
            border_color = _lerp_color(border_color, QColor(pal.accent), 0.45)
        painter.setPen(border_color)
        painter.setBrush(QColor(pal.bg_secondary))
        painter.drawPath(path)
