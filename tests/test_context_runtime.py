"""Tests for the runtime, agent process decorator, and tool dispatch."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from druids import ExecutionFailed, LocalImage, Runtime
from druids.process import (
    ProcessScope,
    _current_process,
    agent,
    agent_process,
    connect,
    current_runtime,
    done,
    fail,
    wait,
)
from tests.helpers import FakeAgentClient, disable_agent_launch, wait_for_server


async def _setup(tmp_path, monkeypatch):
    runtime = Runtime()
    await runtime.start()
    scope = ProcessScope(parent=None, runtime=runtime, image=LocalImage(tmp_path))
    token = _current_process.set(scope)
    disable_agent_launch(runtime, monkeypatch)
    return runtime, scope, token


async def _teardown(runtime, scope, token):
    await scope.cleanup()
    await runtime.close()
    _current_process.reset(token)


async def _make_client(runtime: Runtime, agent_id: str) -> FakeAgentClient:
    client = FakeAgentClient(runtime.server_url, runtime.execution_id or "", agent_id)
    await asyncio.to_thread(client.connect)
    return client


def test_agent_process_decorator(tmp_path: Path, monkeypatch) -> None:
    async def run() -> None:
        monkeypatch.chdir(tmp_path)

        @agent_process(image=LocalImage(tmp_path / "builder"), timeout=5)
        async def program() -> str:
            runtime = current_runtime()
            disable_agent_launch(runtime, monkeypatch)
            builder = await agent("builder")

            @builder.on("submit")
            async def submit(summary: str = "") -> str:
                done(summary)
                return "submitted"

            assert runtime.server_url is not None
            await asyncio.to_thread(wait_for_server, runtime.server_url)

            client = await _make_client(runtime, "builder")
            try:
                await asyncio.to_thread(client.register)
                await builder.send("Implement the thing")
                assert await asyncio.to_thread(client.next_event, "message") == {"text": "Implement the thing"}
                assert await asyncio.to_thread(client.tool_call, "submit", {"summary": "done"}) == "submitted"
            finally:
                await asyncio.to_thread(client.close)
            return await wait()

        assert await program() == "done"
        assert _current_process.get() is None

    asyncio.run(run())


def test_register_and_submit_flow(tmp_path: Path, monkeypatch) -> None:
    async def run() -> None:
        runtime, scope, token = await _setup(tmp_path, monkeypatch)
        try:
            builder = await agent("builder")

            @builder.on("submit")
            async def submit(summary: str = "") -> str:
                done(summary)
                return "submitted"

            await asyncio.to_thread(wait_for_server, runtime.server_url)
            client = await _make_client(runtime, "builder")
            try:
                tools = await asyncio.to_thread(client.register)

                tool_names = [tool["name"] for tool in tools]
                assert tool_names[:3] == ["message", "send_file", "download_file"]
                assert "submit" in tool_names

                await builder.send("Implement the thing")
                assert await asyncio.to_thread(client.next_event, "message") == {"text": "Implement the thing"}

                assert await asyncio.to_thread(client.tool_call, "submit", {"summary": "done"}) == "submitted"
                assert await wait() == "done"
            finally:
                await asyncio.to_thread(client.close)
        finally:
            await _teardown(runtime, scope, token)

    asyncio.run(run())


def test_dynamic_tool_registration_pushes_new_tool_event(tmp_path: Path, monkeypatch) -> None:
    async def run() -> None:
        runtime, scope, token = await _setup(tmp_path, monkeypatch)
        try:
            builder = await agent("builder")

            @builder.on("finish")
            async def finish() -> str:
                done("ok")
                return "ok"

            await asyncio.to_thread(wait_for_server, runtime.server_url)
            client = await _make_client(runtime, "builder")
            try:
                await asyncio.to_thread(client.register)

                @builder.on("late_tool")
                async def late_tool(message: str = "") -> str:
                    return message.upper()

                event = await asyncio.to_thread(client.next_event, "tool_registered")
                assert event["name"] == "late_tool"
                assert event["parameters"]["properties"]["message"]["type"] == "string"

                assert await asyncio.to_thread(client.tool_call, "late_tool", {"message": "hello"}) == "HELLO"
                assert await asyncio.to_thread(client.tool_call, "finish") == "ok"
                assert await wait() == "ok"
            finally:
                await asyncio.to_thread(client.close)
        finally:
            await _teardown(runtime, scope, token)

    asyncio.run(run())


def test_builtin_message_and_file_transfer(tmp_path: Path, monkeypatch) -> None:
    async def run() -> None:
        runtime, scope, token = await _setup(tmp_path, monkeypatch)
        try:
            builder = await agent("builder", image=LocalImage(tmp_path / "builder"))
            reviewer = await agent("reviewer", image=LocalImage(tmp_path / "reviewer"))
            connect(builder, reviewer)

            @builder.on("finish")
            async def finish() -> str:
                done("complete")
                return "complete"

            await asyncio.to_thread(wait_for_server, runtime.server_url)
            builder_client = await _make_client(runtime, "builder")
            reviewer_client = await _make_client(runtime, "reviewer")
            try:
                await asyncio.to_thread(builder_client.register)
                await asyncio.to_thread(reviewer_client.register)

                await builder.machine.write_file("artifact.txt", b"hello")
                send_result = await asyncio.to_thread(
                    builder_client.tool_call,
                    "send_file",
                    {"receiver": "reviewer", "path": "artifact.txt"},
                )
                assert "Sent 5 bytes" in send_result
                assert await reviewer.machine.read_file("artifact.txt") == b"hello"

                message_result = await asyncio.to_thread(
                    builder_client.tool_call,
                    "message",
                    {"receiver": "reviewer", "message": "done"},
                )
                assert message_result == "Message sent to reviewer."
                assert await asyncio.to_thread(reviewer_client.next_event, "message") == {"text": "[From: builder] done"}

                await builder.machine.write_file("artifact-2.txt", b"world")
                download_result = await asyncio.to_thread(
                    reviewer_client.tool_call,
                    "download_file",
                    {"sender": "builder", "path": "artifact-2.txt", "dest_path": "copied.txt"},
                )
                assert "Downloaded 5 bytes" in download_result
                assert await reviewer.machine.read_file("copied.txt") == b"world"

                assert await asyncio.to_thread(builder_client.tool_call, "finish") == "complete"
                assert await wait() == "complete"
            finally:
                await asyncio.to_thread(builder_client.close)
                await asyncio.to_thread(reviewer_client.close)
        finally:
            await _teardown(runtime, scope, token)

    asyncio.run(run())


def test_connection_enforcement_returns_error(tmp_path: Path, monkeypatch) -> None:
    async def run() -> None:
        runtime, scope, token = await _setup(tmp_path, monkeypatch)
        try:
            builder = await agent("builder", image=LocalImage(tmp_path / "builder"))
            reviewer = await agent("reviewer", image=LocalImage(tmp_path / "reviewer"))
            connect(builder, reviewer, direction="forward")

            @builder.on("finish")
            async def finish() -> str:
                done("ok")
                return "ok"

            await asyncio.to_thread(wait_for_server, runtime.server_url)
            builder_client = await _make_client(runtime, "builder")
            reviewer_client = await _make_client(runtime, "reviewer")
            try:
                await asyncio.to_thread(builder_client.register)
                await asyncio.to_thread(reviewer_client.register)

                error_type, error_msg = await asyncio.to_thread(
                    reviewer_client.tool_call_error,
                    "message",
                    {"receiver": "builder", "message": "nope"},
                )
                assert "not connected" in error_msg

                assert await asyncio.to_thread(builder_client.tool_call, "finish") == "ok"
                assert await wait() == "ok"
            finally:
                await asyncio.to_thread(builder_client.close)
                await asyncio.to_thread(reviewer_client.close)
        finally:
            await _teardown(runtime, scope, token)

    asyncio.run(run())


def test_dynamic_agent_creation_inside_handler(tmp_path: Path, monkeypatch) -> None:
    async def run() -> None:
        runtime, scope, token = await _setup(tmp_path, monkeypatch)
        try:
            builder = await agent("builder")
            created: dict[str, object] = {}

            @builder.on("spawn")
            async def spawn_agent() -> str:
                reviewer = await agent("reviewer", machine=builder.machine)
                created["reviewer"] = reviewer
                done("spawned")
                return reviewer.name

            await asyncio.to_thread(wait_for_server, runtime.server_url)
            client = await _make_client(runtime, "builder")
            try:
                await asyncio.to_thread(client.register)
                assert await asyncio.to_thread(client.tool_call, "spawn") == "reviewer"
                assert await wait() == "spawned"
                assert created["reviewer"].machine is builder.machine
            finally:
                await asyncio.to_thread(client.close)
        finally:
            await _teardown(runtime, scope, token)

    asyncio.run(run())


def test_fail_raises_execution_failed(tmp_path: Path, monkeypatch) -> None:
    async def run() -> None:
        runtime, scope, token = await _setup(tmp_path, monkeypatch)
        try:
            builder = await agent("builder")

            @builder.on("reject")
            async def reject(reason: str = "") -> str:
                fail(reason)
                return "rejected"

            await asyncio.to_thread(wait_for_server, runtime.server_url)
            client = await _make_client(runtime, "builder")
            try:
                await asyncio.to_thread(client.register)
                assert await asyncio.to_thread(client.tool_call, "reject", {"reason": "bad build"}) == "rejected"

                with pytest.raises(ExecutionFailed) as exc_info:
                    await wait()
                assert exc_info.value.reason == "bad build"
            finally:
                await asyncio.to_thread(client.close)
        finally:
            await _teardown(runtime, scope, token)

    asyncio.run(run())


def test_duplicate_agent_names_are_rejected(tmp_path: Path, monkeypatch) -> None:
    async def run() -> None:
        runtime, scope, token = await _setup(tmp_path, monkeypatch)
        try:
            await agent("builder")
            with pytest.raises(ValueError, match="already exists"):
                await agent("builder")
        finally:
            await _teardown(runtime, scope, token)

    asyncio.run(run())


def test_register_validates_execution_id(tmp_path: Path, monkeypatch) -> None:
    async def run() -> None:
        runtime, scope, token = await _setup(tmp_path, monkeypatch)
        try:
            await agent("builder")
            await asyncio.to_thread(wait_for_server, runtime.server_url)

            client = FakeAgentClient(runtime.server_url, "wrong-execution-id", "builder")
            await asyncio.to_thread(client.connect)
            try:
                def bad_register():
                    client.sync()
                    client._send({
                        "type": "event",
                        "event_type": "register",
                        "data": {"execution_id": "wrong-execution-id"},
                    })
                    return client._drain_until(lambda e: e.get("type") == "error")

                entry = await asyncio.to_thread(bad_register)
                assert "Execution ID mismatch" in entry["data"]["error"]
            finally:
                await asyncio.to_thread(client.close)
        finally:
            await _teardown(runtime, scope, token)

    asyncio.run(run())


# ---------------------------------------------------------------------------
# Runtime log
# ---------------------------------------------------------------------------


def test_runtime_log_records_lifecycle_and_structure(tmp_path: Path, monkeypatch) -> None:
    """The runtime log captures execution_started, agent_created,
    agent_spawned, connection_added, and execution_ended."""
    async def run() -> None:
        runtime = Runtime(log_dir=tmp_path / "logs")
        await runtime.start()
        scope = ProcessScope(parent=None, runtime=runtime, image=LocalImage(tmp_path))
        token = _current_process.set(scope)
        disable_agent_launch(runtime, monkeypatch)
        try:
            assert runtime.log is not None

            a = await agent("alpha")
            b = await agent("beta")
            connect(a, b)

            entries = runtime.log.after(0)
            types = [e.type for e in entries]
            assert types[0] == "execution_started"
            assert "agent_created" in types
            assert "connection_added" in types
            # `agent_spawned` would appear after a real launch; tests
            # using ``disable_agent_launch`` skip that path.

            # agent_created carries machine.describe() info
            created = next(e for e in entries if e.type == "agent_created" and e.data["agent"] == "alpha")
            assert created.data["machine"]["kind"] == "LocalMachine"

            # connection_added carries the edge
            conn = next(e for e in entries if e.type == "connection_added")
            assert {conn.data["a"], conn.data["b"]} == {"alpha", "beta"}
            assert conn.data["direction"] == "both"
        finally:
            await scope.cleanup()
            await runtime.close()
            _current_process.reset(token)

        # After close, the runtime log is persisted on disk.
        log_files = list((tmp_path / "logs").rglob("_runtime.jsonl"))
        assert len(log_files) == 1
        content = log_files[0].read_text().splitlines()
        assert any('"execution_started"' in line for line in content)
        assert any('"execution_ended"' in line for line in content)

    asyncio.run(run())
