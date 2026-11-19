"""Theme-aware setup and troubleshooting guide for connecting ACP AI agents."""

from __future__ import annotations

import os
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtCore import QUrl
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from pocket_agent.agents import AgentSpec
from pocket_agent.config import CONFIG_FILE
from pocket_agent.ui.theme import FONT_MONO, FONT_UI, apply_windows_backdrop, get_palette, lucide_icon

_AGENT_SETUP_STEPS: dict[str, tuple[str, str]] = {
    "agy": (
        "Antigravity CLI",
        "Install Antigravity and make sure `agy` is on PATH. Sign in via Antigravity before launching Pocket Agent.",
    ),
    "gemini": (
        "Gemini CLI",
        "Install: `npm install -g @google/gemini-cli` then run `gemini` once in PowerShell to sign in.",
    ),
    "codex": (
        "Codex (ACP)",
        "Install `codex-acp` and sign in with `codex` (or set `OPENAI_API_KEY`) in your environment.",
    ),
    "opencode": (
        "OpenCode",
        "Install `opencode` and configure a provider/auth by running `opencode` once in a terminal.",
    ),
    "claude": (
        "Claude Code (ACP)",
        "Install `claude-agent-acp` plus Claude Code, then run `claude` once in PowerShell to sign in.",
    ),
}


