"""Tests for ACP agent registry and command resolution."""

from pathlib import Path

from pocket_agent.agents import AgentRegistry, AgentSpec


def test_builtin_and_custom_agents_merge() -> None:
    reg = AgentRegistry(
        custom_entries=[
            {
                "id": "my-agent",
                "name": "My Custom ACP",
                "command": "python",
                "args": ["-m", "my_acp"],
            }
        ]
    )
    assert reg.get("agy") is not None
    custom = reg.get("my-agent")
    assert custom is not None
    assert custom.args == ("-m", "my_acp")


def test_resolve_executable_handles_explicit_file(tmp_path: Path) -> None:
    fake_exe = tmp_path / "dummy-agent.exe"
    fake_exe.write_text("", encoding="utf-8")
    spec = AgentSpec(id="dummy", name="Dummy", command=str(fake_exe))
    assert spec.resolve_executable() == str(fake_exe)
    assert spec.is_available() is True


def test_missing_executable_returns_none() -> None:
    spec = AgentSpec(id="missing", name="Missing", command="definitely_not_a_real_binary_xyz_123")
    assert spec.resolve_executable() is None
    assert spec.is_available() is False
