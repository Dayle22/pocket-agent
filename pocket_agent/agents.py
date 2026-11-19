"""ACP agent registry and Windows executable resolution."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class AgentSpec:
    """Definition of an ACP-compatible AI CLI agent."""

    id: str
    name: str
    command: str
    args: tuple[str, ...] = ()
    description: str = ""
    env: dict[str, str] = field(default_factory=dict)

    def resolve_executable(self) -> str | None:
        """Resolve the runnable executable path on Windows/macOS/Linux.

        Prefers .exe, .cmd, .bat over .ps1 so asyncio.create_subprocess_exec can launch directly.
        """
        cmd_path = Path(self.command)
        if cmd_path.is_file():
            return str(cmd_path)

        found = shutil.which(self.command)
        if found:
            if found.lower().endswith(".ps1"):
                cmd_sibling = Path(found).with_suffix(".cmd")
                if cmd_sibling.is_file():
                    return str(cmd_sibling)
                exe_sibling = Path(found).with_suffix(".exe")
                if exe_sibling.is_file():
                    return str(exe_sibling)
            return found

        # Fallback scan of common user-local npm/app bin directories on Windows
        if os.name == "nt":
            extra_dirs = [
                Path.home() / "AppData" / "Local" / "agy" / "bin",
                Path.home() / "AppData" / "Roaming" / "npm",
            ]
            for directory in extra_dirs:
                for ext in (".exe", ".cmd", ".bat"):
                    candidate = directory / f"{self.command}{ext}"
                    if candidate.is_file():
                        return str(candidate)
        return None

    def is_available(self) -> bool:
        return self.resolve_executable() is not None


BUILTIN_AGENTS: tuple[AgentSpec, ...] = (
    AgentSpec(
        id="agy",
        name="Antigravity CLI",
        command="agy",
        args=("acp",),
        description="Google Antigravity ACP server (agy acp)",
    ),
    AgentSpec(
        id="gemini",
        name="Gemini CLI",
        command="gemini",
        args=("--acp",),
        description="Google Gemini CLI in ACP mode",
    ),
    AgentSpec(
        id="codex",
        name="Codex (ACP)",
        command="codex-acp",
        args=(),
        description="OpenAI Codex via codex-acp adapter",
    ),
    AgentSpec(
        id="opencode",
        name="OpenCode",
        command="opencode",
        args=("acp",),
        description="OpenCode ACP server",
    ),
    AgentSpec(
        id="claude",
        name="Claude Code (ACP)",
        command="claude-agent-acp",
        args=(),
        description="Anthropic Claude Code via ACP adapter",
    ),
)


class AgentRegistry:
    """Combines built-in ACP presets with user-configured custom agents."""

    def __init__(self, custom_entries: Iterable[dict] = ()) -> None:
        self._agents: dict[str, AgentSpec] = {spec.id: spec for spec in BUILTIN_AGENTS}
        for item in custom_entries:
            try:
                spec = AgentSpec(
                    id=str(item["id"]),
                    name=str(item["name"]),
                    command=str(item["command"]),
                    args=tuple(str(a) for a in item.get("args", [])),
                    description=str(item.get("description", "")),
                    env={str(k): str(v) for k, v in item.get("env", {}).items()},
                )
                self._agents[spec.id] = spec
            except (KeyError, TypeError):
                continue

    def all(self) -> list[AgentSpec]:
        return list(self._agents.values())

    def get(self, agent_id: str) -> AgentSpec | None:
        return self._agents.get(agent_id)

    def default_available(self) -> AgentSpec:
        for spec in self._agents.values():
            if spec.is_available():
                return spec
        return BUILTIN_AGENTS[0]
