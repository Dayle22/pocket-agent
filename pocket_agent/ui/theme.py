"""Dark/Light palettes, Windows 11 Acrylic DWM backdrop helper, vector icons, and Markdown HTML renderer."""

from __future__ import annotations

import ctypes
import html
import os
import re
from dataclasses import dataclass

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PyQt6.QtWidgets import QWidget

FONT_UI = "'Segoe UI', -apple-system, BlinkMacSystemFont, sans-serif"
FONT_MONO = "'Cascadia Code', Consolas, 'Courier New', monospace"


@dataclass(frozen=True)
class ThemePalette:
    """Complete colour tokens for Dark or Light mode (solid & translucent Acrylic)."""

    is_dark: bool
    bg_primary: str
    bg_tint_rgba: tuple[int, int, int, int]
    bg_card: str
    bg_card_blur: str
    bg_secondary: str
    bg_secondary_alt: str
    bg_hover: str
    border: str
    border_subtle: str
    text_normal: str
    text_muted: str
    text_faint: str
    accent: str
    accent_hover: str
    heading_color: str
    code_block_bg: str
    code_inline_bg: str
    diff_add_bg: str
    diff_add_word: str
    diff_rem_bg: str
    diff_rem_word: str
    green: str
    red: str
    orange: str
    yellow: str


DARK_PALETTE = ThemePalette(
    is_dark=True,
    bg_primary="#161618",
    bg_tint_rgba=(22, 22, 26, 76),
    bg_card="#1c1c20",
    bg_card_blur="rgba(34, 34, 40, 88)",
    bg_secondary="#202025",
    bg_secondary_alt="#26262c",
    bg_hover="rgba(255, 255, 255, 0.08)",
    border="#2e2e36",
    border_subtle="rgba(255, 255, 255, 0.07)",
    text_normal="#e4e4e7",
    text_muted="#a1a1aa",
    text_faint="#71717a",
    accent="#7f6df2",
    accent_hover="#9b8cf7",
    heading_color="#ffffff",
    code_block_bg="rgba(24, 24, 29, 138)",
    code_inline_bg="rgba(255, 255, 255, 0.08)",
    diff_add_bg="rgba(46, 160, 67, 0.18)",
    diff_add_word="rgba(46, 160, 67, 0.40)",
    diff_rem_bg="rgba(248, 81, 73, 0.18)",
    diff_rem_word="rgba(248, 81, 73, 0.40)",
    green="#4ade80",
    red="#f85149",
    orange="#e5933a",
    yellow="#e2b93d",
)

LIGHT_PALETTE = ThemePalette(
    is_dark=False,
    bg_primary="#f6f6f9",
    bg_tint_rgba=(245, 245, 250, 96),
    bg_card="#ffffff",
    bg_card_blur="rgba(255, 255, 255, 104)",
    bg_secondary="#ffffff",
    bg_secondary_alt="#ececf2",
    bg_hover="rgba(0, 0, 0, 0.06)",
    border="#dcdce4",
    border_subtle="rgba(0, 0, 0, 0.07)",
    text_normal="#18181b",
    text_muted="#52525b",
    text_faint="#71717a",
    accent="#6d5ae6",
    accent_hover="#5a46d4",
    heading_color="#09090b",
    code_block_bg="rgba(240, 240, 246, 150)",
    code_inline_bg="rgba(0, 0, 0, 0.06)",
    diff_add_bg="rgba(34, 197, 94, 0.14)",
    diff_add_word="rgba(34, 197, 94, 0.34)",
    diff_rem_bg="rgba(239, 68, 68, 0.14)",
    diff_rem_word="rgba(239, 68, 68, 0.34)",
    green="#16a34a",
    red="#dc2626",
    orange="#d97706",
    yellow="#ca8a04",
)


