# Agent Event Log — Spec Draft

## Overview

Each agent gets a single, ordered, append-only **event log**. The server is
the authority — it assigns sequence numbers and holds the canonical copy.
The agent process (pi extension) keeps a local mirror and syncs over a
**WebSocket** connection.

The WebSocket replaces the current SSE stream + HTTP POST endpoints for
tool calls and registration. Everything is a log event.

---

## Log entry format

Every entry in the log has the same envelope:

```json
{
  "seq": 1,
  "ts": 1713100000.123,
  "type": "tool_call",
  "origin": "agent",
  "data": { ... }
}
```

| Field    | Description                                      |
|----------|--------------------------------------------------|
| `seq`    | Monotonic integer, assigned by server, per agent  |
| `ts`     | Unix timestamp (float), set by server on append   |
| `type`   | Event type string                                 |
| `origin` | `"agent"` or `"server"`                           |
| `data`   | Type-specific payload                             |

---

## Event types

### Agent → Server (origin: "agent")

#### Lifecycle

| Type               | Data                          | Description                                |
|--------------------|-------------------------------|--------------------------------------------|
| `register`         | `{execution_id}`              | Agent announces itself on connect          |
| `connected`        | `{}`                          | WebSocket opened (before register)         |
| `disconnected`     | `{reason?}`                   | Agent going away                           |

#### Druids tool calls (orchestrator-managed tools)

| Type               | Data                          | Description                                |
|--------------------|-------------------------------|--------------------------------------------|
| `tool_call`        | `{call_id, tool, params}`     | Agent invokes a druids tool                |

#### Pi agent activity (what the LLM is doing locally)

These are emitted by the extension by hooking pi's event system.
They're informational — the server doesn't act on them, just logs them.

| Type               | Data                                        | Description                                |
|--------------------|---------------------------------------------|--------------------------------------------|
| `turn_start`       | `{turn_index}`                              | LLM turn started                           |
| `turn_end`         | `{turn_index}`                              | LLM turn ended                             |
| `message_start`    | `{role}`                                    | Message started (user/assistant/toolResult) |
| `message_chunk`    | `{text}`                                    | Streaming LLM output (assistant tokens)    |
| `message_end`      | `{role}`                                    | Message finished                           |
| `pi_tool_start`    | `{tool_call_id, tool, args}`                | Pi-local tool invoked (bash, read, etc.)   |
| `pi_tool_end`      | `{tool_call_id, tool, result?, is_error?}`  | Pi-local tool finished                     |

### Server → Agent (origin: "server")

These are pushed to the agent over the WebSocket.

| Type              | Data                                       | Description                                     |
|-------------------|--------------------------------------------|-------------------------------------------------|
| `registered`      | `{tools: [...]}`                           | Confirms registration, sends initial tool defs   |
| `tool_result`     | `{call_id, result}` or `{call_id, error}`  | Result of a druids tool call                     |
| `tool_registered` | `{name, description, parameters}`          | New tool definition pushed after registration    |
| `message`         | `{text}`                                   | Message delivered to the agent                   |
| `shutdown`        | `{}`                                       | Runtime is shutting down                         |

### Server-only (logged but not pushed to agent)

| Type              | Data                                    | Description                                     |
|-------------------|-----------------------------------------|-------------------------------------------------|
| `agent_created`   | `{agent}`                               | Runtime created the agent object                 |
| `agent_spawned`   | `{agent, tmux_session}`                 | Agent process launched                           |
| `done`            | `{result}`                              | `exit()` called                                  |
| `failed`          | `{reason}`                              | `fail()` called                                  |

---

## WebSocket protocol

### Connection

```
ws://{server}/agents/{agent_id}/ws
```

### Message framing

All messages are JSON. Two shapes:

**Agent → Server** (requests / events from agent):
```json
{"type": "sync", "after": 6}
{"type": "event", "event_type": "register", "data": {"execution_id": "abc"}}
{"type": "event", "event_type": "tool_call", "data": {"call_id": "x1", "tool": "submit", "params": {"summary": "done"}}}
{"type": "event", "event_type": "turn_start", "data": {"turn_index": 0}}
{"type": "event", "event_type": "pi_tool_start", "data": {"tool_call_id": "tc-7", "tool": "bash", "args": {"command": "ls -la"}}}
```

**Server → Agent** (log entries):
```json
{"seq": 7, "ts": 1713100000.1, "type": "registered", "origin": "server", "data": {"tools": [...]}}
{"seq": 8, "ts": 1713100000.2, "type": "tool_result", "origin": "server", "data": {"call_id": "x1", "result": "submitted"}}
```

The server always sends canonical log entries — same shape as what's stored.

### Lifecycle

```
1. Agent opens WebSocket
2. Agent sends:  {"type": "sync", "after": 0}
3. Agent sends:  {"type": "event", "event_type": "register", "data": {"execution_id": "..."}}
4. Server appends register event to log (seq=1)
5. Server appends registered event to log (seq=2)
6. Server sends: {"seq": 1, ...register...}
7. Server sends: {"seq": 2, ...registered with tools...}
8. Agent is now live

On reconnect:
1. Agent opens new WebSocket
2. Agent sends:  {"type": "sync", "after": 8}
3. Server replays seq 9, 10, ...
4. Agent resumes
```

### Tool call flow (druids tools)

```
Agent sends:    {"type": "event", "event_type": "tool_call",
                 "data": {"call_id": "tc-1", "tool": "submit", "params": {"summary": "done"}}}
                                                          |
Server:  appends tool_call to log (seq=N)                 |
         runs handler                                     |
         appends tool_result to log (seq=N+1)             |
                                                          |
Server sends:   {"seq": N,   "type": "tool_call",   ...}
Server sends:   {"seq": N+1, "type": "tool_result", ...}
```

The agent correlates result to request via `call_id`.

### Pi activity events (informational)

```
Agent sends:    {"type": "event", "event_type": "pi_tool_start",
                 "data": {"tool_call_id": "tc-7", "tool": "bash", "args": {"command": "ls"}}}
                                                          |
Server:  appends to log (seq=N), does NOT act on it       |
Server sends:   {"seq": N, ...pi_tool_start...}           |
                                                          |
Agent sends:    {"type": "event", "event_type": "pi_tool_end",
                 "data": {"tool_call_id": "tc-7", "tool": "bash", "result": "..."}}
                                                          |
Server:  appends to log (seq=N+1)                         |
Server sends:   {"seq": N+1, ...pi_tool_end...}
```

Server just logs these. They're for observability — watching what an agent
is doing in real time, and having a full record after the fact.

---

## Storage

The log is stored as a JSONL file per agent:

```
logs/{execution_id}/{agent_name}.jsonl
```

One line per event, same format as the log entry envelope.

---

## What changes

| Current                          | New                                              |
|----------------------------------|--------------------------------------------------|
| `POST /agents/register`         | `register` event over WebSocket                  |
| `POST /agents/{id}/tool_call`   | `tool_call` event over WebSocket                 |
| `GET /agents/{id}/events` (SSE) | WebSocket connection with sync                   |
| `_AgentSession` (channel+flag)  | `AgentEventLog` (append-only log + WebSocket)    |
| `AgentChannel` / `SSEEvent`     | Removed                                          |
| Orchestrator JSONL logging       | Built into the event log                         |
| Extension: HTTP POST + SSE       | Extension: WebSocket only                        |

---

## Open questions

- **Keepalive**: WebSocket ping/pong frames, or application-level
  heartbeat events?
- **message_chunk volume**: Streaming tokens could be very chatty. Buffer
  or throttle before sending? Or log at message_end with full text only?
