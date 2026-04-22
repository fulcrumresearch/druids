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
