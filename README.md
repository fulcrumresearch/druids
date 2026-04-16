# druids

A lightweight async multi-agent orchestration library. Agents run as [pi](https://github.com/mariozechner/pi-coding-agent) instances in tmux sessions, coordinated by Python process functions.

## Quick start

```python
import asyncio
from druids import LocalImage, agent, agent_process, done, wait


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

## Core concepts

### Processes

The unit of composition is a **process function** — an async function decorated with `@agent_process` that creates agents, wires them up, and returns a result.

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

- **Root process** (no active runtime): creates a `Runtime` and websocket server, tears them down on return.
- **Nested process** (runtime already active): creates a child scope, inherits the runtime.
- `done(value)` / `fail(reason)` signal completion from tool handlers.
- `await wait()` blocks until `done()` or `fail()` is called.
- Agents are cleaned up automatically when their owning process returns.

### Composition

Processes compose by calling each other:

```python
@agent_process(image=LocalImage())
async def main():
    code = await write_code("fibonacci function")
    review = await review_code(code)
    return code
```

Concurrent fan-out with `asyncio.gather`:

```python
@agent_process(image=LocalImage())
async def main():
    results = await asyncio.gather(
        research("Rust"),
        research("Python"),
    )
    return results
```

### Observation and retry

`spawn()` runs a process in the background and returns a handle with an event stream:

```python
@agent_process(image=LocalImage())
async def main():
    handle = spawn(flaky_task, "write a haiku")

    async for event in handle.events:
        if event.type == "failed":
            handle = spawn(flaky_task, "write a haiku")
        if event.type == "done":
            return event.data
```

Processes can emit custom events with `emit(type, data)`. Agent event logs are also async-iterable via `agent.events`.

### Public agents and client events

A process can expose agents and callable handlers to its parent:

```python
@agent_process
async def worker_pool():
    @client_event
    async def submit_task(task: str) -> str:
        w = await agent(f"worker-{uuid.uuid4().hex[:8]}")
        public(w)
        await w.send(f"Do: {task}")
        return w.name

    return await wait()

handle = spawn(worker_pool)
name = await handle.call("submit_task", task="build a server")
worker = handle.agents[name]
```

## API

### Decorator

- `@agent_process(image=, timeout=, log_dir=)` — wrap an async function as a process

### Ambient functions

- `await agent(name, system_prompt=, image=, machine=)` — create an agent
- `await machine(image=)` — spawn a standalone machine
- `connect(a, b, direction=)` — allow agents to message/send files
- `done(result)` — signal process success
- `fail(reason)` — signal process failure
- `await wait()` — block until `done()` or `fail()`
- `emit(type, data)` — emit a process event
- `spawn(fn, *args, **kwargs)` — run a process in background, returns `ProcessHandle`
- `public(agent)` — expose an agent to the parent process
- `@client_event` — register a handler callable by the parent via `handle.call()`
- `current_runtime()` — access the runtime (rarely needed)

### Agent methods

- `agent.on(tool_name)` — decorator to register a tool handler
- `agent.send(message)` — send a message to the agent
- `agent.exec(command)` — run a shell command on the agent's machine
- `agent.events` — async-iterable log of raw agent events

### ProcessHandle

- `handle.events` — async-iterable stream of process events
- `handle.agents` — dict of public agents
- `handle.call(name, **kwargs)` — call a client event handler
- `handle.cancel()` — cancel the process
