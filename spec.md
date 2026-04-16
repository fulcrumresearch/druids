# Process Model Spec

## Overview

Replace the current `Runtime.exit()`/`fail()`/`wait()` model with a unified `@agent_process` decorator where **functions are the unit of composition**. A process is an async function that creates agents, wires them up, and returns a result. Processes signal completion via `done()`/`fail()` and `await wait()`.

There is one `Runtime` (singleton infrastructure). Composition happens through `ProcessScope`s — lightweight ownership boundaries tracked via a ContextVar.

## Core Concepts

### `@agent_process` decorator

Replaces `@agent_runtime`. Works at every level:

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


@agent_process(image=LocalImage())
async def main():
    result = await build_and_review("build a web server")
    return result

asyncio.run(main())
```

Behavior:
- **Root call** (no active runtime): creates a `Runtime`, starts the server, creates a root `ProcessScope`. On return, shuts everything down.
- **Nested call** (runtime already active): creates a child `ProcessScope` under the current one. Inherits the runtime.
- Accepts optional kwargs: `image=`, `timeout=`, `log_dir=` (only meaningful at root level for runtime config; `timeout` applies at any level).

### `done()`, `fail()`, `wait()`

Process-scoped completion primitives. Called from Python code (in tool handlers), not agent tools.

```python
def done(result=None):
    """Signal that this process completed successfully."""
    scope = _current_process.get()
    scope._outcome.set_result(result)

def fail(reason):
    """Signal that this process failed."""
    scope = _current_process.get()
    scope._outcome.set_exception(ProcessFailed(reason))

async def wait():
    """Block until done() or fail() is called. Returns the done value, raises on fail."""
    scope = _current_process.get()
    return await scope._outcome
```

These are the old `exit()`/`fail()`/`wait()` but scoped per-process instead of global on the runtime.

A process function can either:
- Use `return await wait()` — set up agents and handlers, then block until a handler calls `done()`
- Just `return value` directly — for processes that don't need to wait on agents (e.g. pure composition of sub-processes)

### `ProcessScope`

Tracks ownership of agents and machines created within a process function.

```python
@dataclass
class ProcessScope:
    parent: ProcessScope | None
    runtime: Runtime
    agents: list[Agent]                      # agents created in this scope
    machines: list[Machine]                  # machines spawned in this scope
    events: EventStream                      # process-level events (explicit emits + lifecycle)
    client_handlers: dict[str, Callable]     # handlers callable by parent
    _outcome: asyncio.Future                 # resolved by done()/fail()

```

`public()` sets a `_public` flag on the agent. `handle.agents` filters `scope.agents` for public ones on access.

Tracked via a single ContextVar (module-level constant — the variable is fixed, the value inside changes per async context, same pattern as the existing `_current_runtime`):

```python
_current_process: ContextVar[ProcessScope | None] = ContextVar(
    "druids_current_process", default=None
)

`_current_runtime` goes away. The runtime is accessed via the scope:

```python
def current_runtime() -> Runtime:
    scope = _current_process.get()
    if scope is None:
        raise RuntimeError("No active process")
    return scope.runtime
```

### Scope lifecycle

The `@agent_process` decorator manages the scope:

```python
parent = _current_process.get()
if parent is None:
    runtime = Runtime(image=image, log_dir=log_dir)
    await runtime.start()
else:
    runtime = parent.runtime

scope = ProcessScope(parent=parent, runtime=runtime)
token = _current_process.set(scope)
try:
    result = await fn(*args, **kwargs)
    scope.events.emit("done", result)
    return result
except Exception as e:
    scope.events.emit("failed", str(e))
    raise
finally:
    _current_process.reset(token)
    await scope.cleanup()
    if parent is None:
        await runtime.close()
```

### Scope cleanup

When a process function returns or raises, its scope tears down the agents and machines it owns:

```python
async def cleanup(self):
    # Kill agent tmux sessions, deregister from runtime
    for ag in self.agents:
        session = _agent_session_name(self.runtime.execution_id, ag.name)
        try:
            await ag.machine.exec(
                f"tmux kill-session -t {session} || true", timeout=5
            )
        except Exception:
            pass
        self.runtime._records.pop(ag.name, None)

    # Stop machines that this scope spawned (not externally provided ones)
    seen = set()
    for m in self.machines:
        if id(m) not in seen:
            seen.add(id(m))
            try:
                await m.stop()
            except Exception:
                pass
```

