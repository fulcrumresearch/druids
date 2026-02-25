"""GitHub App authentication utilities."""

import logging
import time

import httpx
import jwt
from fastapi import HTTPException

from orpheus.config import settings


logger = logging.getLogger(__name__)


async def get_installation_token(repo_full_name: str) -> str:
    """Get a GitHub installation access token for a repository.

    The token allows the agent to push commits and create PRs as orpheus[bot].
    """
    # 1. Create JWT signed with App private key (expires in 10 min)
    now = int(time.time())
    payload = {
        "iat": now - 60,  # issued 60s ago to account for clock drift
        "exp": now + 600,  # expires in 10 minutes
        "iss": str(settings.github_app_id),
    }
    private_key = settings.github_app_private_key.get_secret_value()
    app_jwt = jwt.encode(payload, private_key, algorithm="RS256")

    headers = {
        "Authorization": f"Bearer {app_jwt}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    async with httpx.AsyncClient() as client:
        # 2. Get installation ID for this repo
        resp = await client.get(
            f"https://api.github.com/repos/{repo_full_name}/installation",
            headers=headers,
        )
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError:
            logger.exception("GitHub API error during installation lookup for %s", repo_full_name)
            raise HTTPException(502, "GitHub API request failed") from None
        installation_id = resp.json()["id"]

        # 3. Create installation access token
        resp = await client.post(
            f"https://api.github.com/app/installations/{installation_id}/access_tokens",
            headers=headers,
        )
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError:
            logger.exception("GitHub API error during token creation for %s", repo_full_name)
            raise HTTPException(502, "GitHub API request failed") from None
        return resp.json()["token"]


async def get_pull_request(repo_full_name: str, pr_number: int) -> dict:
    """Fetch pull request details from the GitHub API."""
    token = await get_installation_token(repo_full_name)
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"https://api.github.com/repos/{repo_full_name}/pulls/{pr_number}",
            headers={
                "Authorization": f"token {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        resp.raise_for_status()
        return resp.json()


async def close_pull_request(repo_full_name: str, pr_number: int) -> None:
    """Close a pull request on GitHub."""
    token = await get_installation_token(repo_full_name)
    async with httpx.AsyncClient() as client:
        resp = await client.patch(
            f"https://api.github.com/repos/{repo_full_name}/pulls/{pr_number}",
            headers={
                "Authorization": f"token {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            json={"state": "closed"},
        )
        resp.raise_for_status()


async def post_issue_comment(repo_full_name: str, issue_number: int, body: str) -> None:
    """Post a comment on a GitHub issue using the App installation token."""
    token = await get_installation_token(repo_full_name)
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"https://api.github.com/repos/{repo_full_name}/issues/{issue_number}/comments",
            headers={
                "Authorization": f"token {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            json={"body": body},
        )
        resp.raise_for_status()
