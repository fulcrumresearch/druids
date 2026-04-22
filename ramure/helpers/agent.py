"""Agent lifecycle helpers: launch, kill, session management."""

from __future__ import annotations

import os
import shlex
import shutil
from typing import TYPE_CHECKING

from ramure.extension import extension_source

if TYPE_CHECKING:
    from ramure.agent import Agent


def agent_extension_path(execution_id: str, agent_name: str) -> str:
    return f"/tmp/ramure-extension-{execution_id}-{agent_name}.ts"


def agent_session_name(execution_id: str, agent_name: str) -> str:
    return f"ramure-{execution_id}-{agent_name}"


def build_agent_launch_command(
    *,
    pi_command: str,
    tmux_command: str,
    extension_path: str,
    env: dict[str, str],
    session_name: str,
) -> str:
    env_prefix = " ".join(
        f"{key}={shlex.quote(value)}" for key, value in env.items()
    )
    pi_invocation = (
        f"env {env_prefix} {shlex.quote(pi_command)} --extension {shlex.quote(extension_path)}"
    )
    return (
        f"{shlex.quote(tmux_command)} has-session -t {shlex.quote(session_name)} 2>/dev/null && "
        f"{shlex.quote(tmux_command)} kill-session -t {shlex.quote(session_name)}; "
        f"{shlex.quote(tmux_command)} new-session -d -s {shlex.quote(session_name)} "
        f"/bin/bash -lc {shlex.quote(pi_invocation)}"
    )


async def launch_agent(agent: Agent, *, server_url: str, execution_id: str) -> str:
    """Write the pi extension and start a tmux session for the agent.

    Returns the tmux session name.
    """
    pi_command = shutil.which("pi")
    tmux_command = shutil.which("tmux")
    if not pi_command or not tmux_command:
        raise RuntimeError("pi and tmux must both be available to launch agents")

    extension_path = agent_extension_path(execution_id, agent.name)
    await agent.machine.write_file(extension_path, extension_source())

    env = {
        "RAMURE_SERVER_URL": server_url,
        "RAMURE_EXECUTION_ID": execution_id,
        "RAMURE_AGENT_ID": agent.name,
        "RAMURE_SYSTEM_PROMPT": agent.system_prompt or "",
    }
    # Pass through the truncation cap from the host env if set; the
    # extension on the VM reads it at startup. Unset = extension's
    # default (16 KiB).
    if os.environ.get("RAMURE_TOOL_RESULT_MAX_BYTES"):
        env["RAMURE_TOOL_RESULT_MAX_BYTES"] = os.environ["RAMURE_TOOL_RESULT_MAX_BYTES"]
    # Forward provider credentials from the host so pi on the remote machine
    # can authenticate. Silently skipped if unset.
    for key in (
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
    ):
        value = os.environ.get(key)
        if value:
            env[key] = value
    session_name = agent_session_name(execution_id, agent.name)
    command = build_agent_launch_command(
        pi_command=pi_command,
        tmux_command=tmux_command,
        extension_path=extension_path,
        env=env,
        session_name=session_name,
    )
    result = await agent.machine.exec(command)
    if not result.ok:
        raise RuntimeError(
            result.stderr.strip()
            or result.stdout.strip()
            or f"Failed to launch agent '{agent.name}'"
        )
    return session_name


async def kill_agent(agent: Agent, *, execution_id: str) -> None:
    """Kill the tmux session for an agent. Best-effort, never raises."""
    session = agent_session_name(execution_id, agent.name)
    try:
        await agent.machine.exec(
            f"tmux kill-session -t {shlex.quote(session)} 2>/dev/null || true",
            timeout=5,
        )
    except Exception:
        pass
