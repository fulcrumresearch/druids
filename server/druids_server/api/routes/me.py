"""User info and dashboard endpoints for the frontend."""

from __future__ import annotations

from fastapi import APIRouter

from druids_server.api.deps import Caller, get_user_executions
from druids_server.db.models.devbox import get_user_devboxes
from druids_server.db.session import get_session
from druids_server.lib.execution import Execution


router = APIRouter()


@router.get("/me", tags=["user"], operation_id="get_me")
async def get_me(caller: Caller):
    """Return the current user. In self-hosted mode this is always the local user."""
    return {
        "id": str(caller.user.id),
        "github_login": caller.user.github_login or "local",
        "github_id": caller.user.github_id,
        "is_admin": True,
    }


@router.get("/me/dashboard", tags=["user"], operation_id="get_dashboard")
async def get_dashboard(caller: Caller):
    """Return dashboard data: devboxes for the current user."""
    async with get_session() as db:
        devboxes = await get_user_devboxes(db, caller.user.id)

    return {
        "devboxes": [
            {
                "repo_full_name": d.repo_full_name,
                "name": d.name,
                "created_at": d.created_at.isoformat() if d.created_at else None,
            }
            for d in devboxes
        ],
    }
