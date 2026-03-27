"""Shared execution trace streaming helpers."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, AsyncIterator, Awaitable, Callable, Literal

from druids_server.api.helpers.trace_format import merge_response_chunks, normalize_event
from druids_server.utils import execution_trace


if TYPE_CHECKING:
    from collections.abc import Mapping


INTERESTING_TYPES = {
    "tool_use",
    "tool_result",
    "prompt",
    "response_chunk",
    "connected",
    "disconnected",
    "error",
    "client_event",
    "topology",
}
KEEPALIVE_INTERVAL_SECONDS = 15.0
POLL_INTERVAL_SECONDS = 0.3


@dataclass(frozen=True)
class ActivityItem:
    """A normalized execution activity event and its stream cursor id."""

    event_id: int
    payload: dict[str, Any]


@dataclass(frozen=True)
class StreamItem:
    """A streaming item for execution trace transports."""

    kind: Literal["activity", "keepalive", "done"]
    activity: ActivityItem | None = None


def read_activity_batch(
    execution_id: str,
    line_cursor: int,
    *,
    raw: bool = False,
) -> tuple[list[ActivityItem], int]:
    """Read, filter, and normalize a trace batch for streaming.

    When raw is True, skip merge_response_chunks so individual tokens
    are emitted as separate events (useful for typing animation).
    """
    new_events, next_line_cursor = execution_trace.read_from(execution_id, line_cursor)
    if not new_events:
        return [], next_line_cursor

    events = new_events if raw else merge_response_chunks(new_events)
    base_id = next_line_cursor - len(new_events)

    items: list[ActivityItem] = []
    for index, event in enumerate(events):
        if event.get("type") not in INTERESTING_TYPES:
            continue
        items.append(
            ActivityItem(
                event_id=base_id + index + 1,
                payload=normalize_event(event, compact=False),
            )
        )
    return items, next_line_cursor


def is_execution_done(executions: Mapping[str, Any], slug: str, line_cursor: int) -> bool:
    """Return True once execution disappears after at least one line has been read."""
    return not executions.get(slug) and line_cursor > 0


def should_emit_keepalive(last_keepalive: float, now: float) -> bool:
    """Return True when keepalive interval elapsed."""
    return now - last_keepalive > KEEPALIVE_INTERVAL_SECONDS


async def iter_execution_stream(
    execution_id: str,
    slug: str,
    executions: Mapping[str, Any],
    *,
    start_line: int = 0,
    raw: bool = False,
    is_disconnected: Callable[[], Awaitable[bool]] | None = None,
) -> AsyncIterator[StreamItem]:
    """Yield normalized execution stream items for SSE and WebSocket transports."""
    line_cursor = start_line
    last_keepalive = time.monotonic()

    while True:
        if is_disconnected and await is_disconnected():
            break

        items, line_cursor = read_activity_batch(execution_id, line_cursor, raw=raw)
        for item in items:
            yield StreamItem(kind="activity", activity=item)

        if is_execution_done(executions, slug, line_cursor):
            yield StreamItem(kind="done")
            break

        now = time.monotonic()
        if should_emit_keepalive(last_keepalive, now):
            yield StreamItem(kind="keepalive")
            last_keepalive = now

        await asyncio.sleep(POLL_INTERVAL_SECONDS)