# Backwards-compatible default aliases (Dark)
BG_PRIMARY = DARK_PALETTE.bg_primary
BG_CARD = DARK_PALETTE.bg_card
BG_SECONDARY = DARK_PALETTE.bg_secondary
BG_SECONDARY_ALT = DARK_PALETTE.bg_secondary_alt
BG_HOVER = "#2a2a32"
BORDER = DARK_PALETTE.border
BORDER_SUBTLE = "#24242b"
TEXT_NORMAL = DARK_PALETTE.text_normal
TEXT_MUTED = DARK_PALETTE.text_muted
TEXT_FAINT = DARK_PALETTE.text_faint
ACCENT = DARK_PALETTE.accent
ACCENT_HOVER = DARK_PALETTE.accent_hover
COLOR_GREEN = DARK_PALETTE.green
COLOR_RED = DARK_PALETTE.red
COLOR_ORANGE = DARK_PALETTE.orange
COLOR_YELLOW = DARK_PALETTE.yellow
DIFF_ADD_BG = DARK_PALETTE.diff_add_bg
DIFF_ADD_WORD = DARK_PALETTE.diff_add_word
DIFF_REM_BG = DARK_PALETTE.diff_rem_bg
DIFF_REM_WORD = DARK_PALETTE.diff_rem_word

_ACTIVE_MODE = "dark"
_ACTIVE_BLUR = True


def detect_windows_dark_mode() -> bool:
    """Return True if Windows apps prefer dark mode (defaults True off-Windows)."""
    if os.name != "nt":
        return True
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        ) as key:
            val, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            return int(val) == 0
    except Exception:
        return True


def resolve_palette(mode: str | None = None) -> ThemePalette:
    """Resolve 'dark' | 'light' | 'system' to a ThemePalette."""
    m = (mode or _ACTIVE_MODE or "dark").lower()
    if m == "light":
        return LIGHT_PALETTE
    if m == "system":
        return DARK_PALETTE if detect_windows_dark_mode() else LIGHT_PALETTE
    return DARK_PALETTE


def set_active_theme(mode: str, blur: bool = True) -> ThemePalette:
    """Set the active global theme mode and blur preference."""
    global _ACTIVE_MODE, _ACTIVE_BLUR
    _ACTIVE_MODE = mode if mode in ("dark", "light", "system") else "dark"
    _ACTIVE_BLUR = bool(blur)
    return resolve_palette(_ACTIVE_MODE)


def get_palette() -> ThemePalette:
    """Return the currently active ThemePalette."""
    return resolve_palette(_ACTIVE_MODE)


def is_blur_enabled() -> bool:
    """Return whether translucent Acrylic backdrop blur is active."""
    return _ACTIVE_BLUR


class _MARGINS(ctypes.Structure):
    _fields_ = [
        ("cxLeftWidth", ctypes.c_int),
        ("cxRightWidth", ctypes.c_int),
        ("cyTopHeight", ctypes.c_int),
        ("cyBottomHeight", ctypes.c_int),
    ]


class _ACCENT_POLICY(ctypes.Structure):
    _fields_ = [
        ("AccentState", ctypes.c_int),
        ("AccentFlags", ctypes.c_int),
        ("GradientColor", ctypes.c_uint32),
        ("AnimationId", ctypes.c_int),
    ]


class _WINDOWCOMPOSITIONATTRIBDATA(ctypes.Structure):
    _fields_ = [
        ("Attribute", ctypes.c_int),
        ("Data", ctypes.POINTER(_ACCENT_POLICY)),
        ("SizeOfData", ctypes.c_size_t),
    ]


