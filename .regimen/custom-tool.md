# Custom tool registration and invocation

Covers the program-defined tool feature. A program creates an agent, registers a tool handler via `@agent.on()`, and the agent discovers and calls it through the MCP bridge. Relates to `server/druids_server/lib/agents/base.py` (deferred session creation), `server/druids_server/api/routes/agent_mcp.py` (MCP tool dispatch), and `runtime/druids_runtime/__init__.py` (tool registration).

## Setup

Start the server. This assumes a running Postgres database and a configured Morph API key.

```bash
cd /home/ubuntu/code/druids/server && uv run uvicorn druids_server.app:app --host 0.0.0.0 --port 8002 &
```

Wait for the server to be ready.

```bash timeout=30
curl -s --retry 5 --retry-connrefused https://me.uzpg.me/api/devboxes | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'{len(d[\"devboxes\"])} devbox(es)')"
```

Expected: prints a count of devboxes, at least 1. If zero, a devbox must be created first (see the devbox-setup regimen).

## Create execution with a custom tool

POST an execution whose program registers a `greet` tool via `@agent.on()` after creating the agent. The agent is prompted to call this tool.

```bash timeout=30
SLUG=$(curl -s -X POST https://me.uzpg.me/api/executions \
  -H "Content-Type: application/json" \
  -d "$(python3 -c "
import json
src = '''
async def program(ctx, spec=\"\", **kwargs):
    agent = await ctx.agent(\"worker\", prompt=\"Call the greet tool with name=Druids. Report exactly what it returns, nothing else.\")

    @agent.on(\"greet\")
    def greet(name: str = \"World\"):
        \"\"\"Greet someone by name.\"\"\"
        return f\"Hello, {name}!\"

    await ctx.wait()
'''
print(json.dumps({'devbox_name': 'test', 'program_source': src, 'args': {'spec': 'custom tool test'}}))")" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['execution_slug'])")
echo "Execution: $SLUG"
```

Expected: prints `Execution: <slug>` with a valid slug string.

## Agent calls the custom tool

Poll the execution activity until the agent calls `mcp__druids__greet` and reports the result. The agent should discover the tool via the MCP server, call it with `name=Druids`, and get `Hello, Druids!` back.

```bash timeout=120
for i in $(seq 1 30); do
  ACTIVITY=$(curl -s "https://me.uzpg.me/api/executions/$SLUG/activity?n=100&compact=false" 2>/dev/null)
  TOOL_CALLED=$(echo "$ACTIVITY" | python3 -c "
import sys, json
data = json.load(sys.stdin)
for e in data.get('recent_activity', []):
    if e.get('type') == 'tool_result' and 'greet' in e.get('tool', ''):
        result = e.get('result', '')
        if 'Hello' in str(result):
            print(f'PASS: tool returned: {result}')
            exit(0)
exit(1)
" 2>/dev/null)
  if [ $? -eq 0 ]; then
    echo "$TOOL_CALLED"
    break
  fi
done
```

Expected: prints `PASS: tool returned: Hello, Druids!` (with possible escaping). The activity trace should contain a `tool_use` event for `mcp__druids__greet` or `druids:greet` with `name=Druids`, followed by a `tool_result` containing `Hello, Druids!`.

## MCP tools/list shows the custom tool

Directly query the MCP endpoint for the agent to verify the `greet` tool is listed.

```bash timeout=10
curl -s -X POST "https://me.uzpg.me/api/executions/$SLUG/agents/worker/mcp" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' \
  | python3 -c "
import sys, json
data = json.load(sys.stdin)
tools = data['result']['tools']
names = [t['name'] for t in tools]
assert 'greet' in names, f'greet not in tool list: {names}'
print(f'PASS: tools/list contains greet (total: {len(tools)} tools)')
"
```

Expected: prints `PASS: tools/list contains greet` with the total tool count.

## Cleanup

```bash
pkill -f "uvicorn druids_server" 2>/dev/null
```
