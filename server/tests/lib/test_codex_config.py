"""Tests for CodexAgent._write_codex_config -- codex-acp developer_instructions."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from druids_server.lib.agents.codex import CodexAgent
from druids_server.lib.agents.config import AgentConfig


def _make_config(system_prompt: str | None = None) -> AgentConfig:
    return AgentConfig(
        name="test-agent",
        system_prompt=system_prompt,
        agent_type="codex",
    )


def _make_machine(exit_code: int = 0, stderr: str = "") -> MagicMock:
    machine = MagicMock()
    result = MagicMock()
    result.exit_code = exit_code
    result.stderr = stderr
    result.ok = exit_code == 0
    machine.exec = AsyncMock(return_value=result)
    machine.write_cli_config = AsyncMock()
    return machine


class TestWriteBackendConfig:
    @pytest.mark.asyncio
    async def test_writes_developer_instructions(self):
        """Writes config.toml with developer_instructions when system_prompt is set."""
        config = _make_config(system_prompt="Be thorough.")
        machine = _make_machine()

        await CodexAgent._write_codex_config(config, machine)

        machine.exec.assert_called_once()
        cmd = machine.exec.call_args[0][0]
        assert "mkdir -p /home/agent/.codex" in cmd
        assert 'developer_instructions = """' in cmd
        assert "Be thorough." in cmd
        assert "CODEX_CFG_EOF" in cmd

    @pytest.mark.asyncio
    async def test_skips_when_no_system_prompt(self):
        """Does not write config.toml when system_prompt is None."""
        config = _make_config(system_prompt=None)
        machine = _make_machine()

        await CodexAgent._write_codex_config(config, machine)

        machine.exec.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_when_system_prompt_empty(self):
        """Does not write config.toml when system_prompt is empty string."""
        config = _make_config(system_prompt="")
        machine = _make_machine()

        await CodexAgent._write_codex_config(config, machine)

        machine.exec.assert_not_called()

    @pytest.mark.asyncio
    async def test_escapes_backslashes(self):
        """Backslashes in system_prompt are escaped for TOML."""
        config = _make_config(system_prompt="path\\to\\file")
        machine = _make_machine()

        await CodexAgent._write_codex_config(config, machine)

        cmd = machine.exec.call_args[0][0]
        assert "path\\\\to\\\\file" in cmd

    @pytest.mark.asyncio
    async def test_escapes_triple_quotes(self):
        """Triple quotes in system_prompt are escaped for TOML."""
        config = _make_config(system_prompt='contains """triple quotes"""')
        machine = _make_machine()

        await CodexAgent._write_codex_config(config, machine)

        cmd = machine.exec.call_args[0][0]
        # Original triple quotes should be escaped
        assert '"""triple quotes"""' not in cmd.split("developer_instructions")[1].split("CODEX_CFG_EOF")[0].replace(
            'developer_instructions = """', ""
        ).replace('\n"""', "")

    @pytest.mark.asyncio
    async def test_logs_warning_on_failure(self):
        """Logs warning when exec fails."""
        config = _make_config(system_prompt="test")
        machine = _make_machine(exit_code=1, stderr="permission denied")

        # Should not raise, just log
        await CodexAgent._write_codex_config(config, machine)

        machine.exec.assert_called_once()

    @pytest.mark.asyncio
    async def test_skips_for_claude_agent_type(self):
        """Base Agent._prepare_machine does NOT call _write_codex_config."""
        config = AgentConfig(
            name="test",
            system_prompt="Be thorough.",
            agent_type="claude",
        )
        machine = _make_machine()

        from druids_server.lib.agents.base import Agent

        await Agent._prepare_machine(config, machine, is_shared=False)

        # write_cli_config is called, but exec (codex config) is not
        machine.write_cli_config.assert_called_once()
        machine.exec.assert_not_called()
