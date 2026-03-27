"""JWT tokens for server authentication.

The server mints JWTs for two scopes:

- "agent": agents on VMs use these to authenticate API calls back to
  the server. Expire after 2 hours.
- "runtime": execution runtimes use these to call server APIs (create agents,
  signal done/fail, remote-exec). Expire after 2 hours.

All tokens are signed with FORWARDING_TOKEN_SECRET (HS256).
"""

from __future__ import annotations

import time

import jwt

from druids_server.config import settings


AGENT_TOKEN_LIFETIME = 2 * 60 * 60  # 2 hours
RUNTIME_TOKEN_LIFETIME = 2 * 60 * 60  # 2 hours


def _secret() -> str:
    return settings.forwarding_token_secret.get_secret_value()


def _mint(claims: dict, lifetime: int) -> str:
    """Sign a JWT with the given claims and lifetime."""
    now = int(time.time())
    payload = {
        **claims,
        "iss": "druids",
        "iat": now,
        "exp": now + lifetime,
    }
    return jwt.encode(payload, _secret(), algorithm="HS256")


def mint_token(user_id: str, execution_slug: str, agent_name: str, scope: str = "agent") -> str:
    """Mint a JWT for an agent."""
    return _mint(
        {"sub": user_id, "execution_slug": execution_slug, "agent_name": agent_name, "scope": scope},
        AGENT_TOKEN_LIFETIME,
    )


def mint_runtime_token(user_id: str, execution_slug: str) -> str:
    """Mint a JWT for an execution runtime."""
    return _mint(
        {"sub": user_id, "execution_slug": execution_slug, "scope": "runtime"},
        RUNTIME_TOKEN_LIFETIME,
    )


def validate_token(token: str) -> dict:
    """Validate a JWT. Returns the claims dict.

    Raises jwt.InvalidTokenError (or subclass) on failure.
    """
    return jwt.decode(
        token,
        _secret(),
        algorithms=["HS256"],
        issuer="druids",
    )
