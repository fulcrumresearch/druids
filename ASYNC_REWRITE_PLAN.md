# Async rewrite plan

Date: 2026-04-11

This document defines the rewrite direction for this repo.

## Goal

Rewrite the current codebase to be **natively async end to end**.

We are being ruthless about simplification.

That means:
- async `Context`
- async `Agent`
- async `Machine` / `Image`
- async server
- async-first tool handlers
- immediate async spawning
- no sync facade
- **no `ctx.run()`**

## Core decision

Two key simplifications are now locked in:

1. **Agents always spawn immediately via `await ctx.agent(...)`.**
   There is no deferred top-level declaration phase.

2. **There is no `ctx.run()`.**
   The server starts up front when the context is entered.

This is the shape we want:

```python
import asyncio
from druids import Context, LocalImage

async def main():
    async with Context(image=LocalImage()) as ctx:
        builder = await ctx.agent("builder", prompt="Implement the feature.")
        reviewer = await ctx.agent("reviewer", prompt="Review the builder's work.")
        ctx.connect(builder, reviewer)

        @builder.on("submit")
        async def submit(summary: str = ""):
            await reviewer.send(f"Review this:\n{summary}")
            return "Submitted for review."

        @reviewer.on("approve")
        async def approve(summary: str = ""):
            await ctx.done(summary)
            return "Done."

        result = await ctx.wait()
        print(result)

asyncio.run(main())
```

The important distinction is:
- `__aenter__` starts the runtime
- `await ctx.agent(...)` creates agents now
- `await ctx.wait()` only waits for terminal completion

There is no “declare now, spawn later” phase anymore.

---

## Design principles

1. **Prefer a clean async model over compatibility.**
2. **Delete complexity instead of adapting it.**
3. **One concurrency model: asyncio.**
4. **Start resources explicitly and early.**
5. **Make agent creation immediate and unsurprising.**
6. **Keep protocol correctness and good extension behavior.**

---

## Public API target

## Context

```python
class Context:
    async def __aenter__(self) -> Context: ...
    async def __aexit__(self, exc_type, exc, tb) -> None: ...

    async def agent(
        self,
        name: str,
        *,
        prompt: str | None = None,
        system_prompt: str | None = None,
        image: Image | None = None,
        machine: Machine | None = None,
    ) -> Agent: ...

    async def machine(self, image: Image | None = None) -> Machine: ...

    def connect(self, a: Agent | str, b: Agent | str, *, direction: str = "both") -> None: ...

    async def done(self, result: Any = None) -> None: ...
    async def fail(self, reason: str) -> None: ...
    async def wait(self, *, timeout: float | None = None) -> Any: ...
```

## Agent

```python
class Agent:
    name: str
    machine: Machine

    def on(self, tool_name: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]: ...
    async def send(self, message: str) -> None: ...
    async def exec(self, command: str, *, user: str = "agent", timeout: int | None = None) -> ExecResult: ...
```

## Image / Machine

```python
class Image:
    async def spawn(self) -> Machine: ...
    def server_url_for(self, port: int) -> str: ...

class Machine:
    async def exec(self, command: str, *, user: str = "agent", timeout: int | None = None) -> ExecResult: ...
    async def write_file(self, path: str, content: bytes | str) -> None: ...
    async def read_file(self, path: str) -> bytes: ...
    async def stop(self) -> None: ...
```

---

## Context lifecycle

## `async with Context(...)`
Entering the context starts the runtime immediately.

On `__aenter__`:
- generate `execution_id`
- open orchestrator log
- start the HTTP server
- initialize execution state
- prepare completion future/event

This replaces the old `ctx.run()` startup responsibility.

## `await ctx.agent(...)`
This always means:

> create the agent now and return only when it is ready

In real-launch mode that includes:
1. resolve machine
2. spawn machine if needed
3. deploy extension
4. launch `pi` in `tmux`
5. wait for `/agents/register`
6. push initial prompt if present
7. return the `Agent`

In manual mode that includes:
1. resolve machine
2. create channel / runtime state
3. skip `pi` launch
4. return the `Agent`

No deferred spawn queue. No top-level special case.

## `await ctx.wait()`
`wait()` is the new “block until completion” primitive.

It does **not** start anything.
It only waits for one of:
- `await ctx.done(result)`
- `await ctx.fail(reason)`
- timeout / cancellation

This is intentionally narrower and simpler than the old `run()`.

## `__aexit__`
Always tears everything down:
- send shutdown events
- stop server
- stop machines
- close log

If the user exits the context without calling `done()` or `fail()`, cleanup still happens.

---

## What to keep from the current codebase

Even with a rewrite, these ideas are worth preserving.

### 1. Strict protocol validation
Keep:
- `execution_id` validation on register
- unknown-agent errors as real HTTP errors
- topology violations as real HTTP errors
- unknown-tool errors as real HTTP errors

### 2. Better extension behavior
Keep:
- env-var-based extension config
- dynamic tool registration using pi’s real extension API
- send user message immediately when idle
- use `deliverAs: "steer"` when busy

### 3. Manual mode for tests
Keep a mode where the server/runtime starts but real `pi` launch is skipped.

That is too useful to lose.

### 4. JSONL orchestration logging
Keep append-only JSONL logs.

---

## What to delete

### 1. Delete the sync public API
Remove:
- sync `ctx.run()`
- sync `agent.send()`
- sync `agent.exec()`
- sync lifecycle methods designed for blocking behavior

### 2. Delete the threaded server
Replace the stdlib threaded HTTP server with a fully async server.

### 3. Delete lazy sync wrappers
The old `ManagedMachine`-style shape should go away.

If we need laziness, it should be expressed in a thin async abstraction, not a sync wrapper with hidden startup.

