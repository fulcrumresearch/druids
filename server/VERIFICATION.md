# Orpheus Verification Process

This document describes how to verify the Orpheus multi-agent system is working correctly.

## Prerequisites

1. Server running: `cd /root/orpheus/server && orpheus-server`
2. CLI configured: `cd /root/orpheus/cli && orpheus auth login`
3. Devbox snapshot exists for the target repo

## Test 1: Basic Task Creation

```bash
# Create a simple spec file
echo "Add a simple hello command that prints 'Hello from Orpheus!'" > /tmp/test-spec.txt

# Run the task
cd /root/orpheus/cli
uv run orpheus exec /tmp/test-spec.txt --repo fulcrumresearch/orpheus
```

Expected output:
```
Task de4a39de-d7bd-480e-963e-4473352dcfef started with 2 executions:
  → orchestrator: 4977c007-7858-4be8-9403-e40c176b33d8
  → swe: 0e03c1bc-1151-42f2-827e-0eacca747cd8
```

## Test 2: List Tasks and Executions

```bash
uv run orpheus tasks
```

Expected: Shows tasks with their executions and instance IDs.

## Test 3: Check Execution Traces

Traces are stored at `~/.orpheus/executions/{execution_id}.jsonl`.

```bash
# List trace files
ls -la ~/.orpheus/executions/

# View recent activity for an orchestrator execution
tail -50 ~/.orpheus/executions/<task-prefix>-orchestrator.jsonl

# Check for tool usage (spawn, send_message, etc.)
grep "tool_use" ~/.orpheus/executions/<task-prefix>-orchestrator.jsonl | head -20
```

Key events to verify:
- `execution_started`: Task initialization
- `program_added`: Agents being added to execution
- `connected`: Agent connected via ACP
- `tool_use`: MCP tools being called (spawn, send_message, etc.)

## Test 4: Verify Multi-Agent Communication

In the orchestrator trace, look for:

1. **Spawn**: Orchestrator spawning executor
```json
{"type": "tool_use", "tool": "mcp__orpheus-mcp__spawn", "params": {"constructor_name": "executor", "kwargs": {"name": "executor-name"}}}
```

2. **Program Added**: New agent registered
```json
{"type": "program_added", "name": "executor-name", "instance_id": "morphvm_xxx"}
```

3. **Message Exchange**: Communication between agents
```json
{"type": "tool_use", "tool": "mcp__orpheus-mcp__send_message", "params": {"sender": "orchestrator", "receiver": "executor-name", "message": "..."}}
```

## Test 5: Verify Code Changes

Check if the executor made changes on its VM:

```bash
cd /root/orpheus/server
uv run python -c "
from orpheus.lib.morph import get_instance
inst = get_instance('morphvm_<executor_instance_id>')
result = inst.exec(\"sudo -u agent bash -c 'cd /home/agent/orpheus && git status && git diff'\")
print(result.stdout)
"
```

## Test 6: Apply Changes (Manual)

Current limitation: The `orpheus apply` command looks at `root_instance_id` (orchestrator's VM), but in multi-agent scenarios, changes are on executor's branched VM.

To manually apply changes from an executor:

```bash
# Get the diff from executor's VM
uv run python -c "
from orpheus.lib.morph import get_instance
inst = get_instance('morphvm_<executor_instance_id>')
result = inst.exec(\"sudo -u agent bash -c 'cd /home/agent/orpheus && git add . && git diff --cached HEAD'\")
print(result.stdout)
" > /tmp/changes.patch

# Apply locally
cd /root/orpheus
git apply /tmp/changes.patch
```

## Test 7: Streamlit Trace Viewer

```bash
cd /root/orpheus/server
streamlit run orpheus/viewer.py
```

The viewer shows:
- Task → Execution → Agent Session hierarchy in sidebar
- Event timeline for selected session
- Tool usage details

## Common Issues

### POST /tasks timeout
If task creation times out, check that init prompts are fire-and-forget (not awaited).

### No changes to apply
Changes may be on executor's branched VM, not the orchestrator's root instance. Find the executor's instance_id in the trace and check that VM directly.

### MCP tools not available
Verify `ORPHEUS_BASE_URL` is set in server/.env to the public URL where the server is accessible.

## Successful Test Evidence

A successful test shows:
1. Task created with multiple executions
2. Orchestrator reads codebase, plans work
3. Orchestrator spawns executor via `spawn` MCP tool
4. Executor gets branched VM (separate morphvm_xxx)
5. Executor makes code changes (visible in git diff on its VM)
6. Executor reports completion via `send_message` MCP tool
7. Changes can be applied locally

Example from verified test (Feb 4, 2026):
- Task: de4a39de-d7bd-480e-963e-4473352dcfef
- Spec: "Add a simple hello command"
- Orchestrator spawned: hello-command-implementer
- Executor instance: morphvm_g96auxsm
- Change: Added `@app.command() def hello()` to cli/orpheus/main.py
- Result: `orpheus hello` prints "Hello from Orpheus!"
