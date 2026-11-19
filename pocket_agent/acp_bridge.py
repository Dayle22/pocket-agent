"""Asyncio ACP client bridge running inside a dedicated Qt background thread."""

from __future__ import annotations

import asyncio
import asyncio.subprocess as aio_subprocess
from collections import deque
from collections.abc import AsyncIterator, Mapping
import contextlib
from contextlib import asynccontextmanager
from dataclasses import dataclass
import logging
import os
from pathlib import Path
import subprocess
import traceback
from typing import Any
import uuid

import acp
from acp.transports import default_environment
from acp.schema import (
    AgentMessageChunk,
    AgentPlanUpdate,
    AgentThoughtChunk,
    AllowedOutcome,
    AvailableCommandsUpdate,
    ConfigOptionUpdate,
    ContentToolCallContent,
    CurrentModeUpdate,
    DeniedOutcome,
    FileEditToolCallContent,
    Implementation,
    PermissionOption,
    ReadTextFileResponse,
    RequestPermissionResponse,
    SessionInfoUpdate,
    TextContentBlock,
    ToolCallProgress,
    ToolCallStart,
    ToolCallUpdate,
    UsageUpdate,
)
from PyQt6.QtCore import QObject, QThread, pyqtSignal

from pocket_agent import __version__
from pocket_agent.agents import AgentSpec


@dataclass(frozen=True)
class ModeInfo:
    """Normalized ACP session mode descriptor."""

    id: str
    name: str
    description: str = ""


@dataclass(frozen=True)
class PermissionOptionInfo:
    """Normalized ACP permission option."""

    option_id: str
    name: str
    kind: str


@dataclass(frozen=True)
class PlanEntryInfo:
    """Normalized ACP plan entry."""

    content: str
    status: str  # "pending" | "in_progress" | "completed"
    priority: str = "medium"


@dataclass(frozen=True)
class AvailableCommandInfo:
    """Normalized ACP slash command."""

    name: str
    description: str = ""
    hint: str = ""


@dataclass(frozen=True)
class UsageInfo:
    """Normalized ACP context usage update."""

    used: int
    size: int
    cost_amount: float | None = None
    cost_currency: str | None = None


@dataclass(frozen=True)
class ConfigChoiceInfo:
    """One selectable choice inside an ACP config option."""

    value: str
    name: str
    description: str = ""
    group: str = ""


@dataclass(frozen=True)
class ConfigOptionInfo:
    """Normalized ACP select config option (model, thought_level, mode, etc.)."""

    id: str
    name: str
    category: str
    current_value: str
    choices: tuple[ConfigChoiceInfo, ...]
    description: str = ""


def _normalize_config_options(raw_options: Any) -> list[ConfigOptionInfo]:
    """Normalize ACP SessionConfigOption list into select descriptors."""
    out: list[ConfigOptionInfo] = []
    for opt in raw_options or []:
        opt_type = str(getattr(opt, "type", "select") or "select")
        if opt_type != "select":
            continue
        choices: list[ConfigChoiceInfo] = []
        for entry in getattr(opt, "options", None) or []:
            group_opts = getattr(entry, "options", None)
            if group_opts is not None:
                group_name = str(getattr(entry, "name", "") or "")
                for sub in group_opts:
                    choices.append(
                        ConfigChoiceInfo(
                            value=str(sub.value),
                            name=str(sub.name),
                            description=str(getattr(sub, "description", "") or ""),
                            group=group_name,
                        )
                    )
            else:
                choices.append(
                    ConfigChoiceInfo(
                        value=str(entry.value),
                        name=str(entry.name),
                        description=str(getattr(entry, "description", "") or ""),
                    )
                )
        out.append(
            ConfigOptionInfo(
                id=str(opt.id),
                name=str(opt.name),
                category=str(getattr(opt, "category", "") or opt.id),
                current_value=str(getattr(opt, "current_value", "") or ""),
                choices=tuple(choices),
                description=str(getattr(opt, "description", "") or ""),
            )
        )
    return out


