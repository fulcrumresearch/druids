# 2-druids

A simpler version of druids with no client-server boundary.

## Overview

A druids program is a plain Python script. When you run it, everything happens within that process: an in-process HTTP server spins up for agent communication, machines are spawned from images, pi agents are launched in tmux panes, and tool calls are handled in-memory. When the program calls `ctx.done()` or `ctx.fail()`, everything tears down.

There is no separate server to deploy, no database, no auth, no ACP. The program is the orchestrator.

## Example

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

## Programs

Programs are plain Python scripts you run directly (`python build.py`). No `program()` function wrapper, no special CLI, no registration with a server.

### Sync API

The entire API is synchronous. The user never writes `async` or `await`.

- `ctx.agent()` — at the top level, records intent (agent is spawned when `ctx.run()` starts). Inside a handler, spawns immediately and blocks until ready.
- `agent.send(message)` — sends a message to the agent. Blocks until delivered.
- `agent.exec(command)` — runs a shell command on the agent's machine. Blocks until complete.
- `ctx.done(result)` / `ctx.fail(reason)` — signals completion. `ctx.run()` unblocks and returns.
- `ctx.connect(a, b)` — enables built-in messaging between two agents.
- `ctx.machine(image)` — creates an explicit machine for sharing between agents.

Under the hood, `ctx.run()` owns the async event loop. Handlers run in a thread pool. Sync methods like `agent.send()` internally submit coroutines to the event loop and block the handler thread until complete (same pattern as FastAPI sync route handlers). This means handlers can do I/O without the user knowing async exists.

### Program-defined tools

Tools are defined with `@agent.on("tool_name")`. The decorated function becomes a tool the agent can call. The tool schema is derived from the function signature:

- Parameter names → tool parameter names
- Type annotations → parameter types
- Default values → optional parameters
- Docstring → tool description
- Return value → tool result sent back to the agent

```python
@builder.on("commit")
def on_commit(message: str = ""):
    """Commit staged changes and notify the critic."""
    result = builder.exec(f"git commit -m '{message}'")
    if result.exit_code != 0:
        return f"Commit failed:\n{result.stderr}"
    critic.send(f"New commit: {message}")
    return f"Committed.\n{result.stdout}"
```

Tools can be added dynamically at any time, including inside other handlers. The framework pushes new tools to the agent's extension in real time.

### Dynamic agent creation

Agents can be created inside handlers. When called inside a handler, `ctx.agent()` spawns the machine and agent immediately (blocking until ready), unlike the top-level where it defers to `ctx.run()`:

```python
task_count = 0

@finder.on("spawn_task")
def on_spawn(name="", spec=""):
    nonlocal task_count
    task_count += 1
    impl = ctx.agent(f"impl-{task_count}", prompt=spec)
    reviewer = ctx.agent(f"review-{task_count}", machine=impl.machine)
    ctx.connect(impl, reviewer)
    return f"Spawned impl-{task_count}."
```

## Agents

Each agent is a pi coding agent instance running with a custom druids extension. Agents run in interactive mode inside tmux panes.

### Interactive by default

Users can attach to any agent's tmux pane to:
- Watch the agent work in real time (see tool calls, thinking, output)
- Type into the pane to intervene directly
- Observe the full pi TUI

The tmux pane is a feature, not an implementation detail. Agents are named, so attaching is as simple as `tmux attach -t druids-{execution_id}-{agent_name}` (or similar).

### Where agents run

Agents run on machines. Machines are spawned from images. Agents can be local or remote — Docker containers, cloud VMs, SSH hosts. The program doesn't care; it talks to the machine abstraction.

### Agent startup sequence

1. Program calls `ctx.agent("builder", prompt="...")`
2. Framework spawns a machine from the image (or uses an explicit machine)
3. Framework deploys the druids extension to the machine via `machine.write_file()`
4. Framework starts pi in a tmux pane on the machine via `machine.exec()`, with the extension loaded and environment variables set (`DRUIDS_SERVER_URL`, `DRUIDS_EXECUTION_ID`, `DRUIDS_AGENT_ID`)
5. The extension connects to the in-process server via SSE (for receiving messages/events) and registers via HTTP POST
6. Server sends the tool list to the extension; extension calls `pi.registerTool()` for each
7. Server pushes the initial prompt via SSE; extension calls `pi.sendUserMessage()` to deliver it

