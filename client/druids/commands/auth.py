"""Authentication commands."""

from __future__ import annotations

import typer
from druids.config import load_config, save_config
from druids.display import print_error, print_success


auth = typer.Typer(help="Manage authentication.", no_args_is_help=True)


@auth.command(name="set-key")
def set_key(
    key: str = typer.Argument(..., help="API key (starts with druid_)"),
):
    """Authenticate with an API key.

    Get a key from the Druids dashboard settings page.
    """
    if not key.startswith("druid_"):
        print_error("Invalid key. API keys start with 'druid_'.")
        raise typer.Exit(1)

    config = load_config()
    config.user_access_token = key
    save_config(config)
    print_success("API key saved.")


@auth.command()
def logout():
    """Clear stored credentials."""
    config = load_config()
    config.user_access_token = None
    save_config(config)
    print_success("Logged out.")


@auth.command()
def status():
    """Show current authentication status."""
    config = load_config()
    if config.user_access_token:
        print_success("Authenticated.")
    else:
        print_error("Not authenticated. Run `druids auth set-key`.")