def apply_windows_backdrop(
    widget: QWidget,
    is_dark: bool,
    blur: bool,
    tint_rgba: tuple[int, int, int, int] | None = None,
) -> bool:
    """Apply Windows Acrylic blur with configurable tint opacity + rounded corners."""
    if os.name != "nt":
        return False
    try:
        hwnd = int(widget.winId())
        if not hwnd:
            return False
        dwmapi = ctypes.windll.dwmapi
        user32 = ctypes.windll.user32

        margins = _MARGINS(-1, -1, -1, -1) if blur else _MARGINS(0, 0, 0, 0)
        dwmapi.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(margins))

        # 33 = DWMWA_WINDOW_CORNER_PREFERENCE -> 2 = DWMWCP_ROUND
        dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(ctypes.c_int(2)), 4)
        # 20 = DWMWA_USE_IMMERSIVE_DARK_MODE
        dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(ctypes.c_int(1 if is_dark else 0)), 4)
        # Disable Win11's heavy default system backdrop so SetWindowCompositionAttribute controls tint alpha.
        dwmapi.DwmSetWindowAttribute(hwnd, 38, ctypes.byref(ctypes.c_int(1)), 4)

        if blur:
            r, g, b, a = tint_rgba if tint_rgba is not None else ((22, 22, 26, 76) if is_dark else (245, 245, 250, 96))
            a = max(1, min(255, int(a)))
            gradient_abgr = (a << 24) | ((b & 0xFF) << 16) | ((g & 0xFF) << 8) | (r & 0xFF)
            policy = _ACCENT_POLICY(4, 2, gradient_abgr, 0)
            data = _WINDOWCOMPOSITIONATTRIBDATA(19, ctypes.pointer(policy), ctypes.sizeof(policy))
            if user32.SetWindowCompositionAttribute(hwnd, ctypes.byref(data)):
                return True
            # Fallback to Win11 DWMSBT_TRANSIENTWINDOW if SetWindowCompositionAttribute is unavailable
            res = dwmapi.DwmSetWindowAttribute(hwnd, 38, ctypes.byref(ctypes.c_int(3)), 4)
            return res == 0

        policy = _ACCENT_POLICY(0, 0, 0, 0)
        data = _WINDOWCOMPOSITIONATTRIBDATA(19, ctypes.pointer(policy), ctypes.sizeof(policy))
        user32.SetWindowCompositionAttribute(hwnd, ctypes.byref(data))
        return True
    except Exception:
        return False


def lucide_pixmap(name: str, color: str = TEXT_MUTED, size: int = 14) -> QPixmap:
    """Render a crisp 24x24 Lucide-style stroke icon scaled cleanly to `size` px."""
    dpr = 2
    px = QPixmap(size * dpr, size * dpr)
    px.setDevicePixelRatio(dpr)
    px.fill(Qt.GlobalColor.transparent)

    painter = QPainter(px)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    scale = size / 24.0
    painter.scale(scale, scale)

    pen = QPen(QColor(color), 1.9, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)

    _draw_icon_path(painter, name, QColor(color))
    painter.end()
    return px


def lucide_icon(name: str, color: str = TEXT_MUTED, size: int = 14) -> QIcon:
    """Return a QIcon containing a Lucide-style stroke icon."""
    return QIcon(lucide_pixmap(name, color=color, size=size))


