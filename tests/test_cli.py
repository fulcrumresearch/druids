"""Tests for ramure's CLI discovery helpers.

These exercise ``_live_ids`` directly because the full CLI is a
Typer app -- easier to unit-test the helpers than shell out.
"""

from __future__ import annotations

import asyncio
import os
import stat
import tempfile
from pathlib import Path

import pytest
import typer

from ramure import cli as ramure_cli


@pytest.fixture
def isolated_socket_dir(monkeypatch):
    # Unix socket paths cap at ~108 bytes, so we need a short tempdir.
    d = Path(tempfile.mkdtemp(prefix="ramure-cli-test-"))
    monkeypatch.setattr(ramure_cli, "SOCKET_DIR", d)
    yield d
    import shutil
    shutil.rmtree(d, ignore_errors=True)


def test_live_ids_empty_when_no_socket_dir(tmp_path, monkeypatch):
    missing = tmp_path / "does-not-exist"
    monkeypatch.setattr(ramure_cli, "SOCKET_DIR", missing)
    assert ramure_cli._live_ids() == []


def test_live_ids_unlinks_stale_sockets(isolated_socket_dir, capsys):
    """A ``.sock`` file with nothing listening on it must be removed
    on read; otherwise every CLI command fails with "Runtime
    unreachable" after any crash.
    """
    stale = isolated_socket_dir / "abc12345.sock"
    stale.touch()  # empty file, nothing listening

    assert ramure_cli._live_ids() == []
    assert not stale.exists(), "stale socket file should be removed"

    # We warn on stderr so the operator sees that state was cleaned.
    captured = capsys.readouterr()
    assert "stale" in captured.err
    assert "abc12345"[:8] in captured.err


def test_live_ids_silent_when_notify_stale_false(isolated_socket_dir, capsys):
    """Callers can opt out of the stale-cleanup note (e.g. if they
    want to suppress it in a scripted context).
    """
    stale = isolated_socket_dir / "deadbeef.sock"
    stale.touch()

    assert ramure_cli._live_ids(notify_stale=False) == []
    assert not stale.exists()
    assert capsys.readouterr().err == ""


def test_live_ids_groups_stale_ids_in_one_note(isolated_socket_dir, capsys):
    """Multiple stale sockets produce one line, not one per file --
    avoids a wall of noise after a crash with many runtimes.
    """
    for n in ("aaaaaaaa", "bbbbbbbb", "cccccccc"):
        (isolated_socket_dir / f"{n}.sock").touch()

    assert ramure_cli._live_ids() == []
    err = capsys.readouterr().err
    # One line, mentioning the count, plural noun, and all three ids.
    assert err.count("\n") == 1
    assert "3 stale sockets" in err
    for n in ("aaaaaaaa", "bbbbbbbb", "cccccccc"):
        assert n in err


def test_live_ids_returns_live_and_removes_stale(isolated_socket_dir):
    """A live socket must be reported; a stale one alongside it must
    still be removed."""

    stale = isolated_socket_dir / "aaaaaa.sock"
    stale.touch()

    # Stand up a real unix-domain server on the other path.
    live_path = isolated_socket_dir / "bbbbbb.sock"

    async def _serve():
        async def _echo(r, w):
            w.close()
            await w.wait_closed()
        return await asyncio.start_unix_server(_echo, path=str(live_path))

    server = asyncio.run(_serve())
    try:
        ids = ramure_cli._live_ids()
    finally:
        server.close()
        asyncio.run(server.wait_closed())

    assert "bbbbbb" in ids
    assert "aaaaaa" not in ids
    assert not stale.exists()


