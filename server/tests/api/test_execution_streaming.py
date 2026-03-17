"""Tests for execution streaming over SSE and WebSocket."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from druids_server.api.deps import get_executions_registry


@asynccontextmanager
async def _fake_session():
    yield None


def _patch_execution_lookup(mock_user, slug: str):
    record = SimpleNamespace(id="exec-1", slug=slug, user_id=mock_user.id)
    return (
        patch("druids_server.api.routes.executions.get_session", _fake_session),
        patch("druids_server.api.routes.executions.get_execution_by_slug", AsyncMock(return_value=record)),
    )


def _parse_sse_response(body: str) -> tuple[list[int], list[dict], bool]:
    activity_ids: list[int] = []
    activity_payloads: list[dict] = []
    saw_done = False

    for block in body.split("\n\n"):
        if not block.strip():
            continue
        lines = [line for line in block.splitlines() if line.strip()]
        if not lines:
            continue

        event_name = "message"
        event_id: int | None = None
        data: str | None = None
        for line in lines:
            if line.startswith("id: "):
                event_id = int(line.removeprefix("id: "))
            elif line.startswith("event: "):
                event_name = line.removeprefix("event: ")
            elif line.startswith("data: "):
                data = line.removeprefix("data: ")

        if event_name == "activity" and data is not None:
            if event_id is not None:
                activity_ids.append(event_id)
            activity_payloads.append(json.loads(data))
        elif event_name == "done":
            saw_done = True

    return activity_ids, activity_payloads, saw_done


def test_streaming_sse_and_websocket_match_filtering_and_done(client, mock_user, mock_execution):
    slug = mock_execution.slug
    user_id = str(mock_user.id)
    trace_batch = [
        {"type": "execution_started", "agent": None},
        {"type": "response_chunk", "agent": "worker", "text": "hello "},
        {"type": "response_chunk", "agent": "worker", "text": "world"},
        {
            "type": "tool_result",
            "agent": "worker",
            "tool": "rg",
            "result": {"aggregated_output": "ok", "exit_code": 0, "duration": 1.5},
        },
        {"type": "client_event", "event": "progress", "data": {"pct": 10}},
    ]

    registry = get_executions_registry()[user_id]

    def fake_read_from(_user_id: str, _slug: str, _line_num: int):
        if _line_num == 0:
            registry.pop(slug, None)
            return deepcopy(trace_batch), len(trace_batch)
        return [], len(trace_batch)

    expected_activity = [
        {"type": "response_chunk", "agent": "worker", "text": "hello world"},
        {
            "type": "tool_result",
            "agent": "worker",
            "tool": "rg",
            "result": "ok",
            "exit_code": 0,
            "duration_secs": 1.5,
        },
        {"type": "client_event", "event": "progress", "data": {"pct": 10}, "ts": None},
    ]

    session_patch, execution_patch = _patch_execution_lookup(mock_user, slug)
    with (
        session_patch,
        execution_patch,
        patch("druids_server.api.helpers.execution_stream.execution_trace.read_from", side_effect=fake_read_from),
        patch("druids_server.api.routes.executions._get_local_user", AsyncMock(return_value=mock_user)),
    ):
        sse_response = client.get(f"/executions/{slug}/stream")
        sse_ids, sse_activity, sse_done = _parse_sse_response(sse_response.text)
        registry[slug] = mock_execution

        with client.websocket_connect(f"/executions/{slug}/ws?token=test-token") as websocket:
            ws_messages = [
                websocket.receive_json(),
                websocket.receive_json(),
                websocket.receive_json(),
                websocket.receive_json(),
            ]

    ws_activity = [message for message in ws_messages if message.get("type") != "done"]
    ws_done = any(message.get("type") == "done" for message in ws_messages)

    assert sse_response.status_code == 200
    assert sse_done is True
    assert ws_done is True
    assert sse_ids == [2, 3, 4]
    assert sse_activity == expected_activity
    assert ws_activity == expected_activity


def test_topology_event_streams_through_sse(client, mock_user, mock_execution):
    """Topology events should pass through the SSE stream with agents and edges."""
    slug = mock_execution.slug
    user_id = str(mock_user.id)
    trace_batch = [
        {"type": "execution_started", "agent": None},
        {"type": "connected", "agent": "alice", "session_id": "s1"},
        {"type": "connected", "agent": "bob", "session_id": "s2"},
        {
            "type": "topology",
            "agent": None,
            "agents": ["alice", "bob"],
            "edges": [{"from": "alice", "to": "bob"}],
            "ts": "2026-01-01T00:00:00+00:00",
        },
    ]

    registry = get_executions_registry()[user_id]

    def fake_read_from(_user_id: str, _slug: str, _line_num: int):
        if _line_num == 0:
            registry.pop(slug, None)
            return deepcopy(trace_batch), len(trace_batch)
        return [], len(trace_batch)

    session_patch, execution_patch = _patch_execution_lookup(mock_user, slug)
    with (
        session_patch,
        execution_patch,
        patch("druids_server.api.helpers.execution_stream.execution_trace.read_from", side_effect=fake_read_from),
        patch("druids_server.api.routes.executions._get_local_user", AsyncMock(return_value=mock_user)),
    ):
        sse_response = client.get(f"/executions/{slug}/stream")
        _, sse_activity, sse_done = _parse_sse_response(sse_response.text)

    assert sse_response.status_code == 200
    assert sse_done is True

    topology_events = [e for e in sse_activity if e.get("type") == "topology"]
    assert len(topology_events) == 1
    assert topology_events[0]["agents"] == ["alice", "bob"]
    assert topology_events[0]["edges"] == [{"from": "alice", "to": "bob"}]
    assert topology_events[0]["ts"] == "2026-01-01T00:00:00+00:00"
