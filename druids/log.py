"""Replicated, persisted wrapper around a Stream.

A ``Log`` holds a ``Stream`` for local consumers plus a parallel sequence
of ``LogEntry`` wrappers that add the metadata needed for replication:
a monotonic ``seq``, a timestamp, and an ``origin`` tag. Each emit also
appends to an on-disk JSONL file (if configured).

The server is the single writer. Remote replicas subscribe via
``subscribe(cb)`` and receive each new entry through
``broadcast(entry)``. Broadcast is best-effort: a subscriber whose
callback raises is removed; the log itself remains the source of truth.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from druids.stream import Event, Stream
from druids.types import to_jsonable


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
            seq=d["seq"],
            ts=d["ts"],
            origin=d["origin"],
        )


class Log:
    """Replicated stream: a Stream plus sequenced, persisted LogEntry wrappers."""

    def __init__(self, path: Path | None = None) -> None:
        self.stream: Stream = Stream()
        self._entries: list[LogEntry] = []
        self._next_seq: int = 1
        self._path: Path | None = path
        self._subscribers: list[Callable[[LogEntry], Awaitable[None]]] = []

        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)

    def emit(self, type: str, data: Any = None, *, origin: str = "server") -> LogEntry | None:
        """Emit an event, recording it in the stream and as a LogEntry."""
        normalized = to_jsonable(data) if data else {}
        event = self.stream.emit(type, normalized)
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
        return entry

    def subscribe(
        self, cb: Callable[[LogEntry], Awaitable[None]]
    ) -> Callable[[], None]:
        """Register a subscriber. Returns an unsubscribe callable."""
        self._subscribers.append(cb)

        def unsubscribe() -> None:
            try:
                self._subscribers.remove(cb)
            except ValueError:
                pass

        return unsubscribe

    async def broadcast(self, entry: LogEntry) -> None:
        """Send an entry to every current subscriber.

        A subscriber whose callback raises is removed. The log's own state
        is unaffected; subscribers are best-effort mirrors.
        """
        dead: list[Callable[[LogEntry], Awaitable[None]]] = []
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
