"""Path resolution for bundled assets and monorepo layout.

When installed as a pip package, assets (frontend, bridge, client wheel) are
bundled under druids_server/_bundled/. When running from the monorepo (dev),
they live in their usual locations relative to the repo root.
"""

from __future__ import annotations

from pathlib import Path


# Package directory: druids_server/
_PKG_DIR = Path(__file__).resolve().parent

# Bundled assets (present in pip-installed wheels)
_BUNDLED_DIR = _PKG_DIR / "_bundled"

# Monorepo root (present when running from git checkout)
_MONOREPO_ROOT = _PKG_DIR.parent.parent


def _resolve(*candidates: Path) -> Path:
    """Return the first candidate path that exists."""
    for path in candidates:
        if path.exists():
            return path
    return candidates[0]  # return first as default (will error naturally)


# Bridge directory containing bridge.py
BRIDGE_DIR = _resolve(
    _BUNDLED_DIR / "bridge",
    _MONOREPO_ROOT / "bridge",
)

# Directory containing pre-built client wheels
CLIENT_WHEEL_DIR = _resolve(
    _BUNDLED_DIR / "client_wheel",
    _MONOREPO_ROOT / "client" / "dist",
)

# Frontend dist directory (built Vue app)
DASHBOARD_DIST = _resolve(
    _BUNDLED_DIR / "frontend",
    _MONOREPO_ROOT / "frontend" / "dist",
)

# Server-specific paths (always relative to monorepo for dev)
SERVER_DIR = _MONOREPO_ROOT / "server"
ROOT_DIR = _MONOREPO_ROOT
ENV_FILE = SERVER_DIR / ".env"
AGENT_SKILLS_DIR = SERVER_DIR / "agent-skills"
STUB_AGENT_PATH = SERVER_DIR / "tests" / "integration" / "stub_agent.py"
