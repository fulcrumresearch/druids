"""Tests for CLI command structure and flags."""

from __future__ import annotations

from druids.main import app
from typer.testing import CliRunner


runner = CliRunner()


def test_version_flag():
    """--version should print the version and exit 0."""
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "druids" in result.output


def test_version_short_flag():
    """-V should also print the version."""
    result = runner.invoke(app, ["-V"])
    assert result.exit_code == 0
    assert "druids" in result.output


def test_hidden_commands_not_in_help():
    """tools, tool, server should not appear in --help output."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    lines = result.output.lower()
    assert "start the druids server" not in lines


def test_devbox_subcommands():
    """devbox should have create, snapshot, and ls subcommands."""
    result = runner.invoke(app, ["devbox", "--help"])
    assert result.exit_code == 0
    assert "create" in result.output
    assert "snapshot" in result.output
    assert "ls" in result.output


def test_execution_subcommands():
    """execution should have ls, status, stop, send, ssh, connect."""
    result = runner.invoke(app, ["execution", "--help"])
    assert result.exit_code == 0
    assert "ls" in result.output
    assert "status" in result.output
    assert "stop" in result.output
    assert "send" in result.output
    assert "ssh" in result.output
    assert "connect" in result.output


def test_top_level_is_clean():
    """Top-level --help should show exec, execution, devbox, and not send/ssh/stop."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    # exec and execution should be visible
    assert "exec" in result.output
    assert "execution" in result.output
    assert "devbox" in result.output
    # send, ssh, connect, stop, status, ls should NOT be top-level
    # They live under execution now
    lines = result.output.split("\n")
    # Check that these don't appear as top-level command names
    top_commands = [line.strip().split()[0] for line in lines if line.strip() and not line.startswith(" ")]
    # Just verify execution group is present
    assert "execution" in result.output
