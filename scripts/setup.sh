#!/usr/bin/env bash
#
# setup.sh -- get Druids running locally.
#
# Installs dependencies, pulls the agent base image, configures the
# server and CLI, and starts the server. After this script finishes,
# you create a devbox and run a program.
#
# Prerequisites: Docker, uv, an Anthropic API key.
#
# Usage:
#   bash scripts/setup.sh
#
# Or non-interactively:
#   ANTHROPIC_API_KEY=sk-ant-... GITHUB_PAT=ghp_... bash scripts/setup.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

info()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
error() { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; }
die()   { error "$@"; exit 1; }

# -- Check prerequisites --

for cmd in docker uv; do
    command -v "$cmd" &>/dev/null || die "'$cmd' not found. Install it first."
done

docker info &>/dev/null || die "Docker daemon is not running."

# -- Pull agent base image --

info "Pulling agent base image..."
docker pull ghcr.io/fulcrumresearch/druids-base:latest

# -- Install Python dependencies --

info "Installing dependencies..."
(cd "$REPO_ROOT/server" && uv sync --quiet)
(cd "$REPO_ROOT/client" && uv sync --quiet)
(cd "$REPO_ROOT/bridge" && uv sync --quiet)

info "Building client wheel..."
(cd "$REPO_ROOT/client" && uv build --quiet)

# -- Write server/.env if missing --

ENV_FILE="$REPO_ROOT/server/.env"

if [[ ! -f "$ENV_FILE" ]]; then
    info "Creating server/.env..."

    if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
        read -rp "Anthropic API key: " ANTHROPIC_API_KEY
        [[ -n "$ANTHROPIC_API_KEY" ]] || die "API key required."
    fi

    # Detect Docker gateway for DRUIDS_BASE_URL
    GATEWAY="$(docker network inspect bridge --format '{{(index .IPAM.Config 0).Gateway}}' 2>/dev/null || true)"
    if [[ -z "$GATEWAY" ]]; then
        if [[ "$(uname)" == "Darwin" ]]; then
            GATEWAY="host.docker.internal"
        else
            GATEWAY="172.17.0.1"
        fi
    fi

    cat > "$ENV_FILE" <<EOF
ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
DRUIDS_BASE_URL=http://${GATEWAY}:8000
EOF

    if [[ -n "${GITHUB_PAT:-}" ]]; then
        echo "GITHUB_PAT=${GITHUB_PAT}" >> "$ENV_FILE"
    else
        read -rp "GitHub PAT (optional, press Enter to skip): " pat
        if [[ -n "$pat" ]]; then
            echo "GITHUB_PAT=${pat}" >> "$ENV_FILE"
        fi
    fi

    info "Wrote $ENV_FILE"
else
    info "server/.env already exists, skipping."
fi

# -- Install CLI --

info "Installing druids CLI..."
uv tool install --force "$REPO_ROOT/client"

CLI_CONFIG="$HOME/.druids/config.json"
mkdir -p "$HOME/.druids"
if [[ -f "$CLI_CONFIG" ]]; then
    if command -v python3 &>/dev/null; then
        python3 -c "
import json, pathlib
p = pathlib.Path('$CLI_CONFIG')
d = json.loads(p.read_text())
d['base_url'] = 'http://localhost:8000'
p.write_text(json.dumps(d, indent=2))
"
    else
        echo '{"base_url": "http://localhost:8000"}' > "$CLI_CONFIG"
    fi
else
    echo '{"base_url": "http://localhost:8000"}' > "$CLI_CONFIG"
fi
info "CLI configured to use http://localhost:8000"

# -- Run database migrations --

info "Running database migrations..."
(cd "$REPO_ROOT/server" && uv run alembic upgrade head)

# -- Start server --

info "Starting server..."
(cd "$REPO_ROOT/server" && uv run druids-server &)

for i in $(seq 1 15); do
    if curl -s http://localhost:8000/ &>/dev/null; then
        info "Server is ready at http://localhost:8000"
        break
    fi
    if [[ $i -eq 15 ]]; then
        die "Server did not start. Check server logs."
    fi
    sleep 1
done

# -- Done --

echo ""
info "Setup complete. Next steps:"
echo ""
echo "  1. Create a devbox for your repo:"
echo "       druids setup start --repo owner/repo"
echo "       # install dependencies in the SSH session, then:"
echo "       druids setup finish --name owner/repo"
echo ""
echo "  2. Run a program:"
echo "       druids exec .druids/basher.py --devbox owner/repo \\"
echo "         task_name=\"test\" task_spec=\"Hello world\""
echo ""
