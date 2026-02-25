# Orpheus Server

FastAPI application that orchestrates agents on MorphCloud VMs. Agents communicate through MCP tools served at `/mcp/` (all tools) and `/mcp/exec` (execution-scoped tools only).

## Running

```
cd server
uv sync
uv run orpheus-server
```

The server starts on port 8000. It requires a `.env` file in this directory (see [SETUP.md](../SETUP.md) for the full walkthrough).

## API structure

Routes are organized into modules under `orpheus/api/routes/`:

- `auth` - GitHub OAuth login and token exchange
- `setup` - Devbox provisioning endpoints
- `programs` - List available agent programs
- `tasks` - Create, list, stop, and inspect tasks
- `executions` - Execution diffs and activity logs
- `mcp` - MCP tools for agent-to-agent communication
- `webhooks` - GitHub webhook receiver for PR feedback

## MCP tools

Agents call these tools through the MCP endpoint. Each requires an `execution_slug` parameter.

- `send_message` - send a prompt to another agent
- `get_file` - read a file from an agent's VM
- `save_file` - write a file to an agent's VM
- `spawn` - create a new agent from a constructor
- `get_programs` - list running programs and their constructors
- `stop_agent` - disconnect and remove an agent
- `get_agent_ssh` - get SSH credentials for an agent's VM
- `expose_port` - expose a port on an agent's VM as a public HTTPS URL
- `submit` - mark execution as complete after creating a PR

The `/mcp` mount includes driver-level tools (task management, program listing, execution diffs) in addition to the above. The `/mcp/exec` mount exposes only the execution-scoped tools listed above.

## Core types

The domain model lives in `orpheus/core/`.

`Program` is the base unit. It has a `name` and a `constructors` dict mapping names to factory functions that produce new programs.

`Agent` extends `Program` with an `ACPConfig` (command, env, working directory) and optional `user_prompt`. Use the `ClaudeAgent` or `CodexAgent` subclasses (in `orpheus/core/agents/`) rather than constructing `Agent` + `ACPConfig` manually. Each subclass creates its config and writes backend-specific files to the VM during `exec()`.

`Execution` is the runtime container. It holds all programs and their connections, keyed by name. Fields include `id: UUID`, `slug: str`, `user_id: str`, and references to the task and repo. When an agent calls `spawn`, the execution looks up the constructor, creates the new agent, and connects to it.

`AgentConnection` wraps the HTTP/SSE link to an agent's bridge process. It implements the ACP client protocol: session creation, prompt delivery, and auto-approval of tool permission requests.

## Traces

Execution traces are logged to `~/.orpheus/executions/{user_id}/{slug}.jsonl`. Each line is a JSON object with a timestamp, event type, agent name, and event-specific fields. The Streamlit viewer (`orpheus/viewer.py`) displays these logs.

## Tests

```
uv run pytest
```

Integration tests in `tests/integration/` require a running server and MorphCloud VMs. They are skipped by default.
