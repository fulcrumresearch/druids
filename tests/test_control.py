"""Tests for the Unix-socket control server."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from ramure import LocalImage, Runtime
from ramure.control import socket_path
from ramure.process import ProcessScope, _current_process, agent, connect
from tests.helpers import disable_agent_launch


@pytest.fixture
def isolated_socket_dir(tmp_path_factory, monkeypatch):
    # Unix socket paths are limited to ~108 bytes on Linux, so we can't
    # use pytest's default tmp_path (typically very deep).
    import tempfile

    d = Path(tempfile.mkdtemp(prefix="drudtest-"))
    monkeypatch.setattr("ramure.control.SOCKET_DIR", d)
    yield d
    import shutil

    shutil.rmtree(d, ignore_errors=True)


async def _call(execution_id: str, msg: dict) -> dict:
    reader, writer = await asyncio.open_unix_connection(str(socket_path(execution_id)))
    writer.write((json.dumps(msg) + "\n").encode())
    await writer.drain()
    line = await reader.readline()
    writer.close()
    await writer.wait_closed()
    return json.loads(line)


def test_status_reports_agents_and_connections(isolated_socket_dir, tmp_path, monkeypatch):
    async def run_test():
        runtime = Runtime(log_dir=tmp_path / "logs")
        await runtime.start()
        scope = ProcessScope(parent=None, runtime=runtime, image=LocalImage(tmp_path))
        token = _current_process.set(scope)
        disable_agent_launch(runtime, monkeypatch)
        try:
            a = await agent("alpha")
            b = await agent("beta")
            connect(a, b)

            reply = await _call(runtime.execution_id, {"cmd": "status"})
            assert reply["execution_id"] == runtime.execution_id
            assert {ag["name"] for ag in reply["agents"]} == {"alpha", "beta"}
            assert reply["agents"][0]["machine"]["kind"] == "LocalMachine"
            assert {(c["a"], c["b"]) for c in reply["connections"]} == {("alpha", "beta"), ("beta", "alpha")}
        finally:
            await scope.cleanup()
            await runtime.close()
            _current_process.reset(token)

    asyncio.run(run_test())


def test_unknown_cmd_returns_error(isolated_socket_dir, tmp_path):
    async def run_test():
        runtime = Runtime(log_dir=tmp_path / "logs")
        await runtime.start()
        try:
            reply = await _call(runtime.execution_id, {"cmd": "nope"})
            assert "error" in reply
        finally:
            await runtime.close()

    asyncio.run(run_test())


def test_agent_cmd_unknown_agent(isolated_socket_dir, tmp_path):
    async def run_test():
        runtime = Runtime(log_dir=tmp_path / "logs")
        await runtime.start()
        try:
            reply = await _call(runtime.execution_id, {"cmd": "agent", "name": "ghost"})
            assert "error" in reply
        finally:
            await runtime.close()

    asyncio.run(run_test())


def test_socket_cleaned_up_on_close(isolated_socket_dir, tmp_path):
    async def run_test():
        runtime = Runtime(log_dir=tmp_path / "logs")
        await runtime.start()
        path = socket_path(runtime.execution_id)
        assert path.exists()
        await runtime.close()
        assert not path.exists()

    asyncio.run(run_test())


def test_ssh_credentials_local_machine_returns_null(isolated_socket_dir, tmp_path, monkeypatch):
    """LocalMachine has no SSH; the control server must pass the
    ``None`` through so the CLI can fall back to a local shell.
    """
    async def run_test():
        runtime = Runtime(log_dir=tmp_path / "logs")
        await runtime.start()
        scope = ProcessScope(parent=None, runtime=runtime, image=LocalImage(tmp_path))
        token = _current_process.set(scope)
        disable_agent_launch(runtime, monkeypatch)
        try:
            await agent("worker")
            reply = await _call(
                runtime.execution_id,
                {"cmd": "ssh_credentials", "name": "worker"},
            )
            assert reply == {"credentials": None}
        finally:
            await scope.cleanup()
            await runtime.close()
            _current_process.reset(token)

    asyncio.run(run_test())


def test_ssh_credentials_unknown_agent(isolated_socket_dir, tmp_path):
    async def run_test():
        runtime = Runtime(log_dir=tmp_path / "logs")
        await runtime.start()
        try:
            reply = await _call(
                runtime.execution_id,
                {"cmd": "ssh_credentials", "name": "ghost"},
            )
            assert "error" in reply
        finally:
            await runtime.close()

    asyncio.run(run_test())


def test_ssh_credentials_returns_creds_when_backend_supports(isolated_socket_dir, tmp_path, monkeypatch):
    """Backends like Morph override ``ssh_credentials()``; the control
    server must surface the full dict (keyed the same way the CLI expects).
    """
    from ramure.machines.base import SSHCredentials
    from ramure.machines.local import LocalMachine

    async def fake_creds(self):
        return SSHCredentials(
            host="ssh.example.com",
            port=2222,
            username="inst_123",
            private_key="-----BEGIN KEY-----\nabc\n",
            password=None,
        )

    monkeypatch.setattr(LocalMachine, "ssh_credentials", fake_creds)

    async def run_test():
        runtime = Runtime(log_dir=tmp_path / "logs")
        await runtime.start()
        scope = ProcessScope(parent=None, runtime=runtime, image=LocalImage(tmp_path))
        token = _current_process.set(scope)
        disable_agent_launch(runtime, monkeypatch)
        try:
            await agent("worker")
            reply = await _call(
                runtime.execution_id,
                {"cmd": "ssh_credentials", "name": "worker"},
            )
            assert reply["credentials"] == {
                "host": "ssh.example.com",
                "port": 2222,
                "username": "inst_123",
                "private_key": "-----BEGIN KEY-----\nabc\n",
                "password": None,
            }
        finally:
            await scope.cleanup()
            await runtime.close()
            _current_process.reset(token)

    asyncio.run(run_test())


def test_send_cmd_delivers_message(isolated_socket_dir, tmp_path, monkeypatch):
    async def run_test():
        runtime = Runtime(log_dir=tmp_path / "logs")
        await runtime.start()
        scope = ProcessScope(parent=None, runtime=runtime, image=LocalImage(tmp_path))
        token = _current_process.set(scope)
        disable_agent_launch(runtime, monkeypatch)
        try:
            ag = await agent("worker")

            reply = await _call(runtime.execution_id, {"cmd": "send", "agent": "worker", "text": "hi"})
            assert reply == {"ok": True}

            # The message should have been appended to the agent's log.
            types = [e.type for e in ag.log.stream.snapshot()]
            assert types[-1] == "message"
            assert ag.log.stream.snapshot()[-1].data["text"] == "hi"
        finally:
            await scope.cleanup()
            await runtime.close()
            _current_process.reset(token)

    asyncio.run(run_test())
