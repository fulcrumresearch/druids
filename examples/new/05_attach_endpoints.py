"""Endpoints as agent tools: attach a process's endpoints to an agent.

A worker pool process exposes ``submit_task`` as an endpoint. The outer
process spawns the pool, creates a dispatcher agent, and attaches the
pool's endpoints as tools on the dispatcher. The dispatcher can then
call ``submit_task`` like any other tool.
"""

import asyncio
import uuid

from druids import (
    LocalImage,
    agent,
    agent_process,
    done,
    emit,
    expose,
    spawn,
    wait,
)


@agent_process
async def worker_pool() -> str:
    @expose
    async def submit_task(task: str) -> str:
        """Create a fresh worker to handle a task. Returns the worker name."""
        w = await agent(f"worker-{uuid.uuid4().hex[:8]}")
        expose(w)
        await w.send(f"Do this: {task}")
        return w.name

    emit("ready", None)
    return await wait()


@agent_process(image=LocalImage())
async def main() -> str:
    pool = spawn(worker_pool)

    # Wait for endpoints to be registered.
    async for event in pool.events:
        if event.type == "ready":
            break

    dispatcher = await agent("dispatcher")
    await pool.attach(dispatcher)

    @dispatcher.on("finish")
    async def on_finish(summary: str) -> str:
        done(summary)
        return "Done."

    await dispatcher.send(
        "Call submit_task three times with different small jobs, "
        "then call finish with a one-line summary."
    )
    return await wait()


if __name__ == "__main__":
    print(asyncio.run(main()))
