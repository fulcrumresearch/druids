# Agent Guidance

This file provides guidance for coding agents (e.g., Claude Code, Codex, Gemini CLI) working on Druids, a multi-agent orchestration system.


## Commands

All commands are relative to the repo root. Server configuration lives in `server/.env` (see `server/druids_server/config.py` for all settings).

### Developing

```bash
cd server && uv run druids-server          # start the server (port from .env, default 8000)
cd server && uv run pytest                 # run server tests
cd server && uv run pytest --slow          # include slow tests
cd server && uv run pytest tests/api/      # run a specific test directory
cd client && uv run pytest                 # run client tests
ruff check --fix                           # lint and autofix
ruff format                                # format code
pre-commit run --all-files                 # run all pre-commit hooks (ruff check + format)
```

Before starting the server for the first time (or after model changes), run `cd server && uv run alembic upgrade head` to apply database migrations. On startup the server builds the client wheel (`client/dist/*.whl`). Server logs append to `server/logs/druids.log`.

### Database Migrations

Alembic is the sole owner of database schema. Run migrations before starting the server:

```bash
cd server && uv run alembic upgrade head                        # apply all migrations
cd server && uv run alembic revision --autogenerate -m "desc"   # create a new migration
cd server && uv run alembic downgrade -1                        # roll back one migration
cd server && uv run alembic history                             # list migration history
```

### Running Programs

Programs are Python files in `.druids/`. The server must be running.

```bash
druids exec build spec="implement feature X"               # run a program
druids exec build -b my-branch spec="..."                  # run on a specific git branch
druids execution ls                                        # list running executions
druids execution ls --all                                  # include stopped executions
druids execution status SLUG                               # check execution status
druids execution activity SLUG                             # show recent agent activity
druids execution stop SLUG                                 # stop an execution
druids execution send SLUG "try a different approach"      # send a message to the builder agent
druids execution send SLUG "check the logs" -a monitor     # send to a specific agent
druids execution ssh SLUG                                  # open a shell on execution VM
druids execution ssh SLUG -a agent-name                    # shell into a specific agent's VM
druids execution connect SLUG                              # resume the agent's coding session
druids execution connect SLUG -a agent-name                # resume a specific agent's session
```

The CLI reads `~/.druids/config.json` for `base_url` and auth token.

### Execution Traces

Traces are JSONL at `~/.druids/executions/{user_id}/{slug}.jsonl`. Each line is a JSON object with `ts`, `type`, `agent`, and event-specific fields.

```bash
cat ~/.druids/executions/*/SLUG.jsonl                                       # read full trace
tail -20 ~/.druids/executions/*/SLUG.jsonl                                  # last 20 events
cat ~/.druids/executions/*/SLUG.jsonl | jq -r 'select(.type == "error")'    # filter errors
```

Event types: `execution_started`, `connected`, `disconnected`, `prompt`, `response_chunk`, `tool_use`, `tool_result`, `execution_stopped`, `error`, `topology`, `client_event`.

### Frontend

The dashboard is served at the server's root URL when `frontend/dist/` exists.

```bash
cd frontend && npm install                     # install dependencies
cd frontend && npm run dev -- --host 0.0.0.0   # start Vite dev server (bind all interfaces)
cd frontend && npm run build                   # production build (creates dist/)
```

### Devbox Management

```bash
druids devbox create --repo owner/repo                # provision a devbox sandbox
druids devbox snapshot --name owner/repo              # snapshot and stop the devbox
druids devbox ls                                      # list all devboxes
druids devbox secret set -d devbox-name KEY VALUE     # set a secret on a devbox
druids devbox secret ls -d devbox-name                # list secrets
```

### Docker Sandbox Backend

When `DRUIDS_SANDBOX_TYPE=docker` in `server/.env`, agent sandboxes run as local Docker containers instead of MorphCloud VMs:

```bash
docker build -f docker/Dockerfile -t druids-base .    # build agent base image (required once)
```


## Workflow

### Git

Do not commit directly to `main`. Instead, create a new branch, push it, and open a PR. Use `gh pr create` for PRs. Branch naming: descriptive kebab-case (e.g. `verify-bot`, `improve-review-prompts`).

### Verification

This is non-negotiable. When opening a PR, you must demonstrate that your change actually works end-to-end. Not "it should work" — you ran it, you saw it work, and you can show your receipts.

What this means in practice:

