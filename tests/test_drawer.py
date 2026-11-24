"""Tests for Obsidian Agent Client UI components, diffs, overlays, and animations."""

from PyQt6.QtCore import QEvent, Qt

from pocket_agent.acp_bridge import (
    AvailableCommandInfo,
    ConfigChoiceInfo,
    ConfigOptionInfo,
    PermissionOptionInfo,
    PlanEntryInfo,
    UsageInfo,
)
from pocket_agent.agents import BUILTIN_AGENTS
from pocket_agent.ui.animations import DotMatrixGrid, LoadingIndicator, SpinningIcon
from pocket_agent.ui.diff_widget import DiffWidget, compute_diff_lines
from pocket_agent.ui.drawer import ChatDrawer, render_markdown_html
from pocket_agent.ui.theme import DARK_PALETTE, LIGHT_PALETTE, get_palette


def test_render_markdown_html_escapes_and_formats() -> None:
    md = "# Title\nHello **world** with `code` and <script>bad</script>\n```python\nprint(1)\n```"
    out = render_markdown_html(md)
    assert "<b>world</b>" in out
    assert "&lt;script&gt;bad&lt;/script&gt;" in out
    assert "<script>" not in out
    assert "print(1)" in out


def test_compute_diff_lines_and_widget_autocollapse(qapp) -> None:
    old = "\n".join(f"line {i}" for i in range(15))
    new = "\n".join(f"line {i} edited" if i in (2, 12) else f"line {i}" for i in range(15))
    lines = compute_diff_lines(old, new)
    assert any(line.kind == "removed" for line in lines)
    assert any(line.kind == "added" for line in lines)

    widget = DiffWidget("src/app.py", old, new, auto_collapse=True, collapse_threshold=4)
    assert widget._should_collapse is True
    assert widget._is_collapsed is True
    widget._toggle_collapse()
    assert widget._is_collapsed is False


def test_drawer_permission_decision_emits_option(qapp) -> None:
    drawer = ChatDrawer(agents=list(BUILTIN_AGENTS), current_agent_id="agy", workspace=".")
    captured: list[tuple[str, object]] = []
    drawer.permission_decided.connect(lambda rid, oid: captured.append((rid, oid)))

    drawer.show_permission_request(
        "req-1",
        "Write file foo.txt",
        "edit",
        [
            PermissionOptionInfo(option_id="allow_once", name="Allow Once", kind="allow_once"),
            PermissionOptionInfo(option_id="reject_once", name="Reject", kind="reject_once"),
        ],
        details={"diffs": [{"path": "foo.txt", "old_text": "a", "new_text": "b"}]},
    )
    drawer.show_permission_request(
        "req-2",
        "Run pytest",
        "execute",
        [PermissionOptionInfo(option_id="allow_once", name="Allow Once", kind="allow_once")],
        details={"command": "pytest -v"},
    )
    assert drawer._perm_bar.isVisible() is True
    assert drawer._perm_dialog._queue_badge.text() == "+1"

    drawer._decide_permission("allow_once")
    assert drawer._perm_bar.isVisible() is True
    drawer._decide_permission("allow_once")
    assert drawer._perm_bar.isVisible() is False
    assert captured == [("req-1", "allow_once"), ("req-2", "allow_once")]


def test_drawer_plan_commands_usage_and_streaming(qapp) -> None:
    drawer = ChatDrawer(agents=list(BUILTIN_AGENTS), current_agent_id="agy", workspace=".")
    drawer.show()
    drawer.set_plan(
        [
            PlanEntryInfo(content="Inspect repo", status="completed"),
            PlanEntryInfo(content="Build UI", status="in_progress"),
            PlanEntryInfo(content="Run tests", status="pending"),
        ]
    )
    assert drawer._plan_strip.isVisible() is True
    assert drawer._plan_strip._count_lbl.text() == "Plan 1/3"

    drawer.set_usage(UsageInfo(used=75000, size=100000))
    assert drawer._usage_label.isVisible() is True
    assert drawer._usage_label.text() == "75%"

    drawer.set_commands([AvailableCommandInfo(name="plan", description="Create a plan")])
    drawer._input.setPlainText("/pl")
    assert drawer._suggestion_popup.isVisible() is True

    drawer.set_connection_state("connected")
    drawer._submit_prompt("Hello")
    drawer.append_thought_chunk("Thinking deeply...")
    drawer.start_tool_call(
        "tc-1",
        "Edit file",
        "edit",
        "in_progress",
        details={"locations": ["pocket_agent/app.py:10"], "diffs": [{"path": "a.py", "old_text": "x", "new_text": "y"}]},
    )
    drawer.update_tool_call("tc-1", "Edit file", "edit", "completed")
    drawer.append_message_chunk("Done!")
    drawer._render_transcript_now()

    assert drawer._turns[-1].text == "Done!"
    assert drawer._turns[-1].tools[0].status == "completed"


