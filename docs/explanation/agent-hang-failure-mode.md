# Agent hang failure mode

When an agent hangs, the entire message pipeline stays "alive" but becomes a black hole. Messages are delivered to the agent but produce no effect, and the server has no mechanism to detect or recover from the situation.

## The failure sequence

### 1. Agent gets stuck on a prompt

The agent (claude-code-acp) processes `session/prompt` requests one at a time. If the active prompt handler gets stuck -- a bash command that hangs, a slow LLM call, an MCP tool call that never returns -- the agent stops producing stdout.

### 2. The server-side receive loop blocks forever

The `_receive_loop` in `Connection` (`acp/connection.py:148-162`) calls `await self._reader.readline()` each iteration. The reader is a `BridgeRelayReader`, which calls `await session.incoming.get()` on the relay hub queue. If the agent stopped producing output, this queue gets no data, and the `get()` blocks indefinitely. The loop is alive but parked.

### 3. Pending futures are never resolved

`AgentConnection.prompt()` calls `send_request("session/prompt", ...)`, which creates an `asyncio.Future` in the state store and awaits it. That future is only resolved when the receive loop reads a matching response from the relay. Since the receive loop is blocked waiting for data, the future hangs forever.

`reject_all_outgoing` exists but is only called in two places:

- `_on_receive_error` -- triggered when the receive loop task fails with an exception. A normal exit (EOF) or indefinite block does not trigger this.
- `Connection.close()` -- only called when someone explicitly tears down the connection.

### 4. New messages are delivered but not processed

This is the part that looks like "messages no longer do anything." When `Agent.prompt()` is called with a new message:

```python
async def prompt(self, text: str) -> None:
    async def _prompt_with_session():
        await self._ensure_session()
        await self.connection.prompt(text)
    task = asyncio.create_task(_prompt_with_session())
    task.add_done_callback(_log_task_exception)
```

A fire-and-forget task is created. It calls `send_request`, which sends the JSON-RPC message through the relay. The bridge pulls it and writes it to the agent's stdin. The agent's ACP receive loop reads it and dispatches it. But claude-code-acp has one active turn at a time. The new prompt queues behind the stuck one. It will never be processed until the stuck prompt finishes (which it will not).

Meanwhile, the fire-and-forget task on the server side is also parked forever on its own future. Each new prompt sent to the agent creates another orphaned task and another unresolvable future. They accumulate silently. The only signal is a `logger.warning` in `_log_task_exception` if the task eventually fails -- but it will not fail, it will just hang.

### 5. Cancel does not reliably help

`AgentConnection.cancel()` sends a `session/cancel` notification. The notification is delivered to the agent. But whether it actually interrupts the stuck handler depends on claude-code-acp's internals. If the agent is blocked on a synchronous operation or a non-cancellable I/O call, the cancel notification arrives at the ACP layer but cannot reach the stuck code.

### 6. The bridge can see the problem but nobody checks

The bridge `/status` endpoint reports `seconds_since_stdout`. If this number is growing, the agent is hung. But nothing on the server side polls this or acts on it.

## Why the design produces this failure

The system is built around a request/response protocol (JSON-RPC) over a unidirectional relay, with no timeouts on individual requests and no out-of-band liveness checking. It is fire-and-forget (`prompt_nowait` / `Agent.prompt()`) all the way through, so the caller never learns the message had no effect. The relay masks the real TCP-level signals (connection close, pipe errors) that would normally tell you the other end is dead.

In a direct TCP connection, if the other end dies, you get a broken pipe or connection reset. The receive loop would see EOF and exit. But the relay interposes the bridge and HTTP long-poll, so the server-side reader just sees an empty queue, not a closed connection.

## Affected code paths

| Component | File | What goes wrong |
|-----------|------|-----------------|
| ACP Connection | `acp/connection.py` | `send_request` awaits future with no timeout; `_receive_loop` clean exit does not reject pending futures |
| Bridge relay reader | `server/druids_server/lib/connection.py` | `readline()` blocks on `session.incoming.get()` forever when bridge stops pushing |
| Bridge relay hub | `server/druids_server/lib/connection.py` | No liveness signal; session stays registered after bridge relay dies |
| Agent.prompt | `server/druids_server/lib/agents/base.py` | Fire-and-forget task hangs silently; caller has no way to detect failure |
| Bridge status | `bridge/bridge.py` | Reports `seconds_since_stdout` but server never checks it |

## Fix: message-aware bridge with prompt queue

The fix makes the bridge a message-aware proxy instead of a dumb pipe. Inspired by the Court project's orchestration agent pattern (`fulcrumresearch/court`), the bridge now parses JSON-RPC messages, queues `session/prompt` requests, and feeds them to the agent one at a time.

### Design

Two concurrent loops with a shared queue:

**Message router** (`route_incoming`): reads messages from the relay, classifies them. `session/prompt` requests go to the queue. `session/cancel` notifications set a cancel flag and forward to the agent. Everything else (initialize, session/new, session/set_model) passes through to the agent immediately.

**Turn processor** (`process_turns`): pops prompts from the queue one at a time. Forwards the prompt to the agent via stdin. Waits for the agent's response (detected by `read_stdout` matching the response ID). Then moves to the next queued prompt.

**Liveness monitor** (`monitor_liveness`): checks every 5 seconds whether a prompt is in-flight and the agent has gone silent. If no stdout for longer than the liveness timeout (default 300s, configurable via `--liveness-timeout`), synthesizes JSON-RPC error responses for the in-flight prompt and all queued prompts, pushes them through the relay, and kills the agent process.

**Stdout reader** (`read_stdout`): now parses each line as JSON. If the line is a response whose `id` matches the in-flight prompt, it signals the turn processor to move on. On EOF (agent died), it drains all pending prompts as error responses.

### What this fixes

1. **Hung agents are detected.** The liveness monitor catches agents that stop producing output and synthesizes error responses. Server-side futures are rejected with a clear error instead of hanging forever.

2. **Agent death is handled.** When the agent process exits, `read_stdout` detects EOF and immediately generates error responses for all pending prompts. The server sees the errors and can act on them.

3. **Cancel always works.** The message router handles cancel directly -- it is always responsive because it is a separate loop from the turn processor. Even if the agent ignores the cancel, the liveness monitor will eventually clean up.

4. **Multiple prompts do not pile up as orphaned futures.** The prompt queue serializes them. Each gets processed in order, or gets an error response if the agent dies or hangs.

5. **Queue visibility.** The `/status` endpoint now reports `queue_depth`, `prompt_in_flight`, and `seconds_since_prompt_activity`.

### What did not change

The server-side code (`AgentConnection`, `BridgeRelayHub`, `Agent.prompt()`) is unchanged. ACP protocol semantics are preserved: `send_request("session/prompt")` still blocks until the turn completes or an error response arrives. The relay transport (HTTP long-poll push/pull) is unchanged.
