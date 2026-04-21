"""Tests for ``ramure.bubble``: forward child-handle events onto the
current process's event stream, tagging ``Event.source`` so the
origin is visible without mixing it into the application payload.
"""

from __future__ import annotations

import asyncio

from ramure import (
    LocalImage,
    agent_process,
    bubble,
    done,
    emit,
    spawn,
    wait,
)


# ---------------------------------------------------------------------------
# Fixtures: two trivial @agent_process functions -- one child emits
# typical dict payloads, one emits non-dict payloads. ``bubble`` must
# pass ``data`` through untouched in both cases.
# ---------------------------------------------------------------------------


@agent_process
async def _child() -> None:
    emit("c_start", {"x": 1})
    emit("c_progress", {"x": 2})
    emit("c_done")
    done(None)


@agent_process
async def _child_non_dict() -> None:
    emit("plain", "hello")
    emit("blank")       # None
    emit("numeric", 42)
    done(None)


# ---------------------------------------------------------------------------
# Tests. Each runs inside a root @agent_process so we get a real
# runtime + scope without boilerplate.
# ---------------------------------------------------------------------------


def _my_events() -> list:
    """Helper: snapshot the current process's own event stream."""
    from ramure.process import _require_scope
    return [
        (e.type, e.data, e.source)
        for e in _require_scope().events.snapshot()
    ]


def test_bubble_without_source_forwards_verbatim() -> None:
    """Default: no source set. Events land on parent's stream with
    ``source=None`` and ``data`` unchanged."""

    @agent_process(image=LocalImage())
    async def parent() -> list:
        handle = spawn(_child)
        bubble(handle)
        async for _ in handle.events:
            pass
        done(_my_events())
        return await wait()

    result = asyncio.run(parent())
    by_type = {t: (d, s) for (t, d, s) in result}
    assert by_type["c_start"]    == ({"x": 1}, None)
    assert by_type["c_progress"] == ({"x": 2}, None)
    assert by_type["c_done"]     == (None,    None)


def test_bubble_with_source_tags_event_envelope() -> None:
    """With a source, each bubbled event's ``Event.source`` is set
    and ``data`` is passed through unchanged -- no key stuffing."""

    @agent_process(image=LocalImage())
    async def parent() -> list:
        handle = spawn(_child)
        bubble(handle, source="kid")
        async for _ in handle.events:
            pass
        done(_my_events())
        return await wait()

    result = asyncio.run(parent())
    by_type = {t: (d, s) for (t, d, s) in result}
    # Dict payload is untouched.
    assert by_type["c_start"]    == ({"x": 1}, "kid")
    assert by_type["c_progress"] == ({"x": 2}, "kid")
    # None payload stays None. No {"source": "kid"} wrapper.
    assert by_type["c_done"]     == (None,    "kid")


def test_bubble_passes_non_dict_payloads_through_unchanged() -> None:
    """No ``_`` coercion. Strings, ints, None all arrive as they
    were emitted; only ``Event.source`` changes."""

    @agent_process(image=LocalImage())
    async def parent() -> list:
        handle = spawn(_child_non_dict)
        bubble(handle, source="t")
        async for _ in handle.events:
            pass
        done(_my_events())
        return await wait()

    result = asyncio.run(parent())
    by_type = {t: (d, s) for (t, d, s) in result}
    assert by_type["plain"]   == ("hello", "t")
    assert by_type["blank"]   == (None,    "t")
    assert by_type["numeric"] == (42,      "t")


def test_bubble_preserves_inner_source_when_chained() -> None:
    """If a child has ALREADY tagged its own source (e.g. it bubbled
    grandchildren), that inner source wins over ours. An event's
    source tells you where it originated, not where it last hopped."""

    @agent_process
    async def _grandchild() -> None:
        emit("deep", {"v": 1})
        done(None)

    @agent_process
    async def _middle() -> None:
        gc = spawn(_grandchild)
        bubble(gc, source="grandchild")
        async for _ in gc.events:
            pass
        done(None)

    @agent_process(image=LocalImage())
    async def parent() -> list:
        mid = spawn(_middle)
        bubble(mid, source="middle")
        async for _ in mid.events:
            pass
        done(_my_events())
        return await wait()

    result = asyncio.run(parent())
    by_type = {t: (d, s) for (t, d, s) in result}
    # The grandchild's event propagates all the way up keeping
    # ``source="grandchild"``, NOT overwritten to ``"middle"``.
    assert by_type["deep"] == ({"v": 1}, "grandchild")


def test_bubble_returns_unsubscribe() -> None:
    """``bubble`` returns a callable (idempotent); stopping the forward
    is tested at the Stream.subscribe level in test_events.py."""

    @agent_process(image=LocalImage())
    async def parent() -> None:
        handle = spawn(_child)
        off = bubble(handle, source="kid")
        assert callable(off)
        async for _ in handle.events:
            pass
        off()
        off()       # idempotent
        done(None)
        return await wait()

    asyncio.run(parent())


def test_bubble_multiple_children_distinct_sources() -> None:
    """Three children, three bubble calls, each event gets its own
    source tag without payload mangling."""

    @agent_process
    async def _child_a() -> None:
        emit("evt", {"k": "a"})
        done(None)

    @agent_process
    async def _child_b() -> None:
        emit("evt", {"k": "b"})
        done(None)

    @agent_process(image=LocalImage())
    async def parent() -> list:
        a = spawn(_child_a)
        b = spawn(_child_b)
        bubble(a, source="A")
        bubble(b, source="B")
        async for _ in a.events:
            pass
        async for _ in b.events:
            pass
        done(_my_events())
        return await wait()

    result = asyncio.run(parent())
    # Same event type from both children; distinguish by source.
    tagged = [(d, s) for (t, d, s) in result if t == "evt"]
    assert ({"k": "a"}, "A") in tagged
    assert ({"k": "b"}, "B") in tagged
