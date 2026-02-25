"""Orpheus server configuration."""

from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Server settings. Reads from environment variables."""

    # Server
    host: str = "0.0.0.0"
    port: int = 8000
    base_url: str = Field(default=...)

    # Database
    database_url: str = "postgresql+asyncpg://postgres@localhost/orpheus"

    # Morph
    morph_api_key: SecretStr = Field(default=..., validation_alias="MORPH_API_KEY")

    # Anthropic
    anthropic_api_key: SecretStr = Field(default=..., validation_alias="ANTHROPIC_API_KEY")
    forwarding_token_secret: SecretStr = Field(default=..., validation_alias="FORWARDING_TOKEN_SECRET")

    # OpenAI
    openai_api_key: SecretStr | None = Field(default=None, validation_alias="OPENAI_API_KEY")

    # GitHub App
    github_client_id: str = Field(default=..., validation_alias="GITHUB_CLIENT_ID")
    github_app_id: int = Field(default=..., validation_alias="GITHUB_APP_ID")
    github_app_private_key: SecretStr = Field(default=..., validation_alias="GITHUB_APP_PRIVATE_KEY")
    github_app_slug: str = Field(default=..., validation_alias="GITHUB_APP_SLUG")
    github_client_secret: SecretStr | None = Field(default=None, validation_alias="GITHUB_CLIENT_SECRET")
    github_webhook_secret: SecretStr | None = Field(default=None, validation_alias="GITHUB_WEBHOOK_SECRET")

    # GitHub user allowlist (comma-separated usernames). None means no restriction.
    github_allowed_users: set[str] | None = Field(default=None, validation_alias="GITHUB_ALLOWED_USERS")

    # Admin users who can see the usage dashboard (comma-separated usernames). Empty means no admins.
    admin_users: set[str] | None = Field(default=None, validation_alias="ORPHEUS_ADMIN_USERS")

    # Stripe
    stripe_api_key: SecretStr | None = Field(default=None, validation_alias="STRIPE_API_KEY")
    stripe_webhook_secret: SecretStr | None = Field(default=None, validation_alias="STRIPE_WEBHOOK_SECRET")
    stripe_price_id: str = Field(default="price_1T1ctYIvhu9esSaWcB4VxmCA", validation_alias="STRIPE_PRICE_ID")

    # Free tier
    free_tier_reviews: int = 15

    # Token budget: max output tokens per execution before the proxy kills it.
    max_output_tokens_per_execution: int = 10_000_000

    # Feature flags
    enable_task_creation: bool = False  # POST /tasks, issue mention handler, file transfer

    @field_validator("github_allowed_users", mode="before")
    @classmethod
    def parse_allowed_users(cls, v: str | set[str] | None) -> set[str] | None:
        if v is None or v == "":
            return None
        if isinstance(v, str):
            return {username.strip().lower() for username in v.split(",") if username.strip()}
        return v

    @field_validator("admin_users", mode="before")
    @classmethod
    def parse_admin_users(cls, v: str | set[str] | None) -> set[str]:
        if v is None or v == "":
            return set()
        if isinstance(v, str):
            return {username.strip().lower() for username in v.split(",") if username.strip()}
        return v

    model_config = {
        "env_prefix": "ORPHEUS_",
        "env_file": ".env",
        "extra": "ignore",
    }


settings = Settings()