def _draw_icon_path(p: QPainter, name: str, color: QColor) -> None:
    if name == "plus":
        p.drawLine(QPointF(12, 5), QPointF(12, 19))
        p.drawLine(QPointF(5, 12), QPointF(19, 12))
    elif name == "x":
        p.drawLine(QPointF(18, 6), QPointF(6, 18))
        p.drawLine(QPointF(6, 6), QPointF(18, 18))
    elif name == "check":
        path = QPainterPath(QPointF(20, 6))
        path.lineTo(QPointF(9, 17))
        path.lineTo(QPointF(4, 12))
        p.drawPath(path)
    elif name == "chevron-down":
        path = QPainterPath(QPointF(6, 9))
        path.lineTo(QPointF(12, 15))
        path.lineTo(QPointF(18, 9))
        p.drawPath(path)
    elif name == "chevron-right":
        path = QPainterPath(QPointF(9, 18))
        path.lineTo(QPointF(15, 12))
        path.lineTo(QPointF(9, 6))
        p.drawPath(path)
    elif name == "chevron-up":
        path = QPainterPath(QPointF(18, 15))
        path.lineTo(QPointF(12, 9))
        path.lineTo(QPointF(6, 15))
        p.drawPath(path)
    elif name == "arrow-down":
        p.drawLine(QPointF(12, 5), QPointF(12, 19))
        path = QPainterPath(QPointF(19, 12))
        path.lineTo(QPointF(12, 19))
        path.lineTo(QPointF(5, 12))
        p.drawPath(path)
    elif name == "arrow-up":
        p.drawLine(QPointF(12, 19), QPointF(12, 5))
        path = QPainterPath(QPointF(5, 12))
        path.lineTo(QPointF(12, 5))
        path.lineTo(QPointF(19, 12))
        p.drawPath(path)
    elif name == "sparkles":
        star = QPainterPath(QPointF(12, 3))
        star.quadTo(QPointF(12, 12), QPointF(21, 12))
        star.quadTo(QPointF(12, 12), QPointF(12, 21))
        star.quadTo(QPointF(12, 12), QPointF(3, 12))
        star.quadTo(QPointF(12, 12), QPointF(12, 3))
        p.drawPath(star)
        p.drawLine(QPointF(19, 4), QPointF(19, 7))
        p.drawLine(QPointF(17.5, 5.5), QPointF(20.5, 5.5))
    elif name == "brain":
        p.drawArc(QRectF(5, 4, 7, 16), 90 * 16, 180 * 16)
        p.drawArc(QRectF(12, 4, 7, 16), -90 * 16, 180 * 16)
        p.drawLine(QPointF(12, 4.5), QPointF(12, 19.5))
        p.drawLine(QPointF(7.5, 9), QPointF(10.5, 9))
        p.drawLine(QPointF(13.5, 14.5), QPointF(16.5, 14.5))
    elif name == "shield":
        sh = QPainterPath(QPointF(12, 3))
        sh.lineTo(QPointF(19, 6))
        sh.lineTo(QPointF(19, 12))
        sh.quadTo(QPointF(19, 18), QPointF(12, 21))
        sh.quadTo(QPointF(5, 18), QPointF(5, 12))
        sh.lineTo(QPointF(5, 6))
        sh.closeSubpath()
        p.drawPath(sh)
    elif name == "folder":
        fp = QPainterPath(QPointF(3, 7))
        fp.quadTo(QPointF(3, 5), QPointF(5, 5))
        fp.lineTo(QPointF(9.5, 5))
        fp.lineTo(QPointF(11.5, 7.5))
        fp.lineTo(QPointF(19, 7.5))
        fp.quadTo(QPointF(21, 7.5), QPointF(21, 9.5))
        fp.lineTo(QPointF(21, 17.5))
        fp.quadTo(QPointF(21, 19.5), QPointF(19, 19.5))
        fp.lineTo(QPointF(5, 19.5))
        fp.quadTo(QPointF(3, 19.5), QPointF(3, 17.5))
        fp.closeSubpath()
        p.drawPath(fp)
    elif name == "minimize-2":
        p.drawLine(QPointF(14, 10), QPointF(21, 3))
        path1 = QPainterPath(QPointF(20, 10))
        path1.lineTo(QPointF(14, 10))
        path1.lineTo(QPointF(14, 4))
        p.drawPath(path1)
        p.drawLine(QPointF(3, 21), QPointF(10, 14))
        path2 = QPainterPath(QPointF(4, 14))
        path2.lineTo(QPointF(10, 14))
        path2.lineTo(QPointF(10, 20))
        p.drawPath(path2)
    elif name == "more-vertical":
        p.setBrush(color)
        for cy in (5.0, 12.0, 19.0):
            p.drawEllipse(QPointF(12, cy), 1.3, 1.3)
    elif name == "ellipsis":
        p.setBrush(color)
        for cx in (5.5, 12.0, 18.5):
            p.drawEllipse(QPointF(cx, 12), 1.3, 1.3)
    elif name == "send-horizontal":
        path = QPainterPath(QPointF(3.7, 3.3))
        path.lineTo(QPointF(21.0, 12.0))
        path.lineTo(QPointF(3.7, 20.7))
        path.lineTo(QPointF(6.5, 12.0))
        path.closeSubpath()
        p.drawPath(path)
        p.drawLine(QPointF(6.5, 12.0), QPointF(21.0, 12.0))
    elif name == "square":
        p.setBrush(color)
        p.drawRoundedRect(QRectF(5.5, 5.5, 13, 13), 2.2, 2.2)
    elif name == "copy":
        p.drawRoundedRect(QRectF(9, 9, 11, 11), 2, 2)
        path = QPainterPath(QPointF(5, 15))
        path.lineTo(QPointF(4, 15))
        path.quadTo(QPointF(3, 15), QPointF(3, 14))
        path.lineTo(QPointF(3, 5))
        path.quadTo(QPointF(3, 3), QPointF(5, 3))
        path.lineTo(QPointF(14, 3))
        path.quadTo(QPointF(15, 3), QPointF(15, 5))
        p.drawPath(path)
    elif name == "save":
        path = QPainterPath(QPointF(15.2, 3))
        path.lineTo(QPointF(5, 3))
        path.quadTo(QPointF(3, 3), QPointF(3, 5))
        path.lineTo(QPointF(3, 19))
        path.quadTo(QPointF(3, 21), QPointF(5, 21))
        path.lineTo(QPointF(19, 21))
        path.quadTo(QPointF(21, 21), QPointF(21, 19))
        path.lineTo(QPointF(21, 8.8))
        path.closeSubpath()
        p.drawPath(path)
        p.drawRect(QRectF(7, 13, 10, 8))
        p.drawRect(QRectF(7, 3, 8, 5))
    elif name == "folder-open":
        path = QPainterPath(QPointF(3, 19))
        path.lineTo(QPointF(3, 6))
        path.quadTo(QPointF(3, 4.5), QPointF(4.5, 4.5))
        path.lineTo(QPointF(9, 4.5))
        path.lineTo(QPointF(11, 7))
        path.lineTo(QPointF(19.5, 7))
        path.quadTo(QPointF(21, 7), QPointF(21, 8.5))
        path.lineTo(QPointF(21, 11))
        p.drawPath(path)
        tray = QPainterPath(QPointF(3, 19))
        tray.lineTo(QPointF(6.2, 11))
        tray.lineTo(QPointF(22, 11))
        tray.lineTo(QPointF(19, 19))
        tray.closeSubpath()
        p.drawPath(tray)
    elif name == "lightbulb":
        path = QPainterPath(QPointF(9, 18))
        path.lineTo(QPointF(15, 18))
        p.drawPath(path)
        p.drawLine(QPointF(10, 21), QPointF(14, 21))
        p.drawArc(QRectF(6, 3, 12, 12), -45 * 16, 270 * 16)
        p.drawLine(QPointF(8.5, 14), QPointF(9.5, 18))
        p.drawLine(QPointF(15.5, 14), QPointF(14.5, 18))
    elif name == "book-open":
        p.drawLine(QPointF(12, 7), QPointF(12, 21))
        left = QPainterPath(QPointF(2, 6))
        left.lineTo(QPointF(8, 6))
        left.quadTo(QPointF(12, 6), QPointF(12, 9))
        left.lineTo(QPointF(12, 20))
        left.quadTo(QPointF(8, 18), QPointF(2, 18))
        left.closeSubpath()
        p.drawPath(left)
        right = QPainterPath(QPointF(22, 6))
        right.lineTo(QPointF(16, 6))
        right.quadTo(QPointF(12, 6), QPointF(12, 9))
        right.lineTo(QPointF(12, 20))
        right.quadTo(QPointF(16, 18), QPointF(22, 18))
        right.closeSubpath()
        p.drawPath(right)
    elif name == "pencil":
        path = QPainterPath(QPointF(17, 3))
        path.lineTo(QPointF(21, 7))
        path.lineTo(QPointF(7, 21))
        path.lineTo(QPointF(3, 21))
        path.lineTo(QPointF(3, 17))
        path.closeSubpath()
        p.drawPath(path)
        p.drawLine(QPointF(14.5, 5.5), QPointF(18.5, 9.5))
    elif name == "trash":
        p.drawLine(QPointF(3, 6), QPointF(21, 6))
        p.drawLine(QPointF(9, 6), QPointF(9, 3.5))
        p.drawLine(QPointF(9, 3.5), QPointF(15, 3.5))
        p.drawLine(QPointF(15, 3.5), QPointF(15, 6))
        p.drawRoundedRect(QRectF(5.5, 6, 13, 15), 2, 2)
        p.drawLine(QPointF(10, 10.5), QPointF(10, 16.5))
        p.drawLine(QPointF(14, 10.5), QPointF(14, 16.5))
    elif name == "search":
        p.drawEllipse(QPointF(11, 11), 7, 7)
        p.drawLine(QPointF(16.2, 16.2), QPointF(21, 21))
    elif name == "square-terminal":
        p.drawRoundedRect(QRectF(3, 3, 18, 18), 2.5, 2.5)
        path = QPainterPath(QPointF(7, 9))
        path.lineTo(QPointF(10.5, 12))
        path.lineTo(QPointF(7, 15))
        p.drawPath(path)
        p.drawLine(QPointF(12.5, 15), QPointF(17, 15))
    elif name == "globe":
        p.drawEllipse(QPointF(12, 12), 9, 9)
        p.drawLine(QPointF(3.3, 12), QPointF(20.7, 12))
        p.drawEllipse(QPointF(12, 12), 4.2, 9)
    elif name == "arrow-left-right":
        p.drawLine(QPointF(4, 8), QPointF(20, 8))
        p1 = QPainterPath(QPointF(16, 4))
        p1.lineTo(QPointF(20, 8))
        p1.lineTo(QPointF(16, 12))
        p.drawPath(p1)
        p.drawLine(QPointF(4, 16), QPointF(20, 16))
        p2 = QPainterPath(QPointF(8, 12))
        p2.lineTo(QPointF(4, 16))
        p2.lineTo(QPointF(8, 20))
        p.drawPath(p2)
    elif name == "hammer":
        p.drawLine(QPointF(4, 20), QPointF(14, 10))
        head = QPainterPath(QPointF(11.5, 7.5))
        head.lineTo(QPointF(15.5, 3.5))
        head.lineTo(QPointF(20.5, 8.5))
        head.lineTo(QPointF(16.5, 12.5))
        head.closeSubpath()
        p.drawPath(head)
    elif name == "list-checks":
        for y in (6.5, 12.0, 17.5):
            tick = QPainterPath(QPointF(3.5, y))
            tick.lineTo(QPointF(5.5, y + 2))
            tick.lineTo(QPointF(8.5, y - 1.5))
            p.drawPath(tick)
            p.drawLine(QPointF(12, y), QPointF(20.5, y))
    elif name == "loader":
        p.drawArc(QRectF(4, 4, 16, 16), 30 * 16, 260 * 16)
    elif name == "circle":
        p.drawEllipse(QPointF(12, 12), 7.5, 7.5)
    elif name == "circle-alert":
        p.drawEllipse(QPointF(12, 12), 9, 9)
        p.drawLine(QPointF(12, 8), QPointF(12, 12.5))
        p.setBrush(color)
        p.drawEllipse(QPointF(12, 16), 0.9, 0.9)
    elif name == "message-circle-more":
        p.drawEllipse(QPointF(12, 12), 9, 9)
        p.setBrush(color)
        for cx in (8.5, 12.0, 15.5):
            p.drawEllipse(QPointF(cx, 12), 1.0, 1.0)
    elif name == "settings":
        p.drawEllipse(QPointF(12, 12), 3.2, 3.2)
        p.drawEllipse(QPointF(12, 12), 7.2, 7.2)
        for dx, dy in ((0, -9.2), (0, 9.2), (-9.2, 0), (9.2, 0), (-6.5, -6.5), (6.5, -6.5), (-6.5, 6.5), (6.5, 6.5)):
            p.drawLine(QPointF(12 + dx * 0.78, 12 + dy * 0.78), QPointF(12 + dx, 12 + dy))
    elif name == "volume-2":
        p.drawPolygon([QPointF(4, 9.5), QPointF(8, 9.5), QPointF(12, 6), QPointF(12, 18), QPointF(8, 14.5), QPointF(4, 14.5)])
        p.drawArc(QRectF(12, 8.5, 5, 7), -55 * 16, 110 * 16)
        p.drawArc(QRectF(13.5, 6, 6.5, 12), -55 * 16, 110 * 16)
    elif name == "smile":
        p.drawEllipse(QPointF(12, 12), 9, 9)
        p.drawArc(QRectF(8, 9, 8, 7), -160 * 16, 140 * 16)
        p.setBrush(color)
        p.drawEllipse(QPointF(9, 9.5), 0.9, 0.9)
        p.drawEllipse(QPointF(15, 9.5), 0.9, 0.9)
    elif name == "circle-dot":
        p.drawEllipse(QPointF(12, 12), 9, 9)
        p.setBrush(color)
        p.drawEllipse(QPointF(12, 12), 2.6, 2.6)
    elif name == "maximize-2":
        p.drawLine(QPointF(14, 10), QPointF(21, 3))
        tr = QPainterPath(QPointF(15, 3))
        tr.lineTo(QPointF(21, 3))
        tr.lineTo(QPointF(21, 9))
        p.drawPath(tr)
        p.drawLine(QPointF(3, 21), QPointF(10, 14))
        bl = QPainterPath(QPointF(3, 15))
        bl.lineTo(QPointF(3, 21))
        bl.lineTo(QPointF(9, 21))
        p.drawPath(bl)
    elif name == "sun":
        p.drawEllipse(QPointF(12, 12), 4, 4)
        for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0), (-0.707, -0.707), (0.707, -0.707), (-0.707, 0.707), (0.707, 0.707)):
            p.drawLine(QPointF(12 + dx * 7, 12 + dy * 7), QPointF(12 + dx * 9.5, 12 + dy * 9.5))
    elif name == "pin":
        p.drawLine(QPointF(12, 17), QPointF(12, 22))
        body = QPainterPath(QPointF(9, 3))
        body.lineTo(QPointF(15, 3))
        body.lineTo(QPointF(15, 9))
        body.lineTo(QPointF(18, 13))
        body.lineTo(QPointF(18, 17))
        body.lineTo(QPointF(6, 17))
        body.lineTo(QPointF(6, 13))
        body.lineTo(QPointF(9, 9))
        body.closeSubpath()
        p.drawPath(body)
    elif name == "power":
        p.drawLine(QPointF(12, 2.5), QPointF(12, 11))
        p.drawArc(QRectF(4, 5, 16, 16), 50 * 16, -280 * 16)
    elif name == "message-square":
        bubble = QPainterPath(QPointF(21, 15))
        bubble.quadTo(QPointF(21, 17), QPointF(19, 17))
        bubble.lineTo(QPointF(7, 17))
        bubble.lineTo(QPointF(3, 21))
        bubble.lineTo(QPointF(3, 5))
        bubble.quadTo(QPointF(3, 3), QPointF(5, 3))
        bubble.lineTo(QPointF(19, 3))
        bubble.quadTo(QPointF(21, 3), QPointF(21, 5))
        bubble.closeSubpath()
        p.drawPath(bubble)
    else:
        p.drawEllipse(QPointF(12, 12), 6, 6)


