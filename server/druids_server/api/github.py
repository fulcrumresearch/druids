"""GitHub authentication utilities."""

from __future__ import annotations

from druids_server.config import settings


GIT_PERMISSIONS = {
    "read": {"contents": "read"},
    "post": {"contents": "read", "pull_requests": "write", "issues": "write"},
    "write": {"contents": "write", "pull_requests": "write"},
}


async def get_installation_token(
    repo_full_name: str,
    permissions: dict[str, str] | None = None,
) -> str:
    """Get a GitHub token for a repository.

    Returns the configured PAT. The permissions argument is accepted for
    interface compatibility but ignored since PATs have fixed scopes.
    """
    if not settings.github_pat:
        raise RuntimeError("GITHUB_PAT is required for git operations")
    return settings.github_pat.get_secret_value()
