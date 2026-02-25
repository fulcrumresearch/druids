"""OAuth endpoints for browser-based GitHub login."""

from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse

from orpheus.api.auth import AuthError, get_github_user
from orpheus.api.deps import _check_allowlist
from orpheus.config import settings
from orpheus.db.models.user import get_or_create_user
from orpheus.db.session import get_session


router = APIRouter(prefix="/oauth", tags=["auth"])
logger = logging.getLogger(__name__)


@router.get("/login")
async def login():
    """Redirect to GitHub OAuth authorization page."""
    if not settings.github_client_secret:
        raise HTTPException(501, "OAuth not configured")

    authorize_url = (
        f"https://github.com/login/oauth/authorize"
        f"?client_id={settings.github_client_id}"
        f"&redirect_uri={settings.base_url}/api/oauth/callback"
        f"&scope=repo"
    )
    return RedirectResponse(authorize_url)


@router.get("/callback")
async def callback(code: str):
    """Handle GitHub OAuth callback. Exchange code for token, set session cookie."""
    if not settings.github_client_secret:
        raise HTTPException(501, "OAuth not configured")

    # Exchange authorization code for access token
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://github.com/login/oauth/access_token",
            json={
                "client_id": settings.github_client_id,
                "client_secret": settings.github_client_secret.get_secret_value(),
                "code": code,
            },
            headers={"Accept": "application/json"},
        )

    if response.status_code != 200:
        logger.error("GitHub token exchange failed: %s", response.status_code)
        raise HTTPException(502, "Failed to exchange authorization code")

    data = response.json()
    access_token = data.get("access_token")
    if not access_token:
        error = data.get("error_description", data.get("error", "unknown"))
        logger.error("GitHub token exchange error: %s", error)
        raise HTTPException(400, "Failed to obtain access token")

    # Validate token and get user info
    try:
        github_user = await get_github_user(access_token)
    except AuthError:
        raise HTTPException(401, "Invalid token from GitHub")

    # Check allowlist before issuing a session
    _check_allowlist(github_user.login)

    # Create or update user in DB
    async with get_session() as db:
        await get_or_create_user(db, github_user.id, access_token, github_login=github_user.login)

    # Set cookie and redirect to dashboard
    redirect = RedirectResponse("/", status_code=302)
    redirect.set_cookie(
        "session_token",
        access_token,
        httponly=True,
        secure=settings.base_url.startswith("https"),
        samesite="lax",
        path="/",
        max_age=60 * 60 * 24 * 30,  # 30 days
    )
    return redirect


@router.get("/logout")
async def logout():
    """Clear session cookie and redirect to login."""
    redirect = RedirectResponse("/", status_code=302)
    redirect.delete_cookie("session_token", path="/")
    return redirect
