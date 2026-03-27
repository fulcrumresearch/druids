# Packaging

Druids can be installed as a pip package or run from a git checkout. This document explains how the packaging works.

## Install flow

```bash
uv tool install "druids[server]"
```

This installs the `druids` CLI and the `druids-server` package together. Start the server with:

```bash
ANTHROPIC_API_KEY=sk-ant-xxx druids server
```

The server starts on port 8000, serves the dashboard frontend, and manages Docker containers for agent sandboxes.

## Package structure

There are three Python packages in the monorepo:

```
client/     -> druids          (CLI and type definitions)
server/     -> druids-server   (server, API, agent orchestration)
runtime/    -> druids-runtime  (runs inside sandbox VMs, not imported by server)
bridge/     -> not a package   (single file deployed to containers)
frontend/   -> not a package   (Vue SPA, built and bundled into server)
```

The client has an optional dependency:

```toml
# client/pyproject.toml
[project.optional-dependencies]
server = ["druids-server"]
```

So `druids[server]` pulls in the server. The `druids server` CLI command imports `druids_server.app` at runtime, meaning the base `druids` package works without the server installed.

## What gets bundled

The server needs three sets of files that live outside its own source tree:

1. `frontend/dist/` -- the built Vue app, served at `/` by the server
2. `bridge/bridge.py` -- copied into Docker containers at agent startup
3. `client/dist/druids-*.whl` -- installed in containers so agents have the CLI

When running from a git checkout (development), the server finds these via relative paths from the monorepo root.

When pip-installed, these files are bundled inside the wheel at `druids_server/_bundled/`. The `server/bundle.py` script copies them there before building:

```bash
cd server
python3 bundle.py   # copies frontend, bridge, client wheel into _bundled/
uv build             # builds the wheel with _bundled/ included
```

## Path resolution

`druids_server/paths.py` resolves each asset by checking the bundled location first, then falling back to the monorepo layout:

```python
BRIDGE_DIR = _resolve(
    _BUNDLED_DIR / "bridge",        # pip-installed
    _MONOREPO_ROOT / "bridge",      # dev checkout
)
```

This means the same server code works in both modes without configuration.

## Dependencies

The server does not import `druids` (client) or `druids-runtime` in production code. It only reads their files to deploy into containers. They are listed as dev dependencies for tests.

The server's actual dependencies are: FastAPI, uvicorn, SQLModel, aiosqlite, Docker SDK, asyncssh, anthropic, and supporting libraries. See `server/pyproject.toml` for the full list.

## Frontend

The frontend is a Vue 3 / Vite SPA in `frontend/`. To rebuild after changes:

```bash
cd frontend
npm install
npm run build
```

The server serves it from `DASHBOARD_DIST` (resolved by `paths.py`). In development, rebuild and restart the server to see changes. There is no hot-reload dev server configured to proxy to the backend (the Vite config has a proxy rule, but `npm run dev` is not part of the standard workflow).

The frontend uses no authentication. The server auto-creates a local user and `/api/me` always returns it. There is no login page, OAuth flow, or API key management.

## Building for distribution

To build both packages for distribution:

```bash
# Build the client wheel
cd client && uv build && cd ..

# Build the frontend
cd frontend && npm run build && cd ..

# Bundle assets and build the server wheel
cd server && python3 bundle.py && uv build && cd ..
```

The resulting wheels are:
- `client/dist/druids-*.whl`
- `server/dist/druids_server-*.whl`
