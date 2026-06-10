"""Tests for Stream."""

from __future__ import annotations

import asyncio

from ramure.stream import Event, Stream


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


# ---------------------------------------------------------------------------
# Stream.subscribe
# ---------------------------------------------------------------------------


def test_subscribe_replays_existing_then_sees_live() -> None:
    """A subscriber registered after some emits sees the backlog, in
    order, before returning; and sees every subsequent emit too."""
    stream = Stream()
    stream.emit("a")
    stream.emit("b")

    seen: list[Event] = []
    stream.subscribe(seen.append)
    # Replay already happened by the time subscribe returned.
    assert [e.type for e in seen] == ["a", "b"]

    stream.emit("c")
    assert [e.type for e in seen] == ["a", "b", "c"]


def test_subscribe_no_replay() -> None:
    """``replay=False`` skips the existing buffer; only new events go."""
    stream = Stream()
    stream.emit("old")

    seen: list[Event] = []
    stream.subscribe(seen.append, replay=False)
    assert seen == []

    stream.emit("new")
    assert [e.type for e in seen] == ["new"]


def test_subscribe_unsubscribe() -> None:
    stream = Stream()
    seen: list[Event] = []
    off = stream.subscribe(seen.append)

    stream.emit("one")
    off()
    stream.emit("two")

    assert [e.type for e in seen] == ["one"]
    # Calling the returned callable twice is a no-op.
    off()


def test_subscribe_raising_subscriber_is_dropped() -> None:
    """A subscriber that raises during delivery must be removed so one
    bad listener can't break the stream for everyone else."""
    stream = Stream()
    seen_ok: list[Event] = []
    call_count = {"bad": 0}

    def bad(ev: Event) -> None:
        call_count["bad"] += 1
        raise RuntimeError("boom")

    stream.subscribe(bad)
    stream.subscribe(seen_ok.append)

    stream.emit("first")
    stream.emit("second")

    assert call_count["bad"] == 1        # only called once, then dropped
    assert [e.type for e in seen_ok] == ["first", "second"]


def test_subscribe_raising_during_replay_never_subscribes() -> None:
    """A subscriber that raises on its FIRST replayed item should not
    end up registered. The follow-up emits must not find it."""
    stream = Stream()
    stream.emit("a")
    call_count = {"n": 0}

    def bad(ev: Event) -> None:
        call_count["n"] += 1
        raise RuntimeError("boom")

    off = stream.subscribe(bad)
    stream.emit("b")

    assert call_count["n"] == 1          # raised during replay, not again
    off()                                # still safe to call


def test_subscribe_preserves_async_iter() -> None:
    """Subscribers and ``async for`` consumers must both see every event."""
    async def run() -> None:
        stream = Stream()
        seen_sync: list[Event] = []
        stream.subscribe(seen_sync.append)

        stream.emit("one")
        stream.emit("two")
        stream.close()

        seen_async = [e async for e in stream]

        assert [e.type for e in seen_sync] == ["one", "two"]
        assert [e.type for e in seen_async] == ["one", "two"]

    asyncio.run(run())


# ---------------------------------------------------------------------------
# Stream.listen_for
# ---------------------------------------------------------------------------


def test_listen_for_default_skips_existing_match() -> None:
    async def run() -> None:
        stream = Stream()
        stream.emit("tool_call", {"tool": "report"})

        event = await stream.listen_for(
            "tool_call",
            timeout=0.01,
            where=lambda e: e.data.get("tool") == "report",
        )

        assert event is None

    asyncio.run(run())


def test_listen_for_replay_true_checks_existing_match() -> None:
    async def run() -> None:
        stream = Stream()
        stream.emit("tool_call", {"tool": "report"})

        event = await stream.listen_for(
            "tool_call",
            timeout=0.1,
            where=lambda e: e.data.get("tool") == "report",
            replay=True,
        )

        assert event == Event(type="tool_call", data={"tool": "report"})

    asyncio.run(run())


def test_listen_for_waits_for_live_match() -> None:
    async def run() -> None:
        stream = Stream()

        task = asyncio.create_task(
            stream.listen_for(
                "tool_call",
                timeout=0.5,
                where=lambda e: e.data.get("tool") == "report",
            )
        )
        await asyncio.sleep(0)

        stream.emit("tool_call", {"tool": "other"})
        stream.emit("tool_call", {"tool": "report"})

        event = await task
        assert event == Event(type="tool_call", data={"tool": "report"})

    asyncio.run(run())


def test_listen_for_returns_none_on_timeout() -> None:
    async def run() -> None:
        stream = Stream()

        event = await stream.listen_for("done", timeout=0.01)

        assert event is None

    asyncio.run(run())


def test_listen_for_returns_none_when_stream_closes() -> None:
    async def run() -> None:
        stream = Stream()
        task = asyncio.create_task(stream.listen_for("done"))
        await asyncio.sleep(0)

        stream.close()

        assert await task is None

    asyncio.run(run())


# ---------------------------------------------------------------------------
# Event.source: envelope metadata
# ---------------------------------------------------------------------------


def test_emit_default_source_is_none() -> None:
    stream = Stream()
    stream.emit("a", {"x": 1})
    ev = stream.snapshot()[0]
    assert ev.source is None
    assert ev.data == {"x": 1}


def test_emit_preserves_source_field() -> None:
    stream = Stream()
    stream.emit("a", {"x": 1}, source="pool")
    ev = stream.snapshot()[0]
    assert ev.type == "a"
    assert ev.data == {"x": 1}         # data is NOT touched
    assert ev.source == "pool"


def test_event_source_survives_async_iter_and_subscribe() -> None:
    async def run() -> None:
        stream = Stream()
        seen_sync: list[Event] = []
        stream.subscribe(seen_sync.append)

        stream.emit("a", {"k": 1}, source="pool")
        stream.emit("b", {"k": 2}, source="env")
        stream.close()

        seen_async = [e async for e in stream]

        assert [(e.type, e.source) for e in seen_sync]  == [("a", "pool"), ("b", "env")]
        assert [(e.type, e.source) for e in seen_async] == [("a", "pool"), ("b", "env")]

    asyncio.run(run())
