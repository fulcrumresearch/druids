"""Example: dynamic agent spawning from handlers.

A finder agent discovers tasks and spawns impl agents for each one.
Demonstrates:
  - Dynamic agent creation inside handlers
  - ctx.connect() at runtime
  - Multiple agents sharing work
"""

from druids import Context, LocalImage

ctx = Context(image=LocalImage())

task_count = 0

finder = ctx.agent(
    "finder",
    prompt=(
        "You are a task finder. Spawn exactly 2 tasks:\n"
        "1. Call spawn_task with name='hello' and spec='Write a file /tmp/hello.txt containing Hello World'\n"
        "2. Call spawn_task with name='goodbye' and spec='Write a file /tmp/goodbye.txt containing Goodbye World'\n"
        "After spawning both, call all_done."
    ),
)


@finder.on("spawn_task")
def on_spawn(name="", spec=""):
    """Spawn an implementation agent for a task."""
    global task_count
    task_count += 1
    agent_name = f"impl-{task_count}"
    impl = ctx.agent(
        agent_name,
        prompt=f"Complete this task: {spec}\nWhen done, call task_complete with a summary.",
    )

    @impl.on("task_complete")
    def on_task_complete(summary=""):
        """Signal that the task is complete."""
        finder.send(f"Task '{name}' completed: {summary}")
        return "Noted."

    return f"Spawned {agent_name} for task '{name}'."


@finder.on("all_done")
def on_all_done():
    """Signal all tasks have been spawned and completed."""
    ctx.done(f"Completed {task_count} tasks.")
    return "Finishing."


ctx.run()
