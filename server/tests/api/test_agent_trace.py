"""Tests for agent trace (incremental ACP event coalescing)."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock
from uuid import uuid4

from druids_server.lib.agents.base import Agent
from druids_server.lib.agents.config import AgentConfig
from druids_server.lib.execution import Execution


def _make_agent(name: str) -> Agent:
    """Create a minimal Agent for trace tests."""
    return Agent(
        config=AgentConfig(name=name),
        machine=MagicMock(),
        bridge_id="test:7462",
        bridge_token="test-token",
        session_id="sess-1",
        connection=MagicMock(),
    )


class FakeConnection:
    """Lightweight connection stub for registering ACP handlers."""

    def __init__(self) -> None:
        self.handlers: dict[str, object] = {}
        self.session_id: str | None = None

    def on(self, event: str, handler: object) -> None:
        self.handlers[event] = handler


# -- Well-formed ACP notification dicts for tests --


def _tool_call_event(
    session_id: str = "sess-1",
    tool_call_id: str = "call-1",
    title: str = "Read file",
    kind: str = "read",
    status: str = "in_progress",
    locations: list | None = None,
) -> dict:
    update: dict = {
        "sessionUpdate": "tool_call",
        "toolCallId": tool_call_id,
        "title": title,
        "kind": kind,
        "status": status,
    }
    if locations is not None:
        update["locations"] = locations
    return {
        "sessionId": session_id,
        "update": update,
    }


def _tool_call_update_event(
    session_id: str = "sess-1",
    tool_call_id: str = "call-1",
    status: str = "completed",
    locations: list | None = None,
    raw_output: str | None = None,
) -> dict:
    update: dict = {
        "sessionUpdate": "tool_call_update",
        "toolCallId": tool_call_id,
        "status": status,
    }
    if locations is not None:
        update["locations"] = locations
    if raw_output is not None:
        update["rawOutput"] = raw_output
    return {
        "sessionId": session_id,
        "update": update,
    }


def _message_event(session_id: str = "sess-1", text: str = "Hello") -> dict:
    return {
        "sessionId": session_id,
        "update": {
            "sessionUpdate": "agent_message_chunk",
            "content": {"type": "text", "text": text},
        },
    }


def _thought_event(session_id: str = "sess-1", text: str = "Thinking...") -> dict:
    return {
        "sessionId": session_id,
        "update": {
            "sessionUpdate": "agent_thought_chunk",
            "content": {"type": "text", "text": text},
        },
    }


def _plan_event(session_id: str = "sess-1", entries: list[dict] | None = None) -> dict:
    if entries is None:
        entries = [{"content": "Read the code", "status": "in_progress", "priority": "high"}]
    return {
        "sessionId": session_id,
        "update": {
            "sessionUpdate": "plan",
            "entries": entries,
        },
    }


# -- Unit tests on Execution directly --


def test_record_agent_event_stores_raw():
    """record_agent_event stores events in agent.raw_events."""
    ex = Execution(id=uuid4(), slug="test", user_id="u1")
    ex.agents["agent-1"] = _make_agent("agent-1")

    ex.record_agent_event("agent-1", _tool_call_event(tool_call_id="c1", title="Read"))
    ex.record_agent_event("agent-1", _tool_call_update_event(tool_call_id="c1", status="completed"))
    ex.record_agent_event("agent-1", _message_event(text="Done"))

    assert len(ex.agents["agent-1"].raw_events) == 3


def test_get_agent_trace_interleaves_messages_and_tools():
    """Messages before, between, and after tool calls appear in correct order."""
    ex = Execution(id=uuid4(), slug="test", user_id="u1")
    ex.agents["w"] = _make_agent("w")

    ex.record_agent_event("w", _message_event(text="I'll read the file."))
    ex.record_agent_event("w", _tool_call_event(tool_call_id="c1", title="Read", status="in_progress"))
    ex.record_agent_event("w", _tool_call_update_event(tool_call_id="c1", status="completed"))
    ex.record_agent_event("w", _message_event(text="Now I'll write."))
    ex.record_agent_event("w", _tool_call_event(tool_call_id="c2", title="Write", kind="edit", status="in_progress"))
    ex.record_agent_event("w", _message_event(text="All done."))

    trace = ex.get_agent_trace("w")
    assert len(trace) == 5
    assert trace[0] == {"type": "message", "text": "I'll read the file."}
    assert trace[1]["type"] == "tool"
    assert trace[1]["tool_call_id"] == "c1"
    assert trace[1]["status"] == "completed"
    assert trace[2] == {"type": "message", "text": "Now I'll write."}
    assert trace[3]["type"] == "tool"
    assert trace[3]["tool_call_id"] == "c2"
    # "All done." is still in the message buffer (no subsequent non-message event)
    assert trace[4] == {"type": "message", "text": "All done."}


def test_get_agent_trace_coalesces_message_chunks():
    """Adjacent message chunks become one message entry."""
    ex = Execution(id=uuid4(), slug="test", user_id="u1")
    ex.agents["w"] = _make_agent("w")

    ex.record_agent_event("w", _message_event(text="Hello "))
    ex.record_agent_event("w", _message_event(text="world"))
    ex.record_agent_event("w", _message_event(text="!"))

    trace = ex.get_agent_trace("w")
    assert len(trace) == 1
    assert trace[0] == {"type": "message", "text": "Hello world!"}


def test_get_agent_trace_merges_tool_call_updates():
    """Tool call start + update produce one entry with final status."""
    ex = Execution(id=uuid4(), slug="test", user_id="u1")
    ex.agents["w"] = _make_agent("w")

    ex.record_agent_event("w", _tool_call_event(tool_call_id="c1", title="Read", kind="read", status="in_progress"))
    ex.record_agent_event("w", _tool_call_update_event(tool_call_id="c1", status="completed"))

    trace = ex.get_agent_trace("w")
    assert len(trace) == 1
    assert trace[0]["type"] == "tool"
    assert trace[0]["tool_call_id"] == "c1"
    assert trace[0]["title"] == "Read"
    assert trace[0]["kind"] == "read"
    assert trace[0]["status"] == "completed"


def test_get_agent_trace_last_n():
    """Only returns last N entries."""
    ex = Execution(id=uuid4(), slug="test", user_id="u1")
    ex.agents["w"] = _make_agent("w")

    for i in range(10):
        ex.record_agent_event("w", _tool_call_event(tool_call_id=f"c{i}", title=f"Tool {i}"))

    trace = ex.get_agent_trace("w", n=3)
    assert len(trace) == 3
    assert trace[0]["tool_call_id"] == "c7"
    assert trace[2]["tool_call_id"] == "c9"


def test_get_agent_trace_truncates_long_messages():
    """Messages over 2000 chars are truncated from the tail."""
    ex = Execution(id=uuid4(), slug="test", user_id="u1")
    ex.agents["w"] = _make_agent("w")

    # Send 30 chunks of 100 chars = 3000 total
    for _ in range(30):
        ex.record_agent_event("w", _message_event(text="x" * 100))
    # Flush by sending a tool call
    ex.record_agent_event("w", _tool_call_event(tool_call_id="c1", title="Read"))

    trace = ex.get_agent_trace("w")
    assert trace[0]["type"] == "message"
    assert len(trace[0]["text"]) == 2000
    assert trace[0]["text"] == "x" * 2000


def test_get_agent_trace_truncates_unflushed_messages():
    """Unflushed messages over 2000 chars are truncated when read."""
    ex = Execution(id=uuid4(), slug="test", user_id="u1")
    ex.agents["w"] = _make_agent("w")

    for _ in range(30):
        ex.record_agent_event("w", _message_event(text="x" * 100))

    trace = ex.get_agent_trace("w")
    assert len(trace) == 1
    assert len(trace[0]["text"]) == 2000


def test_get_agent_trace_extracts_tool_path():
    """Path is extracted from the locations field."""
    ex = Execution(id=uuid4(), slug="test", user_id="u1")
    ex.agents["w"] = _make_agent("w")

    ex.record_agent_event(
        "w",
        _tool_call_event(
            tool_call_id="c1",
            title="Read",
            locations=[{"path": "/src/main.py"}],
        ),
    )

    trace = ex.get_agent_trace("w")
    assert trace[0]["path"] == "/src/main.py"


def test_get_agent_trace_extracts_path_from_update():
    """Path added via tool_call_update is merged into the entry."""
    ex = Execution(id=uuid4(), slug="test", user_id="u1")
    ex.agents["w"] = _make_agent("w")

    ex.record_agent_event("w", _tool_call_event(tool_call_id="c1", title="Read"))
    ex.record_agent_event(
        "w",
        _tool_call_update_event(
            tool_call_id="c1",
            status="completed",
            locations=[{"path": "/src/lib.py"}],
        ),
    )

    trace = ex.get_agent_trace("w")
    assert trace[0]["path"] == "/src/lib.py"
    assert trace[0]["status"] == "completed"


def test_get_agent_trace_surfaces_tool_output():
    """rawOutput from tool_call_update is surfaced as output on the trace entry."""
    ex = Execution(id=uuid4(), slug="test", user_id="u1")
    ex.agents["w"] = _make_agent("w")

    ex.record_agent_event("w", _tool_call_event(tool_call_id="c1", title="Bash", kind="execute"))
    ex.record_agent_event(
        "w",
        _tool_call_update_event(
            tool_call_id="c1",
            status="completed",
            raw_output="hello world",
        ),
    )

    trace = ex.get_agent_trace("w")
    assert len(trace) == 1
    assert trace[0]["output"] == "hello world"
    assert trace[0]["status"] == "completed"


def test_get_agent_trace_truncates_long_tool_output():
    """Tool output over 2000 chars is truncated from the tail."""
    ex = Execution(id=uuid4(), slug="test", user_id="u1")
    ex.agents["w"] = _make_agent("w")

    ex.record_agent_event("w", _tool_call_event(tool_call_id="c1", title="Read", kind="read"))
    ex.record_agent_event(
        "w",
        _tool_call_update_event(
            tool_call_id="c1",
            status="completed",
            raw_output="x" * 3000,
        ),
    )

    trace = ex.get_agent_trace("w")
    assert len(trace[0]["output"]) == 2000


def test_get_agent_trace_merges_duplicate_tool_call_events():
    """Repeated tool_call events with the same ID are merged, not duplicated."""
    ex = Execution(id=uuid4(), slug="test", user_id="u1")
    ex.agents["w"] = _make_agent("w")

    # Real wire format: tool_call sent twice with same ID (first without path, second with)
    ex.record_agent_event("w", _tool_call_event(tool_call_id="c1", title="Read", kind="read", status="pending"))
    ex.record_agent_event(
        "w",
        _tool_call_event(
            tool_call_id="c1",
            title="Read",
            kind="read",
            status="pending",
            locations=[{"path": "/etc/hostname"}],
        ),
    )
    ex.record_agent_event("w", _tool_call_update_event(tool_call_id="c1", status="completed"))

    trace = ex.get_agent_trace("w")
    assert len(trace) == 1
    assert trace[0]["tool_call_id"] == "c1"
    assert trace[0]["status"] == "completed"
    assert trace[0]["path"] == "/etc/hostname"


def test_get_agent_trace_unknown_agent():
    """Returns empty list for an agent with no events."""
    ex = Execution(id=uuid4(), slug="test", user_id="u1")
    assert ex.get_agent_trace("nonexistent") == []


def test_get_agent_trace_includes_thoughts():
    """Thought chunks are coalesced and included as 'thought' entries."""
    ex = Execution(id=uuid4(), slug="test", user_id="u1")
    ex.agents["w"] = _make_agent("w")

    ex.record_agent_event("w", _message_event(text="Hello"))
    ex.record_agent_event("w", _thought_event(text="Let me "))
    ex.record_agent_event("w", _thought_event(text="think about this."))
    ex.record_agent_event("w", _message_event(text="Done"))

    trace = ex.get_agent_trace("w")
    assert len(trace) == 3
    assert trace[0] == {"type": "message", "text": "Hello"}
    assert trace[1] == {"type": "thought", "text": "Let me think about this."}
    assert trace[2] == {"type": "message", "text": "Done"}


def test_get_agent_trace_includes_plan():
    """Plan events appear in the trace with entries."""
    ex = Execution(id=uuid4(), slug="test", user_id="u1")
    ex.agents["w"] = _make_agent("w")

    ex.record_agent_event("w", _message_event(text="I have a plan."))
    ex.record_agent_event(
        "w",
        _plan_event(
            entries=[
                {"content": "Read the code", "status": "completed", "priority": "high"},
                {"content": "Write tests", "status": "in_progress", "priority": "high"},
                {"content": "Open PR", "status": "pending", "priority": "medium"},
            ]
        ),
    )
    ex.record_agent_event("w", _tool_call_event(tool_call_id="c1", title="Read"))

    trace = ex.get_agent_trace("w")
    assert len(trace) == 3
    assert trace[0] == {"type": "message", "text": "I have a plan."}
    assert trace[1]["type"] == "plan"
    assert len(trace[1]["entries"]) == 3
    assert trace[1]["entries"][0] == {"content": "Read the code", "status": "completed"}
    assert trace[1]["entries"][1] == {"content": "Write tests", "status": "in_progress"}
    assert trace[1]["entries"][2] == {"content": "Open PR", "status": "pending"}
    assert trace[2]["type"] == "tool"


def test_get_agent_trace_plan_updates_are_separate_entries():
    """Each plan event creates a new trace entry (full replacement semantics)."""
    ex = Execution(id=uuid4(), slug="test", user_id="u1")
    ex.agents["w"] = _make_agent("w")

    ex.record_agent_event(
        "w",
        _plan_event(
            entries=[
                {"content": "Step 1", "status": "in_progress", "priority": "high"},
            ]
        ),
    )
    ex.record_agent_event(
        "w",
        _plan_event(
            entries=[
                {"content": "Step 1", "status": "completed", "priority": "high"},
                {"content": "Step 2", "status": "in_progress", "priority": "high"},
            ]
        ),
    )

    trace = ex.get_agent_trace("w")
    assert len(trace) == 2
    assert len(trace[0]["entries"]) == 1
    assert len(trace[1]["entries"]) == 2
    assert trace[1]["entries"][0]["status"] == "completed"


def test_get_agent_trace_unflushed_not_mutated():
    """Reading the trace does not mutate internal state."""
    ex = Execution(id=uuid4(), slug="test", user_id="u1")
    ex.agents["w"] = _make_agent("w")

    ex.record_agent_event("w", _message_event(text="streaming..."))

    t1 = ex.get_agent_trace("w")
    assert len(t1) == 1
    assert t1[0]["text"] == "streaming..."

    # More chunks arrive
    ex.record_agent_event("w", _message_event(text=" more text"))
    t2 = ex.get_agent_trace("w")
    assert len(t2) == 1
    assert t2[0]["text"] == "streaming... more text"

    # Flush happens on tool call
    ex.record_agent_event("w", _tool_call_event(tool_call_id="c1", title="Read"))
    t3 = ex.get_agent_trace("w")
    assert len(t3) == 2
    assert t3[0]["text"] == "streaming... more text"
    assert t3[1]["type"] == "tool"


def test_malformed_events_stored_raw():
    """Malformed events are stored in raw_events without error."""
    ex = Execution(id=uuid4(), slug="test", user_id="u1")
    ex.agents["agent-1"] = _make_agent("agent-1")

    ex.record_agent_event("agent-1", _tool_call_event(tool_call_id="c1"))
    ex.record_agent_event("agent-1", {"garbage": True})
    ex.record_agent_event("agent-1", _message_event(text="Still works"))

    assert len(ex.agents["agent-1"].raw_events) == 3

    # Trace includes the valid events, skips malformed
    trace = ex.get_agent_trace("agent-1")
    assert len(trace) == 2
    assert trace[0]["type"] == "tool"
    assert trace[1] == {"type": "message", "text": "Still works"}


def test_bind_trace_stores_raw_acp_events():
    """_bind_trace stores every session/update event."""
    ex = Execution(id=uuid4(), slug="test", user_id="u1")
    agent = _make_agent("agent-1")
    conn = FakeConnection()
    agent.connection = conn
    ex._bind_trace("agent-1", conn)
    ex.agents["agent-1"] = agent

    handler = conn.handlers["session/update"]

    asyncio.run(
        handler(
            _tool_call_event(
                tool_call_id="call-1",
                title="Read file",
                kind="read",
                status="in_progress",
            )
        )
    )
    asyncio.run(handler(_thought_event(text="let me think...")))

    assert len(agent.raw_events) == 2

    trace = agent.get_trace()
    assert len(trace) == 2
    assert trace[0]["type"] == "tool"
    assert trace[0]["tool_call_id"] == "call-1"
    assert trace[1]["type"] == "thought"
    assert trace[1]["text"] == "let me think..."


# -- Endpoint tests via TestClient --


def test_get_agent_trace_endpoint(client, mock_execution):
    """GET /executions/{slug}/agents/{agent}/trace returns the agent trace."""
    mock_execution.get_agent_trace.return_value = [
        {"type": "message", "text": "Hello"},
        {"type": "tool", "tool_call_id": "c1", "title": "Read", "kind": "read", "status": "completed"},
    ]
    mock_execution.has_agent.side_effect = lambda name: name == "worker"
    mock_execution.agents = {"worker": MagicMock()}

    from tests.api.conftest import SLUG

    response = client.get(f"/executions/{SLUG}/agents/worker/trace")
    assert response.status_code == 200
    data = response.json()
    assert data["agent"] == "worker"
    assert len(data["trace"]) == 2


def test_get_agent_trace_endpoint_with_n(client, mock_execution):
    """GET /executions/{slug}/agents/{agent}/trace?n=10 passes n through."""
    mock_execution.get_agent_trace.return_value = []
    mock_execution.has_agent.side_effect = lambda name: name == "worker"
    mock_execution.agents = {"worker": MagicMock()}

    from tests.api.conftest import SLUG

    response = client.get(f"/executions/{SLUG}/agents/worker/trace?n=10")
    assert response.status_code == 200
    mock_execution.get_agent_trace.assert_called_with("worker", n=10)


def test_get_agent_trace_endpoint_unknown_agent(client, mock_execution):
    """GET /executions/{slug}/agents/{unknown}/trace returns 404."""
    mock_execution.agents = {}
    mock_execution.has_agent.side_effect = lambda name: False

    from tests.api.conftest import SLUG

    response = client.get(f"/executions/{SLUG}/agents/unknown/trace")
    assert response.status_code == 404