1. **Run the system.** Start the server, use the CLI, open the frontend — whatever is appropriate for the change. Do not just run unit tests and call it done.
2. **Exercise the change as a user would.** If you added an API endpoint, call it. If you fixed a bug, reproduce the scenario and confirm it's fixed. If you changed the frontend, load the page and verify it renders correctly.
3. **Document what you did.** Record the commands you ran and their output, screenshots if relevant, or any other evidence that the thing works.
4. **Put it in the PR.** The PR description must include a verification section showing the steps you took and the results. A PR without this will be sent back.

### Commit Messages

One-liner following Conventional Commits. Use `(client)`, `(server)`, or `(bridge)` scope when only one component is affected. Use backticks for code references. Break large changes into smaller, atomic commits.

```
feat(server): add new MCP tool for file operations
fix(client): handle missing config file gracefully
refactor(bridge): simplify SSE event framing
feat(server): add `/api/tasks` route for task management
fix: correct typo in `spawn_agent` function
```

### Prompt History

When you change agent prompts (in program files under `server/druids_server/lib/` or `.druids/`), create a new entry in `prompt_history/{agent-name}/`. Filename: `{YYYY-MM-DD}-{revision-desc}-{commit-hash}.md`. Each entry records what changed and why, and accumulates observations as the version runs in production. See `prompt_history/README.md` for the full structure.

### Sleeping

Do not use `sleep` in Bash commands. You are an agent, not a cron job. When you need to wait for something, just try the thing and see if it worked. If it did not, try again on your next tool call. The time between your tool calls is already measured in seconds, which is more than enough delay for any server or process. You will not overwhelm anything by retrying without sleeping — you are slow compared to clock speeds.

If a command blocks until completion (like `gh run watch` or a build command), just run it. If a command checks status and returns immediately, call it, read the result, and decide what to do next. Do not insert `sleep` between checks. Do not write `while true; do sleep 30; check; done` loops. Just call the tool, look at the output, and act.

### Testing

All new code must have tests for API endpoints and domain logic. When modifying an untested module, add tests for the code you touch. The bridge has no standalone tests; verify changes by running an execution.


## Code Conventions

These apply to all Python code in the repo. Component-specific conventions are in [server/README.md](server/README.md) and [client/README.md](client/README.md).

### Version

Python 3.11+. Use `from __future__ import annotations` in all files. Modern syntax: `X | None` not `Optional[X]`, `list[str]` not `List[str]`. Do not import `Optional`, `Union`, `List`, `Dict`, or `Tuple` from `typing`.

### Naming

Variables and parameters use `snake_case`. Standard abbreviations: `inst` (MorphCloud instance), `conn` (agent connection), `msg` (message), `proc` (subprocess), `req` (request), `resp` (response), `db` (database session), `ex` (execution). Spell out all other names. No single-letter variables except loop indices or lambdas.

Functions use verb-based names:

- `get_*` retrieves existing, returns object or `None`
- `create_*` creates and persists (I/O)
- `make_*` constructs in memory without I/O
- `ensure_*` creates if absent, returns existing if present
- `start_*` / `stop_*` manage lifecycle
- `send_*` transmits over connection
- `is_*` / `has_*` for boolean checks

Async functions do not use an `a` prefix.

Classes use `PascalCase`. No `Manager`, `Handler`, or `Service` suffixes. Exception classes end with `Error`. Request/response models use verb-noun: `CreateTaskRequest`, `StartSetupResponse`.

### Type Annotations

`Literal` types for constrained strings (not `Enum`). `TYPE_CHECKING` guards to break circular imports.

### Defaults

Never use fake defaults. If a value can logically be absent, type it as `X | None` and default to `None`. Do not use surrogate empty values like `""`, `0`, `[]`, or `{}` to mean "not set". The type system should reflect the actual domain: a missing name is `None`, not an empty string.

### Docstrings

All public functions must have a docstring. Simple functions: one-line. Complex functions: Google-style with Args/Returns.

### Functions

Split when a function has distinct phases or when readability suffers. If you add section comments inside a function, extract instead. Keep parameter count to 1-4. Prefer early returns and guard clauses.

### Error Handling

Do not catch exceptions just to log and re-raise. `try`/`except` only when an error is an unmovable part of some API; do not use them superfluously. Broad `except Exception` only at true API boundaries and must include a comment. Do not use bare `assert` in production code. Never expose internal error details in HTTP responses; log server-side and return a generic message.
