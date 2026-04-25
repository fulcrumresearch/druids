# ramure

ramure is an opinionated and lightweight Python library for building reliable agent software. It makes it easy to define programs where agents communicate across environments to accomplish a task.

Agent software are complex distributed systems. The goal of ramure is to make it easier to build and robustify these systems, in 2 notable ways:

- **Infrastructure primitives**: for agent communication, provisioning, and the software environments in which they run
- **Fault-tolerant and modular design**: ramure's abstractions encourage modularity and fault-tolerance in the design of agent software, using ideas from distributed systems programming like Erlang

See [here] for the motivation behind ramure's design.


Here is an example 

insert commented example


```
uv run <program>
```

Some examples tasks that ramure makes easy with agents:

- optimization
- custom software generation pipelines with user input
- data pipelines


## Install

```bash
pip install ramure
```

`ramure` depends on `pi` and `tmux` for the machines on which agents run.

## Quick start

```python
import asyncio
from ramure import LocalImage, agent, agent_process, done, wait


@agent_process(image=LocalImage(), timeout=30)
async def summarize(text: str) -> str:
    worker = await agent("worker")

    @worker.on("finish")
    async def on_finish(summary: str) -> str:
        """Call this with your summary when done."""
        done(summary)
        return "Done."

    await worker.send(f"Summarize this text, then call finish:\n\n{text}")
    return await wait()


asyncio.run(summarize("The quick brown fox jumped over the lazy dog. " * 20))
```

Here the agent does the fuzzy work, while Python defines the lifecycle and what counts as completion. Structuring how information moves in your program makes it easier to reliably use agent labor.

## Core concepts

### Agent processes

We define an **agent process** (AP) as a set of agents working and collaborating on machines with a shared lifecycle.

In ramure, the central object is the `agent_process`, defined by the `@agent_process` decorator. Inside the function, you define agents and machines as well as how they should communicate.

```python
@agent_process
async def build_and_review(spec: str) -> str:
    builder = await agent("builder")
    auditor = await agent("auditor")
    connect(builder, auditor)

    @builder.on("submit")
    async def on_submit(code: str):
        await auditor.send(f"Review:\n{code}")
        return "Submitted"

    @auditor.on("approve")
    async def on_approve(code: str):
        done(code)
        return "Approved"

    @auditor.on("reject")
    async def on_reject(feedback: str):
        await builder.send(f"Fix: {feedback}")
        return "Sent back"

    await builder.send(f"Implement: {spec}")
    await auditor.send("Review the builder's work.")
    return await wait()
```

A process gives you one place to define:

- which agents and machines belong to the task
- what success and failure mean
- which events matter
- how child work is supervised
- what gets cleaned up when the task ends

A few lifecycle rules:

- **Root process**: no active runtime yet, so ramure creates one and tears it down on return.
- **Nested process**: inherits its parent's runtime and runs as a child scope.
- `done(value)` / `fail(reason)` signal completion from deterministic code or agent tool handlers.
- `await wait()` blocks until `done()` or `fail()` is called.
- Agents and machines owned by a process are cleaned up automatically when it returns.
- `image=` sets the default environment for agents and machines created inside that process.

### Composition

Processes compose by calling each other like normal async functions:

```python
@agent_process(image=LocalImage())
async def main():
    code = await write_code("fibonacci function")
    review = await review_code(code)
    return code
```

You can also fan out concurrently with `asyncio.gather`:

```python
@agent_process(image=LocalImage())
async def main():
    results = await asyncio.gather(
        research("Rust"),
        research("Python"),
    )
    return results
```

### Observation, bubbling, and retry

APs compose. An AP can call another AP the way you'd call any async function, or it can `spawn()` and obtain a `ProcessHandle` whose events become observable in real time:

```python
@agent_process(image=LocalImage())
async def main():
    handle = spawn(flaky_task, "write a haiku")

    async for event in handle.events:
        if event.type == "failed":
            handle = spawn(flaky_task, "write a haiku")
        elif event.type == "done":
            return event.data
```

That handle holds on to all the events of the child AP, and lets you observe it as it's running, retry if it fails, and pass events along to higher-level supervisors using `bubble()`.

