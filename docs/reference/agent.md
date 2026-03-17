# Agent

The agent object is returned by [`ctx.agent()`](ctx.md#ctxagent). It
provides methods to register tool handlers, send prompts, run shell
commands, and expose ports.

## agent.on()

```python
@agent.on(tool_name)
async def handler(param1: str, param2: int) -> str:
    ...
```

Register a tool handler. The decorated function becomes an MCP tool
available to the agent. The runtime generates the tool schema from the
function's parameter names and type annotations.

| Parameter | Type | Description |
|---|---|---|
| `tool_name` | `str` | The tool name the agent sees. |

The handler function's signature defines the tool's input schema:
- Parameter names become tool parameter names.
- Type annotations are used for the schema (if provided).
- The function's docstring becomes the tool description.
- The return value is sent back to the agent as the tool result.

Handlers can be sync or async. If async, the runtime awaits the coroutine.

### Example

```python
@executor.on("submit_for_review")
async def on_submit(diff: str, summary: str) -> str:
    """Submit code for review."""
    await reviewer.send(f"Review this diff:\n{diff}\n\nSummary: {summary}")
    return "Submitted for review."
```

The executor agent sees a `submit_for_review` tool with `diff` and `summary`
string parameters and the description "Submit code for review."

## agent.send()

```python
await agent.send(message)
```

Send a prompt to the agent. Blocks until the agent is provisioned and
connected, then delivers the message fire-and-forget (does not wait for
the agent to finish processing).

| Parameter | Type | Description |
|---|---|---|
| `message` | `str` | The message text to send. |

## agent.exec()

```python
result = await agent.exec(command)
```

Run a shell command on the agent's VM. Blocks until the agent is provisioned.

| Parameter | Type | Description |
|---|---|---|
| `command` | `str` | Shell command to execute. |

Returns an object with:

| Field | Type | Description |
|---|---|---|
| `exit_code` | `int` | Process exit code. |
| `stdout` | `str` | Standard output. |
| `stderr` | `str` | Standard error. |
| `ok` | `bool` | `True` if `exit_code == 0`. |

### Example

```python
result = await agent.exec("cd /home/agent/repo && git diff")
if result.ok:
    print(result.stdout)
```

## agent.expose()

```python
url = await agent.expose(name, port)
```

Expose a port on the agent's VM as a public HTTPS URL. Blocks until the
agent is provisioned.

| Parameter | Type | Description |
|---|---|---|
| `name` | `str` | Service name identifier. |
| `port` | `int` | Port number to expose (1-65535, excluding the bridge port). |

Returns the public HTTPS URL as a string.