# ---------------------------------------------------------------------------
# _ssh_argv
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_home(monkeypatch, tmp_path):
    """Point ``Path.home()`` at a tempdir so the key file ends up
    somewhere we can inspect and delete, not in the real ``~/.ramure``.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    return tmp_path


CREDS = {
    "host": "ssh.cloud.morph.so",
    "port": 22,
    "username": "inst_abc",
    "private_key": "-----BEGIN KEY-----\ntest\n-----END KEY-----\n",
    "password": None,
}


def test_ssh_argv_writes_key_with_0600_and_builds_argv(isolated_home):
    argv = ramure_cli._ssh_argv(CREDS, tty=True)

    # argv[0] is the program for execvp.
    assert argv[0] == "ssh"
    # Target appears as user@host and is the penultimate or final arg.
    assert f"{CREDS['username']}@{CREDS['host']}" in argv
    assert "-p" in argv and str(CREDS["port"]) in argv
    assert "-t" in argv  # tty requested
    # Hostkey checking is disabled -- these VMs rotate.
    assert "StrictHostKeyChecking=no" in argv
    assert "UserKnownHostsFile=/dev/null" in argv

    # -i <path> must point at a real 0600 file containing the key.
    i_idx = argv.index("-i")
    key_path = Path(argv[i_idx + 1])
    assert key_path.exists()
    mode = stat.S_IMODE(key_path.stat().st_mode)
    assert mode == 0o600, f"expected 0600, got {oct(mode)}"
    assert key_path.read_text() == CREDS["private_key"]

    # Key directory itself should be 0700.
    key_dir = key_path.parent
    assert key_dir == isolated_home / ".ramure" / "keys"
    assert stat.S_IMODE(key_dir.stat().st_mode) == 0o700


def test_ssh_argv_appends_remote_command(isolated_home):
    argv = ramure_cli._ssh_argv(
        CREDS, remote_command="tmux attach-session -t foo", tty=True
    )
    # The remote command must be the very last arg so ssh passes it
    # through verbatim; any earlier and ssh treats it as an option.
    assert argv[-1] == "tmux attach-session -t foo"


def test_ssh_argv_reuses_key_file_across_calls(isolated_home):
    argv1 = ramure_cli._ssh_argv(CREDS)
    argv2 = ramure_cli._ssh_argv(CREDS)
    p1 = Path(argv1[argv1.index("-i") + 1])
    p2 = Path(argv2[argv2.index("-i") + 1])
    # Identical creds -> same hash -> same cached file on disk.
    assert p1 == p2
    # And only one file in the keys dir.
    keys_dir = isolated_home / ".ramure" / "keys"
    assert [p.name for p in sorted(keys_dir.iterdir()) if not p.name.startswith(".tmp-")] == [p1.name]


def test_ssh_argv_distinct_keys_for_distinct_credentials(isolated_home):
    other = dict(CREDS, private_key="-----BEGIN KEY-----\nOTHER\n")
    argv1 = ramure_cli._ssh_argv(CREDS)
    argv2 = ramure_cli._ssh_argv(other)
    p1 = Path(argv1[argv1.index("-i") + 1])
    p2 = Path(argv2[argv2.index("-i") + 1])
    assert p1 != p2
    assert p1.read_text() == CREDS["private_key"]
    assert p2.read_text() == other["private_key"]


def test_ssh_argv_adds_trailing_newline_if_missing(isolated_home):
    # Some SDKs hand us a key without a trailing newline; ssh is picky.
    creds = dict(CREDS, private_key="-----BEGIN KEY-----\nnoeol")
    argv = ramure_cli._ssh_argv(creds)
    key_path = Path(argv[argv.index("-i") + 1])
    assert key_path.read_text().endswith("\n")


def test_ssh_argv_omits_tty_flag_when_not_requested(isolated_home):
    argv = ramure_cli._ssh_argv(CREDS)
    assert "-t" not in argv


# ---------------------------------------------------------------------------
# _remote_tmux_attach / _remote_login_shell / _guard_finished
# ---------------------------------------------------------------------------


def test_remote_tmux_attach_sudos_to_agent_user():
    """Agent tmux sessions live on the ``agent`` user's tmux socket;
    attaching as root (the Morph SSH login user) sees an empty socket.
    The CLI hardcodes ``sudo -u agent -- tmux attach ...`` so the
    socket resolves under agent's uid.
    """
    cmd = ramure_cli._remote_tmux_attach("ramure-x-worker")
    assert cmd == "sudo -u agent -- tmux attach-session -t ramure-x-worker"


def test_remote_tmux_attach_session_name_is_quoted():
    """Session names come from runtime state; shell-quote so a
    weird name (spaces, semicolons) can never splice into the
    remote shell.
    """
    cmd = ramure_cli._remote_tmux_attach("x; rm -rf /")
    assert cmd == "sudo -u agent -- tmux attach-session -t 'x; rm -rf /'"


def test_remote_login_shell_returns_none_when_ssh_already_agent():
    # If the backend's SSH login user is already ``agent``, there's
    # nothing to switch -- drop straight into the shell.
    creds = dict(CREDS, username="agent")
    assert ramure_cli._remote_login_shell(creds) is None


def test_remote_login_shell_sudos_to_agent_by_default():
    # Morph logs in as the instance id (-> root); we want a login
    # shell as ``agent`` so $HOME / PATH / env match where the
    # agent itself ran.
    assert ramure_cli._remote_login_shell(CREDS) == "sudo -iu agent"


def test_guard_finished_allows_live_agent(capsys):
    # ``alive: True`` -> no-op.
    ramure_cli._guard_finished(
        {"name": "w", "alive": True}, "w", force=False, action="ssh"
    )
    # Missing ``alive`` (older runtimes) also treated as live: we
    # don't want a runtime upgrade to silently break ssh/connect.
    ramure_cli._guard_finished(
        {"name": "w"}, "w", force=False, action="ssh"
    )
    assert capsys.readouterr().err == ""


def test_guard_finished_blocks_ended_agent_without_force(capsys):
    with pytest.raises(typer.Exit):
        ramure_cli._guard_finished(
            {"name": "w", "alive": False, "outcome": "timeout"},
            "w",
            force=False,
            action="ssh",
        )
    err = capsys.readouterr().err
    assert "has ended" in err
    assert "timeout" in err
    assert "--force" in err
    assert "ssh" in err


def test_guard_finished_allows_ended_agent_with_force(capsys):
    # Force = explicit opt-in to debugging a corpse. Must not die.
    ramure_cli._guard_finished(
        {"name": "w", "alive": False, "outcome": "done"},
        "w",
        force=True,
        action="ssh",
    )
    assert capsys.readouterr().err == ""


# ---------------------------------------------------------------------------
# _parse_kv_args / _format_endpoint_signature  (used by `ramure call` /
# `ramure status`)
# ---------------------------------------------------------------------------


def test_parse_kv_args_handles_typed_values():
    """JSON-first parsing means ``count=3`` is an int, ``flag=true`` is
    a bool, ``tags=["a","b"]`` is a list -- without a `--type` flag
    per arg. Anything that isn't valid JSON is left as a raw string.
    """
    parsed = ramure_cli._parse_kv_args([
        "count=3",
        "flag=true",
        'tags=["a","b"]',
        "spec=hello world",     # invalid JSON -> string fallback
        'msg="quoted"',         # quoted JSON string -> unquoted str
        "obj={\"k\": 1}",
    ])
    assert parsed == {
        "count": 3,
        "flag": True,
        "tags": ["a", "b"],
        "spec": "hello world",
        "msg": "quoted",
        "obj": {"k": 1},
    }


def test_parse_kv_args_rejects_missing_eq(capsys):
    """A typo like ``ramure call add 3`` should die with a clear
    error, not silently treat ``3`` as a flag-shaped no-op.
    """
    with pytest.raises(typer.Exit):
        ramure_cli._parse_kv_args(["justakey"])
    assert "key=value" in capsys.readouterr().err


def test_parse_kv_args_rejects_empty_key(capsys):
    with pytest.raises(typer.Exit):
        ramure_cli._parse_kv_args(["=oops"])
    assert "empty key" in capsys.readouterr().err


def test_format_endpoint_signature_renders_required_and_default():
    """The signature line in `ramure status` should read like Python:
    required positional first, optional with their defaults. Reused
    here to make sure we don't regress the affordance display when
    we touch the schema.
    """
    sig = ramure_cli._format_endpoint_signature(
        {
            "name": "add_task",
            "parameters": {
                "properties": {
                    "spec": {"type": "string"},
                    "priority": {"type": "integer", "default": 0},
                },
                "required": ["spec"],
            },
        }
    )
    assert sig == "add_task(spec: string, priority: integer = 0)"


def test_format_endpoint_signature_handles_no_params():
    sig = ramure_cli._format_endpoint_signature(
        {"name": "tasks", "parameters": {"properties": {}, "required": []}}
    )
    assert sig == "tasks()"


# ---------------------------------------------------------------------------
# _render_status_md and helpers
# ---------------------------------------------------------------------------


def test_render_status_md_full_digest():
    """The markdown digest is what an operator-agent reads to act on
    the run. Pin its shape so changes are deliberate: section
    headers, summary, signatures, call pairing, and the trailing
    "how to interact" footer all need to land.
    """
    status = {
        "execution_id": "abcdef0123",
        "program": "/tmp/demo.py",
        "started_at": 1_700_000_000.0,
        "pid": 4242,
        "summary": "Toy task queue.",
        "agents": [
            {
                "name": "alpha",
                "machine": {"kind": "LocalMachine", "workdir": "/work"},
                "tmux_session": "ramure-abcdef01-alpha",
            }
        ],
        "connections": [{"a": "alpha", "b": "beta"}],
    }
    endpoints = [
        {
            "name": "add_task",
            "description": "Append a task.\nMore detail.",
            "parameters": {
                "properties": {"spec": {"type": "string"}},
                "required": ["spec"],
            },
        }
    ]
    entries = [
        {"type": "agent_created", "data": {"agent": "alpha"}, "ts": 1_700_000_001.0, "seq": 1},
        {"type": "endpoint_called", "data": {"endpoint": "add_task", "kwargs": {"spec": "x"}, "caller": "external:cli"}, "ts": 1_700_000_002.0, "seq": 2},
        {"type": "endpoint_returned", "data": {"endpoint": "add_task", "caller": "external:cli", "ok": True}, "ts": 1_700_000_003.0, "seq": 3},
    ]

    out = ramure_cli._render_status_md(status, endpoints, entries)

    # Header + metadata.
    assert out.startswith("# ramure execution abcdef0123")
    assert "- Program: /tmp/demo.py" in out
    assert "- PID: 4242" in out

    # Summary block exists when summary is set.
    assert "## Summary" in out
    assert "Toy task queue." in out

    # Affordance signature + first-line docstring.
    assert "## Affordances" in out
    assert "`add_task(spec: string)`" in out
    assert "Append a task." in out
    assert "More detail." not in out  # only the first line

    # Agents + connections from the status payload.
    assert "`alpha`" in out
    assert "workdir=/work" in out
    assert "alpha -> beta" in out

    # Recent endpoint calls section pairs called+returned.
    assert "Recent endpoint calls" in out
    assert "add_task(spec='x')" in out
    assert "by external:cli -> ok" in out

    # Recent events section excludes endpoint_* (those go in the
    # calls section) but keeps lifecycle events.
    assert "Recent events" in out
    assert "agent_created alpha" in out

    # Footer points the reader at the live CLI -- this is the part
    # that makes the file useful as an LLM brief.
    assert "## How to interact" in out
    assert "ramure status -i abcdef01" in out
    assert "ramure call <endpoint>" in out


def test_render_status_md_omits_empty_sections():
    """No summary, no endpoints, no agents -> the digest still
    renders cleanly with explicit "none yet" placeholders rather
    than trailing whitespace.
    """
    status = {"execution_id": "x" * 10, "started_at": 0, "program": "p", "pid": 1}
    out = ramure_cli._render_status_md(status, [], [])
    assert "## Summary" not in out
    assert "_(no endpoints exposed)_" in out
    assert "_(none yet)_" in out
    assert "Recent endpoint calls" not in out
    assert "Recent events" not in out


def test_format_recent_calls_pairs_call_with_return():
    entries = [
        {"type": "endpoint_called", "data": {"endpoint": "f", "kwargs": {"a": 1}, "caller": "external:cli"}, "ts": 1.0, "seq": 1},
        {"type": "endpoint_returned", "data": {"endpoint": "f", "caller": "external:cli", "ok": False, "error": "boom"}, "ts": 2.0, "seq": 2},
        {"type": "endpoint_called", "data": {"endpoint": "g", "kwargs": {}, "caller": "external:cli"}, "ts": 3.0, "seq": 3},
        # No matching return for g -> still in flight.
    ]
    rendered = ramure_cli._format_recent_calls(entries)
    # Most recent first.
    assert rendered[0].endswith("g() by external:cli -> ...")
    assert "f(a=1) by external:cli -> error: boom" in rendered[1]


def test_guard_finished_omits_outcome_clause_when_unknown(capsys):
    with pytest.raises(typer.Exit):
        ramure_cli._guard_finished(
            {"name": "w", "alive": False},
            "w",
            force=False,
            action="connect",
        )
    err = capsys.readouterr().err
    # No "(outcome: ...)" clause when outcome is missing.
    assert "outcome" not in err.lower()
    assert "connect" in err
