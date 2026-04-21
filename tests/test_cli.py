"""Tests for ramure's CLI discovery helpers.

These exercise ``_live_ids`` directly because the full CLI is a
Typer app -- easier to unit-test the helpers than shell out.
"""

from __future__ import annotations

import asyncio
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