def _extract_tool_details(obj: Any) -> dict[str, Any]:
    """Extract command, locations, diffs, and text outputs from ACP tool call objects."""
    command = ""
    raw_in = getattr(obj, "raw_input", None)
    if isinstance(raw_in, dict) and isinstance(raw_in.get("command"), str):
        command = raw_in["command"]
        args = raw_in.get("args")
        if isinstance(args, list) and args:
            command = f"{command} {' '.join(str(a) for a in args)}"

    locations: list[str] = []
    for loc in getattr(obj, "locations", None) or []:
        path = getattr(loc, "path", None)
        if path:
            line = getattr(loc, "line", None)
            locations.append(f"{path}:{line}" if line is not None else str(path))

    diffs: list[dict[str, Any]] = []
    outputs: list[str] = []
    for item in getattr(obj, "content", None) or []:
        if isinstance(item, FileEditToolCallContent) or getattr(item, "type", None) == "diff":
            diffs.append(
                {
                    "path": str(getattr(item, "path", "") or ""),
                    "old_text": getattr(item, "old_text", None),
                    "new_text": str(getattr(item, "new_text", "") or ""),
                }
            )
        elif isinstance(item, ContentToolCallContent) or getattr(item, "type", None) == "content":
            inner = getattr(item, "content", None)
            txt = _extract_text(inner)
            if txt:
                outputs.append(txt)

    raw_out = getattr(obj, "raw_output", None)
    if isinstance(raw_out, str) and raw_out.strip():
        outputs.append(raw_out)
    elif isinstance(raw_out, dict):
        for key in ("stdout", "output", "text", "message"):
            val = raw_out.get(key)
            if isinstance(val, str) and val.strip():
                outputs.append(val)

    return {
        "command": command,
        "locations": locations,
        "diffs": diffs,
        "outputs": outputs,
    }


class _PocketAcpClient(acp.Client):
    """ACP Client implementation forwarding notifications and requests to the bridge."""

    def __init__(self, bridge: "AcpBridge") -> None:
        self._bridge = bridge

    async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
        if isinstance(update, AgentThoughtChunk):
            text = _extract_text(update.content)
            if text:
                self._bridge.thought_chunk.emit(text)
        elif isinstance(update, AgentMessageChunk):
            text = _extract_text(update.content)
            if text:
                self._bridge.message_chunk.emit(text)
        elif isinstance(update, ToolCallStart):
            details = _extract_tool_details(update)
            self._bridge.tool_started.emit(
                update.tool_call_id,
                update.title or getattr(update, "name", None) or "Tool Call",
                str(update.kind or "other"),
                str(update.status or "in_progress"),
                details,
            )
        elif isinstance(update, ToolCallProgress):
            details = _extract_tool_details(update)
            self._bridge.tool_updated.emit(
                update.tool_call_id,
                update.title or getattr(update, "name", None) or "",
                str(update.kind or ""),
                str(update.status or "in_progress"),
                details,
            )
        elif isinstance(update, AgentPlanUpdate):
            entries = [
                PlanEntryInfo(
                    content=str(e.content),
                    status=str(e.status or "pending"),
                    priority=str(getattr(e, "priority", "medium") or "medium"),
                )
                for e in (getattr(update, "entries", None) or [])
            ]
            self._bridge.plan_updated.emit(entries)
        elif isinstance(update, AvailableCommandsUpdate):
            cmds: list[AvailableCommandInfo] = []
            for c in getattr(update, "available_commands", None) or []:
                inp = getattr(c, "input", None)
                hint = str(getattr(inp, "hint", "") or "") if inp is not None else ""
                cmds.append(
                    AvailableCommandInfo(
                        name=str(c.name),
                        description=str(getattr(c, "description", "") or ""),
                        hint=hint,
                    )
                )
            self._bridge.commands_updated.emit(cmds)
        elif isinstance(update, UsageUpdate):
            cost = getattr(update, "cost", None)
            amount = float(cost.amount) if cost is not None and getattr(cost, "amount", None) is not None else None
            currency = str(cost.currency) if cost is not None and getattr(cost, "currency", None) else None
            self._bridge.usage_updated.emit(
                UsageInfo(
                    used=int(getattr(update, "used", 0) or 0),
                    size=int(getattr(update, "size", 0) or 0),
                    cost_amount=amount,
                    cost_currency=currency,
                )
            )
        elif isinstance(update, CurrentModeUpdate):
            if update.current_mode_id:
                self._bridge.mode_changed.emit(update.current_mode_id)
        elif isinstance(update, ConfigOptionUpdate):
            opts = _normalize_config_options(getattr(update, "config_options", None))
            self._bridge.config_options_updated.emit(opts)
        elif isinstance(update, SessionInfoUpdate):
            if update.title:
                self._bridge.session_title_changed.emit(update.title)

    async def request_permission(
        self,
        session_id: str,
        tool_call: ToolCallUpdate,
        options: list[PermissionOption],
        **kwargs: Any,
    ) -> RequestPermissionResponse:
        return await self._bridge._await_permission(tool_call, options)

    async def read_text_file(
        self,
        session_id: str,
        path: str,
        line: int | None = None,
        limit: int | None = None,
        **kwargs: Any,
    ) -> ReadTextFileResponse:
        target = Path(path)
        content = target.read_text(encoding="utf-8", errors="replace")
        if line is not None or limit is not None:
            lines = content.splitlines(keepends=True)
            start = max(0, (line or 1) - 1)
            end = start + limit if limit is not None else None
            content = "".join(lines[start:end])
        return ReadTextFileResponse(content=content)

    async def write_text_file(
        self,
        session_id: str,
        path: str,
        content: str,
        **kwargs: Any,
    ) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return None


