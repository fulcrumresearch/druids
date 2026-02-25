"""Tests for CodexAgent._write_config() -- codex-acp developer_instructions."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from orpheus.lib.agents.codex import CodexAgent


# Patch upload_skills for all tests -- config tests should not depend on skill files
pytestmark = pytest.mark.usefixtures("_no_skill_upload")


@pytest.fixture(autouse=False)
def _no_skill_upload():
    with patch("orpheus.lib.agents.codex.upload_skills", new_callable=AsyncMock):
        yield


def _make_codex_agent(system_prompt: str | None = None) -> CodexAgent:
    with patch("orpheus.lib.agents.codex.settings") as mock_settings:
        mock_key = MagicMock()
        mock_key.get_secret_value.return_value = "sk-test"
        mock_settings.openai_api_key = mock_key
        return CodexAgent(name="test-agent", system_prompt=system_prompt)


def _make_machine(exit_code: int = 0, stderr: str = "") -> MagicMock:
    machine = MagicMock()
    result = MagicMock()
    result.exit_code = exit_code
    result.stderr = stderr
    result.ok = exit_code == 0
    machine.exec = AsyncMock(return_value=result)
    return machine


class TestWriteCodexConfig:
    @pytest.mark.asyncio
    async def test_writes_developer_instructions(self):
        """Writes config.toml with developer_instructions when system_prompt is set."""
        agent = _make_codex_agent(system_prompt="Be thorough.")
        machine = _make_machine()

        await agent._write_config(machine)

        machine.exec.assert_called_once()
        cmd = machine.exec.call_args[0][0]
        assert "mkdir -p /home/agent/.codex" in cmd
        assert 'developer_instructions = """' in cmd
        assert "Be thorough." in cmd
        assert "CODEX_CFG_EOF" in cmd

    @pytest.mark.asyncio
    async def test_skips_when_no_system_prompt(self):
        """Does not write config.toml when system_prompt is None."""
        agent = _make_codex_agent(system_prompt=None)
        machine = _make_machine()

        await agent._write_config(machine)

        machine.exec.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_when_system_prompt_empty(self):
        """Does not write config.toml when system_prompt is empty string."""
        agent = _make_codex_agent(system_prompt="")
        machine = _make_machine()

        await agent._write_config(machine)

        machine.exec.assert_not_called()

    @pytest.mark.asyncio
    async def test_escapes_backslashes(self):
        """Backslashes in system_prompt are escaped for TOML."""
        agent = _make_codex_agent(system_prompt="path\\to\\file")
        machine = _make_machine()

        await agent._write_config(machine)

        cmd = machine.exec.call_args[0][0]
        assert "path\\\\to\\\\file" in cmd

    @pytest.mark.asyncio
    async def test_escapes_triple_quotes(self):
        """Triple quotes in system_prompt are escaped for TOML."""
        agent = _make_codex_agent(system_prompt='contains """triple quotes"""')
        machine = _make_machine()

        await agent._write_config(machine)

        cmd = machine.exec.call_args[0][0]
        # Original triple quotes should be escaped
        assert '"""triple quotes"""' not in cmd.split("developer_instructions")[1].split("CODEX_CFG_EOF")[0].replace(
            'developer_instructions = """', ""
        ).replace('\n"""', "")

    @pytest.mark.asyncio
    async def test_logs_warning_on_failure(self):
        """Logs warning when exec fails."""
        agent = _make_codex_agent(system_prompt="test")
        machine = _make_machine(exit_code=1, stderr="permission denied")

        # Should not raise, just log
        await agent._write_config(machine)

        machine.exec.assert_called_once()
