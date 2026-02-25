"""Tests for execution activity endpoint behavior."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from orpheus.lib import execution_trace
from orpheus.lib.execution import Execution


class FakeConnection:
    """Lightweight connection stub for registering ACP handlers."""

    def __init__(self) -> None:
        self.handlers: dict[str, object] = {}

    def on(self, event: str, handler: object) -> None:
        self.handlers[event] = handler


@asynccontextmanager
async def _fake_session():
    yield None


def _patch_execution_lookups(mock_user, slug: str):
    record = SimpleNamespace(id="exec-1", slug=slug, task_id="task-1")
    task = SimpleNamespace(user_id=mock_user.id)
    return (
        patch(
            "orpheus.api.routes.executions.get_session",
            _fake_session,
        ),
        patch(
            "orpheus.api.routes.executions.get_execution_by_slug",
            AsyncMock(return_value=record),
        ),
        patch(
            "orpheus.api.routes.executions.get_task",
            AsyncMock(return_value=task),
        ),
    )


def _clear_trace_file(user_id: str, slug: str) -> None:
    trace_path = execution_trace._get_file(user_id, slug)
    if trace_path.exists():
        trace_path.unlink()


def test_activity_compact_mode(client, mock_user, tmp_path, monkeypatch):
    slug = "exec-compact"
    user_id = str(mock_user.id)
    monkeypatch.setattr(execution_trace, "EXECUTIONS_DIR", tmp_path)
    _clear_trace_file(user_id, slug)

    long_text = "x" * 2500
    execution_trace.tool_use(user_id, slug, "agent-1", "rg", {"command": long_text})
    execution_trace.tool_result(
        user_id,
        slug,
        "agent-1",
        "rg",
        {"aggregated_output": long_text, "exit_code": 2, "duration": 1.5},
    )
    execution_trace.prompt(user_id, slug, "agent-1", "p" * 800)
    execution_trace.response_chunk(user_id, slug, "agent-1", "r" * 800)

    session_patch, execution_patch, task_patch = _patch_execution_lookups(mock_user, slug)
    with session_patch, execution_patch, task_patch:
        response = client.get(f"/executions/{slug}/activity")

    assert response.status_code == 200
    activity = response.json()["recent_activity"]

    tool_use = next(event for event in activity if event["type"] == "tool_use")
    assert set(tool_use.keys()) == {"type", "agent", "tool", "ts"}

    tool_result = next(event for event in activity if event["type"] == "tool_result")
    assert "result" not in tool_result
    assert tool_result["exit_code"] == 2
    assert tool_result["duration_secs"] == 1.5

    response_chunk = next(event for event in activity if event["type"] == "response_chunk")
    assert len(response_chunk["text"]) == 500


def test_activity_truncation_full(client, mock_user, tmp_path, monkeypatch):
    slug = "exec-truncate"
    user_id = str(mock_user.id)
    monkeypatch.setattr(execution_trace, "EXECUTIONS_DIR", tmp_path)
    _clear_trace_file(user_id, slug)

    long_text = "y" * 2500
    execution_trace.tool_use(user_id, slug, "agent-1", "rg", {"command": long_text})
    execution_trace.tool_result(
        user_id,
        slug,
        "agent-1",
        "rg",
        {"aggregated_output": long_text, "exit_code": 0, "duration": 2.0},
    )
    execution_trace.prompt(user_id, slug, "agent-1", "p" * 900)
    execution_trace.response_chunk(user_id, slug, "agent-1", "r" * 900)

    session_patch, execution_patch, task_patch = _patch_execution_lookups(mock_user, slug)
    with session_patch, execution_patch, task_patch:
        response = client.get(f"/executions/{slug}/activity?compact=false")

    assert response.status_code == 200
    activity = response.json()["recent_activity"]

    tool_use = next(event for event in activity if event["type"] == "tool_use")
    assert len(tool_use["params"]["command"]) == 2000

    tool_result = next(event for event in activity if event["type"] == "tool_result")
    assert len(tool_result["result"]) == 2000
    assert tool_result["exit_code"] == 0
    assert tool_result["duration_secs"] == 2.0

    prompt = next(event for event in activity if event["type"] == "prompt")
    assert len(prompt["text"]) == 500

    response_chunk = next(event for event in activity if event["type"] == "response_chunk")
    assert len(response_chunk["text"]) == 500


def test_activity_tool_name_correlation(client, mock_user, tmp_path, monkeypatch):
    slug = "exec-tool-name"
    user_id = str(mock_user.id)
    monkeypatch.setattr(execution_trace, "EXECUTIONS_DIR", tmp_path)
    _clear_trace_file(user_id, slug)

    execution = Execution(
        id=uuid4(),
        slug=slug,
        root=MagicMock(),
        user_id=user_id,
    )
    conn = FakeConnection()
    execution._setup_handlers("agent-1", conn)

    handler = conn.handlers["session/update"]
    asyncio.run(
        handler(
            {
                "update": {
                    "sessionUpdate": "tool_call",
                    "title": "Search Devbox in devbox.py",
                    "rawInput": {"command": "rg -n Devbox"},
                    "toolCallId": "call-1",
                }
            }
        )
    )
    asyncio.run(
        handler(
            {
                "update": {
                    "sessionUpdate": "tool_call_update",
                    "status": "completed",
                    "rawOutput": {"aggregated_output": "ok"},
                    "toolCallId": "call-1",
                }
            }
        )
    )

    session_patch, execution_patch, task_patch = _patch_execution_lookups(mock_user, slug)
    with session_patch, execution_patch, task_patch:
        response = client.get(f"/executions/{slug}/activity")

    assert response.status_code == 200
    activity = response.json()["recent_activity"]
    tool_result = next(event for event in activity if event["type"] == "tool_result")
    assert tool_result["tool"] == "Search Devbox in devbox.py"
