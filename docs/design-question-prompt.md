# Design question: state coordination in multi-agent orchestration

## Context

We're building **druids**, a Python framework for orchestrating multiple AI coding agents. A druids program is a Python script. When you run it, an in-process HTTP server starts, machines are spawned (Docker containers or local processes), and AI agents (powered by [pi](https://github.com/badlogic/pi-mono), an LLM coding assistant) are launched in tmux panes. The agents communicate with the orchestrator through tool calls (HTTP POST) and receive messages via SSE.

The API is **natively async end-to-end**. No sync facade. The program author writes `async def main()`, `await`s agent creation, and uses `async with` for context lifecycle:

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

        @reviewer.on("reject")
        async def reject(feedback: str = ""):
            await builder.send(f"Fix this:\n{feedback}")
            return "Feedback sent."

        result = await ctx.wait()
        print(result)

asyncio.run(main())
```

Key design decisions already locked in:

- **No two-phase split.** There is no "declare now, spawn later." `await ctx.agent()` always spawns immediately — creates machine, deploys extension, launches pi, waits for registration, returns a ready Agent.
- **No `ctx.run()`.** The server starts when the context is entered (`async with`). `await ctx.wait()` only blocks until `done()` or `fail()` is called — it does not start anything.
- **Asyncio-native throughout.** Machines, images, agents, server, tool handlers — all async. No threading model for the core.
- **Tool handlers are `async def`.** Sync handlers are accepted as a convenience (auto-awaited), but the primary model is async.

## The system

- **Each agent is a full LLM coding agent** running on its own machine (container or host), with filesystem access, shell, etc.
- **Tools are the structured interface** between agents and the program. An agent calls a tool, the program's handler runs, returns a result to the agent.
- **Messages are the unstructured interface** — free-form text delivered to the agent's LLM context via SSE.
- **Topology is explicit** — agents can only message agents they're `connect()`ed to. Program-defined tools provide alternative communication paths under program control.
- **Agents are created dynamically** — handlers can spawn new agents at runtime, wire them up, give them prompts.
- **Tool handlers run concurrently.** Multiple agents can call tools at the same time. Each tool call is dispatched as a separate async task (or potentially via `asyncio.to_thread` for sync handlers).

## The problem

Tool handlers close over shared mutable state in the program script:

```python
results = []
completed = set()

@worker.on("submit_result")
async def on_submit_result(name: str = "", result: str = ""):
    results.append({"name": name, "result": result})
    completed.add(name)
    if len(completed) >= expected_count:
        await ctx.done(results)
    return "Recorded."