def _extract_text(content_block: Any) -> str:
    if content_block is None:
        return ""
    text = getattr(content_block, "text", None)
    if isinstance(text, str):
        return text
    return ""


logger = logging.getLogger("pocket_agent.acp")
_CONNECT_TIMEOUT_SECONDS = 30.0


def _subprocess_creation_flags() -> int:
    """Return Windows creationflags that suppress child console windows."""
    if os.name == "nt":
        return int(getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000))
    return 0


@asynccontextmanager
async def _spawn_agent_process(
    to_client: Any,
    command: str,
    *args: str,
    env: Mapping[str, str] | None = None,
    cwd: str | Path | None = None,
    limit: int = 16 * 1024 * 1024,
    shutdown_timeout: float = 2.0,
) -> AsyncIterator[tuple[acp.ClientSideConnection, aio_subprocess.Process]]:
    """Spawn an ACP stdio subprocess without flashing a Windows console window."""
    merged_env = dict(default_environment())
    if env:
        merged_env.update(env)

    exec_kwargs: dict[str, Any] = {
        "stdin": aio_subprocess.PIPE,
        "stdout": aio_subprocess.PIPE,
        "stderr": aio_subprocess.PIPE,
        "env": merged_env,
        "cwd": str(cwd) if cwd is not None else None,
        "limit": limit,
    }
    flags = _subprocess_creation_flags()
    if flags:
        exec_kwargs["creationflags"] = flags

    process = await asyncio.create_subprocess_exec(command, *args, **exec_kwargs)
    if process.stdout is None or process.stdin is None:
        process.kill()
        await process.wait()
        raise RuntimeError("ACP agent subprocess requires stdout/stdin pipes")

    conn = acp.ClientSideConnection(to_client, process.stdin, process.stdout)
    try:
        yield conn, process
    finally:
        with contextlib.suppress(Exception):
            await conn.close()
        if process.stdin is not None:
            with contextlib.suppress(Exception):
                process.stdin.close()
                await asyncio.wait_for(process.stdin.wait_closed(), timeout=shutdown_timeout)
        try:
            await asyncio.wait_for(process.wait(), timeout=shutdown_timeout)
        except asyncio.TimeoutError:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=shutdown_timeout)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()


def _format_connection_error(
    spec: AgentSpec,
    exc: Exception | str,
    stderr_lines: list[str] | deque[str],
) -> str:
    """Format an actionable error message from an ACP failure and captured stderr."""
    if isinstance(exc, asyncio.TimeoutError):
        base = f"{spec.name} timed out after {int(_CONNECT_TIMEOUT_SECONDS)}s while starting."
    else:
        base = str(exc).strip() or exc.__class__.__name__

    tail = [line.strip() for line in stderr_lines if line and line.strip()][-4:]
    parts = [base]
    if tail:
        parts.append("CLI output: " + " | ".join(tail))

    combined = f"{base} {' '.join(tail)}".lower()
    if any(tok in combined for tok in ("login", "auth", "unauthorized", "api key", "token", "sign in")):
        parts.append(f"Tip: run `{spec.command}` in PowerShell once to sign in, then click Reconnect.")
    elif isinstance(exc, asyncio.TimeoutError):
        parts.append(f"Tip: run `{spec.command}` in PowerShell to check if it is waiting for login or confirmation.")
    return "\n".join(parts)