### System prompt

The program can set a `system_prompt` on each agent. This is appended to pi's default system prompt, so agents get pi's built-in tool guidance plus the program's role-specific instructions. The extension uses the `before_agent_start` event to inject the druids system prompt each turn.

```python
builder = ctx.agent(
    "builder",
    system_prompt="You are a builder agent. You implement specs.\n\nRead SETUP.md first.",
    prompt="Implement rate limiting on POST /api/keys.",
)
```

### Initial prompt delivery

The initial prompt (the `prompt` parameter on `ctx.agent()`) is delivered via the same path as `agent.send()` — pushed from the server to the extension via SSE after registration. One uniform message delivery path, no special cases.

## Images & Machines

### Image

A snapshot of an environment that can be spawned into a running machine. Subclass for different backends.

```python
class Image:
    async def spawn(self) -> Machine: ...
```

Concrete examples: `DockerImage("my-image")`, `MorphImage("snapshot-id")`, `LocalImage()`.

### Machine

A running environment. Subclass for different backends.

```python
class Machine:
    async def exec(self, command: str) -> ExecResult: ...
    async def write_file(self, path: str, content: bytes) -> None: ...
    async def read_file(self, path: str) -> bytes: ...
    async def stop(self) -> None: ...
```

- `exec` — run a shell command. Used to start pi, install packages, run arbitrary commands.
- `write_file` — write a file. Used to deploy the extension, write config files.
- `read_file` — read a file. Used to pull logs, artifacts.
- `stop` — tear down the machine.

Concrete examples: `DockerMachine`, `MorphMachine`, `LocalMachine`.

### Default image

Set a default image on the context. Override per agent or per machine.

```python
ctx = Context(image=DockerImage("base"))

# Uses default image
builder = ctx.agent("builder", prompt="...")

# Overrides default
special = ctx.agent("special", image=GpuImage("xl"), prompt="...")
```

### Shared machines

By default, each agent gets its own machine spawned from the image. To share a machine between agents, create it explicitly and assign agents to it:

```python
machine = ctx.machine(DockerImage("base"))
builder = ctx.agent("builder", machine=machine, prompt="...")
critic = ctx.agent("critic", machine=machine, prompt="...")
```

Multiple agents on one machine share the filesystem. Each gets its own pi process in its own tmux pane.

## Agent Connectivity

### Isolation by default

Agents are isolated. They cannot message each other unless explicitly connected. This prevents agents from going rogue and messaging everyone.

### Connecting agents

`ctx.connect()` enables the built-in `message` tool between two agents:

```python
ctx.connect(builder, reviewer)                        # bidirectional
ctx.connect(builder, logger, direction="forward")     # builder → logger only
```

### Built-in agent tools

These are always registered on every agent. The server handles them directly with topology enforcement:

| Tool | Arguments | Description |
|---|---|---|
| `message` | `receiver`, `message` | Send a message to a connected agent. Blocked if no connection exists. |
| `list_agents` | (none) | List all agent names in the execution. |
| `send_file` | `receiver`, `path`, `dest_path?` | Transfer a file to a connected agent's machine. |
| `download_file` | `sender`, `path`, `dest_path?` | Pull a file from a connected agent's machine. |

The `message` tool flow: agent calls `message(receiver="reviewer", message="done")` → extension POSTs to server → server checks topology (is sender connected to receiver?) → if yes, server pushes message to receiver via SSE → receiver's extension calls `pi.sendUserMessage("[From: builder] done")`.

The `send_file`/`download_file` flow: extension POSTs to server → server checks topology → server reads file from source machine via `machine.read_file()` → writes to destination machine via `machine.write_file()`.

### Program-routed communication

