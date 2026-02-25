"""Monorepo path constants. Single source of truth for Path(__file__)-derived paths."""

from pathlib import Path


# server/orpheus/paths.py -> parent.parent.parent = repo root
ROOT_DIR = Path(__file__).resolve().parent.parent.parent
SERVER_DIR = ROOT_DIR / "server"

AGENT_SKILLS_DIR = SERVER_DIR / "agent-skills"
BRIDGE_DIR = ROOT_DIR / "bridge"
CLI_WHEEL_DIR = ROOT_DIR / "cli" / "dist"
ENV_FILE = SERVER_DIR / ".env"
PROGRAMS_DIR = SERVER_DIR / "programs"
STUB_AGENT_PATH = SERVER_DIR / "tests" / "integration" / "stub_agent.py"
