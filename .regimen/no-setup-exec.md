# No-setup execution with file provisioning

Covers the devbox-less execution path and file provisioning (`files` field). When no `devbox_name` or `repo_full_name` is provided, the server falls back to `settings.docker_image` (the base image). The `files` dict is written to each agent's sandbox before the program runs. Relates to `server/druids_server/api/routes/executions.py`, `server/druids_server/lib/execution.py`, `server/druids_server/lib/sandbox/base.py`, and `client/druids/client.py`.

## Setup

Start the server with the Docker sandbox backend.

```bash
cd /home/ubuntu/code/druids/server && env $(cat .env.public | xargs) uv run uvicorn druids_server.app:app --host 0.0.0.0 --port 8002 > /tmp/druids-nosetup.log 2>&1 &
```

Wait for the server to be ready.

```bash timeout=30
curl -s --retry 5 --retry-connrefused http://localhost:8002/health | head -c 20
```

Expected: returns HTML (the frontend). Server is listening on port 8002.

## No-setup execution runs on base image

Create an execution with no `devbox_name` or `repo_full_name`. The server should provision a container from the default base image and run the agent successfully.

```bash timeout=60
python3 -c "
import json, urllib.request

program = 'async def program(ctx, spec=\"\", **kwargs):\n    agent = await ctx.agent(\"worker\", prompt=\"Say hello world, then stop.\")\n    await ctx.wait()\n'

body = json.dumps({'program_source': program}).encode()
req = urllib.request.Request('http://localhost:8002/api/executions', data=body, headers={'Content-Type': 'application/json'})
resp = urllib.request.urlopen(req)
print(resp.read().decode())
"
```

Expected: HTTP 200 with JSON containing `execution_slug` and `execution_id`. No error about missing devbox.

## Agent provisions and responds

Poll the execution until the agent connects and makes at least one Anthropic API call. Then check the stream for a response.

```bash timeout=90
SLUG=$(python3 -c "
import json, urllib.request

program = 'async def program(ctx, spec=\"\", **kwargs):\n    agent = await ctx.agent(\"worker\", prompt=\"Say the word ping and nothing else.\")\n    await ctx.wait()\n'

body = json.dumps({'program_source': program}).encode()
req = urllib.request.Request('http://localhost:8002/api/executions', data=body, headers={'Content-Type': 'application/json'})
resp = urllib.request.urlopen(req)
data = json.loads(resp.read())
print(data['execution_slug'])
")

echo "Execution: $SLUG"

# Poll until agent appears
for i in $(seq 1 30); do
  STATUS=$(curl -s "http://localhost:8002/api/executions/$SLUG" | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d.get('agents',[])), d['status'])")
  AGENTS=$(echo "$STATUS" | cut -d' ' -f1)
  STATE=$(echo "$STATUS" | cut -d' ' -f2)
  if [ "$AGENTS" -gt 0 ]; then
    echo "Agent connected after $i checks"
    break
  fi
  if [ "$STATE" = "failed" ]; then
    echo "FAILED: $(curl -s "http://localhost:8002/api/executions/$SLUG" | python3 -c "import sys,json; print(json.load(sys.stdin).get('error',''))")"
    exit 1
  fi
done

# Wait for a response in the stream
for i in $(seq 1 15); do
  STREAM=$(curl -s -m 5 "http://localhost:8002/api/executions/$SLUG/stream" 2>/dev/null || true)
  if echo "$STREAM" | grep -q "response_chunk"; then
    echo "Agent responded"
    echo "$STREAM" | grep "response_chunk" | head -1
    exit 0
  fi
done

echo "FAILED: no response_chunk in stream"
exit 1
```

Expected: prints "Agent connected" and "Agent responded" with a response_chunk event containing text from the agent. The agent runs on the base Docker image without any devbox.

## File provisioning writes files to sandbox

Create an execution with a `files` dict. The agent should be able to read the provisioned files.

```bash timeout=90
SLUG=$(python3 -c "
import json, urllib.request

program = 'async def program(ctx, spec=\"\", **kwargs):\n    agent = await ctx.agent(\"worker\", prompt=\"Read /home/agent/hello.txt and /home/agent/data.json. Report their exact contents. Then stop.\")\n    await ctx.wait()\n'

body = json.dumps({
    'program_source': program,
    'files': {
        '/home/agent/hello.txt': 'Hello from file provisioning!',
        '/home/agent/data.json': json.dumps({'key': 'test-value', 'num': 42})
    }
}).encode()

req = urllib.request.Request('http://localhost:8002/api/executions', data=body, headers={'Content-Type': 'application/json'})
resp = urllib.request.urlopen(req)
data = json.loads(resp.read())
print(data['execution_slug'])
")

echo "Execution: $SLUG"

# Wait for agent response that mentions file contents
for i in $(seq 1 30); do
  STREAM=$(curl -s -m 5 "http://localhost:8002/api/executions/$SLUG/stream" 2>/dev/null || true)
  if echo "$STREAM" | grep -q "response_chunk"; then
    echo "Agent responded"
    echo "$STREAM" | grep "response_chunk" | tail -1
    # Check that the response mentions the file contents
    if echo "$STREAM" | grep "response_chunk" | grep -q "Hello from file provisioning"; then
      echo "PASS: agent read hello.txt"
    fi
    if echo "$STREAM" | grep "response_chunk" | grep -q "test-value"; then
      echo "PASS: agent read data.json"
    fi
    exit 0
  fi

  STATUS=$(curl -s "http://localhost:8002/api/executions/$SLUG" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['status'])")
  if [ "$STATUS" = "failed" ]; then
    echo "FAILED: $(curl -s "http://localhost:8002/api/executions/$SLUG" | python3 -c "import sys,json; print(json.load(sys.stdin).get('error',''))")"
    exit 1
  fi
done

echo "FAILED: timed out waiting for response"
exit 1
```

Expected: the agent reads both files and the stream contains response_chunk events mentioning "Hello from file provisioning" and "test-value". The server log should also show "wrote 2 file(s)".

## Nonexistent devbox returns 404

Requesting a devbox that does not exist should still return HTTP 404 with a helpful message, even though no-setup execution is allowed.

```bash timeout=10
HTTP_CODE=$(curl -s -o /tmp/nosetup-404.txt -w "%{http_code}" -X POST http://localhost:8002/api/executions \
  -H "Content-Type: application/json" \
  -d '{"program_source": "async def program(ctx): pass", "devbox_name": "nonexistent-devbox-xyz"}')

echo "HTTP $HTTP_CODE"
cat /tmp/nosetup-404.txt
```

Expected: HTTP 404 with a JSON body containing "No devbox for 'nonexistent-devbox-xyz'".

## Cleanup

```bash
kill %1 2>/dev/null; lsof -ti:8002 | xargs kill -9 2>/dev/null; docker rm -f $(docker ps -aq) 2>/dev/null; echo "cleaned up"
```
