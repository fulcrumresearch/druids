"""Initialize a repo for Druids."""

from __future__ import annotations

import importlib.resources
import json
from pathlib import Path
from typing import Any

import typer
from druids.config import get_config, is_local_server
from druids.display import console, print_error, print_success


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


def _install_programs(base: Path) -> None:
    """Copy bundled starter programs into .druids/."""
    druids_dir = base / ".druids"
    druids_dir.mkdir(parents=True, exist_ok=True)

    for filename in PROGRAMS:
        dest = druids_dir / filename
        if dest.exists():
            console.print(f"  [dim]skip[/dim] {filename} (already exists)")
            continue
        source = importlib.resources.files("druids.programs").joinpath(filename)
        dest.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        print_success(f"  {filename} -> {dest}")


def _install_llms_txt(base: Path) -> None:
    """Copy bundled llms.txt into the target directory."""
    dest = base / "llms.txt"
    if dest.exists():
        console.print(f"  [dim]skip[/dim] llms.txt (already exists)")
        return
    source = importlib.resources.files("druids.data").joinpath("llms.txt")
    dest.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    print_success(f"  llms.txt -> {dest}")


SKILLS = {
    "druids-driver": "SKILL.md",
    "write-spec": "write-spec.md",
}


def _install_skills(base: Path) -> None:
    """Copy bundled skills into .claude/skills/."""
    for skill_name, filename in SKILLS.items():
        dest = base / ".claude" / "skills" / skill_name / "SKILL.md"
        if dest.exists():
            console.print(f"  [dim]skip[/dim] {skill_name} (already exists)")
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        source = importlib.resources.files("druids.skills").joinpath(filename)
        dest.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        print_success(f"  {skill_name} -> {dest}")


def _install_mcp_config(base: Path) -> None:
    """Add or update the druids entry in .mcp.json."""
    config = get_config()
    if not config.user_access_token and not is_local_server(config):
        print_error("Not authenticated — skipping .mcp.json. Run 'druids auth set-key <key>' first.")
        return

    mcp_path = base / ".mcp.json"

    # Load existing config or start fresh
    existing: dict[str, Any] = {}
    if mcp_path.exists():
        try:
            existing = json.loads(mcp_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, ValueError):
            pass

    servers = existing.setdefault("mcpServers", {})

    if "druids" in servers:
        console.print("  [dim]skip[/dim] .mcp.json (druids entry already exists)")
        return

    base_url = str(config.base_url).rstrip("/")
    entry: dict[str, Any] = {
        "type": "http",
        "url": f"{base_url}/mcp/",
    }
    if config.user_access_token:
        entry["headers"] = {"Authorization": f"Bearer {config.user_access_token}"}

    servers["druids"] = entry
    mcp_path.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
    print_success(f"  druids -> {mcp_path}")


def init_command(
    no_programs: bool = typer.Option(False, "--no-programs", help="Skip copying starter programs"),
):
    """Initialize a repo for Druids.

    Copies starter programs to .druids/, adds MCP config to .mcp.json,
    and prints a snippet to add to your coding agent instructions.
    """
    base = Path.cwd()

    if not no_programs:
        console.print("[bold]Programs[/bold]")
        _install_programs(base)

    console.print("\n[bold]Skills[/bold]")
    _install_skills(base)

    console.print("\n[bold]Docs[/bold]")
    _install_llms_txt(base)

    console.print("\n[bold]MCP[/bold]")
    _install_mcp_config(base)

    console.print("\n[bold]Add this to your coding agent instructions (e.g. CLAUDE.md):[/bold]\n")
    console.print(CLAUDE_MD_SECTION.strip())
    console.print()
