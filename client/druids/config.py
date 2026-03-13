"""Configuration management for `~/.druids/config.json`.

Agent identity (execution_slug, agent_name) comes from per-process env vars
set by the bridge. The config file only stores machine-level settings
(base_url, token). This lets multiple agents on the same machine have
distinct identities.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from pydantic import HttpUrl
from pydantic_settings import BaseSettings


_config: Config | None = None


class Config(BaseSettings):
    """Druids CLI configuration."""

    # Machine-level (from config file)
    base_url: HttpUrl = "https://druids.dev"
    user_access_token: str | None = None

    # Per-process (from env vars, set by bridge)
    execution_slug: str | None = None
    agent_name: str | None = None


def get_config() -> Config:
    """Get the configuration."""
    global _config
    if _config is None:
        _config = load_config()
    return _config


def load_config() -> Config:
    """Load machine config from file, agent identity from env vars."""
    home = Path.home()
    config_path = home / ".druids" / "config.json"
    if config_path.exists():
        data = json.loads(config_path.read_text())
        config = Config(**data)
    else:
        config = Config()

    # Per-process identity and token (set by bridge via agent.config.env)
    if env_token := os.environ.get("DRUIDS_ACCESS_TOKEN"):
        config.user_access_token = env_token
    if env_agent := os.environ.get("DRUIDS_AGENT_NAME"):
        config.agent_name = env_agent
    if env_slug := os.environ.get("DRUIDS_EXECUTION_SLUG"):
        config.execution_slug = env_slug

    return config


def is_local_server(config: Config) -> bool:
    """Check if the configured server is a local instance."""
    host = config.base_url.host or ""
    return host in ("localhost", "127.0.0.1")


def save_config(config: Config) -> None:
    """Save config to `~/.druids/config.json`."""
    home = Path.home()
    config_path = home / ".druids" / "config.json"
    config_path.parent.mkdir(exist_ok=True)
    config_path.write_text(config.model_dump_json(indent=2))
