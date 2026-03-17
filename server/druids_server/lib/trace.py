"""Incremental trace of ACP agent events.

Ingests raw ACP session update events one at a time and produces a typed,
coalesced trace. Adjacent message or thought chunks merge into single entries.
Tool call start and update events merge by `toolCallId`. Plan events append
as-is (the protocol sends the full list each time).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal


_TEXT_LIMIT = 2000


@dataclass
class MessageEntry:
    """Coalesced agent message."""

    type: Literal["message"] = "message"
    text: str = ""


@dataclass
class ThoughtEntry:
    """Coalesced agent thought."""

    type: Literal["thought"] = "thought"
    text: str = ""


@dataclass
class ToolEntry:
    """A tool call, merged from start and update events."""

    type: Literal["tool"] = "tool"
    tool_call_id: str = ""
    title: str | None = None
    status: str | None = None
    kind: str | None = None
    path: str | None = None
    output: str | None = None

    def to_dict(self) -> dict:
        """Serialize to dict, omitting None fields."""
        return {k: v for k, v in asdict(self).items() if v is not None}


@dataclass
class PlanStep:
    """A single step in an agent plan."""

    content: str = ""
    status: str = "pending"


@dataclass
class PlanEntry:
    """A snapshot of the agent's plan at a point in time."""

    type: Literal["plan"] = "plan"
    entries: list[PlanStep] = field(default_factory=list)


TraceEntry = MessageEntry | ThoughtEntry | ToolEntry | PlanEntry


def trace_entry_to_dict(entry: TraceEntry) -> dict:
    """Convert a trace entry to a JSON-serializable dict."""
    if isinstance(entry, ToolEntry):
        return entry.to_dict()
    return asdict(entry)


class Trace:
    """Incremental, coalesced trace of an agent's ACP events.

    Call `ingest` for each raw ACP event. Call `tail` to read the last N
    entries including any in-progress (unflushed) text.
    """

    def __init__(self) -> None:
        self._entries: list[TraceEntry] = []
        self._msg_parts: list[str] = []
        self._thought_parts: list[str] = []
        self._tool_calls: dict[str, ToolEntry] = {}

    def ingest(self, params: dict) -> None:
        """Process a single ACP session/update event."""
        update = params.get("update", {})
        session_update = update.get("sessionUpdate")

        if session_update == "agent_message_chunk":
            self._flush_thoughts()
            content = update.get("content", {})
            if content.get("type") == "text":
                self._msg_parts.append(content.get("text", ""))

        elif session_update == "agent_thought_chunk":
            self._flush_messages()
            content = update.get("content", {})
            if content.get("type") == "text":
                self._thought_parts.append(content.get("text", ""))

        elif session_update in ("tool_call", "tool_call_update"):
            self._flush_text()
            tool_call_id = update.get("toolCallId", "")
            existing = self._tool_calls.get(tool_call_id)
            if existing:
                _merge_tool(update, existing)
            else:
                entry = ToolEntry(tool_call_id=tool_call_id)
                _merge_tool(update, entry)
                self._entries.append(entry)
                self._tool_calls[tool_call_id] = entry

        elif session_update == "plan":
            self._flush_text()
            raw_entries = update.get("entries", [])
            self._entries.append(
                PlanEntry(
                    entries=[
                        PlanStep(
                            content=e.get("content", ""),
                            status=e.get("status", "pending"),
                        )
                        for e in raw_entries
                    ],
                )
            )

    def tail(self, n: int = 50) -> list[TraceEntry]:
        """Return the last `n` trace entries.

        Includes any in-progress message or thought that has not yet been
        flushed (because the agent is still streaming).
        """
        entries = list(self._entries)
        if self._msg_parts:
            entries.append(MessageEntry(text=_truncate("".join(self._msg_parts))))
        if self._thought_parts:
            entries.append(ThoughtEntry(text=_truncate("".join(self._thought_parts))))
        return entries[-n:]

    def _flush_messages(self) -> None:
        if not self._msg_parts:
            return
        self._entries.append(MessageEntry(text=_truncate("".join(self._msg_parts))))
        self._msg_parts.clear()

    def _flush_thoughts(self) -> None:
        if not self._thought_parts:
            return
        self._entries.append(ThoughtEntry(text=_truncate("".join(self._thought_parts))))
        self._thought_parts.clear()

    def _flush_text(self) -> None:
        self._flush_messages()
        self._flush_thoughts()


def _truncate(text: str) -> str:
    """Truncate text to `_TEXT_LIMIT`, keeping the tail."""
    if len(text) > _TEXT_LIMIT:
        return text[-_TEXT_LIMIT:]
    return text


def _merge_tool(update: dict, entry: ToolEntry) -> None:
    """Merge ACP tool call fields into a ToolEntry."""
    for key in ("title", "status", "kind"):
        val = update.get(key)
        if val is not None:
            setattr(entry, key, val)
    locations = update.get("locations")
    if locations and isinstance(locations, list) and len(locations) > 0:
        path = locations[0].get("path")
        if path:
            entry.path = path
    raw_output = update.get("rawOutput")
    if raw_output is not None:
        if isinstance(raw_output, str) and len(raw_output) > _TEXT_LIMIT:
            raw_output = raw_output[-_TEXT_LIMIT:]
        entry.output = raw_output