```

When multiple workers call `submit_result` concurrently, this races. Even in asyncio (single-threaded), if there's an `await` between reading and writing shared state, another task can interleave. And if sync handlers run via `asyncio.to_thread`, they're on actual threads with actual data races.

This is the simplest case. Real programs have more complex shared state:
- A task queue that agents pull from and push to
- A shared knowledge base of findings that multiple agents contribute to
- Merge coordination — multiple agents modifying the same codebase, needing to sequence git operations
- A dependency graph of tasks where completion of one unblocks others
- Running cost/token budgets that all agents decrement

The program author is not a concurrency expert. They're writing what looks like a straightforward script. The framework should make the common cases safe without requiring locks, semaphores, or careful reasoning about interleaving.

## Current architecture

- **Concurrency model:** asyncio. The HTTP server (starlette/uvicorn) is async. Tool handlers are async (or sync handlers run via `to_thread`). Agent spawning is `asyncio.gather`-able.
- **No state isolation.** Handlers are closures over variables in the enclosing `main()` scope. The framework provides no state management.
- **No handler serialization.** If two agents call the same tool simultaneously, both handlers run as concurrent tasks.
- **Agent lifecycle.** Agents have a name, a machine, tool handlers, and a prompt. No per-agent state provided by the framework. Dynamic creation is supported — `await ctx.agent()` inside a handler spawns immediately.
- **Communication.** Messages (text via SSE) and tool calls (structured, via HTTP POST → async handler → HTTP response). Topology enforcement on the built-in `message` tool. Program-defined tools have no topology restrictions.

## Relevant prior work we've considered

**Actor model (Hewitt, 1973; Erlang/OTP):** Agents are already actors — named, receive messages, can create new actors. Missing: private per-actor state and per-actor message serialization (one handler at a time per agent).

**CSP / π-calculus (Hoare 1978, Milner 1992):** The program defines a communication topology that changes at runtime. Agents are processes, tools and messages are channels. The π-calculus models the case where new channels (connections, tools) are created dynamically.

**Tuple spaces (Gelernter, 1985, Linda):** Shared associative memory with concurrency-safe read/write/take operations. An alternative to direct messaging for coordinating shared state. The ad-hoc module-level dicts and lists in current programs are an unsafe version of this.

**Ownership types (Clarke 1998; Rust):** Each piece of state has one owner; others coordinate through the owner. Can't enforce in Python, but the API can make ownership conventional.

**Supervision trees (Erlang/OTP):** Failure handling via restart policies. Not implemented yet, but the abstractions should be ready for it.

**Event sourcing / CQRS:** State derived from an append-only event log. The JSONL orchestrator log already captures tool calls and messages. Leaning into this could serialize concurrent mutations by construction.

## Design tensions

1. **Simplicity vs safety.** The appeal of druids is that it's a Python script with `async`/`await`. Adding actor state, serialization primitives, or coordination objects adds API surface. But unserialized concurrent handlers on shared state is a trap for users.

2. **Flexibility vs structure.** Some programs need a simple counter. Others need a task dependency graph. A one-size-fits-all state model either over-constrains simple cases or under-serves complex ones.

3. **Implicit vs explicit.** Per-agent handler serialization could be automatic (the framework always serializes) or opt-in. Automatic is safer but limits throughput for independent tools on the same agent.

4. **Synchronous control flow vs callback wiring.** Currently, inter-agent coordination is callback-based — you send a message, some handler fires later. We're considering an `agent.ask("tool_name", "prompt")` pattern which sends a prompt and awaits until the agent calls the named tool. This makes sequential coordination natural but changes the programming model.

5. **asyncio concurrency semantics.** In a single-threaded asyncio loop, handlers that don't `await` won't interleave — but this is a subtle guarantee that breaks if someone uses `to_thread` or adds an `await`. The safety guarantee shouldn't depend on whether a handler has an `await` in it.

## Our current ideas

We have two directions we're exploring. They're not mutually exclusive but they address different parts of the problem.

### Idea 1: Erlang-style agent-owned state with serialized handlers

Each agent owns a state dict. Handlers for that agent are serialized — one at a time, like an Erlang process mailbox. The agent's state is passed to (or accessible from) its handlers. No shared mutable globals needed for the common case.

```python
async with Context(image=LocalImage()) as ctx:
    coordinator = await ctx.agent("coordinator")
    coordinator.state["results"] = []
    coordinator.state["pending"] = set()

    @coordinator.on("record_result")
    async def record_result(name: str = "", result: str = ""):
        # Safe: handlers for coordinator are serialized.
        # No concurrent mutation of coordinator.state.
        coordinator.state["results"].append({"name": name, "result": result})
        coordinator.state["pending"].discard(name)
        if not coordinator.state["pending"]:
            await ctx.done(coordinator.state["results"])
        return "Recorded."
