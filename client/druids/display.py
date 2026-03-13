"""Display helpers using Rich."""

from __future__ import annotations

from typing import Any

from rich.console import Console


console = Console()


def print_success(message: str) -> None:
    """Print a success message."""
    console.print(f"[green]✓[/green] {message}")


def print_error(message: str) -> None:
    """Print an error message."""
    console.print(f"[red]✗[/red] {message}")


def format_event(event: dict[str, Any]) -> str | None:
    """Format a trace event dict into a Rich-markup line. Returns None for events to skip."""
    event_type = event.get("type", "")
    agent = event.get("agent", "?")
    tag = f"[bold cyan]{agent}[/bold cyan]"

    if event_type == "connected":
        return f"  [green]>>>[/green] {tag} connected"

    if event_type == "disconnected":
        return f"  [dim red]<<<[/dim red] {tag} disconnected"

    if event_type == "prompt":
        return f"  [dim]|[/dim] {tag} [dim]prompt sent[/dim]"

    if event_type == "response_chunk":
        text = (event.get("text") or "").strip()
        if not text:
            return None
        if len(text) > 120:
            text = text[:120] + "..."
        return f"  [dim]|[/dim] {tag} {text}"

    if event_type == "tool_use":
        tool = event.get("tool", "?")
        return f"  [magenta]>[/magenta] {tag} [magenta]{tool}[/magenta]"

    if event_type == "tool_result":
        tool = event.get("tool", "?")
        exit_code = event.get("exit_code")
        if exit_code is not None and exit_code != 0:
            return f"  [red]<[/red] {tag} [magenta]{tool}[/magenta] [red]exit {exit_code}[/red]"
        return f"  [green]<[/green] {tag} [magenta]{tool}[/magenta]"

    if event_type == "error":
        error = event.get("error", "unknown error")
        return f"  [bold red]![/bold red] {tag} [red]{error}[/red]"

    if event_type == "client_event":
        event_name = event.get("event", "?")
        data = event.get("data", {})
        summary = data.get("summary", "") if isinstance(data, dict) else str(data)
        if summary:
            return f"  [yellow]*[/yellow] {tag} {event_name}: {summary}"
        return f"  [yellow]*[/yellow] {tag} {event_name}"

    return None
