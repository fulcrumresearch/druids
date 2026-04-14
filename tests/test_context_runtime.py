from __future__ import annotations

import asyncio
import urllib.error
from pathlib import Path

import pytest

from druids import Context, ExecutionFailed, LocalImage
from tests.helpers import FakeAgentClient, disable_agent_launch, wait_for_server


def test_register_and_submit_flow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        monkeypatch.chdir(tmp_path)

        async with Context(image=LocalImage(tmp_path / "builder")) as ctx:
            disable_agent_launch(ctx, monkeypatch)
            builder = await ctx.agent("builder")

            @builder.on("submit")
            async def submit(summary: str = "") -> str:
                await ctx.done(summary)
                return "submitted"

            assert ctx.server_url is not None
            await asyncio.to_thread(wait_for_server, ctx.server_url)

            client = FakeAgentClient(ctx.server_url, ctx.execution_id or "", "builder")
            client.start_events()
            tools = await asyncio.to_thread(client.register)

            tool_names = [tool["name"] for tool in tools]
            assert tool_names[:3] == ["message", "send_file", "download_file"]
            assert "submit" in tool_names

            await builder.send("Implement the thing")
            assert await asyncio.to_thread(client.next_event, "message") == {"text": "Implement the thing"}

            assert await asyncio.to_thread(client.tool_call, "submit", {"summary": "done"}) == "submitted"
            assert await ctx.wait(timeout=5) == "done"

    asyncio.run(run())


