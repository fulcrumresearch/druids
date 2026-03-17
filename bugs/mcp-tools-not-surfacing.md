# Bug: Program-registered MCP tools not surfacing to agents

## Summary

Tools registered via `@agent.on("tool_name")` in Druids programs are never available to agents on their VMs. The MCP endpoint works (verified via curl), the ACP SDK supports MCP servers (verified via standalone test), but agents report "No such tool available" when trying to call program-defined tools.

This blocks all multi-agent programs that rely on tool-based coordination (autoresearch, build.py patterns with custom tools, etc.).

## Evidence

### 1. Agent explicitly says tools are missing

From execution `tender-invention`, the scientist agent's session JSONL:

```
"I don't see any MCP tools in my tool list. I have these tools available:
Bash, Read, Write, Edit, Glob, Grep, Task, WebFetch, WebSearch,
NotebookEdit, TodoWrite, Skill, EnterPlanMode, ExitPlanMode, TaskOutput, TaskStop"
```

### 2. Agent tried to call tools, got errors

The agent attempted to call `read_state` and `run_experiments` but Claude Code routed them through the `Skill` tool (slash commands), not MCP:

```json
{"name": "Skill", "input": {"skill": "read_state"}}
// Result: "Unknown skill: read_state"

{"name": "Skill", "input": {"skill": "run_experiments", "args": "[...]"}}
// Result: "Unknown skill: run_experiments"
```

In a later test (`amber-nocturne`), the agent called tools by name directly:

```
tool_call: ping → "Error: No such tool available: ping"
tool_call: get_counter → "Error: No such tool available: get_counter"
```

### 3. MCP endpoint works fine

Curling the server's MCP endpoint returns all tools correctly:

```bash
curl -X POST 'https://druids.dev/api/executions/wistful-ballade/agents/tester/mcp' \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"jsonrpc":"2.0","method":"tools/list","id":1}'
```

Returns:
```json
{"tools": [
  {"name": "expose", ...},
  {"name": "message", ...},
  {"name": "ping", "description": "Ping. Returns your message.", ...},
  {"name": "get_counter", ...},
  {"name": "increment", ...},
  {"name": "finish", ...}
]}
```

### 4. Standalone ACP test proves MCP works

Running `claude-code-acp` directly on a Druids VM with a local MCP server:

```python
send("session/new", {
    "cwd": "/home/agent/repo",
    "mcpServers": [{"name": "test-tools", "url": "http://127.0.0.1:9999", "type": "http", "headers": []}]
})
```

The agent discovers and tries to call `mcp__test-tools__ping`. The tool IS surfaced. However, the call fails at the permission stage.

Key output:
```
tool_call {"_meta": {"claudeCode": {"toolName": "mcp__test-tools__ping"}},
  "toolCallId": "toolu_01WFuU5oKtj74FvSLsLH1s3k",
  "rawInput": {"message": "hello"}, "status": "pending"}

session/request_permission  → approved with {"outcome": "allow"}

tool_call_update: status: "failed", rawOutput: "The user doesn't..."
```

## Root Cause Analysis

The MCP integration has two issues:

### Issue A: MCP server connection fails silently in production

When Druids creates an agent via `_connect_agent()`, it builds the MCP server URL and passes it in `session/new`:

```python
# execution.py line 543
druids_mcp_url = f"{settings.base_url}/api/executions/{self.slug}/agents/{agent.name}/mcp"
mcp_servers.append({
    "name": "druids",
    "url": druids_mcp_url,
    "headers": {"Authorization": f"Bearer {agent.config.env.get('DRUIDS_ACCESS_TOKEN', '')}"},
})
```

This gets serialized correctly (verified). But the agent never connects to the MCP server. Possible causes:

1. **The MCP URL is unreachable from the agent VM.** The URL is `https://druids.dev/api/executions/.../mcp`. The VM can reach `druids.dev` (it connects for the bridge relay). But maybe there's a TLS/cert issue, or the MCP endpoint requires a different auth path.

2. **The `session/new` request format doesn't match what `claude-code-acp` expects.** The headers are passed as `[{"name": "Authorization", "value": "Bearer ..."}]` (list of HttpHeader objects). The standalone test used `"headers": []` (empty). Maybe non-empty headers cause a parsing issue.

3. **Server restart loses state.** We observed that after a server restart, `get_execution` returned `agents: [], connections: []` even though VMs were still running. If the server restarts between `session/new` and the agent's first MCP `tools/list` call, the MCP endpoint returns 404 and the agent silently gives up on MCP tools.

### Issue B: Permission handling for MCP tool calls

Even when MCP tools are discovered (standalone test), the tool call fails at the permission stage. The `session/request_permission` request comes in, the test script responds with `{"outcome": "allow"}`, but the tool still fails with "The user doesn't...".

In Druids, `_connect_agent` starts the agent with `--dangerously-skip-permissions`, which should bypass all permission checks. But MCP tool calls might go through a different permission path that isn't bypassed.

## Reproduction Steps

### Minimal program that demonstrates the bug

```python
async def program(ctx, **kwargs):
    agent = await ctx.agent(
        "tester",
        system_prompt="You are a tool tester.",
        prompt="Call the `ping` tool with message='hello'.",
        model="claude-sonnet-4-6",
        git="read",
    )

    @agent.on("ping")
    async def on_ping(message: str = ""):
        """Ping. Returns your message."""
        ctx.done(f"pong: {message}")
        return f"pong: {message}"

    await ctx.wait()
```

