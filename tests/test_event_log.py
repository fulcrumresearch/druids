"""Tests for the AgentEventLog."""

from __future__ import annotations

import json
from pathlib import Path

from druids.event_log import AgentEventLog, LogEntry


def test_append_assigns_sequential_ids() -> None:
    log = AgentEventLog()
    e1 = log.append("register", "agent", {"execution_id": "abc"})
    e2 = log.append("registered", "server", {"tools": []})
    e3 = log.append("tool_call", "agent", {"call_id": "tc-1", "tool": "submit"})

    assert e1.seq == 1
    assert e2.seq == 2
    assert e3.seq == 3
    assert len(log) == 3
    assert log.last_seq == 3


def test_entries_after_returns_correct_slice() -> None:
    log = AgentEventLog()
    log.append("a", "agent")
    log.append("b", "server")
    log.append("c", "agent")
    log.append("d", "server")

    after_0 = log.entries_after(0)
    assert [e.seq for e in after_0] == [1, 2, 3, 4]

    after_2 = log.entries_after(2)
    assert [e.seq for e in after_2] == [3, 4]

    after_4 = log.entries_after(4)
    assert after_4 == []

    after_99 = log.entries_after(99)
    assert after_99 == []


def test_persistence_writes_jsonl(tmp_path: Path) -> None:
    log = AgentEventLog(log_dir=tmp_path, agent_name="worker")
    log.append("register", "agent", {"execution_id": "abc"})
    log.append("registered", "server", {"tools": []})

    log_file = tmp_path / "worker.jsonl"
    assert log_file.exists()

    lines = log_file.read_text().strip().split("\n")
    assert len(lines) == 2

    first = json.loads(lines[0])
    assert first["seq"] == 1
    assert first["type"] == "register"
    assert first["origin"] == "agent"
    assert first["data"]["execution_id"] == "abc"

    second = json.loads(lines[1])
    assert second["seq"] == 2
    assert second["type"] == "registered"


def test_log_entry_round_trip() -> None:
    entry = LogEntry(seq=5, ts=1713100000.0, type="tool_call", origin="agent", data={"tool": "bash"})
    d = entry.to_dict()
    restored = LogEntry.from_dict(d)
    assert restored == entry
    assert json.loads(entry.to_json()) == d


def test_no_persistence_without_log_dir() -> None:
    log = AgentEventLog()
    log.append("test", "agent")
    assert log._log_path is None
    assert len(log) == 1
