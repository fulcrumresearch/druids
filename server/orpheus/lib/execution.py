"""Execution - runtime that manages programs and agent connections."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from string import Template
from typing import TYPE_CHECKING
from uuid import UUID

from orpheus.config import settings
from orpheus.db.models.execution import update_execution
from orpheus.db.session import get_session

from . import execution_trace
from .connection import AgentConnection
from .machine import Machine
from .program import Program


if TYPE_CHECKING:
    from .agents.base import Agent

logger = logging.getLogger(__name__)


@dataclass
class ExposedService:
    """A port exposed as a public HTTPS URL on an agent's VM."""

    agent_name: str
    service_name: str
    port: int
    url: str


@dataclass
class Execution:
    """Runtime that holds and manages programs."""

    id: UUID  # matches ExecutionRecord.id
    slug: str  # "gentle-nocturne-claude" (for routing, display)
    root: Program
    user_id: str  # for trace file namespacing
    devbox_machine: Machine | None = None
    mcp_url: str | None = None
    mcp_auth_token: str | None = None  # Auth token for MCP calls
    task_id: UUID | None = None  # Reference to persisted Task UUID
    repo_full_name: str | None = None  # GitHub repo (owner/repo) for git operations
    git_branch: str | None = None  # Branch to checkout after VM provision
    task_spec: str | None = None  # Task description for $spec template variable

    # All programs indexed by name
    programs: dict[str, Program] = field(default_factory=dict)

    # Connections for agents (agent name -> connection)
    connections: dict[str, AgentConnection] = field(default_factory=dict)

    # Exposed HTTP services (from expose_port calls)
    exposed_services: list[ExposedService] = field(default_factory=list)

    # Event queue for incoming agent events
    _events: asyncio.Queue = field(default_factory=asyncio.Queue)

    # Lifecycle status: "created", "starting", "running", "submitted", "stopped", "error"
    status: str = "created"
    status_error: str | None = None

    # Submit signal - set when agent calls the `submit` MCP tool
    _done: asyncio.Event = field(default_factory=asyncio.Event)
    submit_summary: str | None = None
    submit_pr_url: str | None = None

    @property
    def done(self) -> bool:
        """Whether the execution has been submitted or otherwise finished."""
        return self._done.is_set()

    async def submit(self, pr_url: str | None = None, summary: str | None = None) -> None:
        """Mark execution as submitted (called by the `submit` MCP tool)."""
        self.status = "submitted"
        self.submit_pr_url = pr_url
        self.submit_summary = summary
        self._done.set()
        execution_trace.submitted(self.user_id, self.slug, pr_url, summary)

    def resume(self) -> None:
        """Resume a submitted execution (e.g. to address PR feedback)."""
        if self.status not in ("submitted", "running"):
            raise RuntimeError(f"Cannot resume execution in status '{self.status}'")
        if self.status == "submitted":
            self.status = "running"
            self._done.clear()

    async def start(self) -> None:
        """Start execution from root."""
        self.status = "starting"
        logger.info("Execution.start slug=%s root=%s", self.slug, self.root.name)
        devbox_snapshot = self.devbox_machine.snapshot_id if self.devbox_machine else None
        execution_trace.started(self.user_id, self.slug, str(self.task_id) if self.task_id else None, devbox_snapshot)
        try:
            await self.run_program(self.root)
            self.status = "running"
            logger.info("Execution.start slug=%s completed, status=running", self.slug)
        except Exception as e:
            self.status = "error"
            self.status_error = repr(e)
            self._done.set()
            logger.error("Execution.start slug=%s failed: %s", self.slug, repr(e))
            raise

    def _template_vars(self, agent: Agent) -> dict[str, str]:
        """Build template variables for substitution into system_prompt and user_prompt."""
        tvars = {
            "execution_slug": self.slug,
            "agent_name": agent.name,
            "working_directory": agent.working_directory,
            "branch_name": f"orpheus/{self.slug}",
        }
        if self.task_spec:
            tvars["spec"] = self.task_spec
        return tvars

    async def _provision_machine(self, program: Agent) -> Machine:
        """Provision a Machine for an agent program.

        Uses _fork_source if set (for instance_source="fork" agents), otherwise
        the devbox machine. create_child tries to fork the source sandbox first
        and falls back to starting fresh from the snapshot.
        """
        source = (
            program._fork_source
            if (program.instance_source == "fork" and program._fork_source)
            else self.devbox_machine
        )
        if not source:
            raise ValueError("No machine source for provisioning")

        metadata = {"orpheus:agent_name": program.name}
        child = await source.create_child(
            metadata=metadata,
            repo_full_name=self.repo_full_name,
            git_branch=self.git_branch,
        )
        await child.git_pull(program.working_directory)
        return child

    async def _write_cli_config(self, machine: Machine) -> None:
        """Write ~/.orpheus/config.json so agents can use the orpheus CLI."""
        if not self.mcp_auth_token:
            return
        import json

        config = json.dumps({"base_url": str(settings.base_url), "user_access_token": self.mcp_auth_token})
        await machine.exec("mkdir -p /home/agent/.orpheus", user="root")
        await machine.exec(f"cat > /home/agent/.orpheus/config.json << 'CLIEOF'\n{config}\nCLIEOF", user="root")
        await machine.exec("chown -R agent:agent /home/agent/.orpheus", user="root")

    async def run_program(self, program: Program) -> None:
        """Run an existing program - exec + connect."""
        self.programs[program.name] = program

        # Inject runtime state into every agent
        if program.is_agent:
            program.repo_full_name = self.repo_full_name
            program.git_branch = self.git_branch

            from orpheus.lib.agents.claude import ClaudeAgent
            from orpheus.lib.forwarding_tokens import mint_token

            if isinstance(program, ClaudeAgent):
                token = mint_token(self.user_id, self.slug, program.name)
                program.config.env["ANTHROPIC_API_KEY"] = token
                program.config.env["ANTHROPIC_BASE_URL"] = f"{settings.base_url}/api/proxy/anthropic"

            # Template runtime values into prompts before exec (CodexAgent writes
            # system_prompt to config during exec, so this must happen first)
            tvars = self._template_vars(program)
            if program.system_prompt:
                program.system_prompt = Template(program.system_prompt).safe_substitute(tvars)
            if program.user_prompt:
                program.user_prompt = Template(program.user_prompt).safe_substitute(tvars)

        # Execute the program
        if program.is_agent:
            machine = await self._provision_machine(program)
            await self._write_cli_config(machine)
            new_programs = await program.exec(machine)
        else:
            new_programs = await program.exec()

        # Log program added
        program_type = "agent" if program.is_agent else "program"
        instance_id = program.machine.instance_id if program.is_agent and program.machine else None
        execution_trace.program_added(self.user_id, self.slug, program.name, program_type, instance_id)

        # If it's an agent, connect and give MCP access
        if program.is_agent:
            await self._connect_agent(program)

        # Run any spawned programs
        for p in new_programs:
            await self.run_program(p)

    async def _connect_agent(self, agent: Agent) -> None:
        """Connect to an agent's bridge and initialize ACP session with MCP."""
        if agent.name in self.connections:
            return

        if not agent.machine or not agent.machine.bridge_id or not agent.machine.bridge_token:
            raise RuntimeError(f"Agent {agent.name} has no bridge relay identity")

        bridge_id = agent.machine.bridge_id
        logger.info("Connecting to agent '%s' via relay id %s", agent.name, bridge_id)

        conn = AgentConnection(bridge_id, agent.machine.bridge_token)

        # Set up event handlers
        self._setup_handlers(agent.name, conn)

        # Initialize connection (codex-acp needs authentication)
        auth_method = "openai-api-key" if agent.config.command == "codex-acp" else None
        await conn.start(auth_method=auth_method)

        # Build MCP servers list
        mcp_servers = []
        if self.mcp_url:
            mcp_config = {"name": "orpheus-mcp", "url": self.mcp_url}
            headers = {"X-Execution-Slug": self.slug, "X-Agent-Name": agent.name}
            if self.mcp_auth_token:
                headers["Authorization"] = f"Bearer {self.mcp_auth_token}"
            mcp_config["headers"] = headers
            mcp_servers.append(mcp_config)
            logger.info(f"Agent '{agent.name}' MCP: orpheus-mcp -> {self.mcp_url}")
        else:
            logger.warning(f"No mcp_url set, agent '{agent.name}' won't have MCP access")

        # Add any additional MCP servers from agent config
        if agent.config.mcp_servers:
            for name, config in agent.config.mcp_servers.items():
                mcp_servers.append({"name": name, **config})
                logger.info(f"Agent '{agent.name}' additional MCP: {name}")

        logger.info(f"Creating session for '{agent.name}' with {len(mcp_servers)} MCP server(s)")
        await conn.new_session(
            cwd=agent.working_directory,
            mcp_servers=mcp_servers if mcp_servers else None,
            system_prompt=agent.system_prompt,
        )

        # Set the model via ACP RPC if the agent specifies one (e.g. ClaudeAgent.model).
        # This must happen after new_session because the ACP session needs to exist first.
        model_id = getattr(agent, "model", None)
        if model_id:
            await conn.set_model(model_id)

        self.connections[agent.name] = conn
        execution_trace.agent_connected(self.user_id, self.slug, agent.name, conn.session_id)
        logger.info(f"Agent '{agent.name}' connected (session={conn.session_id})")

        # Send init prompt if configured (fire-and-forget, don't block on response)
        if agent.user_prompt:
            logger.info(f"Sending init prompt to '{agent.name}'")
            execution_trace.prompt(self.user_id, self.slug, agent.name, agent.user_prompt)
            asyncio.create_task(conn.prompt(agent.user_prompt))

    async def _disconnect_agent(self, agent_name: str) -> None:
        """Disconnect from an agent."""
        if agent_name in self.connections:
            conn = self.connections[agent_name]
            execution_trace.agent_disconnected(self.user_id, self.slug, agent_name)
            await conn.close()
            del self.connections[agent_name]

    def _setup_handlers(self, agent_name: str, conn: AgentConnection) -> None:
        """Set up notification handlers for an agent connection."""
        tool_titles: dict[str, str] = {}

        async def on_session_update(params: dict) -> None:
            update = params.get("update", {})
            session_update = update.get("sessionUpdate")

            if session_update == "agent_message_chunk":
                # Text response chunk
                content = update.get("content", {})
                if content.get("type") == "text":
                    text = content.get("text", "")
                    if text:
                        execution_trace.response_chunk(self.user_id, self.slug, agent_name, text)

            elif session_update == "tool_call":
                # Tool call started
                tool_call_id = update["toolCallId"]
                title = update["title"]
                raw_input = update.get("rawInput", {})
                tool_titles[tool_call_id] = title
                execution_trace.tool_use(self.user_id, self.slug, agent_name, title, raw_input)

            elif session_update == "tool_call_update":
                # Tool call progress/result
                status = update.get("status")
                if status == "completed":
                    tool_call_id = update["toolCallId"]
                    title = tool_titles.pop(tool_call_id)
                    raw_output = update.get("rawOutput")
                    execution_trace.tool_result(self.user_id, self.slug, agent_name, title, raw_output)

            await self._events.put((agent_name, {"type": "session_update", **params}))

        conn.on("session/update", on_session_update)

    async def _ensure_agent_reachable(self, agent_name: str) -> None:
        """Resume the agent's VM if paused and reconnect if the connection was lost.

        Machine.exec() handles transparent resume internally, so we just need to
        probe the VM and reconnect if the SSE connection dropped.
        """
        program = self.programs.get(agent_name)
        if not program or not program.is_agent or not program.machine:
            return

        # Probe the VM to trigger transparent resume if paused
        try:
            await program.machine.exec("true", check=False)
        except Exception:
            logger.warning("Machine for agent '%s' is unreachable", agent_name)
            return

        # Check if the SSE reader task is dead; if so, reconnect
        conn = self.connections.get(agent_name)
        if conn and conn._reader._task and conn._reader._task.done():
            logger.info("SSE reader for agent '%s' is dead, reconnecting", agent_name)
            await self._disconnect_agent(agent_name)
            await asyncio.sleep(2)
            await self._connect_agent(program)
        elif agent_name not in self.connections:
            logger.info("Reconnecting to agent '%s' after connection loss", agent_name)
            await self._connect_agent(program)

    async def send(self, sender: str, receiver: str, content: str) -> None:
        """Send message to an agent via its connection.

        Fire-and-forget: delivers the message and returns immediately without
        waiting for the receiver to finish processing. This avoids blocking the
        sender for the duration of the receiver's LLM turn (which can be 60s+).
        """
        if receiver not in self.programs:
            raise ValueError(f"Unknown agent: {receiver}")

        conn = self.connections.get(receiver)
        if not conn:
            # Only do the expensive reachability check if we have no connection
            await self._ensure_agent_reachable(receiver)
            conn = self.connections.get(receiver)
            if not conn:
                raise RuntimeError(f"Agent {receiver} not connected after resume attempt")
        formatted = f"[From: {sender}] {content}"
        execution_trace.prompt(self.user_id, self.slug, receiver, formatted)
        await conn.prompt_nowait(formatted)

    async def prompt(self, agent_name: str, text: str) -> None:
        """Send a prompt directly to an agent."""
        await self._ensure_agent_reachable(agent_name)
        conn = self.connections.get(agent_name)
        if not conn:
            raise RuntimeError(f"Agent {agent_name} not connected after resume attempt")
        execution_trace.prompt(self.user_id, self.slug, agent_name, text)
        await conn.prompt(text)

    async def spawn(self, spawner_name: str, constructor_name: str, **kwargs) -> list[Program]:
        """Spawn new program(s) from a constructor and run them.

        Constructors may return a single Program or a list (e.g. multi-agent
        templates from YAML specs). This method normalizes both cases and
        returns the full list of spawned programs.
        """
        if spawner_name not in self.programs:
            raise ValueError(f"Unknown spawner: {spawner_name}")

        spawner = self.programs[spawner_name]

        if constructor_name not in spawner.constructors:
            raise ValueError(f"Constructor {constructor_name} not found on {spawner_name}")

        # Create from constructor -- may return a single Program or a list
        constructor = spawner.constructors[constructor_name]
        result = constructor(**kwargs)
        new_programs = result if isinstance(result, list) else [result]

        for new_program in new_programs:
            # Set fork source for instance_source="fork" agents
            if new_program.is_agent and new_program.instance_source == "fork":
                if spawner.is_agent and spawner.machine:
                    new_program._fork_source = spawner.machine
                else:
                    raise ValueError(f"Cannot fork: spawner {spawner_name} has no machine")

            await self.run_program(new_program)

        return new_programs

    # --- Events ---

    async def events(self):
        """Async iterator for incoming events from agents."""
        while True:
            event = await self._events.get()
            yield event

    # --- Lifecycle ---

    async def start_and_wait(self) -> None:
        """Start execution, persist status to DB, and wait for completion.

        Designed to be run as a background asyncio task. Catches exceptions
        and persists failure status so callers don't need their own wrapper.
        """
        devbox_snapshot = self.devbox_machine.snapshot_id if self.devbox_machine else None
        logger.info("start_and_wait slug=%s root=%s snapshot=%s", self.slug, self.root.name, devbox_snapshot)
        try:
            await self.start()
            root_instance_id = self.root.machine.instance_id if self.root.is_agent and self.root.machine else None
            logger.info(
                "start_and_wait slug=%s started successfully, root.instance_id=%s",
                self.slug,
                root_instance_id,
            )
            async with get_session() as db:
                if self.root.is_agent:
                    await update_execution(db, self.id, status="running", root_instance_id=root_instance_id)
                    logger.info(
                        "start_and_wait slug=%s DB updated to running with instance_id=%s",
                        self.slug,
                        root_instance_id,
                    )
            await self.wait()
            logger.info("start_and_wait slug=%s finished waiting", self.slug)
        except Exception:
            logger.exception("start_and_wait slug=%s failed during startup", self.slug)
            async with get_session() as db:
                await update_execution(db, self.id, status="failed")
            logger.info("start_and_wait slug=%s DB updated to failed", self.slug)

    async def wait(self, timeout: float | None = None) -> bool:
        """Wait for the execution to finish (via the `submit` MCP tool).

        Args:
            timeout: Max seconds to wait. None = no timeout.

        Returns:
            True if finished, False if timed out.
        """
        try:
            await asyncio.wait_for(self._done.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False

    async def stop(self, reason: str = "user_request") -> None:
        """Stop all programs, connections, and instances."""
        self.status = "stopped"
        # No explicit token revocation needed. The registry check in the proxy
        # route rejects requests once the execution is removed from the registry.

        # Disconnect all agents
        for agent_name in list(self.connections.keys()):
            await self._disconnect_agent(agent_name)

        # Stop all VM instances via Machine
        for program in self.programs.values():
            if program.is_agent and program.machine:
                execution_trace.instance_stopped(self.user_id, self.slug, program.name, program.machine.instance_id)
                try:
                    await program.machine.stop()
                except Exception:
                    logger.warning("Failed to stop machine for agent '%s'", program.name)

        self.programs.clear()
        self._done.set()
        execution_trace.stopped(self.user_id, self.slug, reason)

        # Persist terminal status to DB. Done after teardown so a DB failure
        # cannot prevent VM cleanup.
        try:
            async with get_session() as db:
                await update_execution(db, self.id, status="stopped")
        except Exception:
            logger.warning("Failed to persist stopped status for execution %s", self.slug, exc_info=True)