Run with:
```bash
druids exec test.py --devbox autoresearch-bench/ar-cc-starter
```

Expected: agent calls `ping`, program completes with "pong: hello".
Actual: agent says it has no tool called `ping`.

### Standalone ACP test (proves MCP works in isolation)

On any Druids VM with a running agent:

1. Start a local MCP server:
```python
# /tmp/mcp_server.py — serves one tool called "ping"
python3 /tmp/mcp_server.py &
```

2. Run `claude-code-acp` with mcpServers:
```python
# Send session/new with mcpServers pointing to localhost:9999
# Agent discovers mcp__test-tools__ping
# But tool call fails at permission stage
```

Full test scripts are at `/tmp/test3.py` and `/tmp/mcp_server.py` on the VM for execution `gentle-madrigal`.

## Plan of Attack

### Step 1: Reproduce locally

1. Run the Druids server locally (`cd server && uvicorn ...`)
2. Run the minimal program above against a local devbox
3. Add logging to `_connect_agent()` to verify:
   - The MCP URL being passed
   - The `session/new` JSON-RPC payload (print the full dict)
   - The `session/new` response (does it acknowledge mcpServers?)

### Step 2: Verify MCP connection from agent VM

1. SSH into the agent VM after it connects
2. Curl the MCP endpoint from the VM to verify reachability:
   ```bash
   curl -X POST "$DRUIDS_MCP_URL" -H "Authorization: Bearer $TOKEN" \
     -H 'Content-Type: application/json' \
     -d '{"jsonrpc":"2.0","method":"tools/list","id":1}'
   ```
3. Check `claude-code-acp` stderr/debug output for MCP connection errors:
   ```bash
   ACP_LOG_LEVEL=debug claude-code-acp --dangerously-skip-permissions
   ```

### Step 3: Fix the MCP server name

The standalone test showed tools are prefixed: `mcp__test-tools__ping`. In Druids, the MCP server is named `"druids"`, so tools would be `mcp__druids__ping`. But the tool handlers are registered as just `"ping"`.

Check: when the MCP endpoint serves `tools/list`, does it return tool names as `"ping"` or `"mcp__druids__ping"`? The endpoint returns plain names (`"ping"`). But `claude-code-acp` adds the prefix internally. The server's tool routing (`_handle_tool_call`) looks up handlers by unprefixed name. So if the agent calls `mcp__druids__ping`, the server needs to strip the prefix before routing.

Check `_handle_tool_call` in `execution.py` and `runtime/__init__.py` for prefix handling.

### Step 4: Fix permission handling for MCP tools

The standalone test showed MCP tool calls trigger `session/request_permission` even with `--dangerously-skip-permissions`. Check:

1. Does `--dangerously-skip-permissions` apply to MCP tools? It might only apply to built-in Claude Code tools.
2. Does the Druids `AgentConnection` handle `session/request_permission` requests? Check `connection.py` for request handlers.
3. If not handled, add a handler that auto-approves all tool calls.

### Step 5: Handle server restarts

The in-memory execution registry is lost on server restart. This means:
- The MCP endpoint returns 404 after restart
- Agent loses all program-defined tools mid-execution

Options:
- Persist execution state to DB (already partially done)
- Reconnect executions on server startup
- At minimum: make the MCP endpoint return a useful error instead of 404

### Step 6: Integration test

Write a test that:
1. Creates an execution with a program that registers tools
2. Waits for the agent to connect
3. Sends a prompt asking the agent to call a tool
4. Verifies the tool handler fires and returns a result
5. Verifies `ctx.done()` is called

## Key Files

| File | What to check |
|------|---------------|
| `server/druids_server/lib/execution.py:520-563` | `_connect_agent` — builds MCP servers, creates session |
| `server/druids_server/lib/connection.py:233-268` | `new_session` — serializes mcpServers for ACP |
| `server/druids_server/api/routes/agent_mcp.py:40-99` | MCP endpoint — serves tools/list and tools/call |
| `server/druids_server/lib/runtime_relay.py:58-76` | Fetches tool schemas from runtime sandbox |
| `runtime/druids_runtime/__init__.py:315-336` | `_handle_tool_call` — routes tool calls to handlers |
| `bridge/bridge.py` | stdin/stdout relay (probably not the issue) |

## Affected Programs

Any program using `@agent.on("tool_name")`:
- `.druids/build.py` (commit, surface, submit_for_review)
- `.druids/autoresearch.py` (read_state, run_experiments, etc.)
- `.druids/labrat.py` (submit_result)
- `.druids/redteamer.py` (report_finding, accept_finding)
- All of them.

## Questions

1. Have program-defined tools EVER worked in production? Or has the system always relied on agents using built-in tools (Terminal, Edit, etc.) and the tools being called via the `druids tool` CLI?

2. Is there a different code path for built-in tools (expose, message, list_agents) vs program-defined tools? Built-in tools might work through the ACP connection directly, not through MCP.

3. What version of `@zed-industries/claude-code-acp` is expected? The VMs have `0.16.2`. Is there a newer version that fixes MCP handling?
