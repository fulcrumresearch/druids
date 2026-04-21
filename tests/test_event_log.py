"""Tests for Log (replicated wrapper around Stream)."""

from __future__ import annotations

import json
from pathlib import Path

from ramure.log import Log, LogEntry
from ramure.stream import Event


def test_emit_assigns_sequential_ids() -> None:
    log = Log()
    e1 = log.emit("register", {"execution_id": "abc"}, origin="agent")
    e2 = log.emit("registered", {"tools": []})
    e3 = log.emit("tool_call", {"call_id": "tc-1", "tool": "submit"}, origin="agent")

    assert e1.seq == 1
    assert e2.seq == 2
    assert e3.seq == 3
    assert len(log) == 3
    assert log.last_seq == 3

    # origin defaults to "server" when not specified
    assert e1.origin == "agent"
    assert e2.origin == "server"
    assert e3.origin == "agent"


def test_after_returns_correct_slice() -> None:
    log = Log()
    log.emit("a", origin="agent")
    log.emit("b")
    log.emit("c", origin="agent")
    log.emit("d")

    assert [e.seq for e in log.after(0)] == [1, 2, 3, 4]
    assert [e.seq for e in log.after(2)] == [3, 4]
    assert log.after(4) == []
    assert log.after(99) == []


def test_persistence_writes_jsonl(tmp_path: Path) -> None:
    log = Log(path=tmp_path / "worker.jsonl")
    log.emit("register", {"execution_id": "abc"}, origin="agent")
    log.emit("registered", {"tools": []})

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
    assert second["origin"] == "server"


def test_log_entry_round_trip() -> None:
    entry = LogEntry(
        type="tool_call",
        data={"tool": "bash"},
        seq=5,
        ts=1713100000.0,
        origin="agent",
    )
    d = entry.to_dict()
    restored = LogEntry.from_dict(d)
    assert restored == entry
    assert json.loads(entry.to_json()) == d
    # ``source`` defaults to None and survives the round-trip.
    assert restored.source is None


def test_log_entry_round_trip_with_source() -> None:
    """An entry tagged with a source round-trips through to_dict /
    from_dict so the factory chain's ``Event.source`` survives
    persistence and replay from disk."""
    entry = LogEntry(
        type="check_ok",
        data={"ok": True},
        source="env",
        seq=1,
        ts=1713100000.0,
        origin="server",
    )
    d = entry.to_dict()
    assert d["source"] == "env"
    restored = LogEntry.from_dict(d)
    assert restored == entry
    assert restored.source == "env"


def test_log_entry_from_dict_tolerates_missing_source() -> None:
    """Old jsonl entries written before Event.source existed have no
    ``source`` key. ``from_dict`` must still parse them."""
    d = {
        "type": "tool_call",
        "data": {"tool": "bash"},
        "seq": 1,
        "ts": 1713100000.0,
        "origin": "agent",
    }
    entry = LogEntry.from_dict(d)
    assert entry.source is None


def test_no_persistence_without_path() -> None:
    log = Log()
    log.emit("test", origin="agent")
    assert log._path is None
    assert len(log) == 1


def test_emit_after_close_returns_none() -> None:
    log = Log()
    log.emit("a")
    log.close()
    result = log.emit("b")
    assert result is None
    assert len(log) == 1


def test_stream_and_log_stay_in_sync() -> None:
    log = Log()
    log.emit("a", {"x": 1})
    log.emit("b", {"x": 2})

    # Stream sees events without replication metadata.
    events = log.stream.snapshot()
    assert events == [Event(type="a", data={"x": 1}), Event(type="b", data={"x": 2})]

    # Log entries carry the same type+data plus seq/ts/origin.
    entries = log.after(0)
    assert [(e.type, e.data) for e in entries] == [("a", {"x": 1}), ("b", {"x": 2})]
    # A LogEntry is-an Event (inheritance).
    assert all(isinstance(e, Event) for e in entries)
