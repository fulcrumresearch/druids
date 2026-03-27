# Configuration

## Server environment variables

The server reads configuration from environment variables, with an optional
`.env` file in the `server/` directory. All variables use the `DRUIDS_` prefix
except where noted.

### Required

| Variable | Description |
|---|---|
| `ANTHROPIC_API_KEY` | Anthropic API key for Claude agents. |

### Optional

| Variable | Default | Description |
|---|---|---|
| `DRUIDS_HOST` | `0.0.0.0` | Server bind address. |
| `DRUIDS_PORT` | `8000` | Server bind port. |
| `DRUIDS_BASE_URL` | `http://localhost:8000` | Public base URL of the server. |
| `DRUIDS_DATABASE_URL` | `sqlite+aiosqlite:///druids.db` | Database connection string. |
| `DRUIDS_DOCKER_IMAGE` | `ghcr.io/fulcrumresearch/druids-base:latest` | Docker image for agent sandboxes. |
| `DRUIDS_DOCKER_CONTAINER_ID` | | Attach to an existing container instead of creating new ones. |
| `DRUIDS_DOCKER_HOST` | `localhost` | Hostname for SSH/HTTP access to Docker containers. |
| `GITHUB_PAT` | | GitHub personal access token. Needed for agents to clone repos and push branches. |
| `OPENAI_API_KEY` | | OpenAI API key for Codex agents. |

### .env.example

```
ANTHROPIC_API_KEY=sk-ant-xxx

# Optional: for git operations (clone, push, PRs)
# GITHUB_PAT=ghp_xxx

# Optional: for Codex agents
# OPENAI_API_KEY=sk-proj-xxx
```

## Client configuration

The CLI stores configuration at `~/.druids/config.json`.

### config.json fields

| Field | Type | Default | Description |
|---|---|---|---|
| `base_url` | `string` | `http://localhost:8000` | Server URL. |
| `user_access_token` | `string \| null` | `null` | Not needed for local mode. |

Example `~/.druids/config.json`:

```json
{
  "base_url": "http://localhost:8000"
}
```
