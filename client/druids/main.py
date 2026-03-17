"""Druids CLI entry point."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from importlib.metadata import version as pkg_version
from pathlib import Path
from typing import Any

import typer

from druids.client import APIError, DruidsClient, NotFoundError
from druids.commands.auth import auth
from druids.commands.init import init_command
from druids.commands.secret import secret
from druids.config import get_config, is_local_server
from druids.display import console, format_event, print_error, print_success
from druids.git import get_repo_from_cwd


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"druids {pkg_version('druids')}")
        raise typer.Exit()


app = typer.Typer(name="druids", help="Run programs on remote sandboxes.", no_args_is_help=True)
app.add_typer(auth, name="auth")
app.command(name="init")(init_command)


@app.callback()
def main_callback(
    version: bool = typer.Option(
        False, "--version", "-V", help="Show version and exit.", callback=_version_callback, is_eager=True
    ),
) -> None:
    """Run programs on remote sandboxes."""


def get_authenticated_client() -> DruidsClient:
    """Get a client, requiring auth unless talking to a local server."""
    config = get_config()
    if not config.user_access_token and not is_local_server(config):
        print_error("Not authenticated. Run 'druids auth set-key <key>' first.")
        raise typer.Exit(1)
    return DruidsClient(config)


# ---------------------------------------------------------------------------
# Devbox commands
# ---------------------------------------------------------------------------

devbox_app = typer.Typer(name="devbox", help="Manage devbox environments.", no_args_is_help=True)
app.add_typer(devbox_app, name="devbox")
devbox_app.add_typer(secret, name="secret")


@devbox_app.command(name="create")
def devbox_create(
    name: str | None = typer.Option(None, "--name", "-n", help="Devbox name (default: repo name or 'default')"),
    repo: str | None = typer.Option(None, "--repo", "-r", help="GitHub repo (owner/repo) to clone into the devbox"),
    public: bool = typer.Option(False, "--public", help="Make this devbox usable by other users on the same repo"),
):
    """Provision a new devbox sandbox.

    Creates a sandbox, optionally clones a repo, and prints SSH credentials
    so you can configure the environment interactively. The sandbox stays
    running until you call `druids devbox snapshot`.
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
    console.print(f"\nWhen done, run: [bold]druids devbox snapshot --name {devbox_name}[/bold]")


@devbox_app.command(name="snapshot")
def devbox_snapshot(
    name: str | None = typer.Option(None, "--name", "-n", help="Devbox name"),
    repo: str | None = typer.Option(None, "--repo", "-r", help="GitHub repo (owner/repo)"),
):
    """Snapshot and stop a devbox sandbox.

    Snapshots the running sandbox, stops it, and stores the snapshot ID
    so the devbox can be used for executions.
    """
    client = get_authenticated_client()

    repo_full_name = repo or (get_repo_from_cwd() if not name else None)

    if not repo_full_name and not name:
        print_error("Provide --name or --repo (or run from inside a git repo).")
        raise typer.Exit(1)

    label = name or repo_full_name
    typer.echo(f"Snapshotting devbox '{label}'...")

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
        ready = "[green]ready[/green]" if d["has_snapshot"] else "[yellow]setup in progress[/yellow]"
        repo = f" ({d['repo_full_name']})" if d.get("repo_full_name") else ""
        console.print(f"[bold]{d['name']}[/bold]{repo} [{ready}]")


# ---------------------------------------------------------------------------
# exec (top-level — the primary workflow command)
# ---------------------------------------------------------------------------


