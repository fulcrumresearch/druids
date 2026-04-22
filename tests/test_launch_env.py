"""Test that host-side env vars we care about propagate into the
tmux/pi launch command.

We exercise the Python side only -- the extension.ts reads these
at startup, which is covered by integration / factory-2 runs.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ramure.agent import Agent
from ramure.helpers.agent import build_agent_launch_command, launch_agent
from ramure.log import Log
from ramure.machines.local import LocalMachine


def _fake_agent() -> Agent:
    return Agent(
        name="t",
        machine=LocalMachine(workdir="/tmp"),
        log=Log(),
    )


def test_tool_result_max_bytes_propagates_from_host_env(monkeypatch, tmp_path):
    """If the factory / runner sets ``RAMURE_TOOL_RESULT_MAX_BYTES``
    on the host, it should reach the agent's pi session via the tmux
    launch command."""
    monkeypatch.setenv("RAMURE_TOOL_RESULT_MAX_BYTES", "4096")

    # Build the env dict we'd pass (mirror launch_agent without
    # actually needing pi/tmux available).
    import os
    env = {
        "RAMURE_SERVER_URL": "ws://example/test",
        "RAMURE_EXECUTION_ID": "eid",
        "RAMURE_AGENT_ID": "t",
        "RAMURE_SYSTEM_PROMPT": "",
    }
    if os.environ.get("RAMURE_TOOL_RESULT_MAX_BYTES"):
        env["RAMURE_TOOL_RESULT_MAX_BYTES"] = os.environ["RAMURE_TOOL_RESULT_MAX_BYTES"]

    cmd = build_agent_launch_command(
        pi_command="/usr/bin/pi",
        tmux_command="/usr/bin/tmux",
        extension_path="/tmp/ext.ts",
        env=env,
        session_name="ramure-eid-t",
    )
    assert "RAMURE_TOOL_RESULT_MAX_BYTES=4096" in cmd


def test_tool_result_max_bytes_absent_when_host_env_unset(monkeypatch):
    """Unset on the host => unset in the command. Extension.ts picks
    its own default (16 KiB)."""
    monkeypatch.delenv("RAMURE_TOOL_RESULT_MAX_BYTES", raising=False)

    import os
    env = {
        "RAMURE_SERVER_URL": "ws://example/test",
        "RAMURE_EXECUTION_ID": "eid",
        "RAMURE_AGENT_ID": "t",
        "RAMURE_SYSTEM_PROMPT": "",
    }
    if os.environ.get("RAMURE_TOOL_RESULT_MAX_BYTES"):
        env["RAMURE_TOOL_RESULT_MAX_BYTES"] = os.environ["RAMURE_TOOL_RESULT_MAX_BYTES"]

    cmd = build_agent_launch_command(
        pi_command="/usr/bin/pi",
        tmux_command="/usr/bin/tmux",
        extension_path="/tmp/ext.ts",
        env=env,
        session_name="ramure-eid-t",
    )
    assert "RAMURE_TOOL_RESULT_MAX_BYTES" not in cmd
