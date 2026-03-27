# Program Design

A program is an async function that orchestrates agents on remote machines. The program controls the creation and orchestration of the agents, which machines they run on, and how they communicate, etc. But the program also owns the high level state of the execution and how agents can modify it.

## Agents

An agent is a worker that runs on a remote VM. You create one with [`ctx.agent()`](../reference/ctx.md#ctxagent):

```python
builder = await ctx.agent(
    "builder",
    system_prompt="You are a builder agent. You implement specs.",
    prompt="## Spec\n\nAdd rate limiting to POST /api/keys",
    working_directory="/home/agent/repo",
)
```

Each agent gets a name (unique within the execution), a system prompt, and an initial user prompt. `ctx.agent()` returns immediately; provisioning happens in the background. The returned [agent object](../reference/agent.md) is usable right away, and methods like `send` and `exec` block until provisioning completes.

## Devboxes

A devbox is a named VM snapshot. When an execution is run on a devbox, the agent starts in the state of that devbox, with all installed packages, configuration files, cloned repos, etc...

If a devbox is configured with GitHub, it will also load a fresh copy of the code, and each agent can be configured with a [git access level](../reference/git-permissions.md).

Secrets (environment variables like API keys) are stored on the devbox and injected into every agent that uses it. MCP server configs can reference these secrets with `$VAR_NAME` syntax.

By default, each agent gets its own VM. When agents need to work on the same filesystem, use `share_machine_with`:

```python
builder = await ctx.agent("builder", git="write", working_directory="/home/agent/repo")
critic = await ctx.agent(
    "critic",
    git="read",
    working_directory="/home/agent/repo",
    share_machine_with=builder,
)
```

## Events

Events are how programs define tools that agents can call. Each event is a function registered with the [`@agent.on("tool_name")`](../reference/agent.md#agenton) decorator. When the agent calls the tool, the handler fires, and its return value goes back to the agent as the tool result. Each event definition on an agent in the program maps to an MCP tool the agent has access to.

```python
@builder.on("submit_for_review")
async def on_submit(summary: str = ""):
    """Submit your verification for audit. Include real evidence."""
    await auditor.send(f"Review this:\n{summary}")
    return "Submitted for audit. Wait for the auditor."
```

Events allow you to inject deterministic structure and control flow to structure how your agents work towards the task of the program. They are useful for defining controlled steps and flows, like:

- forcing a model to iterate against hard tests and harness signals
- building a verification hierarchy, where agents spawn outputs that are verified and redteamed by other agents until they match a set of properties
- controlling distributed task state, like having a lock around the ways agents write to shared resources or user-facing systems

Programs can also send messages to agents directly with `agent.send()`. The agent processes it like any other message and can call tools in response. This is the primary way event handlers route information between agents.

```python
@reviewer.on("reject")
async def on_reject(feedback: str = ""):
    await builder.send(f"The reviewer rejected your submission:\n{feedback}")
    return "Feedback sent to builder."
```

### Lifecycle

`await ctx.done(result)` signals successful completion. `await ctx.fail(reason)` signals failure. Without one of these, the execution runs until its TTL expires. The TTL is set when creating an execution (in seconds, default 0 which defers to the server maximum of 2 hours). When the TTL fires, all agents are stopped and the execution is marked as failed.

Most programs end via an event handler calling `await ctx.done()` or `await ctx.fail()`. Long-lived programs that accept client events indefinitely call `await ctx.wait()` at the end of the program function to block until one of these signals fires.

Programs can also snapshot an agent's VM at any point with `agent.snapshot_machine(name)`. This captures the current state of the machine (installed packages, files, configuration) and registers it as a new devbox that future executions can use.


## Communication


Events and `agent.send` allow programmatic communication with agents, but agents can also talk to each other directly via the built-in `message` tool.

You can connect agents to each other so they can communicate via `ctx.connect()`:

```python
ctx.connect(builder, reviewer)  # bidirectional
ctx.connect(builder, logger, direction="forward")  # builder -> logger only
```


## Clients 

Clients consume information about the execution of a program, and can receive and send messages to the program to steer it. For example, a frontend can allow a user to monitor and change the program execution.

Executions emit a stream of activity events (tool calls, responses, connections, custom events from `ctx.emit()`) that clients can consume via SSE at `GET /api/executions/{slug}/stream`. Clients can also send events to the execution via `POST /api/events/send`. See the [client API reference](../reference/client-api.md) for endpoint details and message formats.

Programs can also push events to connected clients with `await ctx.emit()`:

```python
@builder.on("surface")
async def on_surface(title: str = "", body: str = ""):
    """Surface a decision or consideration for the driver."""
    await ctx.emit("consideration", {"title": title, "body": body})
    return "Surfaced to driver."
```

Clients can also send information to the execution. They can do this in an unstructured way via messaging, and via custom events defined in the program, just like agents.

These are registered with [`@ctx.on_client_event()`](../reference/ctx.md#ctxon_client_event):

```python
@ctx.on_client_event("input")
async def handle_input(text=""):
    """Route driver input to the builder."""
    await builder.send(f"[Driver]: {text}")
    return {"ack": True}
```

When a client sends this event it will fire the handler and the return value goes back to the client as the event result.