@app.command(context_settings={"allow_extra_args": True, "allow_interspersed_args": True})
def exec(
    ctx: typer.Context,
    program_file: Path = typer.Argument(..., help="Path to program.py file"),
    devbox: str | None = typer.Option(None, "--devbox", "-d", help="Devbox name (default: devbox for current repo)"),
    branch: str | None = typer.Option(None, "--branch", "-b", help="Git branch to checkout"),
    ttl: int = typer.Option(0, "--ttl", help="Time-to-live in seconds (0 = server default)"),
    no_stream: bool = typer.Option(False, "--no-stream", help="Don't stream events after starting"),
    no_setup: bool = typer.Option(False, "--no-setup", help="Run on the default base image without a devbox"),
    add_files: list[str] | None = typer.Option(None, "--add-files", "-f", help="Local files to copy into the sandbox"),
):
    """Run a program on a remote sandbox.

    Resolves the devbox by name, or by detecting the current git repo. Use
    --no-setup to skip devbox resolution and run on the default base image.
    Use --add-files to copy local files into each agent's sandbox.

    Extra arguments are passed as key=value pairs to the program function.
    Example: druids exec build spec="build a feature"
    """
    client = get_authenticated_client()

    # Resolve bare names like "build" to ".druids/build.py"
    if not program_file.exists():
        candidate = Path(".druids") / f"{program_file}.py"
        if candidate.exists():
            program_file = candidate
        else:
            print_error(f"Program file not found: {program_file}")
            raise typer.Exit(1)

    # Resolve devbox: explicit name, current repo, or skip with --no-setup
    devbox_name = devbox
    repo_full_name = None
    if not no_setup and not devbox_name:
        repo_full_name = get_repo_from_cwd()
        if not repo_full_name:
            print_error("Provide --devbox, run from inside a git repo, or use --no-setup.")
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

    # Read local files to include in the sandbox
    files: dict[str, str] | None = None
    if add_files:
        files = {}
        for file_path in add_files:
            p = Path(file_path)
            if not p.exists():
                print_error(f"File not found: {file_path}")
                raise typer.Exit(1)
            # Place in /home/agent/ with the same filename
            dest = f"/home/agent/{p.name}"
            files[dest] = p.read_text()

    label = devbox_name or repo_full_name or "base image"
    console.print(f"[dim]exec[/dim] {program_file.name} [dim]on[/dim] [bold]{label}[/bold]")

    try:
        data = client.create_execution(
            program_source,
            devbox_name=devbox_name,
            repo_full_name=repo_full_name,
            args=args or None,
            git_branch=branch,
            ttl=ttl,
            files=files,
        )
    except APIError as e:
        print_error(f"Error: {e}")
        raise typer.Exit(1)

    slug = data["execution_slug"]
    url = f"{client.base_url}/executions/{slug}"
    console.print(f"[green]>>>[/green] [bold]{slug}[/bold]")
    console.print(f"  [dim]{url}[/dim]")

    if no_stream:
        console.print(f"  [dim]Run[/dim] druids execution status {slug} [dim]to check progress.[/dim]")
        return

    console.print()
    try:
        for event in client.stream_execution(slug):
            line = format_event(event)
            if line is not None:
                console.print(line)
    except KeyboardInterrupt:
        console.print("\n[dim]Detached. Execution still running.[/dim]")
        console.print(f"  [dim]Run[/dim] druids execution status {slug} [dim]to check progress.[/dim]")
        return

    # Fetch final status and show error if the execution failed
    try:
        final = client.get_execution(slug)
        if final.get("error"):
            console.print(f"\n[red]Error:[/red] {final['error']}")
    except Exception:
        pass

    console.print(f"\n[green]---[/green] [dim]done[/dim]")


# ---------------------------------------------------------------------------
# execution (subcommand group: ls, status, stop, send, ssh, connect)
# ---------------------------------------------------------------------------

execution_app = typer.Typer(name="execution", help="Manage running executions.", no_args_is_help=True)
app.add_typer(execution_app, name="execution")


