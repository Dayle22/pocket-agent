"""Shared theme-aware popup menus so the pet, tray and chat drawer all feel like one app."""

from __future__ import annotations

import hashlib
import os
import tempfile
from collections.abc import Callable
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction, QActionGroup, QColor, QIcon, QPainter, QPainterPath, QPixmap, QShowEvent
from PyQt6.QtWidgets import QMenu, QWidget

from pocket_agent.ui.theme import (
    FONT_UI,
    apply_windows_backdrop,
    get_palette,
    is_blur_enabled,
    lucide_pixmap,
)

ICON_SIZE = 16
_ICON_DIR = Path(tempfile.gettempdir()) / "pocket_agent_icons"


def _icon_file(name: str, color: str, size: int = 12) -> str:
    """Cache a Lucide icon as PNG so Qt stylesheets can reference it via url()."""
    _ICON_DIR.mkdir(parents=True, exist_ok=True)
    digest = hashlib.md5(f"{name}|{color}|{size}".encode()).hexdigest()[:10]
    path = _ICON_DIR / f"{name}-{digest}.png"
    if not path.exists():
        lucide_pixmap(name, color, size).save(str(path), "PNG")
    return path.as_posix()


def _use_native_blur() -> bool:
    return is_blur_enabled() and os.name == "nt"


def menu_qss() -> str:
    """Single source of truth for every popup menu in Pocket Agent."""
    pal = get_palette()
    arrow = _icon_file("chevron-right", pal.text_faint, 12)
    native_blur = _use_native_blur()
    bg = "transparent" if native_blur else pal.bg_primary
    border = "none" if native_blur else f"1px solid {pal.border}"
    radius = "0px" if native_blur else "10px"
    sep = pal.border_subtle if native_blur else pal.border
    return f"""
        QMenu {{
            background: {bg};
            color: {pal.text_normal};
            border: {border};
            border-radius: {radius};
            padding: 5px;
            font-family: {FONT_UI};
            font-size: 12px;
        }}
        QMenu::item {{
            background: transparent;
            padding: 6px 28px 6px 8px;
            border-radius: 6px;
        }}
        QMenu::item:selected {{
            background: {pal.bg_hover};
            color: {pal.heading_color};
        }}
        QMenu::item:disabled {{
            color: {pal.text_faint};
            background: transparent;
        }}
        QMenu::icon {{
            padding-left: 8px;
        }}
        QMenu::indicator {{
            width: 0px;
            height: 0px;
        }}
        QMenu::separator {{
            height: 1px;
            background: {sep};
            margin: 5px 8px;
        }}
        QMenu::right-arrow {{
            image: url("{arrow}");
            width: 12px;
            height: 12px;
            margin-right: 8px;
        }}
    """


def themed_icon(name: str, color: str | None = None) -> QIcon:
    """Lucide stroke icon in the standard menu tint."""
    return QIcon(lucide_pixmap(name, color or get_palette().text_muted, ICON_SIZE))


def _check_icon() -> QIcon:
    """Accent tick when checked, empty slot when not, so labels stay aligned."""
    blank = QPixmap(ICON_SIZE * 2, ICON_SIZE * 2)
    blank.setDevicePixelRatio(2)
    blank.fill(Qt.GlobalColor.transparent)
    icon = QIcon()
    icon.addPixmap(blank, QIcon.Mode.Normal, QIcon.State.Off)
    icon.addPixmap(lucide_pixmap("check", get_palette().accent, ICON_SIZE), QIcon.Mode.Normal, QIcon.State.On)
    return icon


class PocketMenu(QMenu):
    """Acrylic-blurred, rounded QMenu matching ChatDrawer's surface, icons, and hover states."""

    def __init__(self, title: str = "", parent: QWidget | None = None, icon: str | None = None) -> None:
        super().__init__(title, parent)
        self._native_blur = _use_native_blur()
        if self._native_blur:
            self.setWindowFlags(self.windowFlags() | Qt.WindowType.FramelessWindowHint)
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
            self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        else:
            self.setWindowFlags(
                self.windowFlags() | Qt.WindowType.FramelessWindowHint | Qt.WindowType.NoDropShadowWindowHint
            )
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setStyleSheet(menu_qss())
        if icon:
            self.setIcon(themed_icon(icon))

    def showEvent(self, event: QShowEvent) -> None:  # type: ignore[override]
        super().showEvent(event)
        if self._native_blur:
            pal = get_palette()
            apply_windows_backdrop(self, is_dark=pal.is_dark, blur=True, tint_rgba=pal.bg_tint_rgba)

    def paintEvent(self, event) -> None:  # type: ignore[override]
        if self._native_blur:
            # Match ChatDrawer: keep client alpha at 1/255 so DWM composites Acrylic without double-darkening.
            clear = QPainter(self)
            clear.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
            clear.fillRect(self.rect(), QColor(0, 0, 0, 1))
            clear.end()

        super().paintEvent(event)

        if self._native_blur:
            pal = get_palette()
            border = QPainter(self)
            border.setRenderHint(QPainter.RenderHint.Antialiasing)
            rect = self.rect().adjusted(0, 0, -1, -1)
            path = QPainterPath()
            path.addRoundedRect(rect.x(), rect.y(), rect.width(), rect.height(), 8, 8)
            border.setPen(QColor(pal.border))
            border.setBrush(Qt.BrushStyle.NoBrush)
            border.drawPath(path)
            border.end()

    def add_submenu(self, title: str, icon: str | None = None) -> PocketMenu:
        sub = PocketMenu(title, self, icon)
        self.addMenu(sub)
        return sub

    def add_item(
        self,
        text: str,
        slot: Callable[..., object] | None = None,
        *,
        icon: str | None = None,
        shortcut: str = "",
        enabled: bool = True,
        tooltip: str = "",
    ) -> QAction:
        act = QAction(f"{text}\t{shortcut}" if shortcut else text, self)
        if icon:
            act.setIcon(themed_icon(icon))
        act.setEnabled(enabled)
        if tooltip:
            act.setToolTip(tooltip)
        if slot is not None:
            act.triggered.connect(slot)
        self.addAction(act)
        return act

    def add_check(
        self,
        text: str,
        checked: bool,
        slot: Callable[..., object] | None = None,
        *,
        group: QActionGroup | None = None,
        enabled: bool = True,
        tooltip: str = "",
    ) -> QAction:
        act = QAction(text, self)
        act.setCheckable(True)
        act.setChecked(checked)
        act.setIcon(_check_icon())
        act.setEnabled(enabled)
        if tooltip:
            act.setToolTip(tooltip)
        if group is not None:
            group.addAction(act)
        if slot is not None:
            act.triggered.connect(slot)
        self.addAction(act)
        return act
