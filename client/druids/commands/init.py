"""Initialize a repo for Druids."""

from __future__ import annotations

from pathlib import Path

import httpx
import typer
from druids.display import console, print_success


GIST_ID = "8869a80c5c3ca2b018f754fa1cd78e9f"
GIST_RAW = f"https://gist.githubusercontent.com/KaivuH/{GIST_ID}/raw"
PROGRAMS = ["build.py", "review.py", "main.py"]

CLAUDE_MD_SECTION = """\

## Druids

This project has a Druids devbox for running coding agents on remote VMs.
When the user asks to build a feature, fix a bug, or do work that benefits
from delegation to background agents, use Druids to launch an execution
instead of implementing locally.

To launch work: write a spec (the write-spec skill has guidelines), choose
a program from `.druids/`, and call `create_execution` with the program
source and spec as args. Monitor with `get_execution` and review the PR
when agents finish.
"""


def _fetch_program(filename: str) -> str:
    """Fetch a program from the canonical gist."""
    resp = httpx.get(f"{GIST_RAW}/{filename}", timeout=10)
    resp.raise_for_status()
    return resp.text


def _install_programs(base: Path) -> None:
    """Fetch starter programs from gist into .druids/."""
    druids_dir = base / ".druids"
    druids_dir.mkdir(parents=True, exist_ok=True)

    for filename in PROGRAMS:
        dest = druids_dir / filename
        if dest.exists():
            console.print(f"  [dim]skip[/dim] {filename} (already exists)")
            continue
        content = _fetch_program(filename)
        dest.write_text(content, encoding="utf-8")
        print_success(f"  {filename} -> {dest}")


def init_command(
    no_programs: bool = typer.Option(False, "--no-programs", help="Skip copying starter programs"),
):
    """Initialize a repo for Druids.

    Copies starter programs to .druids/ and prints a snippet to add to your
    coding agent instructions.
    """
    base = Path.cwd()

    if not no_programs:
        console.print("[bold]Programs[/bold]")
        _install_programs(base)

    console.print("\n[bold]Add this to your coding agent instructions (e.g. CLAUDE.md):[/bold]\n")
    console.print(CLAUDE_MD_SECTION.strip())
    console.print()
