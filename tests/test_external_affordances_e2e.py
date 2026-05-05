"""End-to-end tests for the external-affordances surface.

Other test modules cover the pieces in isolation:

* ``test_control.py`` exercises the control socket protocol directly.
* ``test_cli.py`` exercises CLI helpers (argv parsing, signature
  rendering) without invoking the Typer app.
* ``test_status_file.py`` synthesizes log entries to drive the writer.

This module wires them together through the real public surface:
**run a Runtime, register an ``@expose``d endpoint, drive the actual
``ramure call`` CLI command via Typer, assert on stdout, on the
runtime log, and on STATUS.md.** This is what catches things like
"the dict key the CLI sends doesn't match the one the dispatcher
reads" -- the kind of bug all three unit suites would silently
miss.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import socket
import tempfile
import threading
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ramure import LocalImage, Runtime
from ramure import cli as ramure_cli
from ramure.control import socket_path
from ramure.process import ProcessScope, _current_process, expose


@pytest.fixture
def isolated_runtime_dirs(monkeypatch, tmp_path):
    """Pin the control socket dir AND the log dir to tmp paths.

    The socket dir has to be short (~108 bytes max on Linux) and the
    log dir has to be writable without polluting ``~/.ramure``. Both
    must point at locations the CLI *and* the runtime see, since the
    CLI imports its own ``SOCKET_DIR`` reference.
    """
    sock_dir = Path(tempfile.mkdtemp(prefix="ramure-e2e-"))
    monkeypatch.setattr("ramure.control.SOCKET_DIR", sock_dir)
    monkeypatch.setattr(ramure_cli, "SOCKET_DIR", sock_dir)
    log_dir = tmp_path / "logs"
    yield sock_dir, log_dir
    shutil.rmtree(sock_dir, ignore_errors=True)


def _run_runtime_in_thread(log_dir: Path, register_endpoints):
    """Boot a real Runtime + root ProcessScope on a background thread.

    The CLI uses *blocking* socket I/O against the control socket;
    if we ran the runtime on the test's event loop we'd deadlock the
    moment the CLI command fired. A separate thread with its own loop
    keeps the runtime serving while the test thread drives Typer.

    ``register_endpoints(scope)`` is called inside the runtime thread
    after the scope is current, so ``@expose`` lands on the right scope.
    Returns ``(stop, runtime_box)`` -- ``stop()`` cleanly tears the
    runtime down; ``runtime_box[0]`` is the live Runtime once ready.
    """
    ready = threading.Event()
    stopping = threading.Event()
    runtime_box: list[Runtime] = []

    def thread_main():
        async def lifecycle():
            rt = Runtime(log_dir=log_dir)
            await rt.start()
            scope = ProcessScope(parent=None, runtime=rt, image=LocalImage(log_dir))
            rt.root_scope = scope
            token = _current_process.set(scope)
            try:
                register_endpoints(scope)
                runtime_box.append(rt)
                ready.set()
                # Idle until the test signals stop.
                while not stopping.is_set():
                    await asyncio.sleep(0.02)
            finally:
                await scope.cleanup()
                rt.root_scope = None
                await rt.close()
                _current_process.reset(token)

        asyncio.run(lifecycle())

    t = threading.Thread(target=thread_main, daemon=True)
    t.start()
    assert ready.wait(timeout=5), "runtime did not come up in time"

    def stop():
        stopping.set()
        t.join(timeout=5)

    return stop, runtime_box


def _wait_for_socket(execution_id: str, timeout: float = 2.0) -> None:
    """Block until the control socket is accepting connections.

    ``Runtime.start()`` returns once the unix server is bound, but on
    a different thread the test may race the bind. Poll cheaply.
    """
    deadline = time.time() + timeout
    path = socket_path(execution_id)
    while time.time() < deadline:
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(0.2)
                s.connect(str(path))
                return
        except OSError:
            time.sleep(0.02)
    raise AssertionError(f"socket {path} never became reachable")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_ramure_call_invokes_endpoint_and_renders_result(isolated_runtime_dirs):
    """Drive the real ``ramure call`` command through Typer end to end.

    This is the load-bearing integration test for the PR: argv
    parsing, socket dispatch, endpoint invocation, and result
    rendering all on the actual public path. If any wire-format
    mismatch (CLI sends ``ap=...``, server reads ``endpoint=...``,
    etc.) creeps in, this test fails immediately.
    """
    _, log_dir = isolated_runtime_dirs

    def register(scope):
        @expose
        async def add_task(spec: str, priority: int = 0) -> dict:
            return {"spec": spec, "priority": priority, "queued": True}

    stop, runtime_box = _run_runtime_in_thread(log_dir, register)
    try:
        rt = runtime_box[0]
        _wait_for_socket(rt.execution_id)
        eid8 = rt.execution_id[:8]

        runner = CliRunner()
        # JSON-typed arg + string-fallback arg in one shot, exercising
        # _parse_kv_args + the actual call dispatch.
        result = runner.invoke(
            ramure_cli.app,
            ["call", "add_task", "spec=write tests", "priority=3", "--json", "-i", eid8],
        )
        assert result.exit_code == 0, f"stderr: {result.stderr if result.stderr_bytes else result.output}"
        # --json prints the result as JSON; parse and check structure.
        payload = json.loads(result.stdout)
        assert payload == {"spec": "write tests", "priority": 3, "queued": True}

        # The runtime log should now carry endpoint_called + endpoint_returned
        # entries with the correct caller tag, since this is what the
        # status file (and any future event tail) reads.
        kinds = [e.type for e in rt.log._entries]
        assert "endpoint_called" in kinds
        assert "endpoint_returned" in kinds
        called = next(e for e in rt.log._entries if e.type == "endpoint_called")
        assert called.data == {
            "endpoint": "add_task",
            "kwargs": {"spec": "write tests", "priority": 3},
            "caller": "external:cli",
        }
        returned = next(e for e in rt.log._entries if e.type == "endpoint_returned")
        assert returned.data["ok"] is True
        assert returned.data["caller"] == "external:cli"
    finally:
        stop()


def test_ramure_status_lists_affordances(isolated_runtime_dirs):
    """``ramure status`` must surface @expose'd endpoints with their
    rendered signatures + first-line docstring -- this is how an
    operator (or operator-agent) discovers what's callable.
    """
    _, log_dir = isolated_runtime_dirs

    def register(scope):
        @expose
        async def add_task(spec: str) -> str:
            """Append a task to the queue."""
            return spec

        @expose
        async def list_tasks() -> list:
            """Return the queued tasks."""
            return []

    stop, runtime_box = _run_runtime_in_thread(log_dir, register)
    try:
        rt = runtime_box[0]
        _wait_for_socket(rt.execution_id)

        runner = CliRunner()
        result = runner.invoke(ramure_cli.app, ["status", "-i", rt.execution_id[:8]])
        assert result.exit_code == 0
        out = result.stdout
        assert "Affordances:" in out
        assert "add_task(spec: string)" in out
        assert "Append a task to the queue." in out
        assert "list_tasks()" in out
        assert "Return the queued tasks." in out
    finally:
        stop()


def test_ramure_call_unknown_endpoint_exits_nonzero(isolated_runtime_dirs):
    """A typo in the endpoint name should fail loudly with a
    non-zero exit code, not silently print ``None``. Important for
    scripted callers (and for agents) that branch on exit status.
    """
    _, log_dir = isolated_runtime_dirs

    def register(scope):
        pass  # no endpoints

    stop, runtime_box = _run_runtime_in_thread(log_dir, register)
    try:
        rt = runtime_box[0]
        _wait_for_socket(rt.execution_id)

        runner = CliRunner()
        result = runner.invoke(
            ramure_cli.app, ["call", "nope", "-i", rt.execution_id[:8]]
        )
        assert result.exit_code != 0
        # Newer Typer versions merge stderr into output; either way the
        # endpoint name should appear in the user-facing error so the
        # operator (or agent) sees what was wrong.
        assert "nope" in result.output
    finally:
        stop()


def test_status_file_reflects_real_endpoint_call(isolated_runtime_dirs):
    """After a real ``ramure call``, STATUS.md's recent-calls section
    should reflect the call. Exercises the full chain:

        CLI -> socket -> _cmd_call -> runtime.log.emit
            -> StatusWriter subscriber -> render -> file

    This is the end-to-end story the PR actually promises. Each of
    its links has unit coverage; this verifies they line up.
    """
    _, log_dir = isolated_runtime_dirs

    def register(scope):
        @expose
        async def echo(msg: str) -> str:
            return msg

    stop, runtime_box = _run_runtime_in_thread(log_dir, register)
    try:
        rt = runtime_box[0]
        _wait_for_socket(rt.execution_id)

        runner = CliRunner()
        result = runner.invoke(
            ramure_cli.app,
            ["call", "echo", "msg=hello", "-i", rt.execution_id[:8]],
        )
        assert result.exit_code == 0

        status_path = log_dir / rt.execution_id / "STATUS.md"
        # The writer is on the runtime's loop (background thread).
        # endpoint_called/returned are structural events so it should
        # flush within a couple hundred ms; poll briefly.
        deadline = time.time() + 3.0
        text = ""
        while time.time() < deadline:
            text = status_path.read_text() if status_path.exists() else ""
            if "Recent endpoint calls" in text and "echo(msg='hello')" in text:
                break
            time.sleep(0.05)

        assert "## Affordances" in text
        assert "echo(msg: string)" in text
        assert "Recent endpoint calls" in text
        assert "echo(msg='hello')" in text
        assert "external:cli" in text
        assert "-> ok" in text
    finally:
        stop()
