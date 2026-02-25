# Setup

Complete walkthrough for setting up an Orpheus development environment. Works on a fresh MorphCloud box, an EC2 instance, or any Debian/Ubuntu machine.

## 1. Run the setup script

The script installs prerequisites (uv, PostgreSQL, gh CLI), Python dependencies for all three components, and ruff. It is idempotent, so you can re-run it safely.

```
git clone https://github.com/fulcrumresearch/orpheus.git
cd orpheus
bash scripts/setup.sh
```

If you already have the repo cloned, just run the script:

```
bash scripts/setup.sh
```

What the script installs:

- `uv` (Python package manager) via the official installer.
- PostgreSQL via apt. Creates the `orpheus` database and configures `pg_hba.conf` to allow passwordless local TCP connections (trust auth for `postgres` role on `127.0.0.1`, scoped to the `orpheus` database only).
- `gh` (GitHub CLI) via the official apt repository.
- Python dependencies for `server/`, `cli/`, and `bridge/` via `uv sync`.
- `ruff` (linter/formatter) via `uv tool install`.
- `pre-commit` via `uv tool install`, with git hooks installed automatically.

The script does not touch API keys, `.env` files, or GitHub App configuration.

## 2. Create `server/.env`

Create `server/.env` with your API keys. The server reads this file on startup via Pydantic `BaseSettings`. The full set of required variables is listed in the Configuration section of [CLAUDE.md](CLAUDE.md). For now, add the three you already have:

```
ORPHEUS_BASE_URL=https://xxx.morphcloud.io
MORPH_API_KEY=morph_xxx
ANTHROPIC_API_KEY=sk-ant-xxx
FORWARDING_TOKEN_SECRET=dev-forwarding-secret
```

The remaining required variables (`GITHUB_CLIENT_ID`, `GITHUB_APP_ID`, `GITHUB_APP_PRIVATE_KEY`, `GITHUB_APP_SLUG`) are written automatically by the setup script in step 3. The server will not start until all required variables are present.

`ORPHEUS_BASE_URL` is the public URL where agents can reach the server's MCP endpoint at `{base_url}/mcp/`. On MorphCloud, you get this by exposing port 8000:

```python
from morphcloud.api import MorphCloudClient

client = MorphCloudClient()
inst = client.instances.get("morphvm_xxx")
url = inst.expose_http_service("orpheus-server", 8000)
print(url)  # https://xxx.morphcloud.io
```

On an EC2 instance or other machine, use whatever public URL reaches port 8000.

Optionally, add `OPENAI_API_KEY=sk-xxx` if you want to run Codex-based agents.

## 3. Create a development GitHub App

Orpheus uses a GitHub App to push commits and create pull requests as `orpheus[bot]`. Each developer creates their own app under the `fulcrumresearch` organization.

Run the setup script from the `server/` directory (it needs to read `server/.env` for the base URL):

```
cd server
uv run python ../scripts/setup_github_app.py
```

The script will:

1. Read `ORPHEUS_BASE_URL` from `server/.env` to derive the webhook URL.
2. Ask for your name (e.g. `alice`). The app will be named `Orpheus by Fulcrum (dev-alice)`.
3. Print a `data:` URI. Copy and paste it into a browser on your local machine. This submits the app manifest to GitHub.
4. Review the app settings on GitHub and click "Create GitHub App".
5. GitHub redirects you to a page with a `?code=...` parameter. Paste the full URL back into the terminal.
6. The script exchanges the code for credentials and writes them to `server/.env` and `~/.orpheus/config.json`.

To verify an existing app:

```
cd server
uv run python ../scripts/setup_github_app.py --check
```

## 4. Enable device flow and install the app

After the GitHub App is created, two manual steps remain.

Enable device flow: the setup script prints a link to your app's settings page. Open it and check the "Enable Device Flow" box under "Optional features". Without this, `orpheus auth login` will not work.

Install the app on your repositories: the setup script prints an installation link. Click it and grant the app access to the repos you want Orpheus to work with.

## 5. Start the server

```
cd server
uv run orpheus-server
```

The server starts on port 8000. Verify it is running:

```
curl http://localhost:8000/health
```

Or from outside the machine, use your public URL:

```
curl https://xxx.morphcloud.io/health
```

## 6. Configure and authenticate the CLI

The GitHub App setup script (step 3) already wrote `github_client_id` and `github_app_slug` to `~/.orpheus/config.json`. If your server is not at the default `https://api.orpheus.dev`, edit the file and set `base_url`:

```json
{
    "base_url": "https://xxx.morphcloud.io",
    "github_client_id": "Iv1.abc123",
    "github_app_slug": "orpheus-by-fulcrum-dev-alice"
}
```

Then log in:

```
orpheus auth login
```

This uses the GitHub device flow. You will be shown a code and a URL. Open the URL in a browser, enter the code, and authorize the app. The CLI stores your token in `~/.orpheus/config.json`.

## 7. Verification checklist

After completing all steps, verify the setup:

- `curl http://localhost:8000/health` returns 200.
- `cd server && uv run python ../scripts/setup_github_app.py --check` passes all checks.
- `orpheus auth status` shows you are authenticated.
- `cd server && uv run pytest` passes the test suite.
- `orpheus exec "Hello world"` creates a task (optional smoke test).
