"""Agent lifecycle helpers: launch, kill, session management."""

from __future__ import annotations

import os
import shlex
from typing import TYPE_CHECKING

from ramure.extension import extension_source

if TYPE_CHECKING:
    from ramure.agent import Agent


def agent_extension_path(execution_id: str, agent_name: str) -> str:
    return f"/tmp/ramure-extension-{execution_id}-{agent_name}.ts"


def agent_session_name(execution_id: str, agent_name: str) -> str:
    return f"ramure-{execution_id}-{agent_name}"


async def _resolve_machine_command(agent: Agent, binary: str) -> str:
    """Resolve an executable path on the agent machine.

    Probing the machine avoids assuming that the host's absolute paths exist
    inside a container or VM. Images used for agents must provide ``pi`` and
    ``tmux`` on the agent user's PATH.
    """
    probe = await agent.machine.exec(f"command -v {shlex.quote(binary)}", timeout=10)
    if probe.ok:
        lines = [line.strip() for line in probe.stdout.splitlines() if line.strip()]
        if lines:
            return lines[0]

    detail = probe.stderr.strip() or probe.stdout.strip()
    suffix = f": {detail}" if detail else ""
    raise RuntimeError(
        f"`{binary}` must be available on the agent machine to launch agents{suffix}"
    )


def build_agent_launch_command(
    *,
    pi_command: str,
    tmux_command: str,
    extension_path: str,
    env: dict[str, str],
    session_name: str,
    model: str | None = None,
) -> str:
    env_prefix = " ".join(
        f"{key}={shlex.quote(value)}" for key, value in env.items()
    )
    model_arg = f" --model {shlex.quote(model)}" if model else ""
    pi_invocation = (
        f"env {env_prefix} {shlex.quote(pi_command)}{model_arg} "
        f"--extension {shlex.quote(extension_path)}"
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
    pi_command = await _resolve_machine_command(agent, "pi")
    tmux_command = await _resolve_machine_command(agent, "tmux")

    extension_path = agent_extension_path(execution_id, agent.name)
    await agent.machine.write_file(extension_path, extension_source())

    env = {
        "RAMURE_SERVER_URL": server_url,
        "RAMURE_EXECUTION_ID": execution_id,
        "RAMURE_AGENT_ID": agent.name,
        "RAMURE_SYSTEM_PROMPT": agent.system_prompt or "",
    }
    # Pass through truncation caps from the host env if set; the
    # extension on the VM reads them at startup. Unset = extension defaults.
    if os.environ.get("RAMURE_TOOL_RESULT_MAX_BYTES"):
        env["RAMURE_TOOL_RESULT_MAX_BYTES"] = os.environ["RAMURE_TOOL_RESULT_MAX_BYTES"]
    if os.environ.get("RAMURE_MESSAGE_MAX_BYTES"):
        env["RAMURE_MESSAGE_MAX_BYTES"] = os.environ["RAMURE_MESSAGE_MAX_BYTES"]
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
        model=agent.model,
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
