# Quickstart

## Using druids.dev (hosted)

Install the CLI:

```bash
uv tool install druids
```

The CLI connects to druids.dev by default. Authenticate and create a devbox:

```bash
druids auth set-key <your-api-key>
druids setup start --repo owner/repo
```

This provisions a sandbox, clones the repo, and drops you into an SSH session. Install your project's dependencies, then finish:

```bash
druids setup finish --name owner/repo
```

Run a program:

```bash
druids exec .druids/basher.py --devbox owner/repo \
  task_name="test" task_spec="Write a hello world script"
```

Monitor the execution:

```bash
druids ls
druids status <slug>
```

---

## Self-hosted

Run the server locally with Docker for agent sandboxes.

### Prerequisites

- [Docker](https://docs.docker.com/get-docker/)
- [uv](https://docs.astral.sh/uv/getting-started/installation/): `curl -LsSf https://astral.sh/uv/install.sh | sh`
- An [Anthropic API key](https://console.anthropic.com/)
- A [GitHub PAT](https://github.com/settings/tokens) with repo scope (optional, for git operations)

### Install from PyPI

```bash
uv tool install --python 3.12 "druids[server]"
```

Start the server:

```bash
ANTHROPIC_API_KEY=sk-ant-xxx druids server
```

Point the CLI at your local server:

```bash
mkdir -p ~/.druids
echo '{"base_url": "http://localhost:8000"}' > ~/.druids/config.json
```

Then create a devbox and run programs as above.

### Install from source

If you prefer to run from a git checkout:

```bash
bash scripts/setup.sh
```

This pulls the agent base image, installs dependencies, configures the server and CLI, and starts the server. It will prompt for your Anthropic API key and optionally a GitHub PAT. Or pass them as environment variables:

```bash
ANTHROPIC_API_KEY=sk-ant-xxx GITHUB_PAT=ghp_xxx bash scripts/setup.sh
```

### Environment variables

The server reads these from the environment or a `server/.env` file:

- `ANTHROPIC_API_KEY` (required) -- your Anthropic API key
- `DRUIDS_BASE_URL` -- how agents reach the server from inside Docker. Use `http://host.docker.internal:8000` (macOS) or `http://172.17.0.1:8000` (Linux). Auto-detected by the setup script.
- `GITHUB_PAT` (optional) -- GitHub personal access token with repo scope, for git operations in agent sandboxes

---

## Troubleshooting

Agents cannot reach the server: `DRUIDS_BASE_URL` must be reachable from inside Docker containers. Use `172.17.0.1` (Linux) or `host.docker.internal` (macOS), not `localhost`. Test with: `docker run --rm curlimages/curl curl -s http://172.17.0.1:8000/`

Bridge fails to start: check the log inside the sandbox (`cat /tmp/bridge-*.log`). Common causes: port conflicts, missing dependencies.

GitHub token errors: verify the PAT has repo scope. Server logs show the specific API error.

Client cannot connect: check `~/.druids/config.json` has the correct `base_url`.
