"""Tests for the animated working-status speech bubble when the chat drawer is minimised."""

from __future__ import annotations

from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QFontMetricsF, QMouseEvent

from pocket_agent.app import PocketAgentApp, _tool_phase_for
from pocket_agent.config import AppConfig
from pocket_agent.ui.animations import ShimmerWaveLabel
from pocket_agent.ui.bubble import SpeechBubble


def test_shimmer_wave_label_animates_bounce_and_shimmer(qapp) -> None:
    label = ShimmerWaveLabel("Thinking...")
    assert label.text() == "Thinking..."
    assert not label.is_active()

    fm = QFontMetricsF(label._font)
    total_w = float(fm.horizontalAdvance(label.text()))
    _, dy_idle, shimmer_idle = label._char_metrics(0, total_w, fm)
    assert dy_idle == 0.0
    assert shimmer_idle == 0.0

    label.set_active(True)
    assert label.is_active()
    assert label._timer.isActive()

    bounces: list[float] = []
    shimmers: list[float] = []
    for _ in range(55):
        label._tick()
        for idx in range(len(label.text())):
            _, dy, sh = label._char_metrics(idx, total_w, fm)
            bounces.append(dy)
            shimmers.append(sh)

    assert min(bounces) < -1.0
    assert max(shimmers) > 0.7

    label.set_text("Reading files...")
    assert label.text() == "Reading files..."
    label.set_active(False)
    assert not label._timer.isActive()
    label.deleteLater()
    qapp.processEvents()


def test_speech_bubble_status_mode_and_click(qapp) -> None:
    bubble = SpeechBubble()
    clicks: list[bool] = []
    bubble.clicked.connect(lambda: clicks.append(True))

    bubble.show_status("Thinking...", 400, 400)
    assert bubble.isVisible()
    assert bubble.is_status_active()
    assert bubble.status_text() == "Thinking..."
    assert not bubble._hide_timer.isActive()
    assert bubble._status_label.is_active()

    # Updating status text in place keeps status mode active
    bubble.show_status("Editing files...", 400, 400)
    assert bubble.is_status_active()
    assert bubble.status_text() == "Editing files..."

    # Left click emits clicked signal
    evt = QMouseEvent(
        QMouseEvent.Type.MouseButtonPress,
        QPointF(10, 10),
        QPointF(10, 10),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    bubble.mousePressEvent(evt)
    assert clicks == [True]

    # Regular toast exits status mode and is NOT hidden by hide_status()
    bubble.show_text("Done!", 400, 400, duration_ms=2000)
    assert not bubble.is_status_active()
    assert not bubble._status_label.is_active()
    bubble.hide_status()
    assert bubble.isVisible()

    bubble.hide()
    bubble.deleteLater()
    qapp.processEvents()


def test_tool_phase_classification() -> None:
    assert _tool_phase_for("read", "Read app.py") == "reading"
    assert _tool_phase_for("search", "Grep symbols") == "reading"
    assert _tool_phase_for("edit", "Update bubble.py") == "editing"
    assert _tool_phase_for("execute", "Run pytest") == "running"
    assert _tool_phase_for("other", "Custom MCP call") == "tools"


def test_working_bubble_only_shows_when_drawer_minimised(qapp, monkeypatch, tmp_path) -> None:
    # Avoid launching real agent subprocesses or mutating user config on disk
    monkeypatch.setattr(AppConfig, "load", classmethod(lambda cls: AppConfig(workspace=str(tmp_path))))
    monkeypatch.setattr(AppConfig, "save", lambda self: None)
    monkeypatch.setattr("pocket_agent.app.AcpBridge.connect_agent", lambda self, spec, cwd: None)
    monkeypatch.setattr("pocket_agent.app.AcpBridge.resolve_permission", lambda self, rid, oid: None)

    app = PocketAgentApp(qapp)
    try:
        # 1. Drawer visible -> working bubble must NOT appear during turn
        app.drawer.show()
        qapp.processEvents()
        assert app.drawer.isVisible()

        app._on_turn_started()
        assert app._turn_active is True
        assert not app.pet.bubble.is_status_active()

        app._on_tool_started("t1", "Read drawer.py", "read", "in_progress", None)
        assert not app.pet.bubble.is_status_active()

        # 2. Minimise/hide drawer mid-task -> working bubble appears with current phase phrase
        app.drawer.hide()
        app.drawer.closed.emit()
        qapp.processEvents()
        assert not app.drawer.isVisible()
        assert app.pet.bubble.is_status_active()
        assert app.pet.bubble.status_text() == "Reading files..."

        # 3. Advance phrase timer cycles through working phrases
        app._advance_working_phrase()
        assert app.pet.bubble.status_text() == "Searching..."

        # 4. Permission request opens the drawer and pauses working bubble for "Need your OK!"
        app._on_permission_requested("req-1", "Run tests", "execute", [])
        assert app.drawer.isVisible()
        assert not app.pet.bubble.is_status_active()
        assert app.pet.bubble._label.text() == "Need your OK!"

        # Resolving permission keeps working bubble hidden while drawer remains open
        app.drawer._decide_permission("allow_once")
        assert not app.pet.bubble.is_status_active()

        # Minimising the drawer again restores the working bubble
        app.drawer.hide()
        app.drawer.closed.emit()
        assert app.pet.bubble.is_status_active()

        # 5. Opening drawer hides working bubble immediately
        app._open_drawer_from_bubble()
        qapp.processEvents()
        assert app.drawer.isVisible()
        assert not app.pet.bubble.is_status_active()

        # 6. Minimise again -> message streaming -> finish shows "Done!"
        app.drawer.hide()
        app.drawer.closed.emit()
        app._on_message_chunk("Here is the fix")
        assert app.pet.bubble.is_status_active()
        assert app.pet.bubble.status_text() == "Writing reply..."

        app._on_turn_finished("end_turn")
        assert not app.pet.bubble.is_status_active()
        assert app.pet.bubble._label.text() == "Done!"
    finally:
        app._working_phrase_timer.stop()
        app._hotkey_filter.unregister()
        qapp.removeNativeEventFilter(app._hotkey_filter)
        app.voice.shutdown()
        app.bridge.shutdown()
        app.pet.bubble.close()
        app.drawer.close()
        app.pet.close_tray()
        app.pet.close()
        app.deleteLater()
        qapp.processEvents()
