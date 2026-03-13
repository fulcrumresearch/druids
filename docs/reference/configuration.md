# Configuration

## Server environment variables

The server reads configuration from environment variables, with an optional
`.env` file in the `server/` directory. All variables use the `DRUIDS_` prefix
except where noted.

Settings use [Pydantic Settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/)
with `env_prefix="DRUIDS_"`.

### Required

| Variable | Description |
|---|---|
| `DRUIDS_BASE_URL` | Public base URL of the server (e.g. `https://druids.dev`). |
| `DRUIDS_SECRET_KEY` | Fernet encryption key for secrets stored in the database. Generate with: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `ANTHROPIC_API_KEY` | Anthropic API key for Claude agents. |
| `FORWARDING_TOKEN_SECRET` | Secret used to sign forwarding tokens for agent-to-server auth. |

### GitHub authentication

Either all four GitHub App fields or `GITHUB_PAT` must be set. Both can be
set simultaneously.

| Variable | Description |
|---|---|
| `GITHUB_PAT` | Personal access token. Used in single-developer local mode. |
| `GITHUB_CLIENT_ID` | GitHub App OAuth client ID. |
| `GITHUB_APP_ID` | GitHub App numeric ID. |
| `GITHUB_APP_PRIVATE_KEY` | GitHub App private key (PEM format). |
| `GITHUB_APP_SLUG` | GitHub App slug (URL name). |
| `GITHUB_CLIENT_SECRET` | GitHub App OAuth client secret. |

### Optional

| Variable | Default | Description |
|---|---|---|
| `DRUIDS_HOST` | `0.0.0.0` | Server bind address. |
| `DRUIDS_PORT` | `8000` | Server bind port. |
| `DRUIDS_DATABASE_URL` | `postgresql+asyncpg://postgres@localhost/druids` | PostgreSQL connection string. |
| `DRUIDS_SANDBOX_TYPE` | `morph` | Sandbox backend: `"morph"` or `"docker"`. |
| `MORPH_API_KEY` | | MorphCloud API key. Required when `DRUIDS_SANDBOX_TYPE=morph`. |
| `DRUIDS_DOCKER_IMAGE` | `druids-base` | Docker image for sandbox containers. Used when `DRUIDS_SANDBOX_TYPE=docker`. |
| `DRUIDS_DOCKER_CONTAINER_ID` | | Attach to an existing container instead of creating new ones. |
| `DRUIDS_DOCKER_HOST` | `localhost` | Hostname for SSH/HTTP access to Docker containers. |
| `OPENAI_API_KEY` | | OpenAI API key for Codex agents. |
| `GITHUB_ALLOWED_USERS` | | Comma-separated GitHub usernames. Restricts login. Empty means no restriction. |
| `DRUIDS_ADMIN_USERS` | | Comma-separated GitHub usernames for the admin dashboard. |
| `DRUIDS_MAX_OUTPUT_TOKENS_PER_EXECUTION` | `10000000` | Maximum output tokens per execution before the proxy stops it. |

### .env.example

```
# Required
DRUIDS_BASE_URL=http://172.17.0.1:8000
DRUIDS_SECRET_KEY=<fernet-key>
ANTHROPIC_API_KEY=sk-ant-xxx
FORWARDING_TOKEN_SECRET=<random-string>

# Required for git operations (agents need this to clone repos and push branches)
GITHUB_PAT=ghp_xxx

# Optional: for Codex agents
# OPENAI_API_KEY=sk-proj-xxx

# Optional: for MorphCloud sandbox backend (default is Docker)
# MORPH_API_KEY=morph_xxx
# DRUIDS_SANDBOX_TYPE=morph

# Optional: restrict login to specific GitHub users (comma-separated)
# GITHUB_ALLOWED_USERS=yourhandle,teammate
```

## Client configuration

The CLI stores machine-level configuration at `~/.druids/config.json`.
Agent identity comes from per-process environment variables set by the
bridge, not from the config file.

### config.json fields

| Field | Type | Default | Description |
|---|---|---|---|
| `base_url` | `string` | `https://druids.dev` | Server URL. |
| `user_access_token` | `string \| null` | `null` | API key (set by `druids auth set-key`). |

Example `~/.druids/config.json`:

```json
{
  "base_url": "https://druids.dev",
  "user_access_token": "druid_abc123"
}
```

### Agent environment variables

These are set automatically by the bridge on agent VMs. They are not
user-configurable.

| Variable | Description |
|---|---|
| `DRUIDS_ACCESS_TOKEN` | Per-agent auth token for server API calls. |
| `DRUIDS_AGENT_NAME` | This agent's name within the execution. |
| `DRUIDS_EXECUTION_SLUG` | The execution slug this agent belongs to. |
