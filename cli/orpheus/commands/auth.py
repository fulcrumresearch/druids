"""Authentication commands."""

import webbrowser

import typer
from orpheus.auth import AuthError, poll_for_user_access_token, request_device_code
from orpheus.config import load_config, save_config
from orpheus.display import console, print_error, print_success


auth = typer.Typer(help="Manage authentication.", no_args_is_help=True)


def has_browser() -> bool:
    """Check if a browser is available."""
    try:
        webbrowser.get()
        return True
    except webbrowser.Error:
        return False


def _prompt_app_installation(app_slug: str) -> None:
    """Print the installation link so the user can add the app to their repos."""
    install_url = f"https://github.com/apps/{app_slug}/installations/new"
    console.print()
    console.print("Install the Orpheus GitHub App on any repos you want agents to work on:")
    console.print(f"  {install_url}")
    console.print()


@auth.command()
def login():
    """Authenticate with Orpheus via GitHub."""
    config = load_config()

    # Request device code
    response = request_device_code()

    # Prompt user
    console.print(f"Your one-time code is [bold green]{response.user_code}[/bold green]")
    console.print()

    if has_browser():
        console.print("Press Enter to open `github.com` in your browser...")
        input()
        webbrowser.open(str(response.verification_uri))
    else:
        console.print(f"Open this URL in a browser: [bold blue]{response.verification_uri}[/bold blue]")
        console.print("Enter the code above, then authorize the application.")
        console.print()

    # Poll for token
    console.print("Waiting for authorization...")
    try:
        user_access_token = poll_for_user_access_token(response.device_code, response.interval)
    except AuthError as error:
        print_error(str(error))
        raise typer.Exit(1)

    # Save
    config.user_access_token = user_access_token
    save_config(config)
    print_success("Authenticated successfully.")

    _prompt_app_installation(config.github_app_slug)


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
        print_error("Not authenticated. Run `orpheus auth login`.")
