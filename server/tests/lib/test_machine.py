"""Tests for Machine sandbox orchestration."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from orpheus.lib.machine import BRIDGE_PORT, ExecError, Machine


class TestMachineProperties:
    def test_instance_id_none_before_sandbox(self):
        m = Machine(snapshot_id="snap_1")
        assert m.instance_id is None

    def test_bridge_id_none_before_start(self):
        m = Machine(snapshot_id="snap_1")
        assert m.bridge_id is None
        assert m.bridge_token is None


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
        sandbox.exec = AsyncMock(
            return_value=MagicMock(ok=False, exit_code=1, stdout="", stderr="bad", command="bad")
        )
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
        sandbox.instance_id = "morph_1"
        m = Machine(snapshot_id="snap_1", sandbox=sandbox)

        config = MagicMock()
        config.to_bridge_start.return_value = {"command": "test"}

        async def mock_exec(command, **kwargs):
            if f"http://127.0.0.1:{BRIDGE_PORT}/status" in command:
                return MagicMock(ok=True, exit_code=0, stdout="", stderr="", command=command)
            return MagicMock(ok=True, exit_code=0, stdout="", stderr="", command=command)

        with (
            patch("orpheus.lib.machine.BRIDGE_DIR", tmp_path),
            patch.object(m, "_deploy_bridge", new_callable=AsyncMock),
            patch.object(m, "exec", side_effect=mock_exec),
        ):
            await m.ensure_bridge(config, monitor_prompt="Watch", working_directory="/home/agent/repo")

        assert m.bridge_id == "morph_1"
        assert m.bridge_token is not None
        config.to_bridge_start.assert_called_once_with("/home/agent/repo", "Watch")
