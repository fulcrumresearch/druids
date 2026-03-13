"""Druids server configuration."""

from __future__ import annotations

import os
import secrets
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings


def _auto_secret(env_var: str) -> str:
    """Return env var value or generate a random secret."""
    return os.environ.get(env_var, secrets.token_hex(32))


def _auto_fernet_key() -> str:
    """Return DRUIDS_SECRET_KEY from env or generate a Fernet key."""
    val = os.environ.get("DRUIDS_SECRET_KEY")
    if val:
        return val
    from cryptography.fernet import Fernet

    return Fernet.generate_key().decode()


class Settings(BaseSettings):
    """Server settings. Reads from environment variables."""

    # Server
    host: str = "0.0.0.0"
    port: int = 8000
    base_url: str = "http://localhost:8000"

    # Database (SQLite by default, override with DRUIDS_DATABASE_URL)
    database_url: str = "sqlite+aiosqlite:///druids.db"

    # Sandbox backend
    sandbox_type: Literal["docker"] = "docker"

    # Docker (used when sandbox_type="docker")
    docker_image: str = "ghcr.io/fulcrumresearch/druids-base:latest"
    docker_container_id: str | None = None  # Attach to existing container instead of creating new ones
    docker_host: str = "localhost"  # Hostname for SSH/HTTP access to Docker containers

    # Encryption key for secrets stored in the database (Fernet)
    secret_key: SecretStr = SecretStr(_auto_fernet_key())

    # Anthropic
    anthropic_api_key: SecretStr = Field(default=..., validation_alias="ANTHROPIC_API_KEY")
    forwarding_token_secret: SecretStr = SecretStr(_auto_secret("FORWARDING_TOKEN_SECRET"))

    # OpenAI
    openai_api_key: SecretStr | None = Field(default=None, validation_alias="OPENAI_API_KEY")

    # GitHub PAT for cloning repos and pushing branches
    github_pat: SecretStr | None = Field(default=None, validation_alias="GITHUB_PAT")

    # Maximum TTL (seconds) for any execution. 0 means no limit.
    max_execution_ttl: int = 86400  # 24 hours (clamp, not default)

    model_config = {
        "env_prefix": "DRUIDS_",
        "env_file": ".env",
        "extra": "ignore",
    }


settings = Settings()
