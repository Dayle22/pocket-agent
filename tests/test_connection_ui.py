"""Tests for ACP connection error formatting, Reconnect actions, and the AI Setup Guide."""

from __future__ import annotations

import asyncio

from pocket_agent.acp_bridge import _format_connection_error
from pocket_agent.agents import BUILTIN_AGENTS, AgentSpec
from pocket_agent.ui.drawer import ChatDrawer
from pocket_agent.ui.overlays import ErrorBannerWidget
from pocket_agent.ui.setup_guide import AgentSetupDialog


def test_format_connection_error_includes_stderr_and_auth_tip() -> None:
    spec = AgentSpec(id="gemini", name="Gemini CLI", command="gemini", args=("--acp",))
    msg = _format_connection_error(
        spec,
        RuntimeError("Connection closed"),
        ["Checking credentials...", "Error: Unauthorized - please login first"],
    )
    assert "Connection closed" in msg
    assert "CLI output:" in msg
    assert "Unauthorized" in msg
    assert "run `gemini` in PowerShell once to sign in" in msg


def test_format_connection_error_timeout_tip() -> None:
    spec = AgentSpec(id="agy", name="Antigravity CLI", command="agy", args=("acp",))
    msg = _format_connection_error(spec, asyncio.TimeoutError(), [])
    assert "timed out" in msg
    assert "run `agy` in PowerShell" in msg


def test_error_banner_reconnect_and_setup_actions(qapp) -> None:
    banner = ErrorBannerWidget()
    reconnects: list[bool] = []
    guides: list[bool] = []
    banner.reconnect_requested.connect(lambda: reconnects.append(True))
    banner.setup_guide_requested.connect(lambda: guides.append(True))

    banner.show_banner("Copied", "Markdown copied", variant="info")
    assert banner._actions_row.isHidden() is True

    banner.show_banner("Connection Failed", "Could not start", variant="error", show_actions=True)
    assert banner._actions_row.isHidden() is False

    banner._reconnect_btn.click()
    assert reconnects == [True]
    assert banner.isVisible() is False

    banner.show_banner("Agent Disconnected", "Exited", variant="error", show_actions=True)
    banner._guide_btn.click()
    assert guides == [True]

    banner.deleteLater()
    qapp.processEvents()


def test_drawer_connection_error_and_reselect_when_disconnected(qapp) -> None:
    drawer = ChatDrawer(agents=list(BUILTIN_AGENTS), current_agent_id="agy", workspace=".")
    reconnects: list[bool] = []
    drawer.reconnect_requested.connect(lambda: reconnects.append(True))

    drawer.set_status("Connection failed", "#f87171")
    drawer.show_connection_error("Connection Failed", "No CLI found")
    assert drawer._error_banner._actions_row.isHidden() is False

    # Clicking the already-selected agent while disconnected retries the connection
    drawer._on_agent_selected("agy")
    assert reconnects == [True]

    drawer.deleteLater()
    qapp.processEvents()


def test_agent_setup_dialog_lists_agents_and_emits_connect(qapp, tmp_path) -> None:
    fake_exe = tmp_path / "agy.exe"
    fake_exe.write_text("stub", encoding="utf-8")
    specs = [
        AgentSpec(id="agy", name="Antigravity CLI", command=str(fake_exe), args=("acp",)),
        AgentSpec(id="missing", name="Missing CLI", command="definitely-missing-cli-xyz"),
    ]
    dlg = AgentSetupDialog(agents=specs, current_agent_id="agy", log_file=tmp_path / "app.log")
    chosen: list[str] = []
    dlg.connect_agent_requested.connect(chosen.append)

    dlg._connect_and_close("agy")
    assert chosen == ["agy"]

    dlg.deleteLater()
    qapp.processEvents()
