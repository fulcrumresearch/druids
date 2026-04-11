from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from druids import Context, ExecutionFailed, LocalImage
from tests.helpers import FakeAgentClient, wait_for_server


def _start_context(ctx: Context, timeout: float = 10) -> tuple[threading.Thread, dict[str, object]]:
    outcome: dict[str, object] = {}

    def runner() -> None:
        try:
            outcome["result"] = ctx.run(timeout=timeout)
        except Exception as exc:  # pragma: no cover - asserted by tests
            outcome["error"] = exc

    thread = threading.Thread(target=runner, name="ctx-runner", daemon=True)
    thread.start()

    deadline = time.time() + 5
    while ctx.server_url is None and time.time() < deadline:
        time.sleep(0.01)
    assert ctx.server_url is not None
    wait_for_server(ctx.server_url)
    return thread, outcome


def test_register_prompt_and_submit_flow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)

    ctx = Context(image=LocalImage(tmp_path / "builder"), launch_mode="manual")
    builder = ctx.agent("builder", prompt="Implement the thing")

    @builder.on("submit")
    def submit(summary: str = "") -> str:
        ctx.done(summary)
        return "submitted"

    thread, outcome = _start_context(ctx)

    client = FakeAgentClient(ctx.server_url, ctx.execution_id, "builder")
    client.start_events()
    tools = client.register()

    tool_names = [tool["name"] for tool in tools]
    assert tool_names[:4] == ["message", "list_agents", "send_file", "download_file"]
    assert "submit" in tool_names

    prompt_event = client.next_event("message")
    assert prompt_event == {"text": "Implement the thing"}

    result = client.tool_call("submit", {"summary": "done"})
    assert result == "submitted"

    thread.join(timeout=5)
    assert outcome["result"] == "done"


def test_dynamic_tool_registration_pushes_new_tool_event(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)

    ctx = Context(image=LocalImage(tmp_path / "builder"), launch_mode="manual")
    builder = ctx.agent("builder")

    @builder.on("finish")
    def finish() -> str:
        ctx.done("ok")
        return "ok"

    thread, outcome = _start_context(ctx)

    client = FakeAgentClient(ctx.server_url, ctx.execution_id, "builder")
    client.start_events()
    client.register()

    @builder.on("late_tool")
    def late_tool(message: str = "") -> str:
        return message.upper()

    event = client.next_event("new_tool")
    assert event["name"] == "late_tool"
    assert event["parameters"]["properties"]["message"]["type"] == "string"

    assert client.tool_call("late_tool", {"message": "hello"}) == "HELLO"
    assert client.tool_call("finish") == "ok"

    thread.join(timeout=5)
    assert outcome["result"] == "ok"


def test_builtin_message_and_file_transfer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)

    ctx = Context(launch_mode="manual")
    builder = ctx.agent("builder", image=LocalImage(tmp_path / "builder"))
    reviewer = ctx.agent("reviewer", image=LocalImage(tmp_path / "reviewer"))
    ctx.connect(builder, reviewer)

    @builder.on("finish")
    def finish() -> str:
        ctx.done("complete")
        return "complete"

    thread, outcome = _start_context(ctx)

    builder_client = FakeAgentClient(ctx.server_url, ctx.execution_id, "builder")
    reviewer_client = FakeAgentClient(ctx.server_url, ctx.execution_id, "reviewer")
    builder_client.start_events()
    reviewer_client.start_events()
    builder_client.register()
    reviewer_client.register()

    builder.machine.write_file("artifact.txt", b"hello")
    send_result = builder_client.tool_call("send_file", {"receiver": "reviewer", "path": "artifact.txt"})
    assert "Sent 5 bytes" in send_result
    assert reviewer.machine.read_file("artifact.txt") == b"hello"

    message_result = builder_client.tool_call("message", {"receiver": "reviewer", "message": "done"})
    assert message_result == "Message sent to reviewer."
    assert reviewer_client.next_event("message") == {"text": "[From: builder] done"}

    builder.machine.write_file("artifact-2.txt", b"world")
    download_result = reviewer_client.tool_call(
        "download_file",
        {"sender": "builder", "path": "artifact-2.txt", "dest_path": "copied.txt"},
    )
    assert "Downloaded 5 bytes" in download_result
    assert reviewer.machine.read_file("copied.txt") == b"world"

    assert builder_client.tool_call("finish") == "complete"
    thread.join(timeout=5)
    assert outcome["result"] == "complete"


def test_connection_enforcement_returns_http_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)

    ctx = Context(launch_mode="manual")
    builder = ctx.agent("builder", image=LocalImage(tmp_path / "builder"))
    reviewer = ctx.agent("reviewer", image=LocalImage(tmp_path / "reviewer"))
    ctx.connect(builder, reviewer, direction="forward")

    @builder.on("finish")
    def finish() -> str:
        ctx.done("ok")
        return "ok"

    thread, outcome = _start_context(ctx)

    builder_client = FakeAgentClient(ctx.server_url, ctx.execution_id, "builder")
    reviewer_client = FakeAgentClient(ctx.server_url, ctx.execution_id, "reviewer")
    builder_client.start_events()
    reviewer_client.start_events()
    builder_client.register()
    reviewer_client.register()

    status, error = reviewer_client.tool_call_error("message", {"receiver": "builder", "message": "nope"})
    assert status == 403
    assert "not connected" in error

    assert builder_client.tool_call("finish") == "ok"
    thread.join(timeout=5)
    assert outcome["result"] == "ok"


def test_dynamic_agent_creation_inside_handler_is_immediate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)

    ctx = Context(image=LocalImage(tmp_path / "shared"), launch_mode="manual")
    builder = ctx.agent("builder")
    created: dict[str, object] = {}

    @builder.on("spawn")
    def spawn() -> str:
        reviewer = ctx.agent("reviewer", machine=builder.machine)
        created["reviewer"] = reviewer
        ctx.done("spawned")
        return reviewer.name

    thread, outcome = _start_context(ctx)

    client = FakeAgentClient(ctx.server_url, ctx.execution_id, "builder")
    client.start_events()
    client.register()
    assert client.tool_call("spawn") == "reviewer"

    thread.join(timeout=5)
    assert outcome["result"] == "spawned"
    reviewer = created["reviewer"]
    assert reviewer.machine is builder.machine
    assert "reviewer" in ctx.agents


def test_ctx_fail_raises_execution_failed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)

    ctx = Context(image=LocalImage(tmp_path / "builder"), launch_mode="manual")
    builder = ctx.agent("builder")

    @builder.on("reject")
    def reject(reason: str = "") -> str:
        ctx.fail(reason)
        return "rejected"

    thread, outcome = _start_context(ctx)

    client = FakeAgentClient(ctx.server_url, ctx.execution_id, "builder")
    client.start_events()
    client.register()
    assert client.tool_call("reject", {"reason": "bad build"}) == "rejected"

    thread.join(timeout=5)
    error = outcome.get("error")
    assert isinstance(error, ExecutionFailed)
    assert error.reason == "bad build"
