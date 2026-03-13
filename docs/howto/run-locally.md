# Run locally

Run the full Druids stack on your machine using Docker for sandboxes.

## Prerequisites

1. Install `uv`:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

2. Install Docker and make sure the daemon is running.

3. Install PostgreSQL and create the database:

On macOS:

```bash
brew install postgresql@14
brew services start postgresql@14
createdb druids
```

On Linux:

```bash
sudo apt install postgresql
sudo -u postgres createdb druids
```

4. Install the GitHub CLI (`gh`):

On macOS:

```bash
brew install gh
```

On Linux, follow the [official apt instructions](https://github.com/cli/cli/blob/trunk/docs/install_linux.md).

## Install dependencies

```bash
cd server && uv sync && cd ..
cd client && uv sync && cd ..
cd bridge && uv sync && cd ..
```

On macOS with Apple Silicon, `greenlet` is not installed automatically due to platform markers but is required by SQLAlchemy's async engine. Install it manually:

```bash
cd server && uv pip install greenlet && cd ..
```

Build the client wheel (the server installs this on agent VMs):

```bash
cd client && uv build && cd ..
```

## Build the Docker image

```bash
docker build -f docker/Dockerfile -t druids-base .
```

This builds the base image that agent containers start from. Override the image name with `DRUIDS_DOCKER_IMAGE` in the server config.

## Configure the server

Create `server/.env`:

```
DRUIDS_BASE_URL=http://host.docker.internal:8000
DRUIDS_SECRET_KEY=<fernet-key>
DRUIDS_SANDBOX_TYPE=docker
ANTHROPIC_API_KEY=sk-ant-xxx
FORWARDING_TOKEN_SECRET=dev-secret
GITHUB_PAT=ghp_xxx
```

Generate `DRUIDS_SECRET_KEY`:

```bash
cd server && uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

`DRUIDS_BASE_URL` must be reachable from inside Docker containers. Do not use `localhost` -- containers cannot reach the host that way. On macOS, use `host.docker.internal`. On Linux, use `172.17.0.1` (the default Docker bridge gateway).

`GITHUB_PAT` is how agents get git access in local mode (there is no GitHub App). Create a [personal access token](https://github.com/settings/tokens) with `repo` scope. Agents use it to clone repos, push branches, and create PRs. Optional: `OPENAI_API_KEY` for Codex agents.

## Start the server

```bash
cd server && uv run druids-server
```

On macOS with Docker Desktop, the Docker socket is at `~/.docker/run/docker.sock` instead of the default `/var/run/docker.sock`. If the server fails to connect to Docker, set `DOCKER_HOST` before starting:

```bash
export DOCKER_HOST=unix://$HOME/.docker/run/docker.sock
cd server && uv run druids-server
```

Tables are created automatically on first boot.

## Configure the client

Point the CLI at your local server:

```bash
mkdir -p ~/.druids
echo '{"base_url": "http://localhost:8000"}' > ~/.druids/config.json
```

No API key is needed. Without a GitHub App configured, the server runs in local mode and skips authentication.

## Set up a devbox

A devbox is a snapshotted container with your repo cloned. Executions fork from it so agents start with a working environment.

```bash
druids setup start --repo owner/repo
```

This provisions a container and clones the repo. You can SSH in to install extra dependencies if needed. When ready, snapshot it:

```bash
druids setup finish --name owner/repo
```

## Verify

Test that containers can reach the server:

```bash
docker run --rm curlimages/curl curl -s http://host.docker.internal:8000/
```

On Linux, replace `host.docker.internal` with `172.17.0.1`.

Run the server tests:

```bash
cd server && uv run pytest
```

Start a test execution:

```bash
druids exec .druids/basher.py task_name="test" task_spec="Hello world"
```

## Troubleshooting

**Agents cannot reach the server.** Check `DRUIDS_BASE_URL`. It must be `http://host.docker.internal:8000` on macOS or `http://172.17.0.1:8000` on Linux, not `localhost`.

**Server crashes with `No module named 'greenlet'`.** On macOS Apple Silicon, run `cd server && uv pip install greenlet`. The SQLAlchemy dependency marker does not cover `arm64`.

**Server cannot connect to Docker.** On macOS with Docker Desktop, set `DOCKER_HOST=unix://$HOME/.docker/run/docker.sock` before starting the server.

**Bridge fails to start.** Check the log inside the container: `cat /tmp/bridge-7462.log`. Common causes: port conflicts, missing dependencies.

**GitHub token errors.** Verify the PAT has access to the target repo. Server logs show the specific API error.

**Database connection refused.** Verify PostgreSQL is running and the `druids` database exists. Default connection string: `postgresql+asyncpg://postgres@localhost/druids`.
