# ctx

The `ctx` object is passed to every program function. It creates agents,
registers event handlers, and controls the execution lifecycle.

## ctx.agent()

```python
agent = await ctx.agent(
    name,
    prompt=None,
    system_prompt=None,
    model="claude",
    git=None,
    working_directory=None,
    share_machine_with=None,
    mcp_servers=None,
)
```

Create and register an agent. Returns immediately. VM provisioning, bridge
startup, and ACP connection run in the background. Methods that need the
connection (`send`, `exec`, `expose`) block until provisioning completes.

### Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `name` | `str` | required | Unique agent name within this execution. |
| `prompt` | `str \| None` | `None` | Initial user prompt sent after provisioning completes. |
| `system_prompt` | `str \| None` | `None` | System prompt for the agent backend. Druids tool documentation is prepended automatically. |
| `model` | `str` | `"claude"` | `"claude"`, `"codex"`, or a specific model ID (e.g. `"claude-sonnet-4-6"`, `"claude-opus-4-6"`). Bare `"claude"` or `"codex"` picks the default model for that backend. |
| `git` | `str \| None` | `None` | Git access level: `"read"`, `"post"`, or `"write"`. `None` means no git token and no repo clone. See [Git permission levels](git-permissions.md). |
| `working_directory` | `str \| None` | `None` | Override the default `/home/agent` directory. |
| `share_machine_with` | `agent \| None` | `None` | An agent (returned by a previous `ctx.agent()` call) to share a VM with. Both agents run on the same VM with separate bridge processes. The shared agent must be provisioned first. |
| `mcp_servers` | `dict \| None` | `None` | MCP servers to attach. Dict keyed by server name; each value has `"url"` and optional `"headers"`. String values containing `$VAR_NAME` are resolved from devbox secrets at startup. |

### Template variables

The `prompt` and `system_prompt` strings support `$variable` substitution
using Python `string.Template`. Available variables:

| Variable | Value |
|---|---|
| `$execution_slug` | The execution's slug identifier. |
| `$agent_name` | This agent's name. |
| `$working_directory` | This agent's working directory. |
| `$branch_name` | Git branch name (`druids/{slug}`). |
| `$spec` | The task specification string (if provided). |

### Example

```python
async def program(ctx):
    worker = await ctx.agent(
        "worker",
        model="claude-sonnet-4-6",
        git="write",
        prompt="Implement the feature described in the spec.",
        system_prompt="You are an implementor agent. Spec:\n$spec",
    )
```

## ctx.done()

```python
await ctx.done(result=None)
```

Signal successful completion. The execution transitions to `"completed"`
status. The optional `result` value is stored with the execution.

## ctx.fail()

```python
await ctx.fail(reason)
```

Signal failure. The execution transitions to `"failed"` status with the
given reason string.

## ctx.wait()

```python
await ctx.wait()
```

Signal readiness and block until the execution is stopped. Call this at the
end of the program function after all agents and handlers are registered.
Starts the internal HTTP server (if not already started) and registers
client event names with the server.

Programs that call `await ctx.done()` or `await ctx.fail()` from a handler do not need
to call `await ctx.wait()`. Use `ctx.wait()` when the program should stay alive
indefinitely and rely on external signals to end.

## ctx.connect()

```python
ctx.connect(a, b, direction="both")
```

Connect two agents so they can message each other using the built-in `message` tool.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `a` | `Agent \| str` | required | First agent (or agent name). |
| `b` | `Agent \| str` | required | Second agent (or agent name). |
| `direction` | `str` | `"both"` | `"both"` for bidirectional, `"forward"` for a-to-b only. |

### Example

```python
ctx.connect(builder, reviewer)  # bidirectional
ctx.connect(builder, logger, direction="forward")  # builder -> logger only
```

## ctx.emit()

```python
await ctx.emit(event, data=None)
```

Emit an event to connected clients (the web dashboard or CLI). Fire-and-forget.

| Parameter | Type | Description |
|---|---|---|
| `event` | `str` | Event name. |
| `data` | `dict \| None` | Optional event payload. |

## ctx.on_client_event()

```python
@ctx.on_client_event(name)
async def handler(**data):
    ...
```

Register a handler for events sent by clients (web dashboard or CLI) to
the execution. The handler receives the event data as keyword arguments.
Return value is sent back to the client.

## ctx properties

| Property | Type | Description |
|---|---|---|
| `ctx.slug` | `str` | Execution slug identifier. |
| `ctx.repo_full_name` | `str \| None` | GitHub repository (`owner/repo`) if associated with a devbox that has a repo. |
| `ctx.spec` | `str \| None` | Task specification string, passed as the `spec` argument to `druids exec`. |
| `ctx.agents` | `dict[str, Agent]` | Read-only copy of agent name to agent mapping. |
| `ctx.connections` | `set[str]` | Names of agents with active ACP connections. Refreshed on each client event. |
