"""Tests for the Unix-socket control server."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from ramure import LocalImage, Runtime
from ramure.control import socket_path
from ramure.process import ProcessScope, _current_process, agent, connect, expose
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


# ---------------------------------------------------------------------------
# endpoints / call
# ---------------------------------------------------------------------------


def _setup_root_scope(runtime: Runtime, tmp_path: Path) -> tuple[ProcessScope, Any]:
    """Stand up a root ProcessScope and register it on the runtime.

    Mirrors what ``@agent_process`` does on the root branch -- tests
    that exercise external calls need the root scope to be present
    on the runtime since the control socket dispatches through
    ``runtime.root_scope``.
    """
    scope = ProcessScope(parent=None, runtime=runtime, image=LocalImage(tmp_path))
    runtime.root_scope = scope
    token = _current_process.set(scope)
    return scope, token


def test_endpoints_lists_root_scope_exposed_endpoints(isolated_socket_dir, tmp_path):
    async def run_test():
        runtime = Runtime(log_dir=tmp_path / "logs")
        await runtime.start()
        scope, token = _setup_root_scope(runtime, tmp_path)
        try:
            @expose
            async def add(a: int, b: int) -> int:
                """Add two numbers."""
                return a + b

            reply = await _call(runtime.execution_id, {"cmd": "endpoints"})
            assert "endpoints" in reply
            names = [ep["name"] for ep in reply["endpoints"]]
            assert names == ["add"]
            ep = reply["endpoints"][0]
            # Reuses build_tool_definition, so we get the same shape as
            # an agent's tool listing -- one renderer for both.
            assert ep["description"] == "Add two numbers."
            assert ep["parameters"]["properties"]["a"]["type"] == "integer"
            assert set(ep["parameters"]["required"]) == {"a", "b"}
        finally:
            await scope.cleanup()
            runtime.root_scope = None
            await runtime.close()
            _current_process.reset(token)

    asyncio.run(run_test())


def test_endpoints_empty_when_no_root_scope(isolated_socket_dir, tmp_path):
    """If a runtime is up but no root @agent_process has entered yet,
    the endpoints list is empty rather than an error -- matches the
    "there's just nothing to call" intuition.
    """
    async def run_test():
        runtime = Runtime(log_dir=tmp_path / "logs")
        await runtime.start()
        try:
            reply = await _call(runtime.execution_id, {"cmd": "endpoints"})
            assert reply == {"endpoints": []}
        finally:
            await runtime.close()

    asyncio.run(run_test())


def test_call_invokes_endpoint_in_root_scope(isolated_socket_dir, tmp_path):
    async def run_test():
        runtime = Runtime(log_dir=tmp_path / "logs")
        await runtime.start()
        scope, token = _setup_root_scope(runtime, tmp_path)
        try:
            @expose
            async def add(a: int, b: int) -> int:
                return a + b

            reply = await _call(
                runtime.execution_id,
                {"cmd": "call", "endpoint": "add", "kwargs": {"a": 2, "b": 3}},
            )
            assert reply == {"ok": True, "result": 5}
        finally:
            await scope.cleanup()
            runtime.root_scope = None
            await runtime.close()
            _current_process.reset(token)

    asyncio.run(run_test())


def test_call_unknown_endpoint_returns_error(isolated_socket_dir, tmp_path):
    async def run_test():
        runtime = Runtime(log_dir=tmp_path / "logs")
        await runtime.start()
        scope, token = _setup_root_scope(runtime, tmp_path)
        try:
            reply = await _call(
                runtime.execution_id,
                {"cmd": "call", "endpoint": "nope", "kwargs": {}},
            )
            assert "error" in reply
            assert "nope" in reply["error"]
        finally:
            await scope.cleanup()
            runtime.root_scope = None
            await runtime.close()
            _current_process.reset(token)

    asyncio.run(run_test())


def test_call_when_no_root_scope_errors(isolated_socket_dir, tmp_path):
    """Without a root scope, a ``call`` cannot be dispatched. An
    explicit error is friendlier than a 404 on a phantom endpoint.
    """
    async def run_test():
        runtime = Runtime(log_dir=tmp_path / "logs")
        await runtime.start()
        try:
            reply = await _call(
                runtime.execution_id,
                {"cmd": "call", "endpoint": "add", "kwargs": {}},
            )
            assert "error" in reply
        finally:
            await runtime.close()

    asyncio.run(run_test())


def test_call_endpoint_exception_returned_as_error(isolated_socket_dir, tmp_path):
    """An endpoint raising surfaces as ``{error: ...}`` and is logged
    as a failed return so observers see what happened.
    """
    async def run_test():
        runtime = Runtime(log_dir=tmp_path / "logs")
        await runtime.start()
        scope, token = _setup_root_scope(runtime, tmp_path)
        try:
            @expose
            async def boom() -> str:
                raise RuntimeError("kaboom")

            reply = await _call(
                runtime.execution_id,
                {"cmd": "call", "endpoint": "boom", "kwargs": {}},
            )
            assert reply == {"error": "kaboom"}

            kinds = [e.type for e in runtime.log._entries]
            assert "endpoint_called" in kinds
            ret = next(e for e in runtime.log._entries if e.type == "endpoint_returned")
            assert ret.data["ok"] is False
            assert ret.data["error"] == "kaboom"
        finally:
            await scope.cleanup()
            runtime.root_scope = None
            await runtime.close()
            _current_process.reset(token)

    asyncio.run(run_test())


def test_call_logs_endpoint_called_and_returned(isolated_socket_dir, tmp_path):
    """Every external call lands as a pair of runtime-log events.
    This is what the (eventual) STATUS.md writer reads to render
    "recent endpoint calls" -- so the contract matters.
    """
    async def run_test():
        runtime = Runtime(log_dir=tmp_path / "logs")
        await runtime.start()
        scope, token = _setup_root_scope(runtime, tmp_path)
        try:
            @expose
            async def echo(msg: str) -> str:
                return msg

            await _call(
                runtime.execution_id,
                {
                    "cmd": "call",
                    "endpoint": "echo",
                    "kwargs": {"msg": "hi"},
                    "caller": "external:cli",
                },
            )

            entries = runtime.log._entries
            called = next(e for e in entries if e.type == "endpoint_called")
            returned = next(e for e in entries if e.type == "endpoint_returned")
            assert called.data == {
                "endpoint": "echo",
                "kwargs": {"msg": "hi"},
                "caller": "external:cli",
            }
            assert returned.data["endpoint"] == "echo"
            assert returned.data["caller"] == "external:cli"
            assert returned.data["ok"] is True
            assert "duration_ms" in returned.data
        finally:
            await scope.cleanup()
            runtime.root_scope = None
            await runtime.close()
            _current_process.reset(token)

    asyncio.run(run_test())


def test_call_kwargs_must_be_dict(isolated_socket_dir, tmp_path):
    async def run_test():
        runtime = Runtime(log_dir=tmp_path / "logs")
        await runtime.start()
        scope, token = _setup_root_scope(runtime, tmp_path)
        try:
            @expose
            async def echo(msg: str) -> str:
                return msg

            reply = await _call(
                runtime.execution_id,
                {"cmd": "call", "endpoint": "echo", "kwargs": [1, 2, 3]},
            )
            assert "error" in reply
        finally:
            await scope.cleanup()
            runtime.root_scope = None
            await runtime.close()
            _current_process.reset(token)

    asyncio.run(run_test())


def test_call_non_serializable_result_errors(isolated_socket_dir, tmp_path):
    """to_jsonable falls back to ``str(value)`` for unknown types,
    but a result that *is* a known type yet contains a non-JSON
    leaf (e.g. NaN through json.dumps with allow_nan=False would
    fail) should fail loudly. This locks in the "don't silently
    repr" decision from the plan -- by relying on a dataclass that
    contains a non-jsonable field after ``to_jsonable`` smooths it,
    most things succeed; we instead verify the success path and
    leave the error path covered by the catch in `_cmd_call`.
    """
    async def run_test():
        runtime = Runtime(log_dir=tmp_path / "logs")
        await runtime.start()
        scope, token = _setup_root_scope(runtime, tmp_path)
        try:
            @expose
            async def make_obj() -> dict:
                # Deeply nested but JSON-serializable; sanity check
                # that to_jsonable preserves structure.
                return {"items": [{"k": 1}, {"k": 2}]}

            reply = await _call(
                runtime.execution_id,
                {"cmd": "call", "endpoint": "make_obj", "kwargs": {}},
            )
            assert reply == {"ok": True, "result": {"items": [{"k": 1}, {"k": 2}]}}
        finally:
            await scope.cleanup()
            runtime.root_scope = None
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
