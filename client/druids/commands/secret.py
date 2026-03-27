"""Secret management commands."""

from __future__ import annotations

import sys
from pathlib import Path

import typer
from druids.client import APIError, DruidsClient
from druids.config import get_config, is_local_server
from druids.display import print_error, print_success
from druids.git import get_repo_from_cwd


secret = typer.Typer(help="Manage secrets on a devbox.", no_args_is_help=True)


def _get_client() -> DruidsClient:
    config = get_config()
    if not config.user_access_token and not is_local_server(config):
        print_error("Not authenticated. Run 'druids auth set-key <key>' first.")
        raise typer.Exit(1)
    return DruidsClient(config)


def _resolve_target(devbox: str | None) -> tuple[str | None, str | None]:
    """Return (devbox_name, repo_full_name) for the target devbox."""
    if devbox:
        return devbox, None
    repo = get_repo_from_cwd()
    if repo:
        return None, repo
    print_error("Provide --devbox or run from inside a git repo.")
    raise typer.Exit(1)


@secret.command(name="set")
def set_secret(
    name: str = typer.Argument(None, help="Secret name (e.g. API_KEY). Omit when using --file."),
    value: str = typer.Argument(None, help="Secret value (omit to read from stdin)"),
    devbox: str | None = typer.Option(None, "--devbox", "-d", help="Devbox name"),
    file: Path | None = typer.Option(None, "--file", "-f", help="Load secrets from a .env file"),
):
    """Set secrets on a devbox.

    Single: druids devbox secret set API_KEY sk-123 --devbox mybox
    From file: druids devbox secret set --file .env --devbox mybox
    From stdin: echo sk-123 | druids devbox secret set API_KEY --devbox mybox
    """
    client = _get_client()
    devbox_name, repo = _resolve_target(devbox)

    if file:
        if not file.exists():
            print_error(f"File not found: {file}")
            raise typer.Exit(1)
        secrets = {}
        for line in file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            k, v = line.split("=", 1)
            secrets[k.strip()] = v.strip().strip("'\"")
        if not secrets:
            print_error("No secrets found in file")
            raise typer.Exit(1)
    else:
        if not name:
            print_error("Provide a secret name, or use --file.")
            raise typer.Exit(1)
        if value is None:
            value = sys.stdin.read().strip()
            if not value:
                print_error("No value provided")
                raise typer.Exit(1)
        secrets = {name: value}

    try:
        client.set_secrets(secrets, devbox_name=devbox_name, repo_full_name=repo)
    except APIError as e:
        print_error(f"Error: {e}")
        raise typer.Exit(1)

    if len(secrets) == 1:
        print_success(f"Secret '{next(iter(secrets))}' set")
    else:
        print_success(f"Set {len(secrets)} secret(s)")


@secret.command(name="ls")
def list_secrets(
    devbox: str | None = typer.Option(None, "--devbox", "-d", help="Devbox name"),
):
    """List secrets on a devbox (names only, not values)."""
    client = _get_client()
    devbox_name, repo = _resolve_target(devbox)

    try:
        secrets = client.list_secrets(devbox_name=devbox_name, repo_full_name=repo)
    except APIError as e:
        print_error(f"Error: {e}")
        raise typer.Exit(1)

    if not secrets:
        typer.echo("No secrets")
        return

    for s in secrets:
        typer.echo(s["name"])


@secret.command(name="rm")
def delete_secret(
    name: str = typer.Argument(..., help="Secret name to delete"),
    devbox: str | None = typer.Option(None, "--devbox", "-d", help="Devbox name"),
):
    """Delete a secret from a devbox."""
    client = _get_client()
    devbox_name, repo = _resolve_target(devbox)

    try:
        client.delete_secret(name, devbox_name=devbox_name, repo_full_name=repo)
    except APIError as e:
        print_error(f"Error: {e}")
        raise typer.Exit(1)
    print_success(f"Secret '{name}' deleted")
