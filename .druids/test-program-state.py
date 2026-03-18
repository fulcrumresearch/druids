"""Test program that emits program_state events to verify frontend rendering."""

import asyncio


async def program(ctx, **kwargs):
    """Emit program_state events with different values to test frontend rendering."""

    # Initial state
    await ctx.emit("program_state", {
        "status": "initializing",
        "step": 1,
        "items_processed": 0,
    })

    await asyncio.sleep(2)

    # Update state
    await ctx.emit("program_state", {
        "status": "processing",
        "step": 2,
        "items_processed": 5,
        "current_item": "task_abc123",
    })

    await asyncio.sleep(2)

    # Final state
    await ctx.emit("program_state", {
        "status": "completed",
        "step": 3,
        "items_processed": 10,
        "total_time": "4s",
    })

    await asyncio.sleep(2)

    ctx.done("Test program completed successfully")
