"""Async-iterable event stream primitive.

A Stream is an append-only, closable, async-iterable buffer of Events.
Consumers iterate with ``async for``; iteration replays existing items
from index 0, blocks for new ones, and stops on ``close()``.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class Event:
    """A single event: a type string and an arbitrary data payload.

    ``source`` is optional envelope metadata identifying which
    process, agent, or other origin emitted the event. It is
    preserved by :meth:`Stream.emit` and by :func:`ramure.bubble`
    so events can be traced across layers of supervision without
    mixing that metadata into the application payload.
    """

    type: str
    data: Any = None
    source: str | None = None


class Stream:
    """Append-only, async-iterable, closable buffer of Events."""

    def __init__(self) -> None:
        self._items: list[Event] = []
        self._waiters: list[asyncio.Event] = []
        self._subscribers: list[Callable[[Event], None]] = []
        self._closed = False

    def emit(
        self, type: str, data: Any = None, *, source: str | None = None
    ) -> Event | None:
        """Emit an event.

        ``source`` is optional envelope metadata -- a short tag
        identifying the origin of the event (typically set by
        :func:`ramure.bubble` when forwarding across process
        boundaries). ``data`` is not touched.

        Returns the event, or ``None`` if the stream is closed.
        """
        if self._closed:
            return None
        event = Event(type=type, data=data, source=source)
        self._items.append(event)
        for w in self._waiters:
            w.set()
        # Subscribers are called synchronously in the emitter's thread.
        # A subscriber that raises is dropped, not propagated: one bad
        # listener shouldn't be able to break the stream for everyone
        # else (same policy as ``Log._deliver``).
        for cb in list(self._subscribers):
            try:
                cb(event)
            except Exception:
                try:
                    self._subscribers.remove(cb)
                except ValueError:
                    pass
        return event

    def subscribe(
        self, cb: Callable[[Event], None], *, replay: bool = True
    ) -> Callable[[], None]:
        """Register ``cb`` to be called synchronously for every new event.

        If ``replay`` is true (default), ``cb`` is also called for
        every event already in the buffer, in order, before returning.
        This keeps subscribers consistent with ``async for`` iteration
        -- both see every event.

        Returns an idempotent unsubscribe callable. Calling it after
        the stream has closed is a no-op.

        A subscriber that raises is dropped silently (see ``emit``).
        Use this for cheap wiring (e.g. forwarding events to a parent
        stream); heavy work belongs in an ``async for`` consumer.
        """
        if replay:
            for ev in self._items:
                try:
                    cb(ev)
                except Exception:
                    # Treat a raise during replay the same as during
                    # live delivery: never add the subscriber.
                    def _noop() -> None:
                        return None

                    return _noop
        self._subscribers.append(cb)

        def _unsubscribe() -> None:
            try:
                self._subscribers.remove(cb)
            except ValueError:
                pass

        return _unsubscribe

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

    async def listen_for(
        self,
        type: str | None = None,
        *,
        timeout: float | None = None,
        where: Callable[[Event], bool] | None = None,
        replay: bool = False,
    ) -> Event | None:
        """Wait for the first matching event.

        ``type`` matches :attr:`Event.type`; ``where`` can add an
        arbitrary predicate over the full event. Returns the matching
        event, or ``None`` if ``timeout`` expires or the stream closes
        before a match.

        By default this listens only for events emitted after this call
        starts. Pass ``replay=True`` to check buffered events first.
        """
        def matches(event: Event) -> bool:
            if type is not None and event.type != type:
                return False
            return where(event) if where is not None else True

        async def find() -> Event | None:
            start = 0 if replay else len(self._items)
            async for event in _StreamIter(self, start=start):
                if matches(event):
                    return event
            return None

        if timeout is None:
            return await find()
        try:
            return await asyncio.wait_for(find(), timeout=timeout)
        except asyncio.TimeoutError:
            return None

    def __len__(self) -> int:
        return len(self._items)

    def __aiter__(self) -> _StreamIter:
        return _StreamIter(self)


class _StreamIter:
    def __init__(self, stream: Stream, *, start: int = 0) -> None:
        self._stream = stream
        self._index = start

    def __aiter__(self) -> _StreamIter:
        return self

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
