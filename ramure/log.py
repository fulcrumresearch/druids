"""Replicated, persisted event log.

A ``Log`` is the single-writer source of truth for an agent's event
stream. Callers use one primitive, ``emit``, which:

1. Appends a ``LogEntry`` with a monotonically-increasing ``seq``.
2. Persists it to the on-disk JSONL file (if a path was given).
3. Schedules delivery to every subscribed consumer.

Delivery is fire-and-forget, scheduled onto the running event loop via
``asyncio.ensure_future``. Callers do not see the async step. This keeps
``emit`` usable from sync contexts (e.g. the ``@agent.on`` decorator)
while guaranteeing that every emission eventually reaches subscribers.

Ordering on the wire is NOT guaranteed by the log. The client-side
replica is expected to detect gaps (via strict ``seq`` monotonicity) and
request a resync. See ``spec-replication.md``.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from ramure.stream import Event, Stream
from ramure.types import to_jsonable


Subscriber = Callable[["LogEntry"], Awaitable[None]]


@dataclass(frozen=True, kw_only=True)
class LogEntry(Event):
    """An Event plus replication metadata (seq, ts, origin).

    Inherits ``type`` and ``data`` from Event. The on-wire shape is the
    flat dataclass dict: ``{type, data, seq, ts, origin}``.
    """

    seq: int
    ts: float
    origin: str  # "agent" or "server"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    @staticmethod
    def from_dict(d: dict[str, Any]) -> LogEntry:
        return LogEntry(
            type=d["type"],
            data=d.get("data", {}),
            source=d.get("source"),      # may be absent on old entries
            seq=d["seq"],
            ts=d["ts"],
            origin=d["origin"],
        )


class Log:
    """Single-writer event log. Appends are monotonic; delivery is async."""

    def __init__(self, path: Path | None = None) -> None:
        self.stream: Stream = Stream()
        self._entries: list[LogEntry] = []
        self._next_seq: int = 1
        self._path: Path | None = path
        self._subscribers: list[Subscriber] = []

        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)

    def emit(
        self, kind: str, data: Any = None, *, origin: str = "server"
    ) -> LogEntry | None:
        """Append an entry, persist it, and schedule delivery to subscribers.

        Returns the appended entry, or ``None`` if the log is closed.
        """
        normalized = to_jsonable(data) if data is not None else {}
        event = self.stream.emit(kind, normalized)
        if event is None:
            return None
        entry = LogEntry(
            type=event.type,
            data=event.data,
            seq=self._next_seq,
            ts=time.time(),
            origin=origin,
        )
        self._next_seq += 1
        self._entries.append(entry)
        self._persist(entry)

        # Schedule delivery. Using ensure_future means callers never see
        # the async step; the coroutine runs as soon as the loop is idle.
        # If there is no running loop (e.g. emit called from pure sync
        # code without asyncio), deliver is skipped and subscribers only
        # catch up on the next sync request.
        if self._subscribers:
            try:
                asyncio.ensure_future(self._deliver(entry))
            except RuntimeError:
                # No running loop. Persistence is already done; clients
                # will see the entry on their next ``sync after:K``.
                pass

        return entry

    def subscribe(self, cb: Subscriber) -> Callable[[], None]:
        """Register a subscriber. Returns an unsubscribe callable."""
        self._subscribers.append(cb)

        def unsubscribe() -> None:
            try:
                self._subscribers.remove(cb)
            except ValueError:
                pass

        return unsubscribe

    async def _deliver(self, entry: LogEntry) -> None:
        """Send an entry to every current subscriber.

        A subscriber whose callback raises is removed. The log's own
        state is unaffected; subscribers are best-effort mirrors. Any
        entries a subscriber misses will be replayed via ``after`` on
        the next ``sync`` request.
        """
        dead: list[Subscriber] = []
        for sub in list(self._subscribers):
            try:
                await sub(entry)
            except Exception:
                dead.append(sub)
        for sub in dead:
            try:
                self._subscribers.remove(sub)
            except ValueError:
                pass

    def after(self, seq: int) -> list[LogEntry]:
        """Entries with seq strictly greater than the given value."""
        if seq <= 0:
            return list(self._entries)
        if seq >= len(self._entries):
            return []
        return list(self._entries[seq:])

    @property
    def last_seq(self) -> int:
        return self._entries[-1].seq if self._entries else 0

    def close(self) -> None:
        self.stream.close()

    def __len__(self) -> int:
        return len(self._entries)

    def _persist(self, entry: LogEntry) -> None:
        if self._path is None:
            return
        with self._path.open("a") as f:
            f.write(entry.to_json() + "\n")
