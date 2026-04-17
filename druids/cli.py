"""Druids CLI.

Commands communicate with a live runtime through its Unix socket at
``~/.druids/runtimes/{execution_id}.sock``. Finished runs have no
socket; use their log files directly (``druids logs``).
"""

from __future__ import annotations

import asyncio
import json
import os
import shlex
from pathlib import Path
from typing import Any

import typer

from druids.control import SOCKET_DIR, socket_path


app = typer.Typer(
    name="druids",
    help="Inspect and interact with druids executions.",
    no_args_is_help=True,
    add_completion=False,
)

ID_OPT = typer.Option(None, "--id", "-i", help="Execution id or prefix (default: the only live run).")


# ---------------------------------------------------------------------------
# Socket client
# ---------------------------------------------------------------------------


def live_runs() -> list[str]:
    """Execution ids with a reachable socket."""
    if not SOCKET_DIR.exists():
        return []
    out = []
    for p in sorted(SOCKET_DIR.glob("*.sock")):
        if _reachable(p):
            out.append(p.stem)
    return out


def _reachable(path: Path) -> bool:
    """Cheap liveness check: try to open the socket."""
    import socket

    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        s.connect(str(path))
        return True
    except (FileNotFoundError, ConnectionRefusedError, PermissionError):
        return False
    finally:
        s.close()


def resolve_id(prefix: str | None) -> str:
    """Resolve a run by id prefix; default = the only live one."""
    ids = live_runs()
    if prefix is None:
        if not ids:
            die("No live runs.")
        if len(ids) > 1:
            die("Multiple live runs; pass --id <prefix>:\n" + "\n".join(f"  {i[:8]}" for i in ids))
        return ids[0]
    matches = [i for i in ids if i.startswith(prefix)]
    if not matches:
        die(f"No live run matches '{prefix}'.")
    if len(matches) > 1:
        die(f"Ambiguous prefix '{prefix}':\n" + "\n".join(f"  {i[:8]}" for i in matches))
    return matches[0]


async def _call(execution_id: str, request: dict[str, Any]) -> dict[str, Any]:
    reader, writer = await asyncio.open_unix_connection(str(socket_path(execution_id)))
    writer.write((json.dumps(request) + "\n").encode())
    await writer.drain()
    line = await reader.readline()
    writer.close()
    await writer.wait_closed()
    return json.loads(line) if line else {"error": "empty reply"}


def call(execution_id: str, request: dict[str, Any]) -> dict[str, Any]:
    reply = asyncio.run(_call(execution_id, request))
    if "error" in reply:
        die(reply["error"])
    return reply


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@app.command("ls")
def cmd_ls() -> None:
    """List live runs."""
    ids = live_runs()
    if not ids:
        typer.echo("No live runs.")
        return
    for i in ids:
        reply = call(i, {"cmd": "status"})
        typer.echo(f"{i[:8]}  {reply.get('program', '?')}")


@app.command("status")
def cmd_status(id_: str = ID_OPT) -> None:
    """Show structure of a live run: agents, machines, connections."""
    eid = resolve_id(id_)
    s = call(eid, {"cmd": "status"})

    typer.echo(f"Execution: {s['execution_id']}")
    typer.echo(f"Program:   {s.get('program', '?')}")
    typer.echo(f"PID:       {s.get('pid', '?')}")
    typer.echo(f"Server:    {s.get('server_url', '?')}")

    if s.get("agents"):
        typer.echo("\nAgents:")
        for ag in s["agents"]:
            m = ag["machine"]
            bits = [m.get("kind", "?")]
            if "workdir" in m:
                bits.append(f"workdir={m['workdir']}")
            tmux = f"  tmux={ag['tmux_session']}" if ag.get("tmux_session") else ""
            typer.echo(f"  {ag['name']}  [{' '.join(bits)}]{tmux}")

    if s.get("connections"):
        typer.echo("\nConnections:")
        for c in s["connections"]:
            typer.echo(f"  {c['a']} -> {c['b']}")


@app.command("send")
def cmd_send(
    agent: str = typer.Argument(..., help="Agent name."),
    message: str = typer.Argument(..., help="Message text."),
    id_: str = ID_OPT,
) -> None:
    """Send a message to an agent."""
    eid = resolve_id(id_)
    call(eid, {"cmd": "send", "agent": agent, "text": message})
    typer.echo(f"Sent to {agent}.")


@app.command("connect")
def cmd_connect(agent: str = typer.Argument(...), id_: str = ID_OPT) -> None:
    """Attach to an agent's tmux session."""
    eid = resolve_id(id_)
    info = call(eid, {"cmd": "agent", "name": agent})
    session = info.get("tmux_session") or f"druids-{eid}-{agent}"
    os.execvp("tmux", ["tmux", "attach-session", "-t", session])


@app.command("ssh")
def cmd_ssh(agent: str = typer.Argument(...), id_: str = ID_OPT) -> None:
    """Open a shell on an agent's machine."""
    eid = resolve_id(id_)
    info = call(eid, {"cmd": "agent", "name": agent})
    machine = info["machine"]
    kind = machine.get("kind")
    if kind != "LocalMachine":
        die(f"ssh not supported for machine kind '{kind}' yet.")
    workdir = machine.get("workdir", os.getcwd())
    shell = os.environ.get("SHELL", "/bin/bash")
    os.execvp(shell, [shell, "-c", f"cd {shlex.quote(workdir)} && exec {shell}"])


@app.command("logs")
def cmd_logs(
    id_: str = ID_OPT,
    agent: str | None = typer.Option(None, "--agent", "-a"),
) -> None:
    """Print the path of a log file.

    Works for live runs (via the socket, to get the full id) and finished
    runs (via id prefix match against ``~/.druids/logs``).
    """
    from druids.runtime import DEFAULT_LOG_DIR

    eid = _resolve_any(id_)
    log_dir = DEFAULT_LOG_DIR / eid
    path = log_dir / (f"{agent}.jsonl" if agent else "_runtime.jsonl")
    if not path.exists():
        die(f"No log at {path}")
    typer.echo(str(path))


def _resolve_any(prefix: str | None) -> str:
    """Resolve an id prefix against live sockets OR disk-only runs."""
    from druids.runtime import DEFAULT_LOG_DIR

    live = set(live_runs())
    on_disk = {p.name for p in DEFAULT_LOG_DIR.iterdir()} if DEFAULT_LOG_DIR.exists() else set()
    pool = sorted(live | on_disk)

    if prefix is None:
        live_list = [i for i in pool if i in live]
        if len(live_list) == 1:
            return live_list[0]
        die("Pass --id <prefix>.")
    matches = [i for i in pool if i.startswith(prefix)]
    if not matches:
        die(f"No run matches '{prefix}'.")
    if len(matches) > 1:
        die(f"Ambiguous prefix '{prefix}':\n" + "\n".join(f"  {i[:8]}" for i in matches))
    return matches[0]


# ---------------------------------------------------------------------------


def die(msg: str) -> None:
    typer.echo(msg, err=True)
    raise typer.Exit(1)


if __name__ == "__main__":
    app()
