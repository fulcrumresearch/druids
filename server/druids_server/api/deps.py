"""FastAPI dependencies."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from druids_server.db.models.user import User, get_or_create_user, get_user
from druids_server.db.session import get_session
from druids_server.lib.execution import Execution
from druids_server.utils.forwarding_tokens import validate_token


logger = logging.getLogger(__name__)


# Shared executions registry: user_id -> slug -> Execution
_executions: dict[str, dict[str, Execution]] = {}


def get_executions_registry() -> dict[str, dict[str, Execution]]:
    """Get the shared executions registry."""
    return _executions


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

security = HTTPBearer(auto_error=False)

_JWT_SCOPES = {"runtime", "agent"}


@dataclass
class CallerIdentity:
    """Authenticated caller identity."""

    user: User
    scope: str | None = None
    execution_slug: str | None = None
    agent_name: str | None = None


async def _get_local_user() -> User:
    """Return the local user, creating one if needed."""
    from sqlalchemy import select as sa_select

    async with get_session() as db:
        result = await db.execute(sa_select(User).order_by(User.created_at).limit(1))
        user = result.scalars().first()
        if user:
            return user
        return await get_or_create_user(db, github_id=0, github_login="local")


async def _resolve_token(token: str) -> CallerIdentity | None:
    """Resolve a bearer token to a CallerIdentity, or None if invalid."""
    try:
        claims = validate_token(token)
        scope = claims.get("scope")
        if scope in _JWT_SCOPES:
            async with get_session() as db:
                user = await get_user(db, UUID(claims["sub"]))
                if not user:
                    return None
                return CallerIdentity(
                    user=user,
                    scope=scope,
                    execution_slug=claims.get("execution_slug"),
                    agent_name=claims.get("agent_name"),
                )
    except jwt.InvalidTokenError:
        pass

    return None


async def authenticate_token(token: str) -> User | None:
    """Resolve a bearer token to a User, or None.

    Thin wrapper around ``_resolve_token`` kept for the WebSocket endpoint
    which only needs the User object.
    """
    caller = await _resolve_token(token)
    return caller.user if caller else None


async def get_caller(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> CallerIdentity:
    """Resolve the caller. External requests get the local user; runtime/agent
    requests are validated via JWT."""
    token = None
    if credentials:
        token = credentials.credentials

    if token:
        caller = await _resolve_token(token)
        if caller:
            return caller

    # No token or invalid token: return local user
    user = await _get_local_user()
    return CallerIdentity(user=user)


# ---------------------------------------------------------------------------
# Authorization guards
# ---------------------------------------------------------------------------


async def require_driver(
    caller: CallerIdentity = Depends(get_caller),
) -> None:
    """Reject requests from agents. Runtimes and users pass through."""
    if caller.scope == "agent":
        raise HTTPException(status_code=403, detail="This endpoint is not available to agents")


# ---------------------------------------------------------------------------
# Derived dependencies
# ---------------------------------------------------------------------------


def get_user_executions(
    caller: CallerIdentity = Depends(get_caller),
) -> dict[str, Execution]:
    """Get executions for the current user, keyed by slug."""
    user_id = str(caller.user.id)
    if user_id not in _executions:
        _executions[user_id] = {}
    return _executions[user_id]


# Type aliases for dependency injection. Use these in endpoint signatures.
Caller = Annotated[CallerIdentity, Depends(get_caller)]
UserExecutions = Annotated[dict[str, Execution], Depends(get_user_executions)]