### 4. Delete “before run / during run” semantic splits
We should not have APIs whose meaning changes depending on whether execution has started.

Immediate creation is simpler and better.

---

## Server architecture

## Recommendation
Use an async ASGI server built with the current dependencies:
- `starlette`
- `uvicorn`

Why:
- already present in `pyproject.toml`
- easy async request handlers
- easy SSE streaming
- no need for thread-based request handling

## Agent channel model
Each agent gets an async channel object with:
- `registered: asyncio.Event`
- per-agent event backlog
- subscriber queues

Needed operations:
- `publish(event)`
- `subscribe()`
- `unsubscribe()`
- `wait_registered()`

## Endpoints
Keep the existing protocol surface:
- `POST /agents/register`
- `POST /agents/{agent_id}/tool_call`
- `GET /agents/{agent_id}/events`

### `POST /agents/register`
Responsibilities:
- validate `execution_id`
- validate `agent_id`
- mark channel as registered
- return built-in + current program-defined tools

### `POST /agents/{agent_id}/tool_call`
Responsibilities:
- validate agent exists
- dispatch built-in or program-defined tool
- surface structured errors correctly

### `GET /agents/{agent_id}/events`
Responsibilities:
- validate agent exists
- stream backlog + future events
- emit keepalives
- close cleanly on shutdown

---

## Tool handler model

The primary model is `async def` handlers.

Example:

```python
@builder.on("submit")
async def submit(summary: str = ""):
    await reviewer.send(summary)
    return "ok"
```

We may still allow sync handlers as a convenience, but only as a convenience:

```python
result = handler(**params)
if inspect.isawaitable(result):
    result = await result
```

That is fine.
What we do **not** want is an architecture centered around threadpool execution of sync handlers.

## `caller` injection
Keep `caller` injection, but do it correctly:
- exclude `caller` from generated tool schema
- inject the actual `Agent` object at invocation time

---

## Machine architecture

All machine and image implementations should be async.

## LocalMachine
Use `asyncio.create_subprocess_shell()` / `create_subprocess_exec()` instead of blocking `subprocess.run()`.

## DockerMachine
Use the Docker CLI through async subprocesses.

This keeps the implementation simple and consistent with the async runtime.

## Shared machines
Shared machines should be explicit and concrete:

```python
machine = await ctx.machine(LocalImage())
worker = await ctx.agent("worker", machine=machine)
reviewer = await ctx.agent("reviewer", machine=machine)
```

No hidden lazy sync wrapper.

---

## Launch model

Keep a launch mode concept, but implement it async.

### Recommended modes
- `"auto"` — launch real `pi` if available, otherwise manual semantics
- `"always"` — require real launch
- `"manual"` — never launch automatically

The important part is that these modes only affect launching external agent processes.
They should **not** affect whether the server/runtime is already up. The runtime starts in `__aenter__`.

---

## Logging

Keep JSONL orchestrator logs.

Suggested events:
- execution started / shutdown
- server started
- agent created / launched / registered / disconnected
- tool dispatched / result / failure
- message routed
- file routed
- done / fail

---

## Testing strategy after rewrite

The rewrite should preserve realism.

Keep these categories:
1. protocol-level tests with fake clients
2. real end-to-end tests spawning actual `pi`
3. messaging tests
4. file-transfer tests
5. topology enforcement tests
6. dynamic tool registration tests
7. readiness tests
8. failure-path tests

All tests should target the new async API.

### Example test shape

```python
async def test_basic_flow():
    async with Context(image=LocalImage(), launch_mode="manual") as ctx:
        worker = await ctx.agent("worker")
        ...
        result = await ctx.wait(timeout=5)
        assert result == "ok"
```

Subprocess e2e tests can still launch tiny scripts using `asyncio.run(main())`.

---

## Migration plan

## Phase 1: lock the API
- update planning docs to the immediate-spawn / no-run model
- update README examples to `async with` + `await ctx.agent(...)` + `await ctx.wait()`

## Phase 2: rewrite machines async
- replace blocking subprocess calls
- remove sync machine wrappers
- make shared machines explicit async objects

## Phase 3: rewrite server async
- replace threaded server with ASGI app
- implement async SSE channels
- keep protocol validation strict

## Phase 4: rewrite context / agent async
- `async with Context`
- immediate `await ctx.agent(...)`
- `await ctx.wait()` instead of `run()`
- async built-ins and async handler dispatch

## Phase 5: rewire extension + launch path
- keep env-var config
- keep dynamic tool push
- keep idle/steer delivery behavior
- wait for register during spawn

## Phase 6: port tests
- port unit/integration tests to async API
- keep real `pi` e2e coverage
- add regression tests for duplicate names and execution-id validation

## Phase 7: delete dead sync code
- remove old sync assumptions entirely
- simplify docs and examples to match reality

---

## Open decisions

### 1. Should sync handlers still be allowed?
Preference: **yes, as a convenience only**.

### 2. Should `done()` / `fail()` be async?
Preference: **yes**.
Uniform async API is cleaner.

### 3. Should `connect()` stay sync?
Preference: **yes**.
It is just in-memory topology mutation.

### 4. Should `wait()` exist?
Preference: **yes**.
We removed `run()` as a startup primitive, but we still want a clean way to await terminal completion.

`wait()` is much simpler than `run()` because it only waits; it does not also boot the world.

---

## Final recommendation

Proceed with the rewrite directly in this repo using this model:

- server starts in `Context.__aenter__`
- agents spawn immediately via `await ctx.agent(...)`
- there is no deferred creation phase
- there is no `ctx.run()`
- `await ctx.wait()` is the completion primitive
- everything internal is asyncio-native

That is the cleanest shape so far.
