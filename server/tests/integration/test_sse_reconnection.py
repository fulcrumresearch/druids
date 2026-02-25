"""Integration test for SSE reader reconnection.

Verifies that AgentConnection recovers after the SSEReader task is
cancelled and restarted, using the bridge's Last-Event-ID replay.
"""

import asyncio

from orpheus.lib.connection import AgentConnection


async def test_reconnect_after_reader_cancel(bridge_with_stub):
    """Cancel SSEReader, restart it, verify the next prompt still works."""
    events = []

    async def on_update(params):
        events.append(params)

    conn = AgentConnection(bridge_with_stub)
    try:
        conn.on("session/update", on_update)
        await conn.start()
        await conn.new_session()

        # First prompt succeeds
        r1 = await asyncio.wait_for(conn.prompt("First"), timeout=5)
        assert r1["stopReason"] == "end_turn"
        await asyncio.sleep(0.3)
        first_event_count = len(events)
        assert first_event_count >= 3  # chunk + tool_call + tool_call_update

        # Record the SSE cursor before we break the reader
        last_id_before = conn._reader._last_event_id
        assert last_id_before > 0

        # Kill the SSE reader (simulates connection drop)
        await conn._reader.stop()

        # Restart it -- reconnects with Last-Event-ID so no data is lost
        conn._reader.start()
        await asyncio.sleep(0.5)

        # Second prompt should succeed through the re-established stream
        r2 = await asyncio.wait_for(conn.prompt("Second"), timeout=5)
        assert r2["stopReason"] == "end_turn"
        await asyncio.sleep(0.3)

        # New notifications arrived
        assert len(events) > first_event_count

        # SSE cursor advanced
        assert conn._reader._last_event_id > last_id_before
    finally:
        await conn.close()
