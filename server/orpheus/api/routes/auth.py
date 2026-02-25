"""Auth endpoints."""

from __future__ import annotations

from fastapi import APIRouter

from orpheus.api.auth import get_github_repos
from orpheus.api.deps import CurrentUser, is_admin_user
from orpheus.config import settings
from orpheus.db.models.devbox import get_user_devboxes
from orpheus.db.models.execution import get_user_execution_count
from orpheus.db.session import get_session


router = APIRouter()


@router.get("/me", tags=["auth"])
async def get_current_user_info(user: CurrentUser):
    """Get current authenticated user info."""
    async with get_session() as db:
        execution_count = await get_user_execution_count(db, user.id)

    return {
        "id": str(user.id),
        "github_id": user.github_id,
        "github_login": user.github_login,
        "subscription_status": user.subscription_status,
        "execution_count": execution_count,
        "free_tier_reviews": settings.free_tier_reviews,
        "is_admin": is_admin_user(user),
        "github_app_install_url": f"https://github.com/apps/{settings.github_app_slug}/installations/new",
    }


@router.get("/me/dashboard", tags=["auth"])
async def get_dashboard_info(user: CurrentUser):
    """Get dashboard info: user details and configured devboxes."""
    async with get_session() as db:
        devboxes = await get_user_devboxes(db, user.id)

    return {
        "user": {
            "id": str(user.id),
            "github_id": user.github_id,
            "github_login": user.github_login,
        },
        "devboxes": [
            {
                "repo_full_name": d.repo_full_name,
                "has_snapshot": d.snapshot_id is not None,
                "setup_slug": d.setup_slug,
                "instance_id": d.instance_id,
                "setup_completed_at": d.setup_completed_at.isoformat() if d.setup_completed_at else None,
            }
            for d in devboxes
        ],
    }


@router.get("/repos", tags=["auth"])
async def list_repos(user: CurrentUser):
    """List user's GitHub repositories."""
    repos = await get_github_repos(user.access_token)
    return {"repos": [repo.model_dump() for repo in repos]}