Agents created with `machine=some_existing_machine` — the scope owns the agent but **not** the machine. Only machines spawned implicitly by `agent()` or explicitly by `machine()` within the scope get tracked.

### Agent/machine registration

`agent()` and `machine()` register to the current scope:

```python
async def agent(name, *, system_prompt=None, image=None, machine=None):
    scope = _current_process.get()
    if scope is None:
        raise RuntimeError("No active process")

    ag = await scope.runtime._create_agent(name, ...)
    scope.agents.append(ag)
    return ag

async def machine(image=None):
    scope = _current_process.get()
    if scope is None:
        raise RuntimeError("No active process")

    m = await (image or scope.runtime.image).spawn()
    scope.machines.append(m)
    return m
```

### State

State lives in function locals. Tool handlers close over them:

```python
@agent_process
async def my_process():
    count = 0                      # <-- this is the state

    worker = await agent("worker")

    @worker.on("increment")
    async def increment():
        nonlocal count
        count += 1
        return str(count)

    ...
```

`agent.state` dict and the `set_state`/`get_state` built-in tools are removed. If an agent needs KV storage, the program author defines it as tool handlers closing over local state.

## Event Streams

Event streams are async-iterable channels of events. Two kinds of things have them: **agents** and **processes**. They differ in what they contain.

### Agent event stream

An agent's event stream contains the **raw events** from that agent — tool calls, messages, turn starts/ends, idle, errors, etc. This is the existing `AgentEventLog` content, exposed as an async iterable.

```python
worker = await agent("worker")
async for event in worker.events:
    # event.type: "tool_call", "message", "turn_start", "turn_end", etc.
    # event.data: the raw event payload
    if event.type == "tool_call" and event.data["tool"] == "submit":
        print("agent submitted")
```

This lets you observe and react to what an agent is doing — detect if it's stuck, going in circles, idle too long, etc.

### Process event stream

A process's event stream contains **only explicit emissions and lifecycle events** — NOT the raw events of its child agents. Processes are opaque by default.

```python
@agent_process
async def build_and_review(spec: str) -> str:
    builder = await agent("builder")
    ...
    emit("review_started", {"spec": spec})
    ...
    emit("review_passed")
    return await wait()
```

From the parent:
```python
handle = spawn(build_and_review, "web server")
async for event in handle.events:
    # sees: "review_started", "review_passed", "done", "failed"
    # does NOT see: builder's tool calls, auditor's messages, etc.
```

### EventStream class

```python
class EventStream:
    def emit(self, event_type: str, data: Any = None) -> None:
        """Emit an event into this stream."""
        ...

    async def __aiter__(self):
        """Async iterate over events as they arrive."""
        ...
```

Ambient helper:

```python
def emit(event_type: str, data: Any = None) -> None:
    scope = _current_process.get()
    if scope is None:
        raise RuntimeError("No active process")
    scope.events.emit(event_type, data)
```

## `spawn()` — Observable Process Handle

Two ways to call a process function:

1. **`await fn()`** — simple, blocks until done, returns result.
2. **`handle = spawn(fn, *args)`** — returns a `ProcessHandle` immediately. The process runs in a background task.

```python
@dataclass
class ProcessHandle:
    events: EventStream                    # process-level events (explicit emits + lifecycle)
    agents: dict[str, Agent]               # public agents exposed by the child process
    scope: ProcessScope

    async def call(self, event_name: str, **kwargs) -> Any:
        """Call a client event handler defined by the child process."""
        handler = self.scope.client_handlers.get(event_name)
        if handler is None:
            raise ValueError(f"No client event '{event_name}'")
        return await handler(**kwargs)

    def cancel(self) -> None:
        """Cancel the process. Triggers cleanup."""
        self.task.cancel()
```

The result is available via the event stream — the "done" event's `data` carries the return value, "failed" carries the reason. No `.result` or `await handle` needed; iterate `handle.events` and read `event.data`.

## Public Agents

By default, agents created inside a process are internal — the parent can't see or talk to them. A process can choose to **expose** agents to its parent:

```python
@agent_process
async def review_service(spec: str) -> str:
    reviewer = await agent("reviewer")
    public(reviewer)
    ...
```

The parent accesses public agents through the handle:

