"""Tests for Stream."""

from __future__ import annotations

import asyncio

from druids.stream import Event, Stream


def test_emit_and_iterate() -> None:
    async def run() -> None:
        stream = Stream()
        stream.emit("a", {"x": 1})
        stream.emit("b", {"x": 2})
        stream.close()

        collected = []
        async for event in stream:
            collected.append(event)

        assert collected == [
            Event(type="a", data={"x": 1}),
            Event(type="b", data={"x": 2}),
        ]

    asyncio.run(run())


def test_iterate_waits_for_new_events() -> None:
    async def run() -> None:
        stream = Stream()
        collected: list[Event] = []

        async def consumer():
            async for event in stream:
                collected.append(event)

        task = asyncio.create_task(consumer())
        await asyncio.sleep(0.01)

        stream.emit("first")
        await asyncio.sleep(0.01)
        assert len(collected) == 1

        stream.emit("second")
        await asyncio.sleep(0.01)
        assert len(collected) == 2

        stream.close()
        await task

        assert [e.type for e in collected] == ["first", "second"]

    asyncio.run(run())


def test_close_stops_iteration() -> None:
    async def run() -> None:
        stream = Stream()
        stream.emit("before_close")
        stream.close()

        collected = []
        async for event in stream:
            collected.append(event)

        assert len(collected) == 1
        assert collected[0].type == "before_close"

    asyncio.run(run())


def test_emit_after_close_is_ignored() -> None:
    async def run() -> None:
        stream = Stream()
        stream.emit("a")
        stream.close()
        stream.emit("b")

        assert len(stream) == 1

    asyncio.run(run())


def test_multiple_iterators() -> None:
    async def run() -> None:
        stream = Stream()
        stream.emit("one")
        stream.emit("two")
        stream.close()

        collected_a = [e async for e in stream]
        collected_b = [e async for e in stream]

        assert len(collected_a) == 2
        assert len(collected_b) == 2

    asyncio.run(run())
