"""Animations ported from obsidian-agent-client: 3x3 wave loader, spin icon, and window fade/slide."""

from __future__ import annotations

import math

from PyQt6.QtCore import (
    QEasingCurve,
    QParallelAnimationGroup,
    QPoint,
    QPointF,
    QPropertyAnimation,
    QRectF,
    QSize,
    Qt,
    QTimer,
)
from PyQt6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPixmap
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QWidget

from pocket_agent.ui.theme import get_palette, lucide_pixmap


def _lerp_color(a: QColor, b: QColor, t: float) -> QColor:
    t = max(0.0, min(1.0, t))
    return QColor(
        int(round(a.red() + (b.red() - a.red()) * t)),
        int(round(a.green() + (b.green() - a.green()) * t)),
        int(round(a.blue() + (b.blue() - a.blue()) * t)),
        int(round(a.alpha() + (b.alpha() - a.alpha()) * t)),
    )


class DotMatrixGrid(QWidget):
    """3x3 dot-matrix wave animation matching .agent-client-loading-dots."""

    # Exact staggered delays (in seconds) from styles.css lines 278-312
    _DELAYS = (0.0, 0.15, 0.30, 0.90, 0.0, 0.15, 0.60, 0.45, 0.30)
    _PERIOD = 1.2

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(18, 21)
        self._phase_s = 0.0
        self._active = False
        pal = get_palette()
        self._base = QColor(pal.border)
        self._peak = QColor(pal.accent)

        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._tick)

    def apply_theme(self) -> None:
        pal = get_palette()
        self._base = QColor(pal.border)
        self._peak = QColor(pal.accent)
        self.update()

    def set_active(self, active: bool) -> None:
        self._active = active
        if active:
            if not self._timer.isActive():
                self._timer.start()
        else:
            self._timer.stop()
        self.update()

    def _tick(self) -> None:
        self._phase_s = (self._phase_s + 0.033) % self._PERIOD
        self.update()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)

        for idx in range(9):
            row = idx // 3
            col = idx % 3
            x = col * 7  # 4px width + 3px gap
            y = row * 8  # 5px height + 3px gap

            if not self._active:
                color = self._base
            else:
                local = ((self._phase_s - self._DELAYS[idx]) % self._PERIOD) / self._PERIOD
                # Match @keyframes dotPulse: peak at 25%, ease back smoothly
                if local <= 0.25:
                    intensity = math.sin((local / 0.25) * (math.pi / 2.0))
                else:
                    intensity = max(0.0, math.cos(((local - 0.25) / 0.75) * (math.pi / 2.0)))
                color = _lerp_color(self._base, self._peak, intensity)

            painter.setBrush(color)
            painter.drawRoundedRect(QRectF(x, y, 4.0, 5.0), 2.0, 2.0)


