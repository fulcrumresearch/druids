"""The ``Runtime``: server, agent registry, and tool dispatch.

One ``Runtime`` is created per root ``@agent_process`` invocation. It
owns the WebSocket server that agents connect to, the map of agent
records (keyed by agent name), and the edge set that authorizes
inter-agent messages.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any

from druids.agent import Agent
from druids.context import _current_process
from druids.helpers import agent_session_name, build_tool_definition
from druids.helpers import launch_agent as _launch_agent_impl
from druids.log import Log
from druids.machines import Image, Machine
from druids.server import Server
from druids.types import ToolCallError


class Runtime:
    """Manages the server, agent registry, and tool dispatch.

    Created automatically by the root ``@agent_process``. Exists as
    infrastructure that process scopes share.
    """

    def __init__(self, *, log_dir: Path | str | None = None):
        self.execution_id: str | None = None
        self.server_url: str | None = None
        self.agents: dict[str, Agent] = {}
        self.edges: set[tuple[str, str]] = set()
        self.log_dir_root = Path(log_dir) if log_dir else None
        self.server_instance: Server | None = None

    async def start(self) -> None:
        self.execution_id = str(uuid.uuid4())
        self.server_instance = Server(self)
        await self.server_instance.start()
        self.server_url = f"ws://127.0.0.1:{self.server_instance.port}"

    async def close(self) -> None:
        if self.server_instance is not None:
            await self.server_instance.stop()
            self.server_instance = None
        self.server_url = None
        self.execution_id = None

    # -- Agent creation (called by ambient agent()) --

    async def create_agent(
        self,
        name: str,
        *,
        system_prompt: str | None = None,
        image: Image | None = None,
        machine: Machine | None = None,
    ) -> tuple[Agent, Machine | None]:
        """Create, register, and launch an agent.

        Returns ``(agent, spawned_machine)`` where *spawned_machine* is the
        machine that was created (so the scope can track it), or ``None`` if
        an existing machine was passed in.
        """
        if name in self.agents:
            raise ValueError(f"Agent '{name}' already exists")
        if machine is not None and image is not None:
            raise ValueError("Pass either machine= or image=, not both")

        spawned_machine: Machine | None = None
        resolved_machine = machine
        if resolved_machine is None:
            resolved_machine = await image.spawn()
            spawned_machine = resolved_machine

        log_path: Path | None = None
        if self.log_dir_root is not None and self.execution_id:
            log_path = self.log_dir_root / self.execution_id / f"{name}.jsonl"

        ag = Agent(
            name=name,
            machine=resolved_machine,
            log=Log(path=log_path),
            system_prompt=system_prompt,
        )

        self.agents[name] = ag
        self._register_builtins(ag)
        ag.log.emit("agent_created", {"agent": name})

        try:
            await self.spawn_agent(ag)
        except Exception:
            self.agents.pop(name, None)
            raise

        return ag, spawned_machine

    def _register_builtins(self, ag: Agent) -> None:
        """Register builtin tools (message, send_file, download_file) on an agent."""
        runtime = self

        async def message(receiver: str, message: str) -> str:
            """Send a message to a connected agent."""
            target = runtime.get_agent(receiver)
            runtime.require_connection(ag.name, receiver)
            await target.send(f"[From: {ag.name}] {message}")
            return f"Message sent to {receiver}."

        async def send_file(receiver: str, path: str, dest_path: str = "") -> str:
            """Send a file to a connected agent."""
            dest = dest_path or path
            target = runtime.get_agent(receiver)
            runtime.require_connection(ag.name, receiver)
            content = await ag.machine.read_file(path)
            await target.machine.write_file(dest, content)
            return f"Sent {len(content)} bytes to {receiver}:{dest}."

        async def download_file(sender: str, path: str, dest_path: str = "") -> str:
            """Download a file from a connected agent."""
            dest = dest_path or path
            source = runtime.get_agent(sender)
            runtime.require_connection(sender, ag.name)
            content = await source.machine.read_file(path)
            await ag.machine.write_file(dest, content)
            return f"Downloaded {len(content)} bytes from {sender}:{path} to {dest}."

        ag.handlers["message"] = message
        ag.handlers["send_file"] = send_file
        ag.handlers["download_file"] = download_file

    # -- Connections --

    def connect_agents(self, a: Agent, b: Agent, *, direction: str = "both") -> None:
        if direction not in {"both", "forward"}:
            raise ValueError("direction must be 'both' or 'forward'")
        self.edges.add((a.name, b.name))
        if direction == "both":
            self.edges.add((b.name, a.name))

    # -- Agent spawn / registration --

    async def spawn_agent(self, agent: Agent) -> None:
        if not await self.launch_agent(agent):
            return
        try:
            await asyncio.wait_for(agent.registered.wait(), timeout=120)
        except asyncio.TimeoutError as exc:
            raise RuntimeError(
                f"Agent '{agent.name}' did not register within 120s. "
                f"Check tmux session: {agent_session_name(self.execution_id or '', agent.name)}"
            ) from exc

    async def launch_agent(self, agent: Agent) -> bool:
        server_url = self.server_url
        if server_url is None:
            raise RuntimeError("Server is not running")

        session_name = await _launch_agent_impl(
            agent, server_url=server_url, execution_id=self.execution_id or ""
        )
        agent.log.emit(
            "agent_spawned",
            {"agent": agent.name, "tmux_session": session_name},
        )
        return True

    def register_agent(self, agent_id: str, execution_id: str) -> list[dict[str, Any]]:
        if execution_id != self.execution_id:
            raise ToolCallError("Execution ID mismatch", status_code=400)
        ag = self.get_agent(agent_id)
        ag.registered.set()
        return [
            build_tool_definition(name, fn) for name, fn in ag.handlers.items()
        ]

    # -- Tool call dispatch --

    async def handle_tool_call(
        self, agent_id: str, tool_name: str, params: dict[str, Any]
    ) -> Any:
        ag = self.get_agent(agent_id)
        handler = ag.handlers.get(tool_name)
        if handler is None:
            raise ToolCallError(
                f"Unknown tool '{tool_name}' for agent '{ag.name}'",
                status_code=404,
            )
        token = _current_process.set(ag.scope)
        try:
            return await handler(**params)
        finally:
            _current_process.reset(token)

    # -- Helpers --

    def get_agent(self, name: str) -> Agent:
        ag = self.agents.get(name)
        if ag is None:
            raise ToolCallError(f"Unknown agent '{name}'", status_code=404)
        return ag

    def require_connection(self, sender: str, receiver: str) -> None:
        if (sender, receiver) not in self.edges:
            raise ToolCallError(
                f"Agent '{sender}' is not connected to '{receiver}'", status_code=403
            )
