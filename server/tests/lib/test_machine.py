"""Tests for Machine sandbox orchestration."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from druids_server.lib.machine import BRIDGE_PORT, ExecError, Machine


class TestMachineProperties:
    def test_instance_id_none_before_sandbox(self):
        m = Machine(snapshot_id="snap_1")
        assert m.instance_id is None


class TestMachineExec:
    @pytest.mark.asyncio
    async def test_exec_requires_sandbox(self):
        m = Machine(snapshot_id="snap_1")
        with pytest.raises(RuntimeError, match="no running sandbox"):
            await m.exec("echo hi")

    @pytest.mark.asyncio
    async def test_exec_delegates_to_sandbox(self):
        sandbox = MagicMock()
        sandbox.exec = AsyncMock(return_value=MagicMock(ok=True, exit_code=0, stdout="ok", stderr="", command="x"))
        m = Machine(snapshot_id="snap_1", sandbox=sandbox)

        result = await m.exec("echo hi", user="root")
        assert result.ok
        sandbox.exec.assert_called_once_with("echo hi", user="root")

    @pytest.mark.asyncio
    async def test_exec_raises_execerror(self):
        sandbox = MagicMock()
        sandbox.exec = AsyncMock(return_value=MagicMock(ok=False, exit_code=1, stdout="", stderr="bad", command="bad"))
        m = Machine(snapshot_id="snap_1", sandbox=sandbox)

        with pytest.raises(ExecError):
            await m.exec("bad")


class TestEnsureBridge:
    @pytest.mark.asyncio
    async def test_ensure_bridge_sets_relay_identity(self, tmp_path):
        (tmp_path / "bridge.py").write_text("# bridge")
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'bridge'")
        (tmp_path / "uv.lock").write_text("# lock")

        sandbox = MagicMock()
        sandbox.instance_id = "instance_1"
        sandbox.write_file = AsyncMock()
        m = Machine(snapshot_id="snap_1", sandbox=sandbox)

        config = MagicMock()
        config.to_bridge_start.return_value = {"command": "test"}

        async def mock_exec(command, **kwargs):
            if f"http://127.0.0.1:{BRIDGE_PORT}/status" in command:
                return MagicMock(ok=True, exit_code=0, stdout="", stderr="", command=command)
            return MagicMock(ok=True, exit_code=0, stdout="", stderr="", command=command)

        with (
            patch("druids_server.lib.machine.BRIDGE_DIR", tmp_path),
            patch.object(m, "_deploy_bridge", new_callable=AsyncMock),
            patch.object(m, "exec", side_effect=mock_exec),
        ):
            bridge_id, bridge_token = await m.ensure_bridge(
                config,
                working_directory="/home/agent/repo",
            )

        assert bridge_id == f"instance_1:{BRIDGE_PORT}"
        assert bridge_token is not None
        config.to_bridge_start.assert_called_once_with("/home/agent/repo")

    @pytest.mark.asyncio
    async def test_concurrent_bridge_calls_get_unique_ports(self):
        """Two concurrent ensure_bridge calls must not get the same port."""
        sandbox = MagicMock()
        sandbox.instance_id = "instance_1"
        sandbox.write_file = AsyncMock()
        m = Machine(snapshot_id="snap_1", sandbox=sandbox)

        config = MagicMock()
        config.to_bridge_start.return_value = {"command": "test"}

        async def mock_exec(command, **kwargs):
            if "/status" in command:
                return MagicMock(ok=True, exit_code=0, stdout="", stderr="", command=command)
            return MagicMock(ok=True, exit_code=0, stdout="", stderr="", command=command)

        # First bridge to establish _bridge_deployed = True
        with (
            patch.object(m, "_deploy_bridge", new_callable=AsyncMock),
            patch.object(m, "exec", side_effect=mock_exec),
        ):
            await m.ensure_bridge(config)

        # Now launch two bridges concurrently
        with patch.object(m, "exec", side_effect=mock_exec):
            id1, id2 = await asyncio.gather(
                m.ensure_bridge(config),
                m.ensure_bridge(config),
            )

        port1 = int(id1[0].split(":")[-1])
        port2 = int(id2[0].split(":")[-1])
        assert port1 != port2, f"Both bridges got port {port1}"

    @pytest.mark.asyncio
    async def test_ensure_bridge_writes_payload_via_write_file(self):
        """ensure_bridge uses sandbox.write_file instead of a heredoc to pass the payload."""
        sandbox = MagicMock()
        sandbox.instance_id = "instance_1"
        sandbox.write_file = AsyncMock()
        m = Machine(snapshot_id="snap_1", sandbox=sandbox)

        # Payload containing the old heredoc delimiter to prove injection is impossible
        config = MagicMock()
        config.to_bridge_start.return_value = {"command": "test", "note": "EOF\ninjected"}

        exec_commands: list[str] = []

        async def mock_exec(command, **kwargs):
            exec_commands.append(command)
            if "/status" in command:
                return MagicMock(ok=True, exit_code=0, stdout="", stderr="", command=command)
            return MagicMock(ok=True, exit_code=0, stdout="", stderr="", command=command)

        with (
            patch.object(m, "_deploy_bridge", new_callable=AsyncMock),
            patch.object(m, "exec", side_effect=mock_exec),
        ):
            await m.ensure_bridge(config)

        # Verify write_file was called with the JSON payload
        sandbox.write_file.assert_called_once()
        path_arg, content_arg = sandbox.write_file.call_args[0]
        assert path_arg == f"/tmp/bridge_start_{BRIDGE_PORT}.json"
        parsed = json.loads(content_arg)
        assert parsed["command"] == "test"
        assert parsed["note"] == "EOF\ninjected"

        # No exec command should contain a heredoc
        for cmd in exec_commands:
            assert "<<" not in cmd, f"Heredoc found in exec command: {cmd}"
