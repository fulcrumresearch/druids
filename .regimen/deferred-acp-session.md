# Deferred ACP session: program-defined tools visible to agents

Covers the timing fix for program-defined tool visibility. When a program calls `ctx.agent()` and then registers tools via `@agent.on()`, the ACP session creation must be deferred until the first `prompt()` call so that ACP's `tools/list` fetch sees the handlers. Relates to `server/druids_server/lib/agents/base.py`, `server/druids_server/lib/execution.py`, and `server/druids_server/api/routes/agent_mcp.py`.

## Setup

Start the server with Docker sandbox support.

```bash
cd /home/ubuntu/code/druids/server && rm -f druids.db druids.db-shm druids.db-wal && uv run uvicorn druids_server.app:app --host 0.0.0.0 --port 8002 &
```

Wait for the server to be ready.

```bash timeout=30
curl -s --retry 5 --retry-connrefused http://localhost:8002/health | head -1
```

Expected: returns HTML (the frontend SPA).

Create a devbox record pointing to the `druids-base` Docker image.

```bash timeout=10
cd /home/ubuntu/code/druids/server && uv run python3 -c "
import asyncio
from druids_server.db.session import get_session, init_db
from druids_server.db.models.devbox import get_or_create_devbox
from druids_server.db.models.user import get_or_create_user
from datetime import datetime, timezone

async def main():
    await init_db()
    async with get_session() as db:
        user = await get_or_create_user(db, github_id=0, github_login='local')
        devbox = await get_or_create_devbox(db, user.id, 'test')
        devbox.name = 'test'
        devbox.repo_full_name = 'test/repo'
        devbox.snapshot_id = 'druids-base'
        devbox.setup_completed_at = datetime.now(timezone.utc)
        db.add(devbox)
        print(f'Devbox ready: {devbox.name}')

asyncio.run(main())
"
```

Expected: prints "Devbox ready: test".

## Program-defined tool is visible to agent

Create an execution with a program that registers a tool via `@agent.on()` after `ctx.agent()` returns. The agent should be able to discover and call the tool.

```bash timeout=120
cd /home/ubuntu/code/druids/server && SLUG=$(curl -s -X POST http://localhost:8002/api/executions \
  -H "Content-Type: application/json" \
  -d "$(python3 -c "
import json
src = '''
async def program(ctx, spec='', **kwargs):
    agent = await ctx.agent('builder', prompt='Call the greet tool with name=Druids. Report what it returns.')

    @agent.on('greet')
    def greet(name: str = 'World'):
        return f'Hello, {name}!'

    await ctx.wait()
'''
print(json.dumps({'devbox_name': 'test', 'program_source': src, 'args': {'spec': 'test'}}))" \
)" | python3 -c "import sys,json; print(json.load(sys.stdin)['execution_slug'])")
echo "Execution slug: $SLUG"

# Poll until the agent trace shows the tool was called
for i in $(seq 1 30); do
  TRACE=$(curl -s "http://localhost:8002/api/executions/$SLUG/agents/builder/trace" 2>/dev/null)
  if echo "$TRACE" | python3 -c "import sys,json; t=json.load(sys.stdin).get('trace',[]); [exit(0) for e in t if e.get('title','')=='mcp__druids__greet']; exit(1)" 2>/dev/null; then
    echo "Tool called successfully"
    echo "$TRACE" | python3 -m json.tool
    break
  fi
  sleep 2
done
```

Expected: the trace contains a tool call with `title` equal to `mcp__druids__greet` and output containing `Hello, Druids!`. This proves:
1. The program registered `greet` via `@agent.on()` after `ctx.agent()` returned
2. The deferred session creation allowed ACP to see the tool during `tools/list`
3. The agent discovered, called, and received the result from the tool

## MCP initialize advertises listChanged

The MCP initialize response should advertise `listChanged: true` in the tools capability.

```bash timeout=10
cd /home/ubuntu/code/druids/server && SLUG=$(curl -s http://localhost:8002/api/executions | python3 -c "import sys,json; execs=json.load(sys.stdin).get('executions',[]); print(execs[0]['execution_slug'] if execs else '')" 2>/dev/null)
if [ -z "$SLUG" ]; then echo "No running execution, skipping"; exit 0; fi
RESP=$(curl -s -X POST "http://localhost:8002/api/executions/$SLUG/agents/builder/mcp" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}')
echo "$RESP" | python3 -c "
import sys, json
r = json.load(sys.stdin)
cap = r['result']['capabilities']['tools']
assert cap.get('listChanged') is True, f'Expected listChanged=true, got {cap}'
print('MCP initialize: listChanged=true confirmed')
"
```

Expected: prints "MCP initialize: listChanged=true confirmed".

## Unit tests pass

```bash timeout=60
cd /home/ubuntu/code/druids/server && uv run python -m pytest tests/ -x -q
```

Expected: all tests pass with zero failures.

## Cleanup

```bash
pkill -f "uvicorn druids_server" 2>/dev/null
docker kill $(docker ps -q) 2>/dev/null
```
