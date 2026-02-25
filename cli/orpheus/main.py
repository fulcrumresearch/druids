"""Orpheus CLI entry point."""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import typer

from orpheus.client import APIError, NotFoundError, OrpheusClient
from orpheus.commands.auth import auth
from orpheus.commands.programs import programs
from orpheus.config import get_config
from orpheus.display import console, print_error, print_success
from orpheus.git import get_repo_from_cwd


app = typer.Typer(name="orpheus", help="AI-powered code review.", no_args_is_help=True)
app.add_typer(auth, name="auth")
app.add_typer(programs, name="programs")


def get_authenticated_client() -> OrpheusClient:
    """Get an authenticated client or exit with error."""
    config = get_config()
    if not config.user_access_token:
        print_error("Not authenticated. Run 'orpheus auth login' first.")
        raise typer.Exit(1)
    return OrpheusClient(config)


@app.command()
def exec(
    spec_file: Path = typer.Argument(..., help="Path to spec file"),
    snapshot: str | None = typer.Option(None, "--snapshot", "-s", help="Base snapshot ID"),
    repo: str | None = typer.Option(None, "--repo", "-r", help="GitHub repo (owner/repo)"),
    branch: str | None = typer.Option(None, "--branch", "-b", help="Git branch to checkout"),
    program: list[str] | None = typer.Option(
        None, "--program", "-p", help="Filter to specific registered programs by label or hash. Repeatable."
    ),
):
    """Run registered programs on a spec. Uses all registered programs by default."""
    client = get_authenticated_client()

    if not spec_file.exists():
        print_error(f"Spec file not found: {spec_file}")
        raise typer.Exit(1)

    repo_full_name = repo or get_repo_from_cwd()
    if not repo_full_name:
        print_error("Could not detect repo. Run from inside a git repo or use --repo flag.")
        raise typer.Exit(1)

    spec_content = spec_file.read_text()

    programs_label = ", ".join(program) if program else "all registered"
    typer.echo(f"Starting task for: {spec_file.name} (repo: {repo_full_name}, programs: {programs_label})")

    try:
        data = client.create_task(spec_content, repo_full_name, snapshot, program_filter=program, git_branch=branch)
    except APIError as e:
        print_error(f"Error: {e}")
        raise typer.Exit(1)

    task_slug = data["task_slug"]
    print_success(f"Task created: [bold]{task_slug}[/bold]")
    console.print(f"  Executions: {', '.join(data['execution_slugs'])}")
    console.print(f"  Run 'orpheus status {task_slug}' to check progress.")


@app.command()
def status(
    task_slug: str = typer.Argument(..., help="Task slug"),
):
    """Check status of a task."""
    client = get_authenticated_client()

    try:
        data = client.get_task(task_slug)
    except NotFoundError as e:
        print_error(str(e))
        raise typer.Exit(1)
    except APIError as e:
        print_error(f"Error: {e}")
        raise typer.Exit(1)

    console.print(f"[bold]Task:[/bold] {data['task_slug']}")
    console.print(f"[bold]Status:[/bold] {'active' if data['is_active'] else 'stopped'}")

    if data.get("executions"):
        console.print("[bold]Executions:[/bold]")
        for ex in data["executions"]:
            status_color = "green" if ex["status"] == "running" else "yellow" if ex["status"] == "completed" else "red"
            console.print(f"  [{status_color}]{ex['slug']}[/{status_color}] ({ex['program_name']}) - {ex['status']}")
            if ex.get("pr_url"):
                console.print(f"    PR: {ex['pr_url']}")
            for svc in ex.get("exposed_services", []):
                console.print(f"    {svc['service_name']} -> {svc['url']}")