def test_dynamic_tool_registration_pushes_new_tool_event(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        monkeypatch.chdir(tmp_path)

        async with Context(image=LocalImage(tmp_path / "builder")) as ctx:
            disable_agent_launch(ctx, monkeypatch)
            builder = await ctx.agent("builder")

            @builder.on("finish")
            async def finish() -> str:
                await ctx.done("ok")
                return "ok"

            assert ctx.server_url is not None
            await asyncio.to_thread(wait_for_server, ctx.server_url)

            client = FakeAgentClient(ctx.server_url, ctx.execution_id or "", "builder")
            client.start_events()
            await asyncio.to_thread(client.register)

            @builder.on("late_tool")
            async def late_tool(message: str = "") -> str:
                return message.upper()

            event = await asyncio.to_thread(client.next_event, "new_tool")
            assert event["name"] == "late_tool"
            assert event["parameters"]["properties"]["message"]["type"] == "string"

            assert await asyncio.to_thread(client.tool_call, "late_tool", {"message": "hello"}) == "HELLO"
            assert await asyncio.to_thread(client.tool_call, "finish") == "ok"
            assert await ctx.wait(timeout=5) == "ok"

    asyncio.run(run())


def test_builtin_message_and_file_transfer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        monkeypatch.chdir(tmp_path)

        async with Context() as ctx:
            disable_agent_launch(ctx, monkeypatch)
            builder = await ctx.agent("builder", image=LocalImage(tmp_path / "builder"))
            reviewer = await ctx.agent("reviewer", image=LocalImage(tmp_path / "reviewer"))
            ctx.connect(builder, reviewer)

            @builder.on("finish")
            async def finish() -> str:
                await ctx.done("complete")
                return "complete"

            assert ctx.server_url is not None
            await asyncio.to_thread(wait_for_server, ctx.server_url)

            builder_client = FakeAgentClient(ctx.server_url, ctx.execution_id or "", "builder")
            reviewer_client = FakeAgentClient(ctx.server_url, ctx.execution_id or "", "reviewer")
            builder_client.start_events()
            reviewer_client.start_events()
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
            assert await ctx.wait(timeout=5) == "complete"

    asyncio.run(run())


def test_connection_enforcement_returns_http_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        monkeypatch.chdir(tmp_path)

        async with Context() as ctx:
            disable_agent_launch(ctx, monkeypatch)
            builder = await ctx.agent("builder", image=LocalImage(tmp_path / "builder"))
            reviewer = await ctx.agent("reviewer", image=LocalImage(tmp_path / "reviewer"))
            ctx.connect(builder, reviewer, direction="forward")

            @builder.on("finish")
            async def finish() -> str:
                await ctx.done("ok")
                return "ok"

            assert ctx.server_url is not None
            await asyncio.to_thread(wait_for_server, ctx.server_url)

            builder_client = FakeAgentClient(ctx.server_url, ctx.execution_id or "", "builder")
            reviewer_client = FakeAgentClient(ctx.server_url, ctx.execution_id or "", "reviewer")
            builder_client.start_events()
            reviewer_client.start_events()
            await asyncio.to_thread(builder_client.register)
            await asyncio.to_thread(reviewer_client.register)

            status, error = await asyncio.to_thread(
                reviewer_client.tool_call_error,
                "message",
                {"receiver": "builder", "message": "nope"},
            )
            assert status == 403
            assert "not connected" in error

            assert await asyncio.to_thread(builder_client.tool_call, "finish") == "ok"
            assert await ctx.wait(timeout=5) == "ok"

    asyncio.run(run())


def test_dynamic_agent_creation_inside_handler_is_immediate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        monkeypatch.chdir(tmp_path)

        async with Context(image=LocalImage(tmp_path / "shared")) as ctx:
            disable_agent_launch(ctx, monkeypatch)
            builder = await ctx.agent("builder")
            created: dict[str, object] = {}

            @builder.on("spawn")
            async def spawn() -> str:
                reviewer = await ctx.agent("reviewer", machine=builder.machine)
                created["reviewer"] = reviewer
                await ctx.done("spawned")
                return reviewer.name

            assert ctx.server_url is not None
            await asyncio.to_thread(wait_for_server, ctx.server_url)

            client = FakeAgentClient(ctx.server_url, ctx.execution_id or "", "builder")
            client.start_events()
            await asyncio.to_thread(client.register)
            assert await asyncio.to_thread(client.tool_call, "spawn") == "reviewer"

            assert await ctx.wait(timeout=5) == "spawned"
            reviewer = created["reviewer"]
            assert reviewer.machine is builder.machine

    asyncio.run(run())


def test_ctx_fail_raises_execution_failed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        monkeypatch.chdir(tmp_path)

        with pytest.raises(ExecutionFailed) as exc_info:
            async with Context(image=LocalImage(tmp_path / "builder")) as ctx:
                disable_agent_launch(ctx, monkeypatch)
                builder = await ctx.agent("builder")

                @builder.on("reject")
                async def reject(reason: str = "") -> str:
                    await ctx.fail(reason)
                    return "rejected"

                assert ctx.server_url is not None
                await asyncio.to_thread(wait_for_server, ctx.server_url)

                client = FakeAgentClient(ctx.server_url, ctx.execution_id or "", "builder")
                client.start_events()
                await asyncio.to_thread(client.register)
                assert await asyncio.to_thread(client.tool_call, "reject", {"reason": "bad build"}) == "rejected"
                await ctx.wait(timeout=5)

        assert exc_info.value.reason == "bad build"

    asyncio.run(run())


def test_duplicate_agent_names_are_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        monkeypatch.chdir(tmp_path)

        async with Context(image=LocalImage(tmp_path / "builder")) as ctx:
            disable_agent_launch(ctx, monkeypatch)
            await ctx.agent("builder")
            with pytest.raises(ValueError, match="already exists"):
                await ctx.agent("builder")

    asyncio.run(run())


def test_register_validates_execution_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        monkeypatch.chdir(tmp_path)

        async with Context(image=LocalImage(tmp_path / "builder")) as ctx:
            disable_agent_launch(ctx, monkeypatch)
            await ctx.agent("builder")

            assert ctx.server_url is not None
            await asyncio.to_thread(wait_for_server, ctx.server_url)

            client = FakeAgentClient(ctx.server_url, "wrong-execution-id", "builder")
            client.start_events()
            with pytest.raises(urllib.error.HTTPError) as exc_info:
                await asyncio.to_thread(client.register)

            assert exc_info.value.code == 400
            body = exc_info.value.read().decode("utf-8")
            assert "Execution ID mismatch" in body

    asyncio.run(run())