class AgentSetupDialog(QDialog):
    """Shows detected ACP CLIs, one-click connect, and first-time login steps."""

    connect_agent_requested = pyqtSignal(str)

    def __init__(
        self,
        agents: list[AgentSpec],
        current_agent_id: str,
        log_file: Path,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._agents = list(agents)
        self._current_agent_id = current_agent_id
        self._log_file = log_file

        self.setWindowTitle("Connect an AI Agent")
        self.resize(520, 560)
        self._build_ui()
        self.apply_theme()

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        apply_windows_backdrop(self, is_dark=get_palette().is_dark, blur=False)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        self._title_lbl = QLabel("Connect an AI Agent", self)
        root.addWidget(self._title_lbl)

        self._intro_lbl = QLabel(
            "Pocket Agent talks to local AI CLIs using the Agent Client Protocol (ACP). "
            "Install and sign in to at least one CLI in PowerShell once, then click Connect below.",
            self,
        )
        self._intro_lbl.setWordWrap(True)
        root.addWidget(self._intro_lbl)

        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._container = QWidget()
        self._container.setStyleSheet("background: transparent;")
        self._content_layout = QVBoxLayout(self._container)
        self._content_layout.setContentsMargins(0, 0, 4, 0)
        self._content_layout.setSpacing(10)
        self._scroll.setWidget(self._container)
        root.addWidget(self._scroll, 1)

        footer = QHBoxLayout()
        footer.setSpacing(8)
        self._refresh_btn = QPushButton("Refresh", self)
        self._refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._refresh_btn.clicked.connect(self._populate_cards)
        footer.addWidget(self._refresh_btn)

        self._config_btn = QPushButton("Open Config", self)
        self._config_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._config_btn.clicked.connect(self._open_config)
        footer.addWidget(self._config_btn)

        self._log_btn = QPushButton("Open Log", self)
        self._log_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._log_btn.clicked.connect(self._open_log)
        footer.addWidget(self._log_btn)

        footer.addStretch(1)
        self._close_btn = QPushButton("Close", self)
        self._close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._close_btn.clicked.connect(self.accept)
        footer.addWidget(self._close_btn)
        root.addLayout(footer)

        self._populate_cards()

    def _clear_layout(self, layout: QVBoxLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def _populate_cards(self) -> None:
        self._clear_layout(self._content_layout)
        pal = get_palette()

        # Detected agents card
        detected_card = QFrame(self._container)
        detected_card.setObjectName("GuideCard")
        d_lay = QVBoxLayout(detected_card)
        d_lay.setContentsMargins(12, 10, 12, 12)
        d_lay.setSpacing(8)

        sec1 = QLabel("Detected Agents on This PC", detected_card)
        sec1.setObjectName("SectionTitle")
        d_lay.addWidget(sec1)

        for spec in self._agents:
            exe = spec.resolve_executable()
            row = QHBoxLayout()
            row.setSpacing(8)

            info_col = QVBoxLayout()
            info_col.setSpacing(2)
            active_tag = " (selected)" if spec.id == self._current_agent_id else ""
            name_lbl = QLabel(f"{spec.name}{active_tag}", detected_card)
            name_lbl.setObjectName("AgentTitle")
            info_col.addWidget(name_lbl)

            status_text = exe if exe else f"Not found (`{spec.command}` missing from PATH)"
            status_lbl = QLabel(status_text, detected_card)
            status_lbl.setObjectName("AgentPathOk" if exe else "AgentPathMissing")
            status_lbl.setWordWrap(True)
            info_col.addWidget(status_lbl)
            row.addLayout(info_col, 1)

            btn = QPushButton("Connect" if exe else "Missing", detected_card)
            btn.setEnabled(exe is not None)
            btn.setCursor(Qt.CursorShape.PointingHandCursor if exe else Qt.CursorShape.ArrowCursor)
            if exe:
                btn.clicked.connect(lambda _c=False, aid=spec.id: self._connect_and_close(aid))
            row.addWidget(btn)
            d_lay.addLayout(row)

        self._content_layout.addWidget(detected_card)

        # First-time setup & troubleshooting card
        steps_card = QFrame(self._container)
        steps_card.setObjectName("GuideCard")
        s_lay = QVBoxLayout(steps_card)
        s_lay.setContentsMargins(12, 10, 12, 12)
        s_lay.setSpacing(8)

        sec2 = QLabel("First-Time Setup & Troubleshooting", steps_card)
        sec2.setObjectName("SectionTitle")
        s_lay.addWidget(sec2)

        for title, desc in _AGENT_SETUP_STEPS.values():
            lbl = QLabel(f"<b>{title}:</b> {desc}", steps_card)
            lbl.setObjectName("GuideBody")
            lbl.setWordWrap(True)
            lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            s_lay.addWidget(lbl)

        custom_lbl = QLabel(
            f"<b>Custom ACP Agent:</b> Edit <code>{CONFIG_FILE}</code> and add an entry to "
            "<code>custom_agents</code> with <code>id</code>, <code>name</code>, <code>command</code>, and <code>args</code>.",
            steps_card,
        )
        custom_lbl.setObjectName("GuideBody")
        custom_lbl.setWordWrap(True)
        custom_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        s_lay.addWidget(custom_lbl)

        tip_lbl = QLabel(
            "<b>If it fails to connect:</b> Run the CLI once in PowerShell to confirm you are signed in "
            "and it starts cleanly, then click <b>Reconnect</b> in Pocket Agent.",
            steps_card,
        )
        tip_lbl.setObjectName("GuideBody")
        tip_lbl.setWordWrap(True)
        s_lay.addWidget(tip_lbl)

        self._content_layout.addWidget(steps_card)
        self._content_layout.addStretch(1)
        self._style_cards(pal)

    def _connect_and_close(self, agent_id: str) -> None:
        self.connect_agent_requested.emit(agent_id)
        self.accept()

    def _open_config(self) -> None:
        if CONFIG_FILE.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(CONFIG_FILE)))
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(CONFIG_FILE.parent)))

    def _open_log(self) -> None:
        target = self._log_file if self._log_file.exists() else self._log_file.parent
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))

    def apply_theme(self) -> None:
        pal = get_palette()
        self.setStyleSheet(
            f"QDialog {{ background: {pal.bg_primary}; color: {pal.text_normal}; font-family: {FONT_UI}; }}"
        )
        self._title_lbl.setStyleSheet(
            f"color: {pal.heading_color}; font-size: 16px; font-weight: 700; background: transparent;"
        )
        self._intro_lbl.setStyleSheet(
            f"color: {pal.text_muted}; font-size: 12px; background: transparent;"
        )
        self._scroll.setStyleSheet(
            f"""
            QScrollArea {{ background: transparent; border: none; }}
            QScrollBar:vertical {{ background: transparent; width: 6px; margin: 4px 1px; }}
            QScrollBar::handle:vertical {{ background: {pal.border}; border-radius: 3px; min-height: 24px; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
            """
        )
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
            QPushButton:disabled {{
                color: {pal.text_faint};
            }}
        """
        for btn in (self._refresh_btn, self._config_btn, self._log_btn, self._close_btn):
            btn.setStyleSheet(btn_qss)
        self._refresh_btn.setIcon(lucide_icon("refresh-cw", pal.text_normal, 12))
        self._style_cards(pal)

    def _style_cards(self, pal) -> None:
        self._container.setStyleSheet(
            f"""
            #GuideCard {{
                background: {pal.bg_card};
                border: 1px solid {pal.border_subtle};
                border-radius: 10px;
            }}
            #SectionTitle {{
                color: {pal.heading_color};
                font-size: 12px;
                font-weight: 600;
                background: transparent;
            }}
            #AgentTitle {{
                color: {pal.text_normal};
                font-size: 12px;
                font-weight: 600;
                background: transparent;
            }}
            #AgentPathOk {{
                color: {pal.green};
                font-size: 10px;
                font-family: {FONT_MONO};
                background: transparent;
            }}
            #AgentPathMissing {{
                color: {pal.text_faint};
                font-size: 10px;
                font-family: {FONT_MONO};
                background: transparent;
            }}
            #GuideBody {{
                color: {pal.text_normal};
                font-size: 11px;
                background: transparent;
            }}
            QPushButton {{
                background: {pal.bg_secondary};
                color: {pal.text_normal};
                border: 1px solid {pal.border};
                border-radius: 7px;
                padding: 4px 10px;
                font-size: 11px;
            }}
            QPushButton:hover:enabled {{
                background: {pal.bg_hover};
                border-color: {pal.accent};
            }}
            QPushButton:disabled {{
                color: {pal.text_faint};
            }}
            """
        )
