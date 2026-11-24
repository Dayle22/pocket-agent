"""Tests for ACP connection error formatting, Reconnect actions, and the AI Setup Guide."""

from __future__ import annotations

import asyncio
import contextlib
import time
from types import SimpleNamespace

from pocket_agent.acp_bridge import AcpBridge, _format_connection_error
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


def _pump_until(qapp, predicate, timeout_s: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def test_switching_agent_during_slow_handshake_cancels_previous_attempt(qapp, monkeypatch, tmp_path) -> None:
    import pocket_agent.acp_bridge as acp_mod

    exe_a = tmp_path / "agent-a.exe"
    exe_b = tmp_path / "agent-b.exe"
    exe_a.write_text("stub", encoding="utf-8")
    exe_b.write_text("stub", encoding="utf-8")
    spec_a = AgentSpec(id="agent-a", name="Agent A", command=str(exe_a))
    spec_b = AgentSpec(id="agent-b", name="Agent B", command=str(exe_b))

    @contextlib.asynccontextmanager
    async def _fake_spawn(_client, exe, *_args, **_kwargs):
        is_a = exe == str(exe_a)
        stop_proc = asyncio.Event()

        class _FakeProc:
            stderr = None

            async def wait(self) -> int:
                await stop_proc.wait()
                return 0

        class _FakeConn:
            async def initialize(self, **_kw):
                if is_a:
                    await asyncio.Event().wait()
                return SimpleNamespace(
                    agent_info=SimpleNamespace(name="Agent B", version="1.0"),
                )

            async def new_session(self, **_kw):
                return SimpleNamespace(
                    session_id="sess-b",
                    modes=None,
                    config_options=None,
                )

        try:
            yield _FakeConn(), _FakeProc()
        finally:
            stop_proc.set()

    monkeypatch.setattr(acp_mod, "_spawn_agent_process", _fake_spawn)

    bridge = AcpBridge()
    connecting: list[str] = []
    connected: list[str] = []
    failed: list[str] = []
    disconnected: list[str] = []
    bridge.connecting.connect(connecting.append)
    bridge.connected.connect(lambda aid, *_rest: connected.append(aid))
    bridge.connection_failed.connect(failed.append)
    bridge.disconnected.connect(disconnected.append)

    try:
        bridge.connect_agent(spec_a, str(tmp_path))
        assert _pump_until(qapp, lambda: "Agent A" in connecting)

        bridge.connect_agent(spec_b, str(tmp_path))
        assert _pump_until(qapp, lambda: "agent-b" in connected)
        qapp.processEvents()

        assert connected == ["agent-b"]
        assert failed == []
        assert disconnected == []

        # Verify no lingering _connect_async tasks remain on the bridge loop
        fut = asyncio.run_coroutine_threadsafe(
            asyncio.sleep(0),
            bridge._loop,
        )
        fut.result(timeout=1.0)
        assert bridge._connect_task is not None and bridge._connect_task.done()
        assert bridge._session_task is not None and not bridge._session_task.done()
    finally:
        bridge.shutdown()


def test_shutdown_during_slow_handshake_exits_promptly(qapp, monkeypatch, tmp_path) -> None:
    import pocket_agent.acp_bridge as acp_mod

    exe_a = tmp_path / "slow-agent.exe"
    exe_a.write_text("stub", encoding="utf-8")
    spec_a = AgentSpec(id="slow-agent", name="Slow Agent", command=str(exe_a))

    @contextlib.asynccontextmanager
    async def _fake_spawn(_client, _exe, *_args, **_kwargs):
        class _FakeProc:
            stderr = None

            async def wait(self) -> int:
                await asyncio.Event().wait()
                return 0

        class _FakeConn:
            async def initialize(self, **_kw):
                await asyncio.Event().wait()

        yield _FakeConn(), _FakeProc()

    monkeypatch.setattr(acp_mod, "_spawn_agent_process", _fake_spawn)

    bridge = AcpBridge()
    connecting: list[str] = []
    bridge.connecting.connect(connecting.append)

    bridge.connect_agent(spec_a, str(tmp_path))
    assert _pump_until(qapp, lambda: "Slow Agent" in connecting)

    t0 = time.monotonic()
    bridge.shutdown()
    elapsed = time.monotonic() - t0
    assert elapsed < 2.5
    assert not bridge._thread.isRunning()
