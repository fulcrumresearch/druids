"""Forwarding tokens for proxy access.

Agents on VMs receive a signed JWT instead of the raw Anthropic API key.
The proxy validates the JWT signature and expiry, then checks the execution
registry to confirm the execution is still active. The registry check is the
primary access control gate -- stopping an execution immediately invalidates
all its tokens. The JWT expiry is defense in depth: if the server restarts
(clearing the in-memory registry) or a token leaks, it is dead after a
bounded window regardless.
"""

from __future__ import annotations

import time

import jwt

from orpheus.config import settings


# Tokens expire 2 hours after minting. Long enough for any legitimate
# execution; short enough that a leaked token cannot be used indefinitely.
TOKEN_LIFETIME_SECONDS = 2 * 60 * 60


def _secret() -> str:
    return settings.forwarding_token_secret.get_secret_value()


def mint_token(user_id: str, execution_slug: str, agent_name: str) -> str:
    """Mint a forwarding token for an agent."""
    now = int(time.time())
    return jwt.encode(
        {
            "sub": user_id,
            "execution_slug": execution_slug,
            "agent_name": agent_name,
            "iss": "orpheus",
            "iat": now,
            "exp": now + TOKEN_LIFETIME_SECONDS,
        },
        _secret(),
        algorithm="HS256",
    )


def validate_token(token: str) -> dict:
    """Validate a forwarding token. Returns the claims dict.

    Raises jwt.InvalidTokenError (or subclass) on failure.
    """
    return jwt.decode(
        token,
        _secret(),
        algorithms=["HS256"],
        issuer="orpheus",
    )
