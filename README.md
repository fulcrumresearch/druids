# Orpheus

Orpheus is a multi-agent orchestration system. It runs agents on MorphCloud VMs and coordinates them through MCP tools. An orchestrator agent can spawn sub-agents, send them messages, and monitor their work. Agents create git branches and open pull requests which are tracked via GitHub webhooks.

Orpheus supports multiple agent backends via ACP (Agent Client Protocol): `claude-code-acp` for Claude agents and `codex-acp` for OpenAI Codex agents.

## Getting started

See [SETUP.md](SETUP.md) for the complete setup walkthrough, from a fresh machine to a running server.

## Directory structure

```
orpheus/
  server/     FastAPI server, core runtime, MCP routes, programs
  cli/        Typer CLI for auth and task management
  bridge/     HTTP/SSE bridge relay running on Morph VMs
  scripts/    Developer tooling (setup script, GitHub App setup)
```

## Commands

### Server (from `server/`)

```bash
uv run orpheus-server                  # Start the server
uv run pytest                          # Run tests
uv run pytest tests/path/test.py       # Run single test file
uv run pytest -k test_name             # Run tests matching pattern
streamlit run orpheus/viewer.py        # Run trace visualizer
```

### CLI

```bash
orpheus auth login                     # GitHub device flow auth
orpheus exec spec.txt                  # Start task (default: orchestrator_with_review)
orpheus exec spec.txt -p claude        # Start task (specific program)
orpheus exec spec.txt --all            # Start task (all programs in parallel)
orpheus tasks                          # List active tasks
orpheus status <task_slug>             # Check task status and PR URLs
orpheus stop <task_slug>               # Stop task
orpheus download <remote_path>         # Download file from VM to stdout
orpheus upload <local_path> <remote>   # Upload file to VM
orpheus setup start                    # Provision devbox VM
orpheus setup save                     # Snapshot devbox
```

### Linting (from repo root)

```bash
ruff check --fix                       # Fix linting issues
ruff format                            # Format code
```

## Documentation

- [SETUP.md](SETUP.md) -- Full setup instructions
- [ARCHITECTURE.md](ARCHITECTURE.md) -- Core types, ACP protocol, bridge internals, API endpoints, database patterns
- [PROGRAMS.md](PROGRAMS.md) -- How to author new agent programs
- [DEVELOPMENT.md](DEVELOPMENT.md) -- MorphCloud setup, GitHub PR flow, CLI development
- [STYLE.md](STYLE.md) -- Code style, naming, type annotations, async patterns
- [TESTING.md](TESTING.md) -- Test organization, fixtures, mocking patterns
