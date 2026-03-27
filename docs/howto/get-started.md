# Getting started

Druids coordinates coding agents across remote machines. You write a Python program that defines agents, what they can do, and how they talk to each other. Druids handles provisioning, execution, and communication.

This guide walks you through a working program so you can see the pieces fit together. By the end you'll have run a multi-agent execution that profiles slow code, spawns parallel optimizers, and merges their fixes.

## Install

See the [quickstart](../../QUICKSTART.md) to set up the server locally. Then install the CLI:

```bash
uv tool install druids
```

## The program

Below is the complete program, broken into sections. You don't need to type it out — it's already included when you run `druids init`. It targets [logstat](https://github.com/fulcrumresearch/logstat), a sample log analytics tool with intentional performance bottlenecks.

### Create an agent

Every program is an `async def` that receives `ctx`, the execution context. `ctx.agent()` provisions a remote VM and starts a coding agent on it. The agent begins working on its prompt immediately.

```python
async def program(ctx):
    working_dir = "/home/agent/repo"
    optimizers = {}

    profiler = await ctx.agent(
        "profiler",
        system_prompt=PROFILER_PROMPT,
        prompt=f"Profile the code in `{working_dir}`.",
        working_directory=working_dir,
    )
```

### Define events

`@agent.on()` defines an event the agent can trigger. The agent sees it as a callable tool — with the function name, parameters, and docstring — but when it calls it, your program code runs. This is how agents modify state in a controlled way: the agent decides *when* to trigger an event, the program decides *what happens*.

Here, when the profiler finds a bottleneck, it triggers `spawn_optimizer`. The program responds by creating a new agent on the same machine.

```python
    @profiler.on("spawn_optimizer")
    async def on_spawn_optimizer(function_name="", file_path="", description=""):
        """Spawn an optimizer agent for a bottleneck."""
        name = f"optimizer-{len(optimizers) + 1}"
```

### Share a machine

`share_machine_with` puts the new agent on the same VM as the profiler. They share a filesystem, so the optimizer can read the profiler's generated logs and benchmark data.

```python
        optimizer = await ctx.agent(
            name,
            system_prompt=OPTIMIZER_PROMPT,
            prompt=f"Optimize `{function_name}` in `{file_path}`.\n\n{description}",
            share_machine_with=profiler,
        )
```

### Connect agents

By default, agents are isolated. `ctx.connect()` opens a channel between them so they can send messages.

```python
        ctx.connect(optimizer, profiler)
```

### Respond to events

Same pattern: the optimizer triggers `submit_fix` when it's done. The program records the result and messages the profiler so it knows to download the fixed file.

```python
        @optimizer.on("submit_fix")
        async def on_submit_fix(before_ms=0.0, after_ms=0.0, file_path="", summary=""):
            """Submit benchmark results and the file you changed."""
            await profiler.send(
                f"[{name}] `{function_name}`: {before_ms}ms -> {after_ms}ms. "
                f"Use download_file to get the fix."
            )
```

### Complete the execution

`await ctx.done()` ends the execution with a result.

```python
    @profiler.on("submit_results")
    async def on_submit_results(before_total=0.0, after_total=0.0, summary=""):
        """Submit the final aggregate benchmark."""
        await ctx.done({"before_ms": before_total, "after_ms": after_total})
```

## Run it

```bash
druids exec optimize --repo fulcrumresearch/logstat --no-setup
```

Go to the dashboard to watch the execution live — you'll see the profiler benchmarking, optimizers spawning, and agents coordinating as fixes come in.

## What's next

This example ran against a sample repo without any setup. To work on your own codebase, you'll need a **devbox** — a snapshotted VM with your repo cloned and dependencies installed. Agents start from it, so they get a working environment. See [Write a program](write-a-program) for how to set one up and write programs for your own projects.

To understand the design behind programs — how agents, events, and machines fit together — read [Program design](../explanation/program-design). For the full API surface, see the [reference docs](../reference/program-api).
