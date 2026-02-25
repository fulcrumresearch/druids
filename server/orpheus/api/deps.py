"""FastAPI dependencies."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from orpheus.api.auth import AuthError, get_github_user
from orpheus.config import settings
from orpheus.lib.execution import Execution
from orpheus.db.models.user import User, get_or_create_user, get_user_by_token
from orpheus.db.session import get_session


logger = logging.getLogger(__name__)


# Shared executions registry: user_id -> slug -> Execution
_executions: dict[str, dict[str, Execution]] = {}


def get_executions_registry() -> dict[str, dict[str, Execution]]:
    """Get the shared executions registry."""
    return _executions


# ---------------------------------------------------------------------------
# Auth dependencies
# ---------------------------------------------------------------------------

security = HTTPBearer(auto_error=False)


def _check_allowlist(github_login: str | None) -> None:
    """Raise 403 if the allowlist is active and the user's login is not on it."""
    allowed = settings.github_allowed_users
    if allowed is None:
        return
    if github_login is None or github_login.lower() not in allowed:
        raise HTTPException(status_code=403, detail="User not on allowlist")


async def get_current_user_optional(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> User | None:
    """Get current user from token, or None if not authenticated.

    Checks Bearer header first, then falls back to session_token cookie
    for browser-based dashboard auth.
    """
    token = None
    if credentials:
        token = credentials.credentials
    elif "session_token" in request.cookies:
        token = request.cookies["session_token"]

    if token is None:
        return None

    async with get_session() as db:
        user = await get_user_by_token(db, token)
        if user:
            # If allowlist is active and we don't have a stored login, re-fetch from GitHub.
            if settings.github_allowed_users is not None and user.github_login is None:
                try:
                    github_user = await get_github_user(token)
                except AuthError:
                    return None
                user.github_login = github_user.login
                await db.flush()
                await db.refresh(user)

            _check_allowlist(user.github_login)
            return user

        # Token not in DB - validate with GitHub
        try:
            github_user = await get_github_user(token)
        except AuthError:
            return None

        _check_allowlist(github_user.login)

        # Create or update user
        user = await get_or_create_user(db, github_user.id, token, github_login=github_user.login)
        return user


async def get_current_user(
    user: User | None = Depends(get_current_user_optional),
) -> User:
    """Get current user, raising 401 if not authenticated."""
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


# Type aliases for dependency injection
CurrentUser = Annotated[User, Depends(get_current_user)]
OptionalUser = Annotated[User | None, Depends(get_current_user_optional)]


def is_admin_user(user: User) -> bool:
    """Check if a user is an admin based on their github_login."""
    if not user.github_login or not settings.admin_users:
        return False
    return user.github_login.lower() in settings.admin_users


async def get_admin_user(user: User = Depends(get_current_user)) -> User:
    """Get current user, raising 403 if not an admin."""
    if not is_admin_user(user):
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


AdminUser = Annotated[User, Depends(get_admin_user)]


# ---------------------------------------------------------------------------
# User-scoped execution dependencies
# ---------------------------------------------------------------------------


def get_user_executions(user: CurrentUser) -> dict[str, Execution]:
    """Get executions for the current user, keyed by slug."""
    user_id = str(user.id)
    if user_id not in _executions:
        _executions[user_id] = {}
    return _executions[user_id]


UserExecutions = Annotated[dict[str, Execution], Depends(get_user_executions)]


# ---------------------------------------------------------------------------
# Caller context from agent identity headers
# ---------------------------------------------------------------------------


@dataclass
class CallerContext:
    """Identity extracted from X-Execution-Slug and X-Agent-Name headers.

    Set automatically by _connect_agent on each agent's MCP config.
    Absent when the driver (CLI) calls tools directly.
    """

    execution_slug: str | None = None
    agent_name: str | None = None


async def get_caller_context(request: Request) -> CallerContext:
    return CallerContext(
        execution_slug=request.headers.get("x-execution-slug"),
        agent_name=request.headers.get("x-agent-name"),
    )


CallerHeaders = Annotated[CallerContext, Depends(get_caller_context)]