def kind_icon_name(kind: str | None) -> str:
    """Map an ACP tool kind to the corresponding Lucide icon name."""
    return {
        "read": "book-open",
        "edit": "pencil",
        "delete": "trash",
        "move": "folder-open",
        "search": "search",
        "execute": "square-terminal",
        "think": "message-circle-more",
        "fetch": "globe",
        "switch_mode": "arrow-left-right",
    }.get(kind or "", "hammer")


_FENCE_RE = re.compile(r"```([^\n`]*)\n(.*?)```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_ITALIC_RE = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)")
_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")


def render_markdown_html(md_text: str, palette: ThemePalette | None = None) -> str:
    """Convert common Markdown into theme-aware HTML fragments."""
    if not md_text:
        return ""
    pal = palette or get_palette()

    parts: list[str] = []
    last = 0
    for match in _FENCE_RE.finditer(md_text):
        before = md_text[last : match.start()]
        if before:
            parts.append(_render_inline_blocks(before, pal))
        lang = html.escape(match.group(1).strip())
        code = html.escape(match.group(2).rstrip("\n"))
        lang_tag = (
            f'<div style="color:{pal.text_faint};font-size:10px;margin-bottom:4px;'
            f'text-transform:uppercase;letter-spacing:0.4px;">{lang}</div>'
            if lang
            else ""
        )
        parts.append(
            f'<div style="background:{pal.code_block_bg};border:1px solid {pal.border};border-radius:6px;'
            f"padding:8px 10px;margin:6px 0;font-family:{FONT_MONO};"
            f'font-size:12px;color:{pal.text_normal};white-space:pre-wrap;">{lang_tag}{code}</div>'
        )
        last = match.end()

    tail = md_text[last:]
    if tail:
        parts.append(_render_inline_blocks(tail, pal))
    return "".join(parts)


