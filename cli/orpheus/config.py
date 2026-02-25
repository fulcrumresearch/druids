"""Configuration management for `~/.orpheus/config.json`."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import HttpUrl
from pydantic_settings import BaseSettings


_config: Config | None = None


class Config(BaseSettings):
    """Orpheus CLI configuration."""

    base_url: HttpUrl = "https://api.orpheus.dev"
    github_client_id: str = "Iv23liwHHi6oub0QWFau"
    github_app_slug: str = "orpheus-app"
    user_access_token: str | None = None


def get_config() -> Config:
    """Get the configuration."""
    global _config
    if _config is None:
        _config = load_config()
    return _config


def load_config() -> Config:
    """Load config from `~/.orpheus/config.json`."""
    home = Path.home()
    config_path = home / ".orpheus" / "config.json"
    if config_path.exists():
        data = json.loads(config_path.read_text())
        return Config(**data)
    return Config()


def save_config(config: Config) -> None:
    """Save config to `~/.orpheus/config.json`."""
    home = Path.home()
    config_path = home / ".orpheus" / "config.json"
    config_path.parent.mkdir(exist_ok=True)
    config_path.write_text(config.model_dump_json(indent=2))