@app.command()
def stop(
    task_slug: str = typer.Argument(..., help="Task slug"),
):
    """Stop a task."""
    client = get_authenticated_client()

    try:
        data = client.stop_task(task_slug)
    except NotFoundError as e:
        print_error(str(e))
        raise typer.Exit(1)
    except APIError as e:
        print_error(f"Error: {e}")
        raise typer.Exit(1)

    print_success(f"Task {data['task_slug']} stopped")


@app.command()
def connect(
    execution_slug: str = typer.Argument(..., help="Execution slug"),
    agent: str | None = typer.Option(None, "--agent", "-a", help="Agent name (default: root instance)"),
):
    """SSH into an execution's VM as the agent user."""
    client = get_authenticated_client()

    try:
        creds = client.get_execution_ssh(execution_slug, agent=agent)
    except NotFoundError as e:
        print_error(str(e))
        raise typer.Exit(1)
    except APIError as e:
        print_error(f"Error: {e}")
        raise typer.Exit(1)

    host = creds["host"]
    username = creds["username"]
    private_key = creds["private_key"]
    password = creds.get("password", "")

    # Write private key to a temp file
    with tempfile.NamedTemporaryFile(mode="w", suffix=".pem", delete=False) as f:
        f.write(private_key)
        key_path = f.name
    os.chmod(key_path, 0o600)

    target = agent or "root"
    console.print(f"Connecting to [bold]{execution_slug}[/bold] ({target}: {username})...")
    console.print(f"[dim]Password (if prompted): {password}[/dim]")

    try:
        subprocess.run(
            [
                "ssh",
                "-i",
                key_path,
                "-o",
                "StrictHostKeyChecking=no",
                "-o",
                "UserKnownHostsFile=/dev/null",
                "-t",
                f"{username}@{host}",
                "sudo -iu agent",
            ],
        )
    finally:
        os.unlink(key_path)


@app.command()
def tasks(
    all_tasks: bool = typer.Option(False, "--all", "-a", help="Include stopped/inactive tasks"),
):
    """List all tasks and their executions."""
    client = get_authenticated_client()

    try:
        task_list = client.list_tasks(active_only=not all_tasks)
    except APIError as e:
        print_error(f"Error: {e}")
        raise typer.Exit(1)

    if not task_list:
        typer.echo("No tasks found")
        return

    for task in task_list:
        status = "[green]active[/green]" if task["is_active"] else "[dim]stopped[/dim]"
        console.print(f"\n{status} [bold]{task['slug']}[/bold]")
        console.print(f"  Created: {task['created_at']}")
        if task.get("executions"):
            for ex in task["executions"]:
                instance = ex.get("root_instance_id") or "no instance"
                pr = f" → {ex['pr_url']}" if ex.get("pr_url") else ""
                console.print(f"    [dim]{ex['slug']}[/dim] ({ex['program_name']}) [{ex['status']}] {instance}{pr}")


@app.command(hidden=True)
def apply(
    execution_slug: str = typer.Argument(..., help="Execution slug"),
    force: bool = typer.Option(False, "--force", "-f", help="Overwrite existing files"),
):
    """Apply diff from an execution's VM to local repo."""
    client = get_authenticated_client()

    try:
        diff = client.get_execution_diff(execution_slug)
    except NotFoundError as e:
        print_error(str(e))
        raise typer.Exit(1)
    except APIError as e:
        print_error(f"Error: {e}")
        raise typer.Exit(1)

    if not diff.strip():
        typer.echo("No changes to apply")
        raise typer.Exit(0)

    typer.echo(diff)
    typer.echo()

    # Find repo root
    repo_root_result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
    )
    if repo_root_result.returncode != 0:
        print_error("Not in a git repository")
        raise typer.Exit(1)
    repo_root = repo_root_result.stdout.strip()

    apply_args = ["git", "apply", "-v"]
    if force:
        apply_args.append("--reject")

    if not force:
        result = subprocess.run(
            ["git", "apply", "--check"],
            input=diff,
            text=True,
            capture_output=True,
            cwd=repo_root,
        )
        if result.returncode != 0:
            print_error(f"Diff cannot be applied cleanly:\n{result.stderr}")
            print_error("Use --force to apply anyway")
            raise typer.Exit(1)

    result = subprocess.run(
        apply_args,
        input=diff,
        text=True,
        capture_output=True,
        cwd=repo_root,
    )

    if result.returncode != 0:
        print_error(f"Failed to apply diff:\n{result.stderr}")
        raise typer.Exit(1)

    if result.stderr:
        typer.echo(result.stderr)
    print_success("Changes applied")