Processes can emit custom events with `emit(type, data)`. If a supervisor wants child events to appear on its own event stream, use `bubble()`:

```python
@agent_process
async def worker_pool(specs: list[str]) -> None:
    for i, spec in enumerate(specs):
        tid = f"t{i:04d}"
        bubble(spawn(run_task, tid, spec), source=tid)
    await wait()
```

Now a parent observing `spawn(worker_pool, specs).events` can see child events too, tagged with `source=tid`.

### Endpoints and afforded interfaces

APs can also encode specific ways in which they are interacted with, by exposing an API that can be called in code, or via another agent. This lets you give a component narrow affordances instead of leaking all of its internal state and tools.

For example, a worker-pool process can expose `add_task()` and `tasks()`, while internally spawning one child process per task:

```python
@agent_process
async def worker_pool() -> None:
    specs: dict[str, str] = {}

    @expose
    async def add_task(spec: str) -> str:
        tid = f"t{len(specs):04d}"
        specs[tid] = spec
        emit("task_added", {"task_id": tid, "spec": spec})
        bubble(spawn(run_task, tid, spec), source=tid)
        return tid

    @expose
    async def tasks() -> dict[str, str]:
        return dict(specs)

    emit("ready", None)
    await wait()
```

Here `run_task` can be another `@agent_process`, such as a single-worker task runner like the quick-start example.

The parent can either call those endpoints directly or attach them as tools on another agent:

```python
@agent_process
async def main():
    pool = spawn(worker_pool)

    async for event in pool.events:
        if event.type == "ready":
            break

    task_id = await pool.call("add_task", spec="Write a haiku about git rebase.")

    dispatcher = await agent("dispatcher")
    await pool.attach(dispatcher, prefix="pool_")
    await dispatcher.send("Use pool_add_task to delegate work.")

    return task_id
```

Endpoints run inside the child process's scope, so calls to `emit()`, `done()`, and `fail()` inside an endpoint affect the child, not the caller.

Child-owned agents are also visible through `handle.agents` once the child has created them.

## API

### Decorator

- `@agent_process(image=, timeout=, log_dir=, host=, port=, base_url=)` — wrap an async function as a process

### Ambient functions

- `await agent(name, system_prompt=, image=, machine=)` — create an agent
- `await machine(image=)` — spawn a standalone machine
- `connect(a, b, direction=)` — allow agents to message/send files
- `done(result)` — signal process success
- `fail(reason)` — signal process failure
- `await wait()` — block until `done()` or `fail()`
- `emit(type, data)` — emit a process event
- `spawn(fn, *args, **kwargs)` — run a process in the background, returns `ProcessHandle`
- `bubble(handle, source=)` — forward a child process's events onto the current process stream
- `@expose` — register an async function as an endpoint callable via `handle.call()` or attachable via `handle.attach()`
- `current_runtime()` — access the active runtime (rarely needed)

### Agent methods

- `agent.on(tool_name)` — decorator to register an async tool handler
- `agent.send(message)` — send a message to the agent
- `agent.exec(command)` — run a shell command on the agent's machine
- `agent.events` — async-iterable log of raw agent events

### ProcessHandle

- `handle.events` — async-iterable stream of process events
- `handle.agents` — dict of the child's agents created so far
- `await handle.call(name, **kwargs)` — call an endpoint
- `await handle.attach(agent, only=, prefix=)` — register endpoints as tools on an agent
- `handle.cancel()` — cancel the process

## CLI

Running a root `@agent_process` opens a Unix socket at
`~/.ramure/runtimes/{execution_id}.sock` and writes a per-run log tree
under `~/.ramure/logs/{execution_id}/`. The `ramure` CLI uses these:

```text
ramure ls                         # live runs
ramure status [--id <prefix>]     # agents, machines, connections
ramure send <agent> <msg> [--id <prefix>]
ramure connect <agent> [--id <prefix>]  # tmux attach
ramure ssh <agent> [--id <prefix>]      # shell on the agent's machine
```

`--id` takes an execution-id prefix. Omit it when there's only one live run.
All commands require the run to be live (socket present). Finished-run logs
remain under `~/.ramure/logs/{execution_id}/`.