class LoadingIndicator(QWidget):
    """Inline 3x3 loading indicator with optional italic status label."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 10, 16, 10)
        layout.setSpacing(8)

        self._reduced_motion = False
        self._grid = DotMatrixGrid(self)
        layout.addWidget(self._grid)

        self._status = QLabel("", self)
        self._status.setVisible(False)
        layout.addWidget(self._status, 1)
        layout.addStretch(1)

        self.apply_theme()
        self.setVisible(False)

    def apply_theme(self) -> None:
        pal = get_palette()
        self._grid.apply_theme()
        self._status.setStyleSheet(
            f"color: {pal.text_muted}; font-size: 12px; font-style: italic; background: transparent;"
        )

    def set_reduced_motion(self, enabled: bool) -> None:
        self._reduced_motion = enabled
        self._grid.set_active(not enabled and not self.isHidden())

    def set_loading(self, active: bool, status_text: str = "") -> None:
        self._grid.set_active(active and not self._reduced_motion)
        if status_text:
            self._status.setText(status_text)
            self._status.setVisible(True)
        else:
            self._status.clear()
            self._status.setVisible(False)
        self.setVisible(active)


class ShimmerWaveLabel(QWidget):
    """Animated status text combining a sweeping gradient shimmer and bouncing letter wave."""

    _PERIOD = 1.8
    _PAD_X = 2.0
    _TOP_HEADROOM = 4.0
    _BOTTOM_PAD = 2.0
    _WAVE_AMP = 2.6

    def __init__(self, text: str = "Thinking...", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._text = text
        self._font = QFont("Segoe UI", 9, QFont.Weight.DemiBold)
        self._phase_s = 0.0
        self._active = False
        self._base = QColor()
        self._peak = QColor()

        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._tick)

        self.apply_theme()

    def text(self) -> str:
        return self._text

    def is_active(self) -> bool:
        return self._active

    def set_text(self, text: str) -> None:
        clean = " ".join(text.split())
        if clean == self._text:
            return
        self._text = clean
        self.updateGeometry()
        self.adjustSize()
        self.update()

    def set_active(self, active: bool) -> None:
        self._active = active
        if active:
            if not self._timer.isActive():
                self._timer.start()
        else:
            self._timer.stop()
            self._phase_s = 0.0
        self.update()

    def apply_theme(self) -> None:
        pal = get_palette()
        self._base = QColor(pal.text_muted)
        self._peak = _lerp_color(QColor(pal.accent), QColor(pal.heading_color), 0.45)
        self.update()

    def sizeHint(self) -> QSize:  # type: ignore[override]
        fm = QFontMetricsF(self._font)
        w = int(math.ceil(fm.horizontalAdvance(self._text) + self._PAD_X * 2.0))
        h = int(math.ceil(fm.height() + self._TOP_HEADROOM + self._BOTTOM_PAD))
        return QSize(max(12, w), max(16, h))

    def minimumSizeHint(self) -> QSize:  # type: ignore[override]
        return self.sizeHint()

    def _tick(self) -> None:
        self._phase_s = (self._phase_s + 0.033) % self._PERIOD
        self.update()

    def _char_metrics(self, idx: int, total_width: float, fm: QFontMetricsF) -> tuple[float, float, float]:
        """Return (x_pos, y_offset, shimmer_intensity) for the character at idx."""
        x = self._PAD_X + fm.horizontalAdvance(self._text[:idx])
        if not self._active:
            return x, 0.0, 0.0

        t = (self._phase_s % self._PERIOD) / self._PERIOD
        beam_u = -0.22 + 1.44 * t
        ch_w = fm.horizontalAdvance(self._text[idx])
        u = (x - self._PAD_X + ch_w * 0.5) / total_width
        dist = abs(u - beam_u)
        shimmer = max(0.0, 1.0 - (dist / 0.24) ** 2)

        wave_arg = (t * 2.0 * math.pi) - idx * 0.45
        bounce = max(0.0, math.sin(wave_arg)) ** 2
        dy = -self._WAVE_AMP * bounce
        return x, dy, shimmer

    def paintEvent(self, event) -> None:  # type: ignore[override]
        if not self._text:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        painter.setFont(self._font)

        fm = QFontMetricsF(self._font)
        total_width = max(1.0, fm.horizontalAdvance(self._text))
        baseline_y = self._TOP_HEADROOM + fm.ascent()

        for idx, ch in enumerate(self._text):
            x, dy, shimmer = self._char_metrics(idx, total_width, fm)
            if ch.isspace():
                continue
            painter.setPen(_lerp_color(self._base, self._peak, shimmer))
            painter.drawText(QPointF(x, baseline_y + dy), ch)


class SpinningIcon(QWidget):
    """Smooth 1.5s linear 360deg spinning icon (agent-client-spin)."""

    def __init__(self, icon_name: str = "loader", color: str | None = None, size: int = 14, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._size = size
        self._icon_name = icon_name
        self._color = color or get_palette().accent
        self._angle = 0.0
        self._spinning = False
        self._pixmap: QPixmap = lucide_pixmap(icon_name, color=self._color, size=size)
        self.setFixedSize(size + 2, size + 2)

        self._timer = QTimer(self)
        self._timer.setInterval(25)  # ~40fps smooth rotation
        self._timer.timeout.connect(self._advance)

    def set_icon(self, icon_name: str, color: str, spinning: bool) -> None:
        if icon_name != self._icon_name or color != self._color:
            self._icon_name = icon_name
            self._color = color
            self._pixmap = lucide_pixmap(icon_name, color=color, size=self._size)
        self._spinning = spinning
        if spinning:
            if not self._timer.isActive():
                self._timer.start()
        else:
            self._timer.stop()
            self._angle = 0.0
        self.update()

    def _advance(self) -> None:
        # 360 deg per 1500ms -> 6 deg per 25ms tick
        self._angle = (self._angle + 6.0) % 360.0
        self.update()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        cx = self.width() / 2.0
        cy = self.height() / 2.0
        painter.translate(cx, cy)
        if self._spinning:
            painter.rotate(self._angle)
        painter.drawPixmap(int(-self._size / 2), int(-self._size / 2), self._pixmap)


class WindowAnimator:
    """Handles smooth fade + subtle slide transitions for floating windows."""

    def __init__(self, widget: QWidget) -> None:
        self._widget = widget
        self.reduced_motion = False
        self._anim: QParallelAnimationGroup | None = None
        self._fade_out_anim: QPropertyAnimation | None = None
        self._hiding = False

    def show_animated(self, slide_px: int = 10, duration_ms: int = 170, on_done=None) -> None:
        self._stop_all()
        self._hiding = False
        if self.reduced_motion:
            self._widget.setWindowOpacity(1.0)
            self._widget.show()
            self._widget.raise_()
            if on_done:
                on_done()
            return
        target_pos = self._widget.pos()
        start_pos = QPoint(target_pos.x(), target_pos.y() + slide_px)

        self._widget.setWindowOpacity(0.0)
        self._widget.move(start_pos)
        self._widget.show()
        self._widget.raise_()

        fade = QPropertyAnimation(self._widget, b"windowOpacity", self._widget)
        fade.setDuration(duration_ms)
        fade.setStartValue(0.0)
        fade.setEndValue(1.0)
        fade.setEasingCurve(QEasingCurve.Type.OutCubic)

        slide = QPropertyAnimation(self._widget, b"pos", self._widget)
        slide.setDuration(duration_ms)
        slide.setStartValue(start_pos)
        slide.setEndValue(target_pos)
        slide.setEasingCurve(QEasingCurve.Type.OutCubic)

        group = QParallelAnimationGroup(self._widget)
        group.addAnimation(fade)
        group.addAnimation(slide)
        if on_done is not None:
            group.finished.connect(on_done)
        self._anim = group
        group.start()

    def hide_animated(self, duration_ms: int = 130, on_done=None) -> None:
        if not self._widget.isVisible() or self._hiding:
            if on_done:
                on_done()
            return
        self._stop_all()
        if self.reduced_motion:
            self._widget.hide()
            self._widget.setWindowOpacity(1.0)
            if on_done:
                on_done()
            return
        self._hiding = True

        fade = QPropertyAnimation(self._widget, b"windowOpacity", self._widget)
        fade.setDuration(duration_ms)
        fade.setStartValue(self._widget.windowOpacity())
        fade.setEndValue(0.0)
        fade.setEasingCurve(QEasingCurve.Type.InCubic)

        def _finish() -> None:
            self._hiding = False
            self._widget.hide()
            self._widget.setWindowOpacity(1.0)
            if on_done:
                on_done()

        fade.finished.connect(_finish)
        self._fade_out_anim = fade
        fade.start()

    def cancel_hide(self) -> None:
        if self._hiding or self._fade_out_anim is not None:
            self._stop_all()
            self._widget.setWindowOpacity(1.0)

    def _stop_all(self) -> None:
        self._hiding = False
        if self._anim is not None:
            self._anim.stop()
            self._anim = None
        if self._fade_out_anim is not None:
            self._fade_out_anim.stop()
            self._fade_out_anim = None