def _render_inline_blocks(text: str, pal: ThemePalette) -> str:
    lines_out: list[str] = []
    for raw_line in text.splitlines():
        escaped = html.escape(raw_line)
        stripped = escaped.lstrip()

        if stripped.startswith("### "):
            lines_out.append(
                f'<div style="font-weight:600;color:{pal.text_normal};margin:6px 0 2px 0;">{_format_spans(stripped[4:], pal)}</div>'
            )
        elif stripped.startswith("## "):
            lines_out.append(
                f'<div style="font-weight:600;font-size:14px;color:{pal.heading_color};margin:8px 0 3px 0;">{_format_spans(stripped[3:], pal)}</div>'
            )
        elif stripped.startswith("# "):
            lines_out.append(
                f'<div style="font-weight:700;font-size:15px;color:{pal.heading_color};margin:8px 0 4px 0;">{_format_spans(stripped[2:], pal)}</div>'
            )
        elif stripped.startswith("- ") or stripped.startswith("* "):
            lines_out.append(
                f'<div style="margin:1px 0 1px 10px;">&bull; {_format_spans(stripped[2:], pal)}</div>'
            )
        elif raw_line.strip() == "":
            lines_out.append('<div style="height:6px;"></div>')
        else:
            lines_out.append(f"<div>{_format_spans(escaped, pal)}</div>")
    return "".join(lines_out)


def _format_spans(escaped: str, pal: ThemePalette) -> str:
    tokens: list[str] = []

    def _stash_code(m: re.Match[str]) -> str:
        idx = len(tokens)
        tokens.append(
            f'<code style="background:{pal.code_inline_bg};color:{pal.accent_hover};padding:1px 4px;'
            f'border-radius:4px;font-family:{FONT_MONO};">{m.group(1)}</code>'
        )
        return f"\x00CODE{idx}\x00"

    out = _INLINE_CODE_RE.sub(_stash_code, escaped)
    out = _LINK_RE.sub(rf'<a href="\2" style="color:{pal.accent_hover};text-decoration:none;">\1</a>', out)
    out = _BOLD_RE.sub(r"<b>\1</b>", out)
    out = _ITALIC_RE.sub(r"<i>\1</i>", out)
    for idx, tok in enumerate(tokens):
        out = out.replace(f"\x00CODE{idx}\x00", tok)
    return out
