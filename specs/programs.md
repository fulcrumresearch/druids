# Programs

This is a design spec. Not all features described here are implemented. Sections marked "not yet implemented" describe planned behavior.

A program is a Python async function that orchestrates agents. It replaces the YAML spec system, the `Program` base class, the constructor/spawn mechanism, and the program discovery system.

## Core concepts

A program has three primitives:

- **Agents**: workers that run on machines. Created with `ctx.agent()`.
- **Event handlers**: callbacks registered with `@agent.on()`. Each handler becomes an MCP tool the agent can call.
- **Lifecycle signals**: `ctx.done()` and `ctx.fail()` end the program.

The program function registers agents and handlers, sends initial messages, then returns. The runtime takes over: it provisions machines, starts ACP sessions, and dispatches events until a lifecycle signal fires or a timeout expires.

## Minimal example

```python
async def program(ctx):
    executor = ctx.agent("executor", model="claude-opus-4-6", prompt="...")

    @executor.on("submit")
    async def on_submit(attempt):
        ctx.done(attempt)

    await executor.send("Implement the feature described in the spec.")
```

The runtime generates an MCP tool called `submit` from the handler signature. When the executor agent calls it, the handler fires, and `ctx.done()` ends the program.

## Event handlers are tools

There is no distinction between "an event the runtime fires" and "a tool the agent can call." Registering `@agent.on("submit")` gives the agent a `submit` tool. When the agent calls the tool, the handler fires. The handler's return value goes back to the agent as the tool result.

```python
@executor.on("ask_question")
async def on_ask(question: str) -> str:
    answer = await orchestrator.send(question)
    return answer
```

This gives the executor an `ask_question` tool with a `question` parameter. The runtime generates the tool schema from the handler's type annotations.

## State lives in closures

Handlers close over local variables. No special state API is needed for most programs.

```python
async def program(ctx):
    retries = 0
    max_retries = 3

    executor = ctx.agent("executor", model="opus", prompt="...")
    reviewer = ctx.agent("reviewer", model="opus", prompt="...")

    @executor.on("submit")
    async def on_executor_submit(attempt):
        await reviewer.send(f"Review this:\n{attempt.diff}")

    @reviewer.on("submit")
    async def on_reviewer_submit(review):
        nonlocal retries
        if review.verdict == "accept":
            return ctx.done(attempt)
        retries += 1
        if retries >= max_retries:
            return ctx.fail("Too many retries")
        await executor.send(f"Address this feedback:\n{review.comments}")

    await executor.send(ctx.spec)
```

## Composition

Composition is function calls. A helper function wires up agents and handlers on the same `ctx`, then returns. No new abstraction is needed.

```python
async def executor_with_review(ctx, name, model, spec):
    executor = ctx.agent(f"{name}-executor", model=model, prompt="...")
    reviewer = ctx.agent(f"{name}-reviewer", model="opus", prompt="...")

    @executor.on("submit")
    async def on_submit(attempt):
        await reviewer.send(f"Review:\n{attempt.diff}")

    @reviewer.on("submit")
    async def on_review(review):
        if review.verdict == "accept":
            ctx.emit("attempt", name=name, attempt=attempt)
        else:
            await executor.send(f"Fix:\n{review.comments}")

    await executor.send(spec)


async def program(ctx):
    models = ["opus", "sonnet", "gpt-5"]

    for model in models:
        await executor_with_review(ctx, name=model, model=model, spec=ctx.spec)

    results = {}

    @ctx.on("attempt")
    async def on_attempt(name, attempt):
        results[name] = attempt
        if len(results) == len(models):
            best = max(results.values(), key=score)
            ctx.done(best)
```

Custom events (`ctx.emit` and `ctx.on`) connect inner and outer scopes. The inner function does not need to know about the outer function. It emits "I produced an accepted attempt" and whoever is listening deals with it. (Not yet implemented.)

## LLM-driven orchestration

When the orchestrator is an LLM, the program gives it tools via `@agent.on()` and routes events to it as messages.

