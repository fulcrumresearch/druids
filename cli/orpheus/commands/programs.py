"""Program management commands."""

from pathlib import Path

import typer
from orpheus.client import APIError, NotFoundError, OrpheusClient
from orpheus.config import get_config
from orpheus.display import console, print_error, print_success


programs = typer.Typer(help="Manage registered programs.", no_args_is_help=True)


def _get_client() -> OrpheusClient:
    config = get_config()
    if not config.user_access_token:
        print_error("Not authenticated. Run 'orpheus auth login' first.")
        raise typer.Exit(1)
    return OrpheusClient(config)


@programs.command(name="list")
def list_programs():
    """List your registered programs."""
    client = _get_client()

    try:
        items = client.list_programs()
    except APIError as e:
        print_error(f"Error: {e}")
        raise typer.Exit(1)

    if not items:
        typer.echo("No programs registered. Use 'orpheus programs add <file>' to add one.")
        return

    console.print(f"[bold]{len(items)} program(s)[/bold]\n")
    for p in items:
        label = p["label"] or "(unlabeled)"
        rating = round(p["rating"])
        matches = p["num_comparisons"]
        console.print(f"  [bold]{label}[/bold]  [dim]{p['hash']}[/dim]  rating={rating}  matches={matches}")


@programs.command()
def add(
    spec_file: Path = typer.Argument(..., help="Path to YAML program spec file"),
    label: str | None = typer.Option(None, "--label", "-l", help="Human-readable label"),
):
    """Register a YAML program spec."""
    client = _get_client()

    if not spec_file.exists():
        print_error(f"File not found: {spec_file}")
        raise typer.Exit(1)

    yaml_content = spec_file.read_text()
    if not yaml_content.strip():
        print_error("File is empty")
        raise typer.Exit(1)

    # Default label to filename without extension
    if not label:
        label = spec_file.stem

    try:
        data = client.add_program(yaml_content, label=label)
    except APIError as e:
        print_error(f"Error: {e}")
        raise typer.Exit(1)

    print_success(f"Registered [bold]{data.get('label') or data['hash']}[/bold] ({data['hash']})")


@programs.command()
def remove(
    spec_hash: str = typer.Argument(..., help="Content hash of the program to remove"),
):
    """Unregister a program by its content hash."""
    client = _get_client()

    try:
        client.remove_program(spec_hash)
    except NotFoundError as e:
        print_error(str(e))
        raise typer.Exit(1)
    except APIError as e:
        print_error(f"Error: {e}")
        raise typer.Exit(1)

    print_success(f"Removed {spec_hash}")
