"""Async-iterable event streams for agents and processes."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Event:
    """A single event in a stream."""

    type: str
    data: Any = None


class EventStream:
    """Async-iterable event stream.

    Events are emitted via emit() and consumed via async iteration.
    Call close() to signal no more events will be emitted.
    """

    def __init__(self) -> None:
        self._entries: list[Event] = []
        self._waiters: list[asyncio.Event] = []
        self._closed = False

    def emit(self, event_type: str, data: Any = None) -> None:
        """Emit an event into this stream."""
        if self._closed:
            return
        self._entries.append(Event(type=event_type, data=data))
        for w in self._waiters:
            w.set()

    def close(self) -> None:
        """Signal that no more events will be emitted."""
        if self._closed:
            return
        self._closed = True
        for w in self._waiters:
            w.set()

    @property
    def closed(self) -> bool:
        return self._closed

    def __len__(self) -> int:
        return len(self._entries)

    def __aiter__(self) -> _EventStreamIterator:
        return _EventStreamIterator(self)


class _EventStreamIterator:
    def __init__(self, stream: EventStream) -> None:
        self._stream = stream
        self._index = 0

    async def __anext__(self) -> Event:
        while True:
            if self._index < len(self._stream._entries):
                event = self._stream._entries[self._index]
                self._index += 1
                return event
            if self._stream._closed:
                raise StopAsyncIteration
            waiter = asyncio.Event()
            self._stream._waiters.append(waiter)
            try:
                await waiter.wait()
            finally:
                self._stream._waiters.remove(waiter)