```python
async def program(ctx):
    orchestrator = ctx.agent("orchestrator", model="opus", prompt="You manage a team.")
    executors = {}

    @orchestrator.on("create_executor")
    async def create_executor(name: str, model: str, task: str):
        """Spin up an executor agent to work on a task."""
        e = ctx.agent(name, model=model, prompt="...")
        executors[name] = e

        @e.on("submit")
        async def on_submit(attempt):
            await orchestrator.send(f"{name} submitted:\n{attempt.summary}")

        @e.on("error")
        async def on_error(err):
            await orchestrator.send(f"{name} crashed: {err}")

        await e.send(task)
        return f"Created {name}, assigned task."

    @orchestrator.on("send_feedback")
    async def send_feedback(name: str, feedback: str):
        """Send feedback to an executor."""
        await executors[name].send(feedback)

    @orchestrator.on("accept")
    async def accept(name: str):
        """Accept an executor's attempt and finish."""
        ctx.done(executors[name].last_attempt)

    await orchestrator.send(f"Implement this:\n{ctx.spec}")
```

The orchestrator agent sees `create_executor`, `send_feedback`, and `accept` as MCP tools. It reasons and calls them. The program is the wiring. The Python layer can enforce constraints (budget, retries, timeouts) that the LLM cannot override.

## The spectrum

All three patterns use the same primitives:

| Pattern | Orchestrator | Who decides |
|---|---|---|
| Hardcoded | Python program | Program author at write time |
| LLM-driven | Agent with tools | LLM at run time |
| Hybrid | Both | Python sets boundaries, LLM operates within them |

## `ctx` API surface

### Agent creation

```python
agent = ctx.agent(name, model=..., prompt=..., share_machine_with=...)
```

Returns an agent handle. Machine provisioning happens lazily on first `send()` or eagerly if the runtime decides to.

### Messaging

```python
await agent.send(message)
```

Send a message to an agent. If the agent is not yet provisioned, this triggers provisioning.

### Event handlers

```python
@agent.on("event_name")
async def handler(param: str) -> str:
    ...
```

Registers a handler. The handler becomes an MCP tool available to the agent. The tool schema is generated from the function signature. The docstring becomes the tool description.

### Custom events (not yet implemented)

```python
ctx.emit("event_name", key=value, ...)

@ctx.on("event_name")
async def handler(key, ...):
    ...
```

Program-level events for communication between handler scopes.

### Lifecycle

```python
ctx.done(result)    # end successfully
ctx.fail(reason)    # end with failure
ctx.spec            # the task specification string
```

### Built-in events (not yet implemented)

These are fired by the runtime, not by agent tool calls:

- `@agent.on("error")` -- agent crashed or timed out
- `@agent.on("idle")` -- agent inactive for N seconds
- `@ctx.on("timeout")` -- global execution timeout

## What this replaces

This spec replaces the earlier model where agents were configured via YAML specs and spawned through MCP tools. The key changes:

| Old pattern | New pattern |
|---|---|
| YAML spec files | Python program functions |
| `Program` base class with constructors | `ctx.agent()` calls |
| `spawn` MCP tool | Programs create agents directly |
| `send_message` MCP tool | Kept. Programs also use `agent.send()` |
| Constructor pattern | Composition via regular Python functions |
| Agent inherits from Program | Agent is a standalone dataclass |

## What stays

- `Agent`, `ClaudeAgent`, `CodexAgent` dataclasses (harness-specific config)
- `Machine` (VM provisioning, bridge deployment, git operations)
- `AgentConnection` (SSE relay to ACP)
- `Execution` (slimmed down: holds agents, manages lifecycle)
- Task/ExecutionRecord DB models (persistence)
- `send_message` MCP tool (inter-agent messaging)

## File format

A program is a `.py` file with a known entry point:

```python
async def program(ctx):
    ...
```

How it gets to the server (inline in the API request, uploaded, stored in the DB) is a separate question.