```

Multiple agents can call `record_result` concurrently, but the handler invocations queue up and execute one at a time. The state is on the agent, mutations are serialized, no locks needed.

The open questions: what about state that genuinely spans agents (a global budget, a shared task graph)? The Erlang answer is "pick one process to own it, everyone else sends messages." That works but forces the program author to think about ownership. And what about failure/restart — if the coordinator crashes, its state is lost unless we persist it.

### Idea 2: Typed request-response with `ask()`

Instead of fire-and-forget `send()` with callback-wired tool handlers, provide a primitive that sends a prompt and awaits a structured response:

```python
@finder.on("spawn_task")
async def spawn_task(spec: str = ""):
    worker = await ctx.agent("worker")

    @worker.on("task_done")
    async def task_done(result: str = ""):
        """Call this when the task is complete."""
        return result

    @worker.on("task_failed")
    async def task_failed(reason: str = ""):
        """Call this if the task cannot be completed."""
        return reason

    outcome = await worker.ask(
        ["task_done", "task_failed"],
        prompt=f"Implement: {spec}",
        timeout=300,
    )

    if outcome.tool == "task_done":
        return f"Worker completed: {outcome.params['result']}"
    else:
        return f"Worker failed: {outcome.params['reason']}"
```

`ask()` sends the prompt and blocks the calling coroutine until the agent calls one of the listed tools. It returns a result object with which tool was called and what parameters the agent passed. The tool handler still runs (and its return value goes back to the agent), but `ask()` gives the caller structured access to what happened.

This turns the interaction from "send and hope a callback fires" into "send and await a typed result." The control flow reads top-down. Error handling is explicit — you list the failure tools, you handle the failure case.

The open questions: what if the agent calls tools not in the list first (e.g., it calls `bash` or `message` before calling `task_done`)? Those should proceed normally — `ask()` only resolves when one of the listed tools is called. What if the agent never calls any of them? Timeout. What if the caller wants to observe intermediate tool calls (progress)? Maybe a callback parameter or an async iterator.

The two ideas compose: agent-owned state makes per-agent coordination safe, `ask()` makes inter-agent coordination sequential and typed. Together they'd cover most of the shared-state problem — you don't need shared mutable globals if you can `ask()` an agent for a result and store it locally.

## Specific questions

1. **What is the right state model for the program layer?** Per-agent state (actor model), shared structured state (tuple space), event-sourced state, or something else? Should the framework provide state primitives, or just serialize handlers and let users manage their own state safely?

2. **What should the handler serialization policy be?** Per-agent (one handler at a time per agent, like an actor mailbox)? Per-tool (one invocation at a time per tool name)? Global (one handler at a time, period)? Configurable? What are the tradeoffs for agent swarms where dozens of agents might call tools concurrently?

3. **How should synchronous coordination work?** The `agent.ask("tool_name", "prompt")` pattern — send a prompt, await until the agent calls a specific tool, return the tool's parameters. Is this the right primitive? What happens on timeout? What if the agent calls a different tool first? What if it never calls the expected tool? Are there better patterns from distributed systems for this kind of "request-response over an unreliable channel where the remote party has agency"?

4. **How should shared resources (codebases, files, databases) be coordinated?** Multiple agents on separate machines may need to modify the same repository. What patterns exist for this? Pessimistic locking (one agent at a time)? Optimistic concurrency (merge conflicts resolved by a coordinator)? Something else?

5. **Are there frameworks, systems, or papers we're missing?** We've looked at actors, CSP, π-calculus, tuple spaces, OTP, event sourcing. What else is relevant for orchestrating concurrent autonomous agents that communicate through structured tool calls and unstructured messages?

## Constraints

- The user API is async Python (`async def`, `await`, `async with`).
- Programs are Python scripts. The solution should not require the user to learn a new DSL or framework beyond the druids API.
- Agents are heavyweight (each is an LLM process on a machine). Spawning is seconds, not microseconds. There will be tens of agents, not thousands.
- Agents are autonomous — they decide what tools to call and when. The program can influence them through prompts and available tools, but cannot force a specific sequence of actions.
- The framework runs in a single Python process on a single asyncio event loop. State coordination is in-memory, not distributed.
- The concurrency model is asyncio. No threading for core logic (though sync handler convenience via `to_thread` may exist).