def test_review_from_settings_restores_section_after_last_request(qapp) -> None:
    drawer = ChatDrawer(agents=list(BUILTIN_AGENTS), current_agent_id="agy", workspace="")
    drawer.show()
    drawer.set_connection_state("connected")
    drawer.open_settings()
    drawer.settings.open_section("Voice")
    section = drawer.settings._stack.currentWidget()
    drawer._input.setPlainText("Unsent draft")
    decisions = []
    drawer.permission_decided.connect(lambda rid, oid: decisions.append((rid, oid)))
    for rid in ("first", "second"):
        drawer.show_permission_request(rid, f"Edit {rid}", "edit", [])
    assert drawer._pages.currentWidget() is drawer.settings
    assert drawer._notice_action.isVisible()
    assert "Edit first" in drawer._notice_text.text()
    drawer._notice_action.click()
    assert drawer._perm_dialog.isVisible()
    drawer._decide_permission(None)
    assert drawer._pages.currentWidget() is drawer._chat_page
    assert "Edit second" in drawer._notice_text.text()
    drawer._decide_permission(None)
    assert decisions == [("first", None), ("second", None)]
    assert drawer._pages.currentWidget() is drawer.settings
    assert drawer.settings._stack.currentWidget() is section
    assert drawer._input.toPlainText() == "Unsent draft"
    assert drawer._notice.isHidden()
    drawer.close()


def test_disconnect_during_review_preserves_draft_and_offers_reconnect(qapp) -> None:
    drawer = ChatDrawer(agents=list(BUILTIN_AGENTS), current_agent_id="agy", workspace="")
    drawer.show()
    drawer.open_settings()
    drawer.show_permission_request("one", "Edit a file", "edit", [])
    drawer._notice_action.click()
    drawer.set_connection_state("disconnected")
    assert drawer._pages.currentWidget() is drawer.settings
    assert not drawer.permission_pending
    assert drawer._notice_action.text() == "Reconnect"
    reconnects = []
    drawer.reconnect_requested.connect(lambda: reconnects.append(True))
    drawer._notice_action.click()
    drawer.close_settings()
    drawer._on_agent_selected("agy")
    assert len(reconnects) == 2
    drawer._input.setPlainText("Keep this draft")
    drawer._input.submitted.emit("Keep this draft")
    assert drawer._input.toPlainText() == "Keep this draft"
    assert not drawer._send_btn.isEnabled()
    drawer.set_connection_state("connected")
    assert drawer._send_btn.isEnabled()
    drawer.close()


def test_drawer_auto_hides_only_when_inactive_and_no_approval_is_pending(qapp, monkeypatch) -> None:
    drawer = ChatDrawer(list(BUILTIN_AGENTS), "agy", "", reduced_motion=True)
    monkeypatch.setattr(drawer, "isActiveWindow", lambda: False)
    drawer.show()
    assert drawer._auto_hide_timer.isActive()
    assert drawer._auto_hide_timer.interval() == 4000
    drawer._input.setPlainText("Keep this draft")

    monkeypatch.setattr(drawer, "isActiveWindow", lambda: True)
    drawer.changeEvent(QEvent(QEvent.Type.ActivationChange))
    assert not drawer._auto_hide_timer.isActive()
    drawer._hide_if_inactive()
    assert drawer.isVisible()

    monkeypatch.setattr(drawer, "isActiveWindow", lambda: False)
    drawer.changeEvent(QEvent(QEvent.Type.ActivationChange))
    assert drawer._auto_hide_timer.isActive()
    drawer.show_permission_request("sample", "Edit a file", "edit", [])
    drawer._hide_if_inactive()
    assert drawer.isVisible()
    drawer.clear_permission()
    assert drawer._auto_hide_timer.isActive()
    drawer._hide_if_inactive()
    assert not drawer.isVisible()
    assert drawer._input.toPlainText() == "Keep this draft"


def test_drop_shadow_flag_updates_open_drawer(qapp, monkeypatch) -> None:
    drawer = ChatDrawer(list(BUILTIN_AGENTS), "agy", "")
    drawer.show()
    monkeypatch.setattr(drawer, "isActiveWindow", lambda: True)
    drawer.set_drop_shadow(False)
    assert drawer.isVisible()
    assert drawer.windowFlags() & Qt.WindowType.NoDropShadowWindowHint
    assert not drawer.settings._drop_shadow_cb.isChecked()
    assert not drawer._auto_hide_timer.isActive()
    drawer.set_drop_shadow(True)
    assert drawer.isVisible()
    assert not drawer.windowFlags() & Qt.WindowType.NoDropShadowWindowHint
    assert drawer.settings._drop_shadow_cb.isChecked()
    assert not drawer._auto_hide_timer.isActive()
    drawer.close()


def test_permission_request_cancels_inflight_fade_out(qapp) -> None:
    from PyQt6.QtTest import QTest

    drawer = ChatDrawer(list(BUILTIN_AGENTS), "agy", "", reduced_motion=False)
    drawer.show()
    drawer.hide_animated()
    assert drawer._animator._hiding is True
    drawer.show_permission_request("late-req", "Run command", "execute", [])
    assert drawer._animator._hiding is False
    assert drawer.windowOpacity() == 1.0
    QTest.qWait(180)
    assert drawer.isVisible()
    assert drawer.windowOpacity() == 1.0
    drawer.close()


