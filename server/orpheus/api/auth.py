"""GitHub OAuth token validation service."""

import httpx
from pydantic import BaseModel


class AuthError(Exception):
    """Authentication error."""

    pass


class GitHubUser(BaseModel):
    """GitHub user info from API."""

    id: int
    login: str
    name: str | None = None
    email: str | None = None


class GitHubRepo(BaseModel):
    """GitHub repository info from API."""

    id: int
    name: str
    full_name: str
    private: bool
    clone_url: str
    ssh_url: str


async def get_github_user(access_token: str) -> GitHubUser:
    """Validate token with GitHub API and return user info.

    Raises AuthError if token is invalid or revoked.
    """
    async with httpx.AsyncClient() as client:
        response = await client.get(
            "https://api.github.com/user",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )

    if response.status_code == 401:
        raise AuthError("Invalid or revoked token")

    if response.status_code != 200:
        raise AuthError(f"GitHub API error: {response.status_code}")

    return GitHubUser.model_validate(response.json())


async def get_github_repos(access_token: str) -> list[GitHubRepo]:
    """Fetch user's GitHub repositories, sorted by most recently pushed.

    Raises AuthError if token is invalid or revoked.
    """
    repos: list[GitHubRepo] = []
    page = 1

    async with httpx.AsyncClient() as client:
        while True:
            response = await client.get(
                "https://api.github.com/user/repos",
                params={"sort": "pushed", "per_page": 100, "page": page},
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )

            if response.status_code == 401:
                raise AuthError("Invalid or revoked token")

            if response.status_code != 200:
                raise AuthError(f"GitHub API error: {response.status_code}")

            data = response.json()
            if not data:
                break

            for repo in data:
                repos.append(GitHubRepo.model_validate(repo))

            # Stop if we got less than 100 (last page)
            if len(data) < 100:
                break

            page += 1

    return repos
