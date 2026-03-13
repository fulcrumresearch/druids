# Client API

Clients connect to running executions to observe agent activity and send events. There are three transport options: SSE for read-only streaming, WebSocket for bidirectional communication, and REST for one-shot client events.

All endpoints require authentication via `Authorization: Bearer <token>` header (REST and SSE) or `?token=<token>` query parameter (WebSocket).

## SSE stream

```
GET /api/executions/{slug}/stream
```

Read-only stream of execution activity. Returns `text/event-stream`.

### Resumption

Pass the `Last-Event-ID` header with the `id` from the last received event to resume after a disconnect. The server replays all events after that cursor.

### Event format

Each SSE message has an `id`, `event` field, and JSON `data`:

```
id: 42
event: activity
data: {"type": "tool_use", "agent": "builder", "tool": "Bash", "params": {"command": "npm test"}, "ts": "2026-03-10T12:00:00Z"}
```

### Event types

The `event` field is one of:

| Event | Description |
|---|---|
| `activity` | Agent activity. The `data` payload contains a `type` field (see below). |
| `done` | Execution finished. No more events will be sent. |

Keepalives are sent as SSE comments (`: keepalive\n\n`) every 15 seconds when idle.

### Activity types

The `type` field inside an `activity` event's `data`:

| Type | Fields | Description |
|---|---|---|
| `tool_use` | `agent`, `tool`, `params`, `ts` | Agent called a tool. |
| `tool_result` | `agent`, `tool`, `result`, `exit_code`?, `duration_secs`?, `ts` | Tool returned a result. |
| `prompt` | `agent`, `text`, `ts` | Message sent to an agent. |
| `response_chunk` | `agent`, `text`, `ts` | Text output from an agent (chunks are merged). |
| `connected` | `agent`, `session_id`, `ts` | Agent connected. |
| `disconnected` | `agent`, `ts` | Agent disconnected. |
| `client_event` | `event`, `data`, `ts` | Program emitted an event via `ctx.emit()`. |
| `error` | `agent`?, `error`, `ts` | Error occurred. `agent` is null for execution-level errors. |

### Example

```python
import httpx

with httpx.stream(
    "GET",
    f"{BASE}/api/executions/{slug}/stream",
    headers={"Authorization": f"Bearer {token}"},
) as r:
    for line in r.iter_lines():
        if line.startswith("data: "):
            print(line[6:])
```


## WebSocket

```
GET /api/executions/{slug}/ws?token=<token>
```

Bidirectional connection. The server streams the same activity events as SSE, and the client can send events to the execution.

### Server messages

The server sends JSON objects. Activity events have the same structure as SSE `data` payloads. In addition, the server sends:

| Type | Fields | Description |
|---|---|---|
| `done` | (none) | Execution finished. |
| `keepalive` | (none) | Sent every 15 seconds when idle. |
| `event_result` | `event`, `result` | Response to a client event. |
| `event_error` | `event`?, `error` | Client event failed. |

### Client messages

Send JSON objects to invoke client event handlers registered by the program:

```json
{"event": "propose", "data": {"feature": "auth", "plan": "Add JWT"}}
```

The server dispatches to the handler registered with `@ctx.on_client_event("propose")` and responds with:

```json
{"type": "event_result", "event": "propose", "result": {"status": "executor_spawned"}}
```

If the handler takes longer than 30 seconds or throws an error:

```json
{"type": "event_error", "event": "propose", "error": "Execution is not responding"}
```

### Close codes

| Code | Meaning |
|---|---|
| 4001 | Missing or invalid authentication token. |
| 4004 | Execution not found or not running. |
| 1011 | Unexpected server error. |

### Example

```python
import asyncio
import json
import websockets

async def main():
    async with websockets.connect(
        f"ws://{host}/api/executions/{slug}/ws?token={token}"
    ) as ws:
        # Send a client event
        await ws.send(json.dumps({"event": "get_state", "data": {}}))

        async for msg in ws:
            event = json.loads(msg)
            if event.get("type") == "event_result":
                print("Result:", event["result"])
            elif event.get("type") == "done":
                break
            else:
                print("Activity:", event)

asyncio.run(main())
```


## REST: send client event

```
POST /api/events/send
```

One-shot endpoint for sending a single client event. Useful when you do not need a persistent connection.

### Request

```json
{
    "execution_slug": "flying-fox",
    "event": "propose",
    "data": {"feature": "auth", "plan": "Add JWT authentication"}
}
```

### Response

```json
{"result": {"status": "executor_spawned", "feature": "auth"}}
```

### Errors

| Status | Meaning |
|---|---|
| 404 | Execution not running or no handler for the event name. |
| 504 | Handler did not respond within 30 seconds. |
| 500 | Handler threw an exception. |

### Discovering available events

`GET /api/executions/{slug}` returns a `client_events` list with the names of all registered handlers:

```json
{
    "execution_slug": "flying-fox",
    "status": "running",
    "client_events": ["get_state", "propose"],
    ...
}
```


## Other useful endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/executions/{slug}` | Execution status, agents, connections, client events. |
| `GET` | `/api/executions/{slug}/activity?n=50&compact=true` | Recent activity (same event types as the stream, paginated). |
| `POST` | `/api/executions/{slug}/agents/{name}/message` | Send a chat message to a specific agent. Body: `{"text": "..."}`. |
| `GET` | `/api/executions/{slug}/diff?agent={name}` | Git diff from an agent's VM. |
