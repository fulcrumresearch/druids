"""Replicated, persisted wrapper around a Stream.

A ``Log`` holds a ``Stream`` for local consumers plus a parallel sequence
of ``LogEntry`` wrappers that add the metadata needed for replication:
a monotonic ``seq``, a timestamp, and an ``origin`` tag. Each emit also
appends to an on-disk JSONL file (if configured) and can be pushed to a
remote subscriber via ``on_push``.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from druids.stream import Event, Stream
from druids.types import to_jsonable


@dataclass(frozen=True)
class LogEntry:
    """A logged Event: the event itself plus replication metadata."""

    seq: int
    ts: float
    origin: str  # "agent" or "server"
    event: Event

    def to_dict(self) -> dict[str, Any]:
        # Flat on-wire shape: {seq, ts, type, origin, data}.
        return {
            "seq": self.seq,
            "ts": self.ts,
            "type": self.event.type,
            "origin": self.origin,
            "data": self.event.data,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    @staticmethod
    def from_dict(d: dict[str, Any]) -> LogEntry:
        return LogEntry(
            seq=d["seq"],
            ts=d["ts"],
            origin=d["origin"],
            event=Event(type=d["type"], data=d.get("data", {})),
        )


class Log:
    """Replicated stream: a Stream plus sequenced, persisted LogEntry wrappers."""

    def __init__(self, path: Path | None = None) -> None:
        self.stream: Stream = Stream()
        self._entries: list[LogEntry] = []
        self._next_seq: int = 1
        self._path: Path | None = path
        self.on_push: Callable[[LogEntry], Awaitable[None]] | None = None

        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)

    def emit(self, type: str, data: Any = None, *, origin: str = "server") -> LogEntry | None:
        """Emit an event, recording it in the stream and as a LogEntry."""
        normalized = to_jsonable(data) if data else {}
        event = self.stream.emit(type, normalized)
        if event is None:
            return None
        entry = LogEntry(
            seq=self._next_seq,
            ts=time.time(),
            origin=origin,
            event=event,
        )
        self._next_seq += 1
        self._entries.append(entry)
        self._persist(entry)
        return entry

    async def push(self, entry: LogEntry) -> None:
        """Forward an entry to the subscribed remote consumer, if any."""
        if self.on_push is not None:
            try:
                await self.on_push(entry)
            except Exception:
                pass

    async def push_all(self, entries: list[LogEntry]) -> None:
        for entry in entries:
            await self.push(entry)

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
