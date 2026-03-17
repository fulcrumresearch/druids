"""
Execution trace logging - append-only JSONL per execution.

Logs to ~/.druids/executions/{user_id}/{slug}.jsonl

Each line is a JSON object with:
- ts: timestamp
- type: event type
- agent: agent name (null for execution-level events)
- ...other fields depending on type
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


EXECUTIONS_DIR = Path.home() / ".druids" / "executions"


def _get_file(user_id: str, slug: str) -> Path:
    """Get trace file for an execution (namespaced by user)."""
    user_dir = EXECUTIONS_DIR / user_id
    user_dir.mkdir(parents=True, exist_ok=True)
    return user_dir / f"{slug}.jsonl"


def _now() -> str:
    """Current timestamp in ISO format."""
    return datetime.now(timezone.utc).isoformat()


def _append(user_id: str, slug: str, event: dict) -> None:
    """Append event to execution trace."""
    event["ts"] = _now()
    with open(_get_file(user_id, slug), "a") as f:
        f.write(json.dumps(event) + "\n")


# ---------------------------------------------------------------------------
# Execution-level events
# ---------------------------------------------------------------------------


def started(user_id: str, slug: str, task_id: str | None, base_snapshot: str | None) -> None:
    """Log execution started."""
    _append(
        user_id,
        slug,
        {
            "type": "execution_started",
            "agent": None,
            "task_id": task_id,
            "base_snapshot": base_snapshot,
        },
    )


def stopped(user_id: str, slug: str, reason: str = "unknown") -> None:
    """Log execution stopped."""
    _append(
        user_id,
        slug,
        {
            "type": "execution_stopped",
            "agent": None,
            "reason": reason,
        },
    )


def program_added(
    user_id: str,
    slug: str,
    name: str,
    program_type: str,
    instance_id: str | None = None,
) -> None:
    """Log program added to execution."""
    _append(
        user_id,
        slug,
        {
            "type": "program_added",
            "agent": None,
            "name": name,
            "program_type": program_type,
            "instance_id": instance_id,
        },
    )


# ---------------------------------------------------------------------------
# Agent-level events
# ---------------------------------------------------------------------------


def agent_connected(user_id: str, slug: str, agent_name: str, session_id: str) -> None:
    """Log agent connected."""
    _append(
        user_id,
        slug,
        {
            "type": "connected",
            "agent": agent_name,
            "session_id": session_id,
        },
    )


def agent_disconnected(user_id: str, slug: str, agent_name: str) -> None:
    """Log agent disconnected."""
    _append(
        user_id,
        slug,
        {
            "type": "disconnected",
            "agent": agent_name,
        },
    )


def prompt(user_id: str, slug: str, agent_name: str, text: str) -> None:
    """Log prompt sent to agent."""
    _append(
        user_id,
        slug,
        {
            "type": "prompt",
            "agent": agent_name,
            "text": text,
        },
    )


def response_chunk(user_id: str, slug: str, agent_name: str, text: str) -> None:
    """Log response chunk from agent."""
    _append(
        user_id,
        slug,
        {
            "type": "response_chunk",
            "agent": agent_name,
            "text": text,
        },
    )


def tool_use(user_id: str, slug: str, agent_name: str, tool: str, params: dict | None = None) -> None:
    """Log tool use by agent."""
    _append(
        user_id,
        slug,
        {
            "type": "tool_use",
            "agent": agent_name,
            "tool": tool,
            "params": params,
        },
    )


def tool_result(user_id: str, slug: str, agent_name: str, tool: str, result: str | None = None) -> None:
    """Log tool result."""
    _append(
        user_id,
        slug,
        {
            "type": "tool_result",
            "agent": agent_name,
            "tool": tool,
            "result": result,
        },
    )


def topology(user_id: str, slug: str, agents: list[str], edges: list[dict[str, str]]) -> None:
    """Log topology update (agents and edges)."""
    _append(
        user_id,
        slug,
        {
            "type": "topology",
            "agent": None,
            "agents": agents,
            "edges": edges,
        },
    )


def client_event(user_id: str, slug: str, event: str, data: dict | None = None) -> None:
    """Log a program-emitted event for clients."""
    _append(
        user_id,
        slug,
        {
            "type": "client_event",
            "agent": None,
            "event": event,
            "data": data or {},
        },
    )


def error(user_id: str, slug: str, agent_name: str | None, error_msg: str) -> None:
    """Log error."""
    _append(
        user_id,
        slug,
        {
            "type": "error",
            "agent": agent_name,
            "error": error_msg,
        },
    )


# ---------------------------------------------------------------------------
# Reading traces
# ---------------------------------------------------------------------------


def read_from(user_id: str, slug: str, start_line: int) -> tuple[list[dict], int]:
    """Read events from line start_line onward.

    Lines are 1-indexed (line 1 is the first event). Pass start_line=0 to read
    from the beginning. Returns (events, new_line_number) where new_line_number
    is the total number of lines read so far -- pass it back as start_line on
    the next call to resume.
    """
    path = _get_file(user_id, slug)
    if not path.exists():
        return [], start_line
    events = []
    current_line = 0
    with open(path) as f:
        for raw in f:
            current_line += 1
            if current_line <= start_line:
                continue
            raw = raw.strip()
            if raw:
                events.append(json.loads(raw))
    return events, current_line


def read_tail(user_id: str, slug: str, n: int = 50) -> list[dict]:
    """Read the last n events from an execution trace."""
    path = _get_file(user_id, slug)
    if not path.exists():
        return []
    from collections import deque

    events = deque(maxlen=n)
    for line in path.read_text().splitlines():
        if line.strip():
            events.append(json.loads(line))
    return list(events)


def count_events(user_id: str, slug: str) -> int:
    """Count total events in a trace without parsing."""
    path = _get_file(user_id, slug)
    if not path.exists():
        return 0
    with open(path) as f:
        return sum(1 for line in f if line.strip())
