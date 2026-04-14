"""Example: dynamic agent spawning from handlers.

A finder agent discovers tasks and spawns implementation agents for each one.
Demonstrates:
  - Dynamic agent creation inside handlers
  - Ambient runtime helpers
  - Multiple agents sharing work
"""

from __future__ import annotations

import asyncio

from druids import LocalImage, agent, agent_runtime, exit, wait


task_count = 0


@agent_runtime(image=LocalImage())
async def main() -> None:
    finder = await agent("finder")

    @finder.on("spawn_task")
    async def on_spawn(name: str = "", spec: str = "") -> str:
        """Spawn an implementation agent for a task."""
        global task_count
        task_count += 1
        agent_name = f"impl-{task_count}"
        impl = await agent(agent_name)

        @impl.on("task_complete")
        async def on_task_complete(summary: str = "") -> str:
            """Signal that the task is complete."""
            await finder.send(f"Task '{name}' completed: {summary}")
            return "Noted."

        await impl.send(
            f"Complete this task: {spec}\nWhen done, call task_complete with a summary."
        )
        return f"Spawned {agent_name} for task '{name}'."

    @finder.on("all_done")
    async def on_all_done() -> str:
        """Signal all tasks have been spawned and completed."""
        exit(f"Completed {task_count} tasks.")
        return "Finishing."

    await finder.send(
        "You are a task finder. Spawn exactly 2 tasks:\n"
        "1. Call spawn_task with name='hello' and spec='Write a file /tmp/hello.txt containing Hello World'\n"
        "2. Call spawn_task with name='goodbye' and spec='Write a file /tmp/goodbye.txt containing Goodbye World'\n"
        "After spawning both, call all_done."
    )

    print(await wait())


if __name__ == "__main__":
    asyncio.run(main())