def test_review_from_settings_restores_focusable_widget_after_notice_click(qapp) -> None:
    drawer = ChatDrawer(list(BUILTIN_AGENTS), "agy", "")
    drawer.show()
    drawer.set_connection_state("connected")
    drawer.open_settings()
    drawer.settings.open_section("Voice")
    drawer.show_permission_request("req-focus", "Edit file", "edit", [])
    # Clicking the notice button moves focus to _notice_action outside _settings_page
    drawer._notice_action.setFocus()
    drawer._notice_action.click()
    drawer._decide_permission(None)
    assert drawer._pages.currentWidget() is drawer.settings
    assert drawer.focusWidget() is drawer.settings._index_btn
    assert drawer.settings._index_btn.focusPolicy() != Qt.FocusPolicy.NoFocus
    drawer.close()


def test_animations_widgets_tick_cleanly(qapp) -> None:
    grid = DotMatrixGrid()
    grid.set_active(True)
    grid._tick()
    assert grid._phase_s > 0.0
    grid.set_active(False)

    loader = LoadingIndicator()
    loader.set_loading(True, "Waiting for permission...")
    assert loader.isVisible() is True
    assert loader._status.text() == "Waiting for permission..."

    spinner = SpinningIcon()
    spinner.set_icon("loader", "#7f6df2", True)
    spinner._advance()
    assert spinner._angle == 6.0
    spinner.set_icon("check", "#4ade80", False)
    assert spinner._angle == 0.0


def test_drawer_model_and_reasoning_config_pills(qapp) -> None:
    drawer = ChatDrawer(agents=list(BUILTIN_AGENTS), current_agent_id="agy", workspace=".")
    captured: list[tuple[str, str]] = []
    drawer.config_option_selected.connect(lambda cid, val: captured.append((cid, val)))

    drawer.set_config_options(
        [
            ConfigOptionInfo(
                id="mode",
                name="Mode",
                category="mode",
                current_value="default",
                choices=(ConfigChoiceInfo("default", "Default"), ConfigChoiceInfo("plan", "Plan")),
            ),
            ConfigOptionInfo(
                id="thought_level",
                name="Thinking Level",
                category="thought_level",
                current_value="medium",
                choices=(
                    ConfigChoiceInfo("low", "Low"),
                    ConfigChoiceInfo("medium", "Medium"),
                    ConfigChoiceInfo("high", "High"),
                ),
            ),
            ConfigOptionInfo(
                id="model",
                name="Model",
                category="model",
                current_value="gemini-4-argon",
                choices=(
                    ConfigChoiceInfo("gemini-4-argon", "Gemini 4 Argon"),
                    ConfigChoiceInfo("gemini-4-hydrogen", "Gemini 4 Hydrogen"),
                ),
            ),
        ]
    )

    assert len(drawer._selector_pills) == 3
    # Sorted: Model, Thinking Level, Mode
    assert drawer._selector_pills[0].text() == "Gemini 4 Argon"
    assert drawer._selector_pills[1].text() == "Medium"
    assert drawer._selector_pills[2].text() == "Default"

    drawer._selector_pills[0]._pick("gemini-4-hydrogen")
    drawer._selector_pills[1]._pick("high")
    assert captured == [("model", "gemini-4-hydrogen"), ("thought_level", "high")]


def test_drawer_light_dark_and_blur_switching(qapp) -> None:
    drawer = ChatDrawer(
        agents=list(BUILTIN_AGENTS),
        current_agent_id="agy",
        workspace=".",
        theme_mode="light",
        backdrop_blur=True,
    )
    assert get_palette() == LIGHT_PALETTE
    assert drawer._backdrop_blur is True

    drawer.set_connection_state("connected")
    drawer._submit_prompt("Hello light world")
    drawer.append_message_chunk("Hi there!")
    drawer._render_transcript_now()

    drawer.set_theme("dark", False)
    assert get_palette() == DARK_PALETTE
    assert drawer._backdrop_blur is False
    drawer.close()
    drawer.deleteLater()
    qapp.processEvents()


def test_pocket_menu_matches_blur_setting(qapp) -> None:
    from PyQt6.QtCore import QPoint
    from pocket_agent.ui.menus import PocketMenu
    from pocket_agent.ui.theme import set_active_theme

    set_active_theme("dark", blur=True)
    m_blur = PocketMenu()
    m_blur.add_item("New Chat", icon="plus")
    sub = m_blur.add_submenu("Appearance", "sun")
    sub.add_check("Dark", True)
    m_blur.popup(QPoint(100, 100))
    assert m_blur.isVisible()
    m_blur.hide()
    m_blur.deleteLater()

    set_active_theme("light", blur=False)
    m_solid = PocketMenu()
    m_solid.add_item("New Chat", icon="plus")
    assert m_solid._native_blur is False
    m_solid.popup(QPoint(100, 100))
    assert m_solid.isVisible()
    m_solid.hide()
    m_solid.deleteLater()
    qapp.processEvents()



