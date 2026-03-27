"""Git helpers for the Druids CLI."""

from __future__ import annotations

import re
import subprocess


def get_repo_from_cwd() -> str | None:
    """Get GitHub repo (user/repo) from current directory's git remote.

    Returns None if not in a git repo or origin remote is not GitHub.
    """
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            check=True,
        )
        url = result.stdout.strip()
    except subprocess.CalledProcessError:
        return None

    # Parse GitHub URLs:
    # - https://github.com/user/repo.git
    # - https://github.com/user/repo
    # - git@github.com:user/repo.git
    # - git@github.com:user/repo

    # HTTPS pattern
    https_match = re.match(r"https://github\.com/([^/]+)/([^/]+?)(?:\.git)?$", url)
    if https_match:
        return f"{https_match.group(1)}/{https_match.group(2)}"

    # SSH pattern
    ssh_match = re.match(r"git@github\.com:([^/]+)/([^/]+?)(?:\.git)?$", url)
    if ssh_match:
        return f"{ssh_match.group(1)}/{ssh_match.group(2)}"

    return None