async def _drain_stderr(
    stream: asyncio.StreamReader | None,
    lines: deque[str],
    agent_id: str,
) -> None:
    """Collect recent stderr lines from the ACP child process for diagnostics."""
    if stream is None:
        return
    try:
        while True:
            raw = await stream.readline()
            if not raw:
                break
            text = raw.decode("utf-8", errors="replace").rstrip()
            if text:
                lines.append(text)
                logger.warning("[%s stderr] %s", agent_id, text)
    except asyncio.CancelledError:
        raise
    except Exception:
        pass


class AcpBridge(QObject):
    """Thread-safe Qt bridge managing a live ACP subprocess and session."""

    connecting = pyqtSignal(str)  # agent_name
    connected = pyqtSignal(str, str, str, str, object, str)  # agent_id, name, version, session_id, list[ModeInfo], current_mode
    disconnected = pyqtSignal(str)  # reason
    connection_failed = pyqtSignal(str)  # error message

    turn_started = pyqtSignal()
    thought_chunk = pyqtSignal(str)
    message_chunk = pyqtSignal(str)
    tool_started = pyqtSignal(str, str, str, str, object)  # tool_call_id, title, kind, status, details
    tool_updated = pyqtSignal(str, str, str, str, object)  # tool_call_id, title, kind, status, details
    turn_finished = pyqtSignal(str)  # stop_reason
    turn_failed = pyqtSignal(str)  # error message

    mode_changed = pyqtSignal(str)
    config_options_updated = pyqtSignal(object)  # list[ConfigOptionInfo]
    session_title_changed = pyqtSignal(str)
    plan_updated = pyqtSignal(object)  # list[PlanEntryInfo]
    commands_updated = pyqtSignal(object)  # list[AvailableCommandInfo]
    usage_updated = pyqtSignal(object)  # UsageInfo
    permission_requested = pyqtSignal(str, str, str, object, object)  # request_id, title, kind, options, details

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._thread = QThread(self)
        self._loop: asyncio.AbstractEventLoop | None = None

        self._conn: Any = None
        self._session_id: str | None = None
        self._session_task: asyncio.Task[None] | None = None
        self._prompt_task: asyncio.Task[None] | None = None
        self._stop_session_event: asyncio.Event | None = None
        self._pending_permissions: dict[str, asyncio.Future[RequestPermissionResponse]] = {}
        self._current_spec: AgentSpec | None = None
        self._current_cwd: str = os.getcwd()

        self._thread.run = self._run_loop  # type: ignore[method-assign]

    def start(self) -> None:
        if not self._thread.isRunning():
            self._thread.start()
            while self._loop is None:
                QThread.msleep(5)

    def shutdown(self) -> None:
        if self._loop is not None and self._loop.is_running():
            fut = asyncio.run_coroutine_threadsafe(self._shutdown_async(), self._loop)
            try:
                fut.result(timeout=3.0)
            except Exception:
                pass
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread.isRunning():
            self._thread.wait(3000)

    def _run_loop(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            loop.run_forever()
        finally:
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.close()

    async def _shutdown_async(self) -> None:
        await self._close_session_async()

    def connect_agent(self, spec: AgentSpec, cwd: str) -> None:
        self.start()
        assert self._loop is not None
        asyncio.run_coroutine_threadsafe(self._connect_async(spec, cwd), self._loop)

    def send_prompt(self, text: str) -> None:
        if not text.strip():
            return
        self.start()
        assert self._loop is not None
        asyncio.run_coroutine_threadsafe(self._send_prompt_async(text), self._loop)

    def cancel_turn(self) -> None:
        if self._loop is not None and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self._cancel_turn_async(), self._loop)

    def set_mode(self, mode_id: str) -> None:
        if self._loop is not None and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self._set_mode_async(mode_id), self._loop)

    def set_config_option(self, config_id: str, value: str) -> None:
        if self._loop is not None and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self._set_config_option_async(config_id, value), self._loop)

    def new_session(self, cwd: str) -> None:
        if self._current_spec is not None:
            self.connect_agent(self._current_spec, cwd)

    def resolve_permission(self, request_id: str, option_id: str | None) -> None:
        if self._loop is None or not self._loop.is_running():
            return
        self._loop.call_soon_threadsafe(self._resolve_permission_on_loop, request_id, option_id)

    def _resolve_permission_on_loop(self, request_id: str, option_id: str | None) -> None:
        fut = self._pending_permissions.pop(request_id, None)
        if fut is None or fut.done():
            return
        if option_id:
            resp = RequestPermissionResponse(outcome=AllowedOutcome(option_id=option_id))
        else:
            resp = RequestPermissionResponse(outcome=DeniedOutcome())
        fut.set_result(resp)

    async def _await_permission(
        self,
        tool_call: ToolCallUpdate,
        options: list[PermissionOption],
    ) -> RequestPermissionResponse:
        assert self._loop is not None
        request_id = str(uuid.uuid4())
        fut: asyncio.Future[RequestPermissionResponse] = self._loop.create_future()
        self._pending_permissions[request_id] = fut

        title = tool_call.title or getattr(tool_call, "name", None) or "Permission required"
        kind = str(tool_call.kind or "other")
        opts = [
            PermissionOptionInfo(option_id=o.option_id, name=o.name, kind=str(o.kind))
            for o in options
        ]
        details = _extract_tool_details(tool_call)
        self.permission_requested.emit(request_id, title, kind, opts, details)
        try:
            return await fut
        finally:
            self._pending_permissions.pop(request_id, None)

    async def _close_session_async(self) -> None:
        if self._prompt_task and not self._prompt_task.done():
            self._prompt_task.cancel()
            try:
                await self._prompt_task
            except BaseException:
                pass
        self._prompt_task = None

        for req_id, fut in list(self._pending_permissions.items()):
            if not fut.done():
                fut.set_result(RequestPermissionResponse(outcome=DeniedOutcome()))
        self._pending_permissions.clear()

        if self._stop_session_event is not None:
            self._stop_session_event.set()
        if self._session_task and not self._session_task.done():
            try:
                await asyncio.wait_for(self._session_task, timeout=4.0)
            except BaseException:
                self._session_task.cancel()
                try:
                    await self._session_task
                except BaseException:
                    pass
        self._session_task = None
        self._stop_session_event = None
        self._conn = None
        self._session_id = None

    async def _connect_async(self, spec: AgentSpec, cwd: str) -> None:
        await self._close_session_async()
        self._current_spec = spec
        self._current_cwd = cwd

        exe = spec.resolve_executable()
        if not exe:
            self.connection_failed.emit(
                f"'{spec.command}' was not found on PATH. Install it or choose another agent."
            )
            return

        self.connecting.emit(spec.name)
        stop_event = asyncio.Event()
        self._stop_session_event = stop_event
        ready_fut: asyncio.Future[None] = asyncio.get_running_loop().create_future()

        self._session_task = asyncio.create_task(
            self._session_runner(spec, exe, cwd, stop_event, ready_fut)
        )
        try:
            await ready_fut
        except Exception as exc:
            self.connection_failed.emit(str(exc))

    async def _session_runner(
        self,
        spec: AgentSpec,
        exe: str,
        cwd: str,
        stop_event: asyncio.Event,
        ready_fut: asyncio.Future[None],
    ) -> None:
        client = _PocketAcpClient(self)
        env = dict(os.environ)
        env.update(spec.env)
        stderr_lines: deque[str] = deque(maxlen=15)
        stderr_task: asyncio.Task[None] | None = None
        try:
            async with _spawn_agent_process(
                client,
                exe,
                *spec.args,
                cwd=cwd,
                env=env,
            ) as (conn, proc):
                self._conn = conn
                stderr_task = asyncio.create_task(_drain_stderr(proc.stderr, stderr_lines, spec.id))

                async def _handshake() -> tuple[Any, Any]:
                    init_resp = await conn.initialize(
                        protocol_version=acp.PROTOCOL_VERSION,
                        client_info=Implementation(name="pocket-agent", version=__version__),
                    )
                    sess_resp = await conn.new_session(cwd=cwd)
                    return init_resp, sess_resp

                init, sess = await asyncio.wait_for(_handshake(), timeout=_CONNECT_TIMEOUT_SECONDS)
                self._session_id = sess.session_id

                agent_info = getattr(init, "agent_info", None)
                agent_name = (getattr(agent_info, "name", None) or spec.name) if agent_info else spec.name
                agent_version = (getattr(agent_info, "version", None) or "") if agent_info else ""

                modes_obj = getattr(sess, "modes", None)
                modes_list: list[ModeInfo] = []
                current_mode = ""
                if modes_obj is not None:
                    current_mode = str(getattr(modes_obj, "current_mode_id", "") or "")
                    for m in getattr(modes_obj, "available_modes", []) or []:
                        modes_list.append(
                            ModeInfo(
                                id=str(m.id),
                                name=str(m.name),
                                description=str(getattr(m, "description", "") or ""),
                            )
                        )

                config_opts = _normalize_config_options(getattr(sess, "config_options", None))

                if not ready_fut.done():
                    ready_fut.set_result(None)
                self.connected.emit(
                    spec.id,
                    agent_name,
                    agent_version,
                    sess.session_id,
                    modes_list,
                    current_mode,
                )
                self.config_options_updated.emit(config_opts)

                stop_task = asyncio.create_task(stop_event.wait())
                proc_task = asyncio.create_task(proc.wait())
                try:
                    done, pending = await asyncio.wait(
                        {stop_task, proc_task},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    for p in pending:
                        p.cancel()
                        with contextlib.suppress(BaseException):
                            await p
                    if proc_task in done and not stop_event.is_set():
                        rc = proc_task.result()
                        err_msg = _format_connection_error(
                            spec,
                            f"{spec.name} exited unexpectedly (code {rc}).",
                            stderr_lines,
                        )
                        self.disconnected.emit(err_msg)
                        return
                finally:
                    for t in (stop_task, proc_task):
                        if not t.done():
                            t.cancel()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            msg = _format_connection_error(spec, exc, stderr_lines)
            logger.warning("ACP connection failed for %s: %s", spec.id, msg)
            if not ready_fut.done():
                ready_fut.set_exception(RuntimeError(msg))
            else:
                self.disconnected.emit(msg)
            return
        finally:
            if stderr_task is not None and not stderr_task.done():
                stderr_task.cancel()
                with contextlib.suppress(BaseException):
                    await stderr_task
            self._conn = None
            self._session_id = None

        self.disconnected.emit("Session closed")

    async def _send_prompt_async(self, text: str) -> None:
        if self._conn is None or not self._session_id:
            self.turn_failed.emit("Not connected to any ACP agent.")
            return
        if self._prompt_task and not self._prompt_task.done():
            self.turn_failed.emit("Agent is still working on the current turn.")
            return

        async def _run_turn() -> None:
            self.turn_started.emit()
            try:
                resp = await self._conn.prompt(
                    session_id=self._session_id,
                    prompt=[TextContentBlock(text=text)],
                )
                stop_reason = str(getattr(resp, "stop_reason", "end_turn") or "end_turn")
                self.turn_finished.emit(stop_reason)
            except asyncio.CancelledError:
                self.turn_finished.emit("cancelled")
            except Exception as exc:
                err = str(exc).strip() or traceback.format_exc(limit=2)
                self.turn_failed.emit(err)

        self._prompt_task = asyncio.create_task(_run_turn())

    async def _cancel_turn_async(self) -> None:
        for req_id, fut in list(self._pending_permissions.items()):
            if not fut.done():
                fut.set_result(RequestPermissionResponse(outcome=DeniedOutcome()))
        if self._conn is not None and self._session_id:
            try:
                await self._conn.cancel(session_id=self._session_id)
            except Exception:
                pass
        if self._prompt_task and not self._prompt_task.done():
            self._prompt_task.cancel()

    async def _set_mode_async(self, mode_id: str) -> None:
        if self._conn is not None and self._session_id and mode_id:
            try:
                await self._conn.set_session_mode(session_id=self._session_id, mode_id=mode_id)
                self.mode_changed.emit(mode_id)
            except Exception as exc:
                self.turn_failed.emit(f"Failed to switch mode: {exc}")

    async def _set_config_option_async(self, config_id: str, value: str) -> None:
        if self._conn is not None and self._session_id and config_id and value:
            try:
                resp = await self._conn.set_config_option(
                    config_id=config_id,
                    session_id=self._session_id,
                    value=value,
                )
                updated = _normalize_config_options(getattr(resp, "config_options", None))
                if updated:
                    self.config_options_updated.emit(updated)
            except Exception as exc:
                self.turn_failed.emit(f"Failed to update {config_id}: {exc}")
