"""Per-agent append-only event log."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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

    Pure data store — append, query, persist. No networking or lifecycle.
    """

    def __init__(self, log_dir: Path | None = None, agent_name: str = "") -> None:
        self._entries: list[LogEntry] = []
        self._next_seq: int = 1
        self._log_path: Path | None = None

        if log_dir is not None and agent_name:
            log_dir.mkdir(parents=True, exist_ok=True)
            self._log_path = log_dir / f"{agent_name}.jsonl"

    def append(
        self,
        event_type: str,
        origin: str,
        data: dict[str, Any] | None = None,
    ) -> LogEntry:
        """Append an event and return the canonical log entry."""
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
        return entry

    def entries_after(self, seq: int) -> list[LogEntry]:
        """Return all entries with seq > the given value."""
        if seq <= 0:
            return list(self._entries)
        start_idx = seq  # entries_after(3) → index 3 → seq 4
        if start_idx >= len(self._entries):
            return []
        return list(self._entries[start_idx:])

    @property
    def last_seq(self) -> int:
        return self._entries[-1].seq if self._entries else 0

    def __len__(self) -> int:
        return len(self._entries)

    def _persist(self, entry: LogEntry) -> None:
        if self._log_path is None:
            return
        with self._log_path.open("a") as f:
            f.write(entry.to_json() + "\n")
