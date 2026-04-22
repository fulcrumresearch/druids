"""Ramure CLI.

Commands talk to a live runtime over its Unix socket at
``~/.ramure/runtimes/{execution_id}.sock``. Finished runs have no
socket; their logs stay in ``~/.ramure/logs/{execution_id}/``.
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import socket
import stat
import tempfile
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


def _live_ids(*, notify_stale: bool = True) -> list[str]:
    """List execution ids whose control socket is actually accepting.

    A ``ControlServer.stop()`` on graceful shutdown unlinks the
    socket path, but a SIGKILL/crash/OOM leaves the socket file
    behind. Subsequent ``connect()`` calls fail with
    ``ECONNREFUSED`` because nothing is listening. If we returned
    every ``*.sock`` we saw, every CLI command (``ls``, ``status``,
    ``send``) would fail with "Runtime unreachable" until the
    operator cleaned up by hand.

    We test-connect each socket; if it refuses, the runtime is
    gone and we unlink the stale file on the spot. When
    ``notify_stale`` is true (the default for interactive CLI use),
    a one-line note is printed to stderr per cleaned socket so
    operators know state was quietly corrected rather than ignored.
    """
    if not SOCKET_DIR.exists():
        return []
    live: list[str] = []
    cleaned: list[str] = []
    for p in sorted(SOCKET_DIR.glob("*.sock")):
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(0.5)
                s.connect(str(p))
            live.append(p.stem)
        except (ConnectionRefusedError, FileNotFoundError, OSError):
            try:
                p.unlink()
                cleaned.append(p.stem)
            except FileNotFoundError:
                pass
    if notify_stale and cleaned:
        label = "socket" if len(cleaned) == 1 else "sockets"
        ids = ", ".join(c[:8] for c in cleaned)
        typer.echo(
            f"note: cleaned {len(cleaned)} stale {label} (no listener): {ids}",
            err=True,
        )
    return live


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
    """Attach to an agent's tmux session.

    For local agents the tmux server is on this host, so we ``tmux
    attach`` directly. For remote agents (e.g. Morph) the tmux session
    lives on the VM; we SSH in and ``tmux attach`` there.
    """
    eid = pick(id_)
    info = call(eid, {"cmd": "agent", "name": agent})
    session = info.get("tmux_session") or f"ramure-{eid}-{agent}"

    creds = call(eid, {"cmd": "ssh_credentials", "name": agent}).get("credentials")
    if creds is None:
        os.execvp("tmux", ["tmux", "attach-session", "-t", session])
        return

    argv = _ssh_argv(
        creds,
        remote_command=f"tmux attach-session -t {shlex.quote(session)}",
        tty=True,
    )
    os.execvp(argv[0], argv)


@app.command("ssh")
def cmd_ssh(agent: str = typer.Argument(...), id_: str = ID_OPT) -> None:
    """Open a shell on an agent's machine.

    For local agents this drops into a shell ``cd``'d to the agent's
    workdir. For remote agents (e.g. Morph) this opens a real SSH
    session using the credentials the backend advertises.
    """
    eid = pick(id_)
    info = call(eid, {"cmd": "agent", "name": agent})
    m = info["machine"]

    creds = call(eid, {"cmd": "ssh_credentials", "name": agent}).get("credentials")
    if creds is None:
        if m.get("kind") != "LocalMachine":
            die(
                f"ssh not supported for machine kind '{m.get('kind')}': "
                "backend exposes no credentials."
            )
        shell = os.environ.get("SHELL", "/bin/bash")
        os.execvp(
            shell,
            [shell, "-c", f"cd {shlex.quote(m.get('workdir', os.getcwd()))} && exec {shell}"],
        )
        return

    argv = _ssh_argv(creds, tty=True)
    os.execvp(argv[0], argv)


def _ssh_argv(
    creds: dict[str, Any],
    *,
    remote_command: str | None = None,
    tty: bool = False,
) -> list[str]:
    """Build an ``ssh`` argv for the given credentials.

    The private key is written to a short-lived 0600 file under
    ``~/.ramure/keys/``. We keep the file around after ``execvp`` --
    ssh needs it for the duration of the session and the process image
    has been replaced, so we can't clean it up from Python. The
    directory is stable per-user and each key file is named by a hash
    of its contents, so repeated connects reuse one file.
    """
    key_dir = Path.home() / ".ramure" / "keys"
    key_dir.mkdir(parents=True, exist_ok=True)
    try:
        key_dir.chmod(0o700)
    except OSError:
        pass
    private_key = creds["private_key"]
    if not private_key.endswith("\n"):
        private_key = private_key + "\n"
    digest = hashlib.sha256(private_key.encode()).hexdigest()[:16]
    key_path = key_dir / f"{digest}.key"
    if not key_path.exists():
        # Write atomically so a concurrent CLI invocation never sees a
        # half-written key.
        fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=str(key_dir))
        try:
            with os.fdopen(fd, "w") as fh:
                fh.write(private_key)
            os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
            os.replace(tmp, key_path)
        except Exception:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass
            raise

    argv: list[str] = [
        "ssh",
        "-i", str(key_path),
        "-p", str(creds["port"]),
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-o", "LogLevel=ERROR",
    ]
    if tty:
        argv.append("-t")
    argv.append(f"{creds['username']}@{creds['host']}")
    if remote_command is not None:
        argv.append(remote_command)
    return argv


def die(msg: str) -> None:
    typer.echo(msg, err=True)
    raise typer.Exit(1)


if __name__ == "__main__":
    app()