@execution_app.command(name="ls")
def execution_list(
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
        url = f"{client.base_url}/executions/{ex['slug']}"
        console.print(
            f"[{status_color}]{ex['slug']}[/{status_color}] \\[{ex['status']}] "
            f"{ex.get('repo_full_name', '')}{pr}{error_suffix}"
        )
        console.print(f"  [dim]{url}[/dim]")


@execution_app.command(name="status")
def execution_status(
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
    url = f"{client.base_url}/executions/{data['execution_slug']}"
    console.print(f"[bold]Execution:[/bold] {data['execution_slug']}")
    console.print(f"[bold]URL:[/bold] {url}")
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


@execution_app.command(name="activity")
def execution_activity(
    slug: str = typer.Argument(..., help="Execution slug"),
    n: int = typer.Option(50, "--n", "-n", help="Number of recent events"),
    compact: bool = typer.Option(True, "--compact/--full", help="Compact or full output"),
):
    """Show recent activity for an execution."""
    client = get_authenticated_client()

    try:
        data = client.get_execution_activity(slug, n=n, compact=compact)
    except NotFoundError as e:
        print_error(str(e))
        raise typer.Exit(1)
    except APIError as e:
        print_error(f"Error: {e}")
        raise typer.Exit(1)

    url = f"{client.base_url}/executions/{data['execution_slug']}"
    console.print(f"[bold]Execution:[/bold] {data['execution_slug']}")
    console.print(f"[bold]URL:[/bold] {url}")
    console.print(f"[bold]Agents:[/bold] {', '.join(data.get('agents', []))}")
    console.print(f"[bold]Events:[/bold] {data.get('event_count', 0)}")
    console.print()

    for event in data.get("recent_activity", []):
        line = format_event(event)
        if line is not None:
            console.print(line)


@execution_app.command(name="stop")
def execution_stop(
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


@execution_app.command(name="send")
def execution_send(
    slug: str = typer.Argument(..., help="Execution slug"),
    message: str = typer.Argument(..., help="Message to send"),
    agent: str | None = typer.Option(None, "--agent", "-a", help="Agent name (default: first agent)"),
):
    """Send a message to an agent in a running execution."""
    client = get_authenticated_client()

    agent_name = agent or "builder"

    try:
        client.send_agent_message(slug, agent_name, message)
    except NotFoundError as e:
        print_error(str(e))
        raise typer.Exit(1)
    except APIError as e:
        print_error(f"Error: {e}")
        raise typer.Exit(1)

    print_success(f"Message sent to {agent_name}.")


@execution_app.command(name="ssh")
def execution_ssh(
    execution_slug: str = typer.Argument(..., help="Execution slug"),
    agent: str | None = typer.Option(None, "--agent", "-a", help="Agent name (default: root instance)"),
):
    """Open a shell on an execution's VM."""
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

    target = agent or "root"
    console.print(f"Connecting to [bold]{execution_slug}[/bold] ({target}: {username})...")
    console.print(f"[dim]Password (if prompted): {password}[/dim]")
    remote_cmd = "sudo -iu agent"

    try:
        subprocess.run(["ssh"] + ssh_opts + ["-t", f"{username}@{host}", remote_cmd])
    finally:
        os.unlink(key_path)


@execution_app.command(name="connect")
def execution_connect(
    execution_slug: str = typer.Argument(..., help="Execution slug"),
    agent: str | None = typer.Option(None, "--agent", "-a", help="Agent name"),
):
    """Resume an agent's coding session interactively.

    Connects to the execution VM and resumes the agent's Claude Code or
    Codex session so you can continue the conversation.
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

    session_id = creds.get("session_id")
    if not session_id:
        print_error(f"No session ID for agent '{agent}'. The agent may not have connected yet.")
        raise typer.Exit(1)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".pem", delete=False) as f:
        f.write(private_key)
        key_path = f.name
    os.chmod(key_path, 0o600)

    ssh_opts = ["-i", key_path, "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null"]

    backend = creds.get("backend", "claude")
    console.print(f"[green]>>>[/green] Resuming [bold]{execution_slug}[/bold] agent [bold]{agent}[/bold]")
    console.print(f"[dim]Session: {session_id} ({backend})[/dim]")
    console.print(f"[dim]Password (if prompted): {password}[/dim]")

    if backend == "codex":
        remote_cmd = f"cd /home/agent && codex resume {session_id}"
    else:
        remote_cmd = f"cd /home/agent && claude --resume {session_id}"
    remote_cmd = f"sudo -iu agent bash -c '{remote_cmd}'"

    try:
        subprocess.run(["ssh"] + ssh_opts + ["-t", f"{username}@{host}", remote_cmd])
    finally:
        os.unlink(key_path)


# ---------------------------------------------------------------------------
# Agent-internal commands (hidden from top-level --help)
# ---------------------------------------------------------------------------


def _resolve_agent_context() -> tuple[str, str]:
    """Get execution_slug and agent_name from config. Used by agents on the VM."""
    config = get_config()
    if not config.execution_slug or not config.agent_name:
        print_error("Not running inside an agent VM. execution_slug and agent_name not set.")
        raise typer.Exit(1)
    return config.execution_slug, config.agent_name


@app.command(hidden=True)
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


@app.command(name="tool", hidden=True, context_settings={"allow_extra_args": True, "allow_interspersed_args": True})
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


# ---------------------------------------------------------------------------
# Misc commands
# ---------------------------------------------------------------------------


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