@app.command(hidden=True)
def download(
    remote_path: str = typer.Argument(..., help="Remote file path on the VM"),
    repo: str | None = typer.Option(None, "--repo", "-r", help="Repo full name (devbox target)"),
    exec_slug: str | None = typer.Option(None, "--exec", "-e", help="Execution slug"),
    agent: str | None = typer.Option(None, "--agent", "-a", help="Agent name"),
):
    """Download a file from a VM to stdout."""
    client = get_authenticated_client()

    # Auto-detect repo from cwd if no explicit target
    target_repo = repo
    if not exec_slug and not target_repo:
        target_repo = get_repo_from_cwd()
    if not target_repo and not exec_slug:
        print_error("Could not detect repo. Use --repo or --exec/--agent.")
        raise typer.Exit(1)

    try:
        response = client.download_file(remote_path, repo=target_repo, execution_slug=exec_slug, agent_name=agent)
    except NotFoundError as e:
        print_error(str(e))
        raise typer.Exit(1)
    except APIError as e:
        print_error(f"Error: {e}")
        raise typer.Exit(1)

    sys.stdout.buffer.write(response.content)


@app.command(hidden=True)
def upload(
    local_path: str = typer.Argument(..., help="Local file path (use '-' for stdin)"),
    remote_path: str = typer.Argument(..., help="Remote file path on the VM"),
    repo: str | None = typer.Option(None, "--repo", "-r", help="Repo full name (devbox target)"),
    exec_slug: str | None = typer.Option(None, "--exec", "-e", help="Execution slug"),
    agent: str | None = typer.Option(None, "--agent", "-a", help="Agent name"),
):
    """Upload a file to a VM."""
    client = get_authenticated_client()

    # Auto-detect repo from cwd if no explicit target
    target_repo = repo
    if not exec_slug and not target_repo:
        target_repo = get_repo_from_cwd()
    if not target_repo and not exec_slug:
        print_error("Could not detect repo. Use --repo or --exec/--agent.")
        raise typer.Exit(1)

    if local_path == "-":
        content = sys.stdin.buffer.read()
        file_path = None
    else:
        file_path = Path(local_path)
        if not file_path.exists():
            print_error(f"File not found: {local_path}")
            raise typer.Exit(1)
        content = None

    try:
        client.upload_file(
            file_path, remote_path, content=content, repo=target_repo, execution_slug=exec_slug, agent_name=agent
        )
    except APIError as e:
        print_error(f"Error: {e}")
        raise typer.Exit(1)

    print_success(f"Uploaded to {remote_path}")


@app.command(name="mcp-config")
def mcp_config():
    """Print MCP server configuration for Claude Code or Claude Desktop."""
    config = get_config()

    if not config.user_access_token:
        print_error("Not authenticated. Run 'orpheus auth login' first.")
        raise typer.Exit(1)

    base_url = str(config.base_url).rstrip("/")
    mcp_block = {
        "mcpServers": {
            "orpheus": {
                "type": "http",
                "url": f"{base_url}/mcp/",
                "headers": {
                    "Authorization": f"Bearer {config.user_access_token}",
                },
            }
        }
    }

    console.print("Add this to your [bold].mcp.json[/bold] (or claude_desktop_config.json):\n")
    typer.echo(json.dumps(mcp_block, indent=2))


if __name__ == "__main__":
    app()
