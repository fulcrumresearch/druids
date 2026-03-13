"""Skill management commands."""

from __future__ import annotations

import importlib.resources
from pathlib import Path

import typer
from druids.display import console, print_success


skill = typer.Typer(help="Manage Claude Code skills.", no_args_is_help=True)

SKILLS = {
    "druids-driver": "SKILL.md",
    "write-spec": "write-spec.md",
}

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


def _read_bundled_skill(filename: str) -> str:
    """Read a bundled skill file from the druids.skills package."""
    return importlib.resources.files("druids.skills").joinpath(filename).read_text(encoding="utf-8")


def _offer_claude_md(base: Path) -> None:
    """Check for CLAUDE.md and offer to append a Druids section."""
    claude_md = base / "CLAUDE.md"

    if claude_md.exists():
        content = claude_md.read_text(encoding="utf-8")
        if "## Druids" in content:
            return
        if typer.confirm("Append a Druids section to CLAUDE.md?", default=False):
            with claude_md.open("a", encoding="utf-8") as f:
                f.write(CLAUDE_MD_SECTION)
            print_success(f"Appended Druids section to {claude_md}")
            return

    console.print("\n[dim]Recommended CLAUDE.md section:[/dim]")
    console.print(CLAUDE_MD_SECTION)


@skill.command()
def install(
    global_install: bool = typer.Option(
        False, "--global", "-g", help="Install globally to ~/.claude (applies to all repos)"
    ),
    target_dir: Path | None = typer.Option(
        None, "--target-dir", "-t", help="Override install directory (for testing or custom paths)"
    ),
):
    """Install Druids skills into a target codebase.

    Creates .claude/skills/ entries for druids-driver and write-spec so
    Claude Code loads the Druids references automatically.

    By default, installs relative to the current working directory.
    Use --global to install to ~/.claude instead.
    """
    if target_dir is not None:
        base = target_dir
    elif global_install:
        base = Path.home()
    else:
        base = Path.cwd()

    for skill_name, filename in SKILLS.items():
        dest = base / ".claude" / "skills" / skill_name / "SKILL.md"
        dest.parent.mkdir(parents=True, exist_ok=True)

        content = _read_bundled_skill(filename)
        dest.write_text(content, encoding="utf-8")

        print_success(f"Installed {skill_name} skill to {dest}")

    _offer_claude_md(base)
