# Design question: state coordination in multi-agent orchestration

## Context

We're building **druids**, a Python framework for orchestrating multiple AI coding agents. A druids program is a plain Python script. When you run it, an in-process HTTP server starts, machines are spawned (Docker containers or local processes), and AI agents (powered by [pi](https://github.com/badlogic/pi-mono), an LLM coding assistant) are launched in tmux panes. The agents communicate with the orchestrator through tool calls (HTTP POST) and receive messages via SSE.

The program author defines the topology: which agents exist, how they connect, and what tools they can call. Tool handlers are plain Python functions that run in a thread pool. The API is synchronous — no async/await exposed to the user.

Here is the spec's example program:

```python
from druids import Context, DockerImage

ctx = Context(image=DockerImage("base"))

builder = ctx.agent("builder", prompt="Implement the feature described in the spec.")
auditor = ctx.agent("auditor", prompt="You audit the builder's work.")

@builder.on("submit")
def on_submit(summary=""):
    """Submit your work for audit."""
    auditor.send(f"Review this:\n{summary}")
    return "Submitted for audit."

@auditor.on("approve")
def on_approve(summary=""):
    """Approve the build."""
    ctx.done(summary)
    return "Done."

@auditor.on("reject")
def on_reject(feedback=""):
    """Reject with feedback."""
    builder.send(f"Fix this:\n{feedback}")
    return "Feedback sent."

ctx.run()
```

Key properties of the system:
- **Each agent is a full LLM coding agent** running on its own machine (container or host), with filesystem access, shell, etc.
- **Tools are the structured interface** between agents and the program. An agent calls a tool, the program's handler runs, returns a result to the agent.
- **Messages are the unstructured interface** — free-form text delivered to the agent's LLM context.
- **Topology is explicit** — agents can only message agents they're `connect()`ed to. Program-defined tools provide alternative communication paths under program control.
- **Agents are created dynamically** — handlers can spawn new agents at runtime, wire them up, give them prompts.
- **Tool handlers run concurrently** in a thread pool. Multiple agents can call tools at the same time.

## The problem

Tool handlers close over shared mutable state in the program script:

```python
results = []
completed = set()

@worker.on("submit_result")
def on_submit_result(name="", result=""):
    results.append({"name": name, "result": result})
    completed.add(name)
    if len(completed) >= expected_count:
        ctx.done(results)
    return "Recorded."
```

When multiple workers call `submit_result` concurrently, this races. `list.append` is GIL-protected in CPython, but the `len(completed) >= expected_count` check and `ctx.done()` call are not atomic — two threads could both see the threshold met and both call `done()`.

This is the simplest case. Real programs have more complex shared state:
- A task queue that agents pull from and push to
- A shared knowledge base of findings that multiple agents contribute to
- Merge coordination — multiple agents modifying the same codebase, needing to sequence git operations
- A dependency graph of tasks where completion of one unblocks others
- Running cost/token budgets that all agents decrement

The program author is not a concurrency expert. They're writing what looks like a simple script. The framework should make the common cases safe without requiring locks, queues, or careful reasoning about thread safety.

## Current architecture

- **Threading model.** Pure threading, no asyncio. The HTTP server (starlette/uvicorn) runs on a daemon thread. Tool handlers execute in a `ThreadPoolExecutor`. `ctx.run()` blocks the main thread on a `threading.Event`.
- **No state isolation.** Handlers are closures over module-level variables. The framework provides no state management — it's whatever Python variables the program author creates.
- **No handler serialization.** If two agents call the same tool simultaneously, both handlers run concurrently on separate threads.
- **Agent lifecycle.** Agents have a name, a machine, tool handlers, and a prompt. No per-agent state provided by the framework. Dynamic creation is supported (spawn inside handlers).
- **Communication.** Messages (text via SSE) and tool calls (structured, via HTTP POST → handler → HTTP response). Topology enforcement on the built-in `message` tool. Program-defined tools have no topology restrictions.

