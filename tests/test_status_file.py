"""Tests for the STATUS.md writer.

The writer is intentionally cheap to test: feed events into a real
runtime log, let the writer task render, read the file. We avoid
the full ``@agent_process`` machinery here so the writer's
behavior (debounce, atomic write, sections) is the only thing
exercised.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from ramure import LocalImage, Runtime
from ramure.process import ProcessScope, _current_process, agent, expose
from ramure.status import StatusWriter, _DEBOUNCE_S
from tests.helpers import disable_agent_launch


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


async def _make_runtime(tmp_path: Path) -> Runtime:
    rt = Runtime(log_dir=tmp_path / "logs")
    await rt.start()
    return rt


async def _wait_for(predicate, timeout: float = 2.0, step: float = 0.02) -> None:
    """Spin until ``predicate()`` is true. The status writer is async
    and debounced; tests would otherwise race the file."""
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(step)
    raise AssertionError("predicate never became true")


# ---------------------------------------------------------------------------
# basic shape
# ---------------------------------------------------------------------------


def test_status_file_appears_with_default_log_dir(tmp_path):
    """A runtime with a log dir should create STATUS.md immediately,
    so an early reader (an agent inspecting the dir on startup)
    sees something useful right away.
    """

    async def run():
        rt = await _make_runtime(tmp_path)
        try:
            status_path = tmp_path / "logs" / rt.execution_id / "STATUS.md"
            assert status_path.exists()
            text = status_path.read_text()
            assert "ramure execution" in text
            assert rt.execution_id in text
            # The orientation header explains what ramure is so an
            # agent dropped into the dir can act without prior
            # knowledge of the library.
            assert "ramure is a Python library" in text
        finally:
            await rt.close()

    asyncio.run(run())


def test_status_file_uses_default_log_dir_when_unspecified(tmp_path, monkeypatch):
    """``log_dir`` defaults to ``~/.ramure/logs`` -- so STATUS.md
    always has a home, even without an explicit log_dir. We
    redirect the default to a tempdir to verify the path resolves
    against it without touching the user's HOME.
    """
    monkeypatch.setattr(
        "ramure.runtime.DEFAULT_LOG_DIR", tmp_path / "fake-default"
    )

    async def run():
        rt = Runtime(log_dir=None)
        await rt.start()
        try:
            assert rt.status is not None
            # Status path lives under the redirected default, not HOME.
            assert str(tmp_path) in str(rt.status.path)
            assert rt.status.path.exists()
        finally:
            await rt.close()

    asyncio.run(run())


def test_status_renders_affordances_after_expose(tmp_path):
    """An ``@expose`` on the root scope must show up in STATUS.md so
    an external reader knows what they can call. This is the
    load-bearing affordance-discovery property.
    """

    async def run():
        rt = await _make_runtime(tmp_path)
        scope = ProcessScope(parent=None, runtime=rt, image=LocalImage(tmp_path))
        rt.root_scope = scope
        token = _current_process.set(scope)
        try:
            @expose
            async def add_task(spec: str) -> str:
                """Append a task."""
                return spec

            # Force a render cycle. We didn't emit a structural
            # event for the @expose itself (endpoints are a
            # property of the root scope, not the log), so trigger
            # one via emit. This is what would happen naturally
            # when the program registers an agent or accepts a
            # call.
            assert rt.log is not None
            rt.log.emit("agent_created", {"agent": "noop"})

            status_path = tmp_path / "logs" / rt.execution_id / "STATUS.md"

            await _wait_for(lambda: "add_task" in status_path.read_text())
            text = status_path.read_text()
            assert "## Affordances" in text
            assert "add_task(spec: string)" in text
            # Docstring first line lands as a description.
            assert "Append a task." in text
        finally:
            await scope.cleanup()
            rt.root_scope = None
            await rt.close()
            _current_process.reset(token)

    asyncio.run(run())


def test_status_lists_agents(tmp_path, monkeypatch):
    async def run():
        rt = await _make_runtime(tmp_path)
        scope = ProcessScope(parent=None, runtime=rt, image=LocalImage(tmp_path))
        rt.root_scope = scope
        token = _current_process.set(scope)
        disable_agent_launch(rt, monkeypatch)
        try:
            await agent("alpha")
            status_path = tmp_path / "logs" / rt.execution_id / "STATUS.md"
            await _wait_for(lambda: "alpha" in status_path.read_text())
            text = status_path.read_text()
            assert "## Agents" in text
            assert "`alpha`" in text
            # tmux session name uses the eid prefix for human readability.
            assert f"ramure-{rt.execution_id[:8]}-alpha" in text
        finally:
            await scope.cleanup()
            rt.root_scope = None
            await rt.close()
            _current_process.reset(token)

    asyncio.run(run())


def test_status_records_recent_endpoint_calls(tmp_path):
    """Endpoint calls land in a dedicated section so an operator
    (human or agent) can see what's been happening without tailing
    the JSONL.
    """

    async def run():
        rt = await _make_runtime(tmp_path)
        scope = ProcessScope(parent=None, runtime=rt, image=LocalImage(tmp_path))
        rt.root_scope = scope
        token = _current_process.set(scope)
        try:
            @expose
            async def echo(msg: str) -> str:
                return msg

            # Simulate a call landing on the runtime log the same
            # way the control-socket dispatcher would.
            assert rt.log is not None
            rt.log.emit(
                "endpoint_called",
                {"endpoint": "echo", "kwargs": {"msg": "hi"}, "caller": "external:cli"},
            )
            rt.log.emit(
                "endpoint_returned",
                {"endpoint": "echo", "caller": "external:cli", "ok": True, "duration_ms": 1},
            )

            status_path = tmp_path / "logs" / rt.execution_id / "STATUS.md"
            await _wait_for(lambda: "Recent endpoint calls" in status_path.read_text())
            text = status_path.read_text()
            assert "echo(msg='hi')" in text
            assert "external:cli" in text
            assert "-> ok" in text
        finally:
            await scope.cleanup()
            rt.root_scope = None
            await rt.close()
            _current_process.reset(token)

    asyncio.run(run())


def test_status_includes_summary_when_set(tmp_path):
    async def run():
        rt = Runtime(log_dir=tmp_path / "logs", summary="A code review pipeline.")
        await rt.start()
        try:
            status_path = tmp_path / "logs" / rt.execution_id / "STATUS.md"
            text = status_path.read_text()
            assert "## Summary" in text
            assert "A code review pipeline." in text
        finally:
            await rt.close()

    asyncio.run(run())


# ---------------------------------------------------------------------------
# update strategy
# ---------------------------------------------------------------------------


def test_status_writes_atomically_no_temp_file_left(tmp_path):
    """Atomic write-then-rename is how readers avoid seeing partial
    files. Verify the parent dir doesn't accumulate ``.STATUS.*``
    leftovers after a few rewrites.
    """

    async def run():
        rt = await _make_runtime(tmp_path)
        try:
            d = tmp_path / "logs" / rt.execution_id
            assert rt.log is not None
            for i in range(5):
                rt.log.emit("agent_created", {"agent": f"a{i}"})

            status_path = d / "STATUS.md"
            await _wait_for(lambda: "a4" in status_path.read_text())

            leftovers = [p for p in d.iterdir() if p.name.startswith(".STATUS.")]
            assert leftovers == []
        finally:
            await rt.close()

    asyncio.run(run())


def test_status_final_snapshot_marks_status_done(tmp_path):
    """When the runtime closes, the file is rewritten one last time
    with ``Status: done`` so the directory is a self-describing
    post-mortem.
    """

    async def run():
        rt = await _make_runtime(tmp_path)
        eid = rt.execution_id
        await rt.close()

        status_path = tmp_path / "logs" / eid / "STATUS.md"
        text = status_path.read_text()
        assert "Status: done" in text

    asyncio.run(run())


def test_status_structural_event_flushes_immediately(tmp_path):
    """``agent_created`` is a structural event -- the writer should
    flush before the debounce window elapses. Otherwise the file
    is uselessly stale right when topology changes.
    """

    async def run():
        rt = await _make_runtime(tmp_path)
        try:
            assert rt.log is not None
            status_path = tmp_path / "logs" / rt.execution_id / "STATUS.md"

            rt.log.emit("agent_created", {"agent": "boom"})

            # Give the writer a brief moment, but well under the
            # debounce window, to confirm it didn't wait.
            for _ in range(20):
                await asyncio.sleep(0.005)
                if "boom" in status_path.read_text():
                    break
            assert "boom" in status_path.read_text(), (
                "structural event should flush before the debounce window"
            )
            # Sanity check: we measured under the debounce.
            assert _DEBOUNCE_S > 0.1
        finally:
            await rt.close()

    asyncio.run(run())
