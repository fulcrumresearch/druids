"""Ramure CLI.

Commands talk to a live runtime over its Unix socket at
``~/.ramure/runtimes/{execution_id}.sock``. Finished runs have no
socket; their logs stay in ``~/.ramure/logs/{execution_id}/``.
"""

from __future__ import annotations

import json
import os
import shlex
import socket
from pathlib import Path
from typing import Any

import typer

from ramure.control import SOCKET_DIR, socket_path


app = typer.Typer(
    name="ramure",
    help="Inspect and interact with ramure executions.",
    no_args_is_help=True,
    add_completion=False,
)

ID_OPT = typer.Option(None, "--id", "-i", help="Execution id or prefix (default: the only live run).")


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def _live_ids() -> list[str]:
    if not SOCKET_DIR.exists():
        return []
    return sorted(p.stem for p in SOCKET_DIR.glob("*.sock"))


def pick(prefix: str | None) -> str:
    """Resolve a live run id by prefix, or the only live run if omitted."""
    ids = [i for i in _live_ids() if prefix is None or i.startswith(prefix)]
    if not ids:
        die(f"No live run matches '{prefix}'." if prefix else "No live runs.")
    if len(ids) > 1:
        label = "Ambiguous prefix" if prefix else "Multiple live runs; pass --id"
        die(f"{label}:\n" + "\n".join(f"  {i[:8]}" for i in ids))
    return ids[0]


# ---------------------------------------------------------------------------
# Socket RPC
# ---------------------------------------------------------------------------


def call(execution_id: str, request: dict[str, Any]) -> dict[str, Any]:
    """Send one request to the runtime's control socket, return the reply."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.connect(str(socket_path(execution_id)))
            s.sendall((json.dumps(request) + "\n").encode())
            reply = _recv_line(s)
    except (FileNotFoundError, ConnectionRefusedError) as exc:
        die(f"Runtime unreachable: {exc}")
    if not reply:
        die("empty reply from runtime")
    data = json.loads(reply)
    if "error" in data:
        die(data["error"])
    return data


def _recv_line(s: socket.socket) -> str:
    buf = bytearray()
    while True:
        chunk = s.recv(4096)
        if not chunk:
            break
        buf.extend(chunk)
        if b"\n" in chunk:
            break
    return buf.decode().rstrip("\n")


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@app.command("ls")
def cmd_ls() -> None:
    """List live runs."""
    ids = _live_ids()
    if not ids:
        typer.echo("No live runs.")
        return
    for i in ids:
        reply = call(i, {"cmd": "status"})
        typer.echo(f"{i[:8]}  {reply.get('program', '?')}")


@app.command("status")
def cmd_status(id_: str = ID_OPT) -> None:
    """Show structure of a live run: agents, machines, connections."""
    s = call(pick(id_), {"cmd": "status"})

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
    call(pick(id_), {"cmd": "send", "agent": agent, "text": message})
    typer.echo(f"Sent to {agent}.")


@app.command("connect")
def cmd_connect(agent: str = typer.Argument(...), id_: str = ID_OPT) -> None:
    """Attach to an agent's tmux session."""
    eid = pick(id_)
    info = call(eid, {"cmd": "agent", "name": agent})
    session = info.get("tmux_session") or f"ramure-{eid}-{agent}"
    os.execvp("tmux", ["tmux", "attach-session", "-t", session])


@app.command("ssh")
def cmd_ssh(agent: str = typer.Argument(...), id_: str = ID_OPT) -> None:
    """Open a shell on an agent's machine."""
    info = call(pick(id_), {"cmd": "agent", "name": agent})
    m = info["machine"]
    if m.get("kind") != "LocalMachine":
        die(f"ssh not supported for machine kind '{m.get('kind')}' yet.")
    shell = os.environ.get("SHELL", "/bin/bash")
    os.execvp(shell, [shell, "-c", f"cd {shlex.quote(m.get('workdir', os.getcwd()))} && exec {shell}"])


def die(msg: str) -> None:
    typer.echo(msg, err=True)
    raise typer.Exit(1)


if __name__ == "__main__":
    app()
