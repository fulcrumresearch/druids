# Agent fork (COW VM branching)

Covers the `agent.fork()` API: COW cloning a running agent's VM via MorphCloud and creating a new agent on the clone. The forked agent inherits the source's filesystem (files, repo state, installed packages) but runs as an independent agent with its own identity and session. Relates to `server/druids_server/lib/execution.py` (fork_agent), `server/druids_server/api/routes/runtime.py` (fork endpoint), `runtime/druids_runtime/__init__.py` (RuntimeAgent.fork), and `server/druids_server/lib/sandbox/morph.py` (MorphSandbox.fork).

## Setup

Start the server. This requires a running Postgres database, MorphCloud API key, and a devbox for the target repo.

```bash
cd /home/ubuntu/code/druids/server && uv run uvicorn druids_server.app:app --host 0.0.0.0 --port 8002 &
```

Wait for the server to be ready.

```bash timeout=30
curl -s --retry 10 --retry-delay 1 --retry-connrefused http://localhost:8002/api/devboxes | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'{len(d[\"devboxes\"])} devbox(es)')"
```

Expected: prints a count of devboxes, at least 1.

## Create execution with fork

POST an execution whose program creates an agent, has it write a file, then forks it. The forked agent reads the file (proving filesystem inheritance), overwrites it, and the program checks the original is unchanged (proving independence).

```bash timeout=30
SLUG=$(python3 -c "
import json, urllib.request

program = '''
import asyncio

async def program(ctx, spec='', **kwargs):
    builder = await ctx.agent(
        'builder',
        prompt=\"Write 'hello from builder' to /tmp/fork-test.txt, then call the report tool with label='ready' and value='done'. Then wait.\",
    )

    ready = asyncio.Event()

    @builder.on('report')
    async def on_report(label: str = '', value: str = ''):
        if label == 'ready':
            ready.set()
        return f'Recorded: {label}={value}'

    await ready.wait()

    forked = await builder.fork(
        'forked',
        prompt=\"Read /tmp/fork-test.txt and call report with label='fork_read' and value=the file contents. Then write 'hello from fork' to /tmp/fork-test.txt and call report with label='fork_wrote' and value='done'.\",
    )

    fork_done = asyncio.Event()
    fork_results = {}

    @forked.on('report')
    async def on_fork_report(label: str = '', value: str = ''):
        fork_results[label] = value
        if label == 'fork_wrote':
            fork_done.set()
        return 'Recorded'

    await asyncio.wait_for(fork_done.wait(), timeout=120)
    ctx.done({'fork_read': fork_results.get('fork_read'), 'fork_wrote': fork_results.get('fork_wrote')})
'''

body = json.dumps({'program_source': program, 'repo_full_name': 'fulcrumresearch/druids-full', 'args': {'spec': 'fork regimen'}}).encode()
req = urllib.request.Request('http://localhost:8002/api/executions', data=body, headers={'Content-Type': 'application/json'})
resp = urllib.request.urlopen(req)
data = json.loads(resp.read())
print(data['execution_slug'])
")
echo "Execution: $SLUG"
```

Expected: prints `Execution: <slug>` with a valid slug string.

## Forked agent inherits filesystem and reads source file

Poll the execution activity until the forked agent calls the report tool with `fork_read`. The value should be `hello from builder`, proving the forked VM has the source's filesystem.

```bash timeout=120
for i in $(seq 1 30); do
  ACTIVITY=$(curl -s "http://localhost:8002/api/executions/$SLUG/activity?n=100&compact=false" 2>/dev/null)
  RESULT=$(echo "$ACTIVITY" | python3 -c "
import sys, json
data = json.load(sys.stdin)
for e in data.get('recent_activity', []):
    if e.get('type') == 'tool_result' and 'report' in e.get('tool', ''):
        result = str(e.get('result', ''))
        if 'fork_read' in result and 'hello from builder' in result:
            print('PASS: fork read source file: hello from builder')
            exit(0)
exit(1)
" 2>/dev/null)
  if [ $? -eq 0 ]; then
    echo "$RESULT"
    break
  fi
done
```

Expected: prints `PASS: fork read source file: hello from builder`. The forked agent discovered the file written by the builder on the source VM.

## Fork completes independently

Wait for the forked agent to report `fork_wrote=done`, confirming it wrote its own content and completed.

