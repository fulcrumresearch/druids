"""Per-agent append-only event log with async iteration and websocket push."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from druids.types import to_jsonable


@dataclass(frozen=True)
class LogEntry:
    seq: int
    ts: float
    type: str
    origin: str  # "agent" or "server"
    data: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "ts": self.ts,
            "type": self.type,
            "origin": self.origin,
            "data": self.data,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    @staticmethod
    def from_dict(d: dict[str, Any]) -> LogEntry:
        return LogEntry(
            seq=d["seq"],
            ts=d["ts"],
            type=d["type"],
            origin=d["origin"],
            data=d.get("data", {}),
        )


class AgentEventLog:
    """Append-only event log for a single agent.

    Stores, persists to JSONL, supports async iteration, and pushes
    entries to a websocket subscriber.
    """

    def __init__(self, log_dir: Path | None = None, agent_name: str = "") -> None:
        self._entries: list[LogEntry] = []
        self._next_seq: int = 1
        self._log_path: Path | None = None
        self._closed = False
        self._waiters: list[asyncio.Event] = []
        self.on_push: Callable[[LogEntry], Awaitable[None]] | None = None

        if log_dir is not None and agent_name:
            log_dir.mkdir(parents=True, exist_ok=True)
            self._log_path = log_dir / f"{agent_name}.jsonl"

    def append(
        self,
        event_type: str,
        origin: str,
        data: dict[str, Any] | None = None,
    ) -> LogEntry:
        entry = LogEntry(
            seq=self._next_seq,
            ts=time.time(),
            type=event_type,
            origin=origin,
            data=to_jsonable(data) if data else {},
        )
        self._next_seq += 1
        self._entries.append(entry)
        self._persist(entry)
        for w in self._waiters:
            w.set()
        return entry

    async def push(self, entry: LogEntry) -> None:
        """Push an entry to the websocket subscriber."""
        if self.on_push is not None:
            try:
                await self.on_push(entry)
            except Exception:
                pass

    async def push_entries(self, entries: list[LogEntry]) -> None:
        for entry in entries:
            await self.push(entry)

    def entries_after(self, seq: int) -> list[LogEntry]:
        if seq <= 0:
            return list(self._entries)
        start_idx = seq
        if start_idx >= len(self._entries):
            return []
        return list(self._entries[start_idx:])

    @property
    def last_seq(self) -> int:
        return self._entries[-1].seq if self._entries else 0

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for w in self._waiters:
            w.set()

    def __len__(self) -> int:
        return len(self._entries)

    def __aiter__(self) -> _LogIterator:
        return _LogIterator(self)

    def _persist(self, entry: LogEntry) -> None:
        if self._log_path is None:
            return
        with self._log_path.open("a") as f:
            f.write(entry.to_json() + "\n")


class _LogIterator:
    def __init__(self, log: AgentEventLog) -> None:
        self._log = log
        self._index = 0

    async def __anext__(self) -> LogEntry:
        while True:
            if self._index < len(self._log._entries):
                entry = self._log._entries[self._index]
                self._index += 1
                return entry
            if self._log._closed:
                raise StopAsyncIteration
            waiter = asyncio.Event()
            self._log._waiters.append(waiter)
            try:
                await waiter.wait()
            finally:
                self._log._waiters.remove(waiter)
