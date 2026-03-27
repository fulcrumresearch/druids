"""Configuration management for the Druids CLI.

Settings are resolved in priority order:

1. Environment variables (``DRUIDS_BASE_URL``, ``DRUIDS_ACCESS_TOKEN``, …)
2. ``server/.env`` in the current git repo (auto-discovered in dev)
3. ``~/.druids/config.json`` (machine-level defaults)
4. Built-in defaults
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from pydantic import Field
from pydantic_settings import (
    BaseSettings,
    JsonConfigSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)


_config: Config | None = None

_CONFIG_JSON = Path.home() / ".druids" / "config.json"


def _get_server_dotenv() -> None:
    """Disabled: server/.env contains Docker networking config (DRUIDS_BASE_URL)
    that conflicts with the CLI's own base_url from config.json."""
    return None


class Config(BaseSettings):
    """Druids CLI configuration."""

    model_config = SettingsConfigDict(
        env_prefix="DRUIDS_",
        populate_by_name=True,
        extra="ignore",
    )

    # Machine-level (from config file or env)
    base_url: str = "https://druids.dev"
    user_access_token: str | None = Field(default=None, validation_alias="DRUIDS_ACCESS_TOKEN")

    # Per-process (from env vars, set by bridge)
    execution_slug: str | None = None
    agent_name: str | None = None

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """init kwargs > env vars > server/.env > config.json > defaults."""
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            JsonConfigSettingsSource(settings_cls, json_file=_CONFIG_JSON),
        )


def get_config() -> Config:
    """Get the configuration."""
    global _config
    if _config is None:
        _config = load_config()
    return _config


def load_config() -> Config:
    """Load config using pydantic-settings source chain."""
    return Config(_env_file=_get_server_dotenv())


def is_local_server(config: Config) -> bool:
    """Check if the configured server is a local instance."""
    return "://localhost" in config.base_url or "://127.0.0.1" in config.base_url


def save_config(config: Config) -> None:
    """Save config to ``~/.druids/config.json``."""
    _CONFIG_JSON.parent.mkdir(exist_ok=True)
    data = {"base_url": config.base_url, "user_access_token": config.user_access_token}
    _CONFIG_JSON.write_text(json.dumps(data, indent=2))
