"""Druids CLI entry point."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

import typer

from druids.client import APIError, DruidsClient, NotFoundError
from druids.commands.auth import auth
from druids.commands.init import init_command
from druids.commands.secret import secret
from druids.commands.skill import skill
from typing import Any

from druids.config import get_config, is_local_server
from druids.display import console, format_event, print_error, print_success
from druids.git import get_repo_from_cwd


app = typer.Typer(name="druids", help="Run programs on remote sandboxes.", no_args_is_help=True)
app.add_typer(auth, name="auth")
app.add_typer(secret, name="secret")
app.add_typer(skill, name="skill", hidden=True)
app.command(name="init")(init_command)


def get_authenticated_client() -> DruidsClient:
    """Get a client, requiring auth unless talking to a local server."""
    config = get_config()
    if not config.user_access_token and not is_local_server(config):
        print_error("Not authenticated. Run 'druids auth set-key <key>' first.")
        raise typer.Exit(1)
    return DruidsClient(config)


setup_app = typer.Typer(name="setup", help="Set up devbox environments.", no_args_is_help=True)
app.add_typer(setup_app, name="setup")

devbox_app = typer.Typer(name="devbox", help="Manage devboxes.", no_args_is_help=True)
app.add_typer(devbox_app, name="devbox")


@setup_app.command()
def start(
    name: str | None = typer.Option(None, "--name", "-n", help="Devbox name (default: repo name or 'default')"),
    repo: str | None = typer.Option(None, "--repo", "-r", help="GitHub repo (owner/repo) to clone into the devbox"),
    public: bool = typer.Option(False, "--public", help="Make this devbox usable by other users on the same repo"),
):
    """Start devbox setup by provisioning a sandbox.

    Creates a sandbox, optionally clones a repo, and prints SSH credentials
    so you can configure the environment interactively. The sandbox stays
    running until you call `druids setup finish`.
    """
    client = get_authenticated_client()

    repo_full_name = repo or (get_repo_from_cwd() if not name else None)

    if not repo_full_name and not name:
        print_error("Provide --name or --repo (or run from inside a git repo).")
        raise typer.Exit(1)

    label = name or repo_full_name or "default"
    typer.echo(f"Provisioning devbox '{label}'...")
    if repo_full_name:
        typer.echo(f"  Repo: {repo_full_name}")

    try:
        data = client.setup_start(name=name, repo_full_name=repo_full_name, public=public)
    except APIError as e:
        print_error(f"Error: {e}")
        raise typer.Exit(1)

    ssh = data["ssh"]
    devbox_name = data["name"]
    port = ssh.get("port", 22)

    # Write private key to a persistent file so the user can SSH in
    key_dir = Path.home() / ".druids"
    key_dir.mkdir(parents=True, exist_ok=True)
    safe_name = devbox_name.replace("/", "--")
    key_path = key_dir / f"devbox-{safe_name}.pem"
    key_path.write_text(ssh["private_key"])
    key_path.chmod(0o600)

    console.print(f"\n[bold]Devbox:[/bold] {devbox_name}")
    console.print(f"[bold]Instance:[/bold] {data['instance_id']}")
    if ssh.get("password"):
        console.print(f"[bold]Password:[/bold] {ssh['password']}")
    console.print("\nConnect with:")
    console.print(f"  ssh -i {key_path} -o StrictHostKeyChecking=no -p {port} {ssh['username']}@{ssh['host']}")
    console.print(f"\nWhen done, run: [bold]druids setup finish --name {devbox_name}[/bold]")


@setup_app.command()
def finish(
    name: str | None = typer.Option(None, "--name", "-n", help="Devbox name"),
    repo: str | None = typer.Option(None, "--repo", "-r", help="GitHub repo (owner/repo)"),
):
    """Finish devbox setup by snapshotting and stopping the sandbox.

    Snapshots the running sandbox, stops it, and stores the snapshot ID
    so the devbox can be used for executions.
    """
    client = get_authenticated_client()

    repo_full_name = repo or (get_repo_from_cwd() if not name else None)

    if not repo_full_name and not name:
        print_error("Provide --name or --repo (or run from inside a git repo).")
        raise typer.Exit(1)

    label = name or repo_full_name
    typer.echo(f"Finishing setup for '{label}'...")

    try:
        data = client.setup_finish(name=name, repo_full_name=repo_full_name)
    except APIError as e:
        print_error(f"Error: {e}")
        raise typer.Exit(1)

    print_success(f"Devbox ready: [bold]{data['name']}[/bold] (snapshot {data['snapshot_id']})")


@devbox_app.command(name="ls")
def devbox_list():
    """List all devboxes."""
    client = get_authenticated_client()

    try:
        devboxes = client.list_devboxes()
    except APIError as e:
        print_error(f"Error: {e}")
        raise typer.Exit(1)

    if not devboxes:
        typer.echo("No devboxes found")
        return

    for d in devboxes:
        ready = "[green]ready[/green]" if d["ready"] else "[yellow]setup in progress[/yellow]"
        repo = f" ({d['repo_full_name']})" if d.get("repo_full_name") else ""
        console.print(f"[bold]{d['name']}[/bold]{repo} [{ready}]")


@app.command(context_settings={"allow_extra_args": True, "allow_interspersed_args": True})
def exec(
    ctx: typer.Context,
    program_file: Path = typer.Argument(..., help="Path to program.py file"),
    devbox: str | None = typer.Option(None, "--devbox", "-d", help="Devbox name (default: devbox for current repo)"),
    branch: str | None = typer.Option(None, "--branch", "-b", help="Git branch to checkout"),
    ttl: int = typer.Option(0, "--ttl", help="Time-to-live in seconds (0 = server default)"),
    no_stream: bool = typer.Option(False, "--no-stream", help="Don't stream events after starting"),
):
    """Run a program on a remote sandbox.

    Resolves the devbox by name, or by detecting the current git repo. The
    devbox already knows whether it has a repo associated.

    Extra arguments are passed as key=value pairs to the program function.
    Example: druids exec program.py spec="build a feature"
    """
    client = get_authenticated_client()

    if not program_file.exists():
        print_error(f"Program file not found: {program_file}")
        raise typer.Exit(1)

    # Resolve devbox: explicit name, or look up by current repo
    devbox_name = devbox
    repo_full_name = None
    if not devbox_name:
        repo_full_name = get_repo_from_cwd()
        if not repo_full_name:
            print_error("Provide --devbox or run from inside a git repo.")
            raise typer.Exit(1)

    program_source = program_file.read_text()

    # Parse extra args as key=value pairs
    args: dict[str, str] = {}
    for arg in ctx.args:
        if "=" not in arg:
            print_error(f"Invalid argument '{arg}'. Expected key=value format.")
            raise typer.Exit(1)
        key, value = arg.split("=", 1)
        args[key] = value

    label = devbox_name or repo_full_name
    console.print(f"[dim]exec[/dim] {program_file.name} [dim]on[/dim] [bold]{label}[/bold]")

    try:
        data = client.create_execution(
            program_source,
            devbox_name=devbox_name,
            repo_full_name=repo_full_name,
            args=args or None,
            git_branch=branch,
            ttl=ttl,
        )
    except APIError as e:
        print_error(f"Error: {e}")
        raise typer.Exit(1)

    slug = data["execution_slug"]
    console.print(f"[green]>>>[/green] [bold]{slug}[/bold]")

    if no_stream:
        console.print(f"  [dim]Run[/dim] druids status {slug} [dim]to check progress.[/dim]")
        return

    console.print()
    try:
        for event in client.stream_execution(slug):
            line = format_event(event)
            if line is not None:
                console.print(line)
    except KeyboardInterrupt:
        console.print(f"\n[dim]Detached. Execution still running.[/dim]")
        console.print(f"  [dim]Run[/dim] druids status {slug} [dim]to check progress.[/dim]")
        return

    # Fetch final status and show error if the execution failed
    try:
        final = client.get_execution(slug)
        if final.get("error"):
            console.print(f"\n[red]Error:[/red] {final['error']}")
    except Exception:
        pass

    console.print(f"\n[green]---[/green] [dim]done[/dim]")


@app.command()
def status(
    slug: str = typer.Argument(..., help="Execution slug"),
):
    """Check status of an execution."""
    client = get_authenticated_client()

    try:
        data = client.get_execution(slug)
    except NotFoundError as e:
        print_error(str(e))
        raise typer.Exit(1)
    except APIError as e:
        print_error(f"Error: {e}")
        raise typer.Exit(1)

    status_color = "green" if data["status"] == "running" else "yellow" if data["status"] == "completed" else "red"
    console.print(f"[bold]Execution:[/bold] {data['execution_slug']}")
    console.print(f"[bold]Status:[/bold] [{status_color}]{data['status']}[/{status_color}]")
    if data.get("error"):
        console.print(f"[red]Error:[/red] {data['error']}")
    if data.get("repo_full_name"):
        console.print(f"[bold]Repo:[/bold] {data['repo_full_name']}")
    if data.get("branch_name"):
        console.print(f"[bold]Branch:[/bold] {data['branch_name']}")
    if data.get("pr_url"):
        console.print(f"[bold]PR:[/bold] {data['pr_url']}")
    if data.get("agents"):
        console.print(f"[bold]Agents:[/bold] {', '.join(data['agents'])}")
    if data.get("connections"):
        console.print(f"[bold]Connected:[/bold] {', '.join(data['connections'])}")
    for svc in data.get("exposed_services", []):
        console.print(f"  {svc['service_name']} -> {svc['url']}")


@app.command()
def stop(
    slug: str = typer.Argument(..., help="Execution slug"),
):
    """Stop an execution."""
    client = get_authenticated_client()

    try:
        data = client.stop_execution(slug)
    except NotFoundError as e:
        print_error(str(e))
        raise typer.Exit(1)
    except APIError as e:
        print_error(f"Error: {e}")
        raise typer.Exit(1)

    print_success(f"Execution {data['execution_slug']} stopped")


def _resolve_agent_context() -> tuple[str, str]:
    """Get execution_slug and agent_name from config. Used by agents on the VM."""
    config = get_config()
    if not config.execution_slug or not config.agent_name:
        print_error("Not running inside an agent VM. execution_slug and agent_name not set.")
        raise typer.Exit(1)
    return config.execution_slug, config.agent_name


@app.command()
def tools():
    """List tools available to this agent. Run from inside an agent VM."""
    client = get_authenticated_client()
    slug, agent_name = _resolve_agent_context()

    try:
        tool_list = client.list_tools(slug, agent_name)
    except APIError as e:
        print_error(f"Error: {e}")
        raise typer.Exit(1)

    if not tool_list:
        typer.echo("No tools registered")
        return

    for name in tool_list:
        typer.echo(name)


@app.command(name="tool", context_settings={"allow_extra_args": True, "allow_interspersed_args": True})
def tool_call(
    ctx: typer.Context,
    tool_name: str = typer.Argument(..., help="Tool name to call"),
):
    """Call a registered tool. Run from inside an agent VM.

    Extra arguments are passed as key=value pairs.
    Example: druids tool submit diff="..." summary="..."
    """
    client = get_authenticated_client()
    slug, agent_name = _resolve_agent_context()

    args: dict[str, str] = {}
    for arg in ctx.args:
        if "=" not in arg:
            print_error(f"Invalid argument '{arg}'. Expected key=value format.")
            raise typer.Exit(1)
        key, value = arg.split("=", 1)
        args[key] = value

    try:
        result = client.call_tool(slug, agent_name, tool_name, args)
    except NotFoundError as e:
        print_error(str(e))
        raise typer.Exit(1)
    except APIError as e:
        print_error(f"Error: {e}")
        raise typer.Exit(1)

    if result is not None:
        typer.echo(json.dumps(result) if isinstance(result, (dict, list)) else str(result))


@app.command()
def connect(
    execution_slug: str = typer.Argument(..., help="Execution slug"),
    agent: str | None = typer.Option(None, "--agent", "-a", help="Agent name (default: root instance)"),
    chat: bool = typer.Option(
        False, "--chat", "-c", help="Resume the agent's coding session instead of opening a shell"
    ),
):
    """SSH into an execution's VM.

    By default opens a shell as the agent user. With --chat, resumes
    the agent's Claude Code or Codex session so you can continue the
    conversation interactively.
    """
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

    with tempfile.NamedTemporaryFile(mode="w", suffix=".pem", delete=False) as f:
        f.write(private_key)
        key_path = f.name
    os.chmod(key_path, 0o600)

    ssh_opts = ["-i", key_path, "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null"]

    if chat:
        session_id = creds.get("session_id")
        if not session_id:
            print_error(f"No session ID for agent '{agent}'. The agent may not have connected yet.")
            os.unlink(key_path)
            raise typer.Exit(1)

        backend = creds.get("backend", "claude")
        console.print(f"[green]>>>[/green] Resuming [bold]{execution_slug}[/bold] agent [bold]{agent}[/bold]")
        console.print(f"[dim]Session: {session_id} ({backend})[/dim]")
        console.print(f"[dim]Password (if prompted): {password}[/dim]")

        if backend == "codex":
            remote_cmd = f"cd /home/agent && codex resume {session_id}"
        else:
            remote_cmd = f"cd /home/agent && claude --resume {session_id}"
        remote_cmd = f"sudo -iu agent bash -c '{remote_cmd}'"
    else:
        target = agent or "root"
        console.print(f"Connecting to [bold]{execution_slug}[/bold] ({target}: {username})...")
        console.print(f"[dim]Password (if prompted): {password}[/dim]")
        remote_cmd = "sudo -iu agent"

    try:
        subprocess.run(["ssh"] + ssh_opts + ["-t", f"{username}@{host}", remote_cmd])
    finally:
        os.unlink(key_path)


@app.command(name="ls")
def list_executions(
    all_executions: bool = typer.Option(False, "--all", "-a", help="Include stopped executions"),
):
    """List executions."""
    client = get_authenticated_client()

    try:
        items = client.list_executions(active_only=not all_executions)
    except APIError as e:
        print_error(f"Error: {e}")
        raise typer.Exit(1)

    if not items:
        typer.echo("No executions found")
        return

    for ex in items:
        status_color = "green" if ex["status"] == "running" else "yellow" if ex["status"] == "completed" else "dim"
        pr = f" -> {ex['pr_url']}" if ex.get("pr_url") else ""
        error_suffix = ""
        if ex.get("error"):
            truncated = ex["error"][:80] + "..." if len(ex["error"]) > 80 else ex["error"]
            error_suffix = f" [red]{truncated}[/red]"
        console.print(
            f"[{status_color}]{ex['slug']}[/{status_color}] \\[{ex['status']}] "
            f"{ex.get('repo_full_name', '')}{pr}{error_suffix}"
        )


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


@app.command(name="mcp-config")
def mcp_config():
    """Print MCP server configuration for Claude Code or Claude Desktop."""
    config = get_config()

    if not config.user_access_token and not is_local_server(config):
        print_error("Not authenticated. Run 'druids auth set-key <key>' first.")
        raise typer.Exit(1)

    base_url = str(config.base_url).rstrip("/")
    mcp_server: dict[str, Any] = {
        "type": "http",
        "url": f"{base_url}/mcp/",
    }
    if config.user_access_token:
        mcp_server["headers"] = {
            "Authorization": f"Bearer {config.user_access_token}",
        }
    mcp_block = {"mcpServers": {"druids": mcp_server}}

    console.print("Add this to your [bold].mcp.json[/bold] (or claude_desktop_config.json):\n")
    typer.echo(json.dumps(mcp_block, indent=2))


@app.command()
def server(
    host: str = typer.Option("0.0.0.0", "--host", "-h", help="Host to bind to"),
    port: int = typer.Option(8000, "--port", "-p", help="Port to bind to"),
):
    """Start the Druids server. Requires druids[server]."""
    try:
        from druids_server.app import main as server_main
    except ImportError:
        print_error("Server not installed. Install with: uv pip install 'druids[server]'")
        raise typer.Exit(1)

    server_main()


if __name__ == "__main__":
    app()