Without `ctx.connect()`, the only way agents can communicate is through program-defined tools:

```python
@builder.on("send_to_reviewer")
def on_send(text=""):
    """Send a message to the reviewer."""
    reviewer.send(text)
    return "Sent."
```

This gives the program full control over message routing, transformation, and side effects.

## Communication Protocol

### In-process server

When `ctx.run()` is called, the framework starts an HTTP server inside the process. This server handles agent registration, tool calls, SSE event streams, and built-in tools.

### Server addressing

The server must be reachable from every machine where agents run.

```python
# Local / Docker — auto-assigns a free port
# Agents on Docker reach it via host.docker.internal:{port}
# Agents on localhost reach it via 127.0.0.1:{port}
ctx = Context(image=DockerImage("base"))

# Remote agents — user provides the public URL
ctx = Context(image=DockerImage("base"), server_url="https://my-server.com:9000")
```

When `server_url` is provided, the server binds to the port from that URL. When omitted, it auto-assigns a free port and constructs the URL from the local address.

### Execution ID

Each program run gets a unique execution ID (UUID), generated at startup. Used for:
- Agent registration (agents belong to a specific execution)
- Tmux session naming
- Log directory naming

### Protocol: extension → server (HTTP POST)

**`POST /agents/register`** — agent registers on startup. Receives the list of available tools (built-in + program-defined).

```json
// Request
{"agent_id": "builder", "execution_id": "abc-123"}

// Response
{"tools": [{"name": "submit", "description": "...", "parameters": {...}}, ...]}
```

**`POST /agents/{agent_id}/tool_call`** — agent calls a tool. Blocks until the handler returns.

```json
// Request
{"tool": "submit", "params": {"summary": "implemented feature X"}}

// Response
{"result": "Submitted for audit."}
```

### Protocol: server → extension (SSE)

**`GET /agents/{agent_id}/events`** — long-lived SSE stream. The extension opens this on startup and keeps it open.

Event types:

| Event | Data | Description |
|---|---|---|
| `message` | `{"text": "..."}` | Message to deliver to the agent via `pi.sendUserMessage()`. |
| `new_tool` | `{"name": "...", "description": "...", "parameters": {...}}` | Register a new tool via `pi.registerTool()`. |
| `shutdown` | `{}` | Agent should shut down. |

### Extension deployment

The framework deploys the extension to each machine at agent startup via `machine.write_file()`. This decouples the image from the druids version. The extension is a single TypeScript file.

The extension receives configuration via environment variables set when pi is launched:
- `DRUIDS_SERVER_URL` — the server URL to connect to
- `DRUIDS_EXECUTION_ID` — this execution's ID
- `DRUIDS_AGENT_ID` — this agent's name

### Extension behavior

The extension is a pi extension (TypeScript) that:

1. On `session_start`: connects to the SSE stream at `{server_url}/agents/{agent_id}/events` and POSTs to `/agents/register`.
2. Receives the tool list and calls `pi.registerTool()` for each tool (built-in + program-defined).
3. Listens on the SSE stream for messages (calls `pi.sendUserMessage()`), new tools (calls `pi.registerTool()`), and shutdown signals.
4. When the agent calls a registered tool: the tool's `execute` function POSTs to `/agents/{agent_id}/tool_call` with the tool name and parameters, and returns the result.
5. On `before_agent_start`: appends the druids system prompt to pi's system prompt.

## Logging

Pi's own session logs capture all agent-level detail (tool calls, thinking, output, errors). The program logs orchestration events to a local JSONL file:

- Agent created / connected / disconnected
- Tool call dispatched and result returned
- Message routed between agents
- `done()` / `fail()` called

Log location: `./logs/{execution_id}/orchestrator.jsonl` (relative to where the program is run).

## Not in scope (for now)

- Git integration
- `agent.expose()` (port forwarding)
- ACP
- External clients / web dashboard / frontend
- Database / migrations / auth
- Agent crash recovery / error handling (to be designed later)
- `agent.fork()` / machine snapshots