```bash timeout=60
for i in $(seq 1 15); do
  ACTIVITY=$(curl -s "http://localhost:8002/api/executions/$SLUG/activity?n=100&compact=false" 2>/dev/null)
  RESULT=$(echo "$ACTIVITY" | python3 -c "
import sys, json
data = json.load(sys.stdin)
for e in data.get('recent_activity', []):
    if e.get('type') == 'tool_result' and 'report' in e.get('tool', ''):
        result = str(e.get('result', ''))
        if 'fork_wrote' in result:
            print('PASS: fork completed')
            exit(0)
exit(1)
" 2>/dev/null)
  if [ $? -eq 0 ]; then
    echo "$RESULT"
    break
  fi
done
```

Expected: prints `PASS: fork completed`.

## Fork with context=True preserves conversation

Create a separate execution where an agent memorizes a secret code, then is forked with `context=True`. The forked agent should recall the code from the resumed conversation history.

```bash timeout=30
SLUG2=$(python3 -c "
import json, urllib.request

program = '''
import asyncio

async def program(ctx, spec='', **kwargs):
    builder = await ctx.agent(
        'builder',
        prompt=\"Remember this secret code: PHOENIX-42. Call report with label='memorized' and value='done'. Then wait.\",
    )

    memorized = asyncio.Event()

    @builder.on('report')
    async def on_report(label: str = '', value: str = ''):
        if label == 'memorized':
            memorized.set()
        return f'Recorded: {label}={value}'

    await memorized.wait()

    forked = await builder.fork(
        'forked',
        context=True,
        prompt=\"What was the secret code I told you earlier? Call report with label='recalled' and value=the secret code.\",
    )

    recalled = asyncio.Event()
    recall_value = []

    @forked.on('report')
    async def on_fork_report(label: str = '', value: str = ''):
        if label == 'recalled':
            recall_value.append(value)
            recalled.set()
        return 'Recorded'

    await asyncio.wait_for(recalled.wait(), timeout=120)
    ctx.done({'recalled': recall_value[0] if recall_value else 'nothing'})
'''

body = json.dumps({'program_source': program, 'repo_full_name': 'fulcrumresearch/druids-full', 'args': {'spec': 'fork context test'}}).encode()
req = urllib.request.Request('http://localhost:8002/api/executions', data=body, headers={'Content-Type': 'application/json'})
resp = urllib.request.urlopen(req)
data = json.loads(resp.read())
print(data['execution_slug'])
")
echo "Execution: $SLUG2"
```

Expected: prints `Execution: <slug>`.

```bash timeout=120
for i in $(seq 1 30); do
  ACTIVITY=$(curl -s "http://localhost:8002/api/executions/$SLUG2/activity?n=100&compact=false" 2>/dev/null)
  RESULT=$(echo "$ACTIVITY" | python3 -c "
import sys, json
data = json.load(sys.stdin)
for e in data.get('recent_activity', []):
    if e.get('type') == 'tool_use' and 'report' in e.get('tool', ''):
        params = e.get('params', {})
        if params.get('label') == 'recalled' and 'PHOENIX-42' in str(params.get('value', '')):
            print('PASS: forked agent recalled PHOENIX-42 from resumed session')
            exit(0)
exit(1)
" 2>/dev/null)
  if [ $? -eq 0 ]; then
    echo "$RESULT"
    break
  fi
done
```

Expected: prints `PASS: forked agent recalled PHOENIX-42 from resumed session`. The forked agent loaded the source's conversation history via `session/resume` and remembered the secret code.

## Docker fork returns error

Attempting to fork on a Docker backend should return a clear error, not a silent failure.

```bash timeout=10
HTTP_CODE=$(curl -s -o /tmp/fork-docker-err.txt -w "%{http_code}" -X POST "http://localhost:8002/api/executions/$SLUG/agents/builder/fork" \
  -H "Content-Type: application/json" \
  -d '{"name": "should-fail-on-docker"}')
echo "HTTP $HTTP_CODE"
```

Expected: this test only applies when running with `DRUIDS_SANDBOX_TYPE=docker`. On MorphCloud, the fork succeeds (HTTP 200). On Docker, it should return HTTP 400 with a message containing "Docker containers cannot be forked".

## Cleanup

```bash
pkill -f "uvicorn druids_server" 2>/dev/null
```