```python
handle = spawn(review_service, spec)
reviewer = handle.agents["reviewer"]
await reviewer.send("also check for security issues")
```

Public agents are still owned by the child process scope — they get cleaned up when the process exits. The parent just gets a reference to interact with them.

```python
def public(ag: Agent) -> None:
    scope = _current_process.get()
    if scope is None:
        raise RuntimeError("No active process")
    ag._public = True
```

## Client Events

A process can define **handlers that the parent can call** — an API exposed upward. This is the inverse of `agent.on()` (which exposes tools downward to the agent).

```python
@agent_process
async def worker_pool():
    workers = []

    @client_event
    async def submit_task(task: str) -> str:
        """Parent calls this to submit work."""
        w = await agent(f"worker-{len(workers)}")
        workers.append(w)
        await w.send(f"Do: {task}")
        return f"Assigned to {w.name}"

    @client_event
    async def get_status() -> dict:
        """Parent calls this to check progress."""
        return {"active_workers": len(workers)}

    return await wait()
```

From the parent:

```python
handle = spawn(worker_pool)
result = await handle.call("submit_task", task="build a server")
status = await handle.call("get_status")
handle.cancel()
```

```python
def client_event(fn):
    scope = _current_process.get()
    if scope is None:
        raise RuntimeError("No active process")
    scope.client_handlers[fn.__name__] = fn
    return fn
```

## Retry Patterns

### Simple try/catch retry

```python
@agent_process
async def main():
    for attempt in range(3):
        try:
            return await build_and_review("web server")
        except Exception:
            if attempt == 2:
                raise
```

### Event-based retry (process level)

```python
@agent_process
async def main():
    handle = spawn(build_and_review, "web server")
    async for event in handle.events:
        if event.type == "failed":
            handle = spawn(build_and_review, "web server")
        if event.type == "done":
            return event.data
```

### Agent-level observation and intervention

```python
@agent_process
async def resilient_worker(task: str) -> str:
    worker = await agent("worker")

    @worker.on("finish")
    async def on_finish(output: str):
        done(output)
        return "Done"

    await worker.send(f"Do: {task}")

    # Watch agent events for problems
    async def monitor():
        idle_count = 0
        async for event in worker.events:
            if event.type == "turn_end":
                idle_count += 1
            if idle_count > 10:
                await worker.send("You seem stuck. Try a different approach.")
                idle_count = 0

    monitor_task = asyncio.create_task(monitor())
    result = await wait()
    monitor_task.cancel()
    return result
```

## What changes in existing code

### Removed
- `Runtime.exit()`, `Runtime.fail()`, `Runtime.wait()`
- `_current_runtime` ContextVar
- `@agent_runtime` decorator
- `_run_until_exit()` helper
- `exit()` ambient function (replaced by process-scoped `done()`)
- `agent.state` dict
- `set_state` / `get_state` built-in tools

### Changed
- `Runtime` — becomes pure infrastructure (server, agent registry, tool dispatch, machine management). No outcome/lifecycle tracking.
- `Runtime.agent()` — stays on runtime as the actual implementation, but `agent()` ambient function also registers to current scope.
- `_builtin_tools()` — remove `set_state`/`get_state`.
- `fail()` — same name, but now process-scoped instead of runtime-scoped.
- Tests — rewrite to use `@agent_process` with `done()`/`wait()` instead of `exit()`/`Runtime.wait()`.

### Added
- `ProcessScope` dataclass with `agents`, `machines`, `events`, `client_handlers`, `_outcome`
- `ProcessHandle` for `spawn()` with `events`, `agents`, `call()`, `cancel()`
- `EventStream` class (async iterable)
- `Agent.events` — exposes `AgentEventLog` as async iterable
- `@agent_process` decorator (replaces `@agent_runtime`)
- `done()`, `wait()` ambient functions
- `spawn()` ambient function
- `emit()` ambient function
- `public()` ambient function
- `client_event` decorator
- `_current_process` ContextVar

### Unchanged
- `Agent` class (minus `.state`, plus `.events`)
- `Machine` / `Image` abstractions
- `Server` (WebSocket protocol)
- `AgentEventLog` (per-agent log, feeds into agent EventStream)
- `extension.ts` (pi extension)
- `connect()`, `agent.on()`, `agent.send()`, `agent.exec()`
- Tool handler registration and dispatch
