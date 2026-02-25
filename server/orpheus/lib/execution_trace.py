"""
Execution trace logging - append-only JSONL per execution.

Logs to ~/.orpheus/executions/{user_id}/{slug}.jsonl

Each line is a JSON object with:
- ts: timestamp
- type: event type
- agent: agent name (null for execution-level events)
- ...other fields depending on type
"""

import json
from datetime import datetime, timezone
from pathlib import Path


EXECUTIONS_DIR = Path.home() / ".orpheus" / "executions"


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


def submitted(user_id: str, slug: str, pr_url: str | None = None, summary: str | None = None) -> None:
    """Log execution submitted (agent called submit tool)."""
    _append(
        user_id,
        slug,
        {
            "type": "execution_submitted",
            "agent": None,
            "pr_url": pr_url,
            "summary": summary,
        },
    )


def pr_comment_received(user_id: str, slug: str, commenter: str, body: str, event_type: str) -> None:
    """Log PR comment received via webhook."""
    _append(
        user_id,
        slug,
        {
            "type": "pr_comment_received",
            "agent": None,
            "commenter": commenter,
            "body": body[:500],
            "event_type": event_type,
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


def instance_stopped(user_id: str, slug: str, name: str, instance_id: str) -> None:
    """Log instance stopped."""
    _append(
        user_id,
        slug,
        {
            "type": "instance_stopped",
            "agent": None,
            "name": name,
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


def error(user_id: str, slug: str, agent_name: str, error_msg: str) -> None:
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


def read(user_id: str, slug: str) -> list[dict]:
    """Read all events from an execution trace."""
    path = _get_file(user_id, slug)
    if not path.exists():
        return []
    events = []
    for line in path.read_text().splitlines():
        if line.strip():
            events.append(json.loads(line))
    return events


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
    return sum(1 for line in open(path) if line.strip())


def list_executions(user_id: str) -> list[str]:
    """List all execution slugs with traces for a user."""
    user_dir = EXECUTIONS_DIR / user_id
    if not user_dir.exists():
        return []
    return [f.stem for f in user_dir.glob("*.jsonl")]


def list_all_executions() -> list[tuple[str, str]]:
    """List all (user_id, slug) pairs with traces."""
    if not EXECUTIONS_DIR.exists():
        return []
    results = []
    for user_dir in EXECUTIONS_DIR.iterdir():
        if user_dir.is_dir():
            for trace_file in user_dir.glob("*.jsonl"):
                results.append((user_dir.name, trace_file.stem))
    return results


def get_summary(user_id: str, slug: str) -> dict | None:
    """Get execution summary by reading first and last events."""
    events = read(user_id, slug)
    if not events:
        return None

    # Find execution_started event for metadata
    started_event = next((e for e in events if e["type"] == "execution_started"), None)
    stopped_event = next((e for e in reversed(events) if e["type"] == "execution_stopped"), None)

    # Collect unique agents
    agents = set(e["agent"] for e in events if e.get("agent"))

    return {
        "user_id": user_id,
        "slug": slug,
        "task_id": started_event.get("task_id") if started_event else None,
        "base_snapshot": started_event.get("base_snapshot") if started_event else None,
        "started_at": started_event.get("ts") if started_event else None,
        "stopped_at": stopped_event.get("ts") if stopped_event else None,
        "status": "stopped" if stopped_event else "running",
        "agents": list(agents),
        "event_count": len(events),
    }
