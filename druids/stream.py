"""Async-iterable event stream primitive.

A Stream is an append-only, closable, async-iterable buffer of Events.
Consumers iterate with ``async for``; iteration replays existing items
from index 0, blocks for new ones, and stops on ``close()``.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Event:
    """A single event: a type string and an arbitrary data payload."""

    type: str
    data: Any = None


class Stream:
    """Append-only, async-iterable, closable buffer of Events."""

    def __init__(self) -> None:
        self._items: list[Event] = []
        self._waiters: list[asyncio.Event] = []
        self._closed = False

    def emit(self, type: str, data: Any = None) -> Event | None:
        """Emit an event. Returns the event, or None if the stream is closed."""
        if self._closed:
            return None
        event = Event(type=type, data=data)
        self._items.append(event)
        for w in self._waiters:
            w.set()
        return event

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for w in self._waiters:
            w.set()

    @property
    def closed(self) -> bool:
        return self._closed

    def snapshot(self) -> list[Event]:
        """Return a copy of items emitted so far, oldest first."""
        return list(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __aiter__(self) -> _StreamIter:
        return _StreamIter(self)


class _StreamIter:
    def __init__(self, stream: Stream) -> None:
        self._stream = stream
        self._index = 0

    async def __anext__(self) -> Event:
        while True:
            if self._index < len(self._stream._items):
                item = self._stream._items[self._index]
                self._index += 1
                return item
            if self._stream._closed:
                raise StopAsyncIteration
            waiter = asyncio.Event()
            self._stream._waiters.append(waiter)
            try:
                await waiter.wait()
            finally:
                self._stream._waiters.remove(waiter)