## Relevant prior work we've considered

**Actor model (Hewitt, 1973; Erlang/OTP):** Agents are already actors — named, receive messages, can create new actors. Missing: private per-actor state and per-actor message serialization (one handler at a time per agent).

**CSP / π-calculus (Hoare 1978, Milner 1992):** The program defines a communication topology that changes at runtime. Agents are processes, tools and messages are channels. The π-calculus models the case where new channels (connections, tools) are created dynamically.

**Tuple spaces (Gelernter, 1985, Linda):** Shared associative memory with concurrency-safe read/write/take operations. An alternative to direct messaging for coordinating shared state. The ad-hoc module-level dicts and lists in current programs are an unsafe version of this.

**Ownership types (Clarke 1998; Rust):** Each piece of state has one owner; others coordinate through the owner. Can't enforce in Python, but the API can make ownership conventional.

**Supervision trees (Erlang/OTP):** Failure handling via restart policies. Not implemented yet, but the abstractions should be ready for it.

**Event sourcing / CQRS:** State derived from an append-only event log. The JSONL orchestrator log already captures tool calls and messages. Leaning into this could serialize concurrent mutations by construction.

## Design tensions

1. **Simplicity vs safety.** The appeal of druids is that it's a plain Python script. Adding actor state, serialization primitives, or coordination objects makes it less plain. But unserialized concurrent handlers on shared state is a trap.

2. **Flexibility vs structure.** Some programs need a simple counter. Others need a task dependency graph. A one-size-fits-all state model either over-constrains simple cases or under-serves complex ones.

3. **Implicit vs explicit.** Per-agent handler serialization could be automatic (the framework always serializes) or opt-in. Automatic is safer but limits throughput for independent tools on the same agent.

4. **Synchronous control flow vs callback wiring.** Currently, inter-agent coordination is callback-based — you send a message, some handler fires later. We're considering `agent.ask("tool_name", "prompt")` which sends a prompt and blocks until the agent calls the named tool. This makes sequential coordination natural but changes the programming model.

## Specific questions

1. **What is the right state model for the program layer?** Per-agent state (actor model), shared structured state (tuple space), event-sourced state, or something else? Should the framework provide state primitives, or just serialize handlers and let users manage their own state safely?

2. **What should the handler serialization policy be?** Per-agent (one handler at a time per agent, like an actor mailbox)? Per-tool (one invocation at a time per tool name)? Global (one handler at a time, period)? Configurable? What are the tradeoffs for agent swarms where dozens of agents might call tools concurrently?

3. **How should synchronous coordination work?** The `agent.ask("tool_name", "prompt")` pattern — send a prompt, block until the agent calls a specific tool, return the tool's parameters. Is this the right primitive? What happens on timeout? What if the agent calls a different tool first? What if it never calls the expected tool? Are there better patterns from distributed systems for this kind of "request-response over an unreliable channel where the remote party has agency"?

4. **How should shared resources (codebases, files, databases) be coordinated?** Multiple agents on separate machines may need to modify the same repository. What patterns exist for this? Pessimistic locking (one agent at a time)? Optimistic concurrency (merge conflicts resolved by a coordinator)? Something else?

5. **Are there frameworks, systems, or papers we're missing?** We've looked at actors, CSP, π-calculus, tuple spaces, OTP, event sourcing. What else is relevant for orchestrating concurrent autonomous agents that communicate through structured tool calls and unstructured messages?

## Constraints

- The user API must stay synchronous. No `async`/`await` in user code.
- Programs are single-file Python scripts. The solution should not require the user to learn a new DSL or framework beyond the druids API.
- Agents are heavyweight (each is an LLM process on a machine). Spawning is seconds, not microseconds. There will be tens of agents, not thousands.
- Agents are autonomous — they decide what tools to call and when. The program can influence them through prompts and available tools, but cannot force a specific sequence of actions.
- The framework runs in a single Python process. State coordination is in-memory, not distributed.
