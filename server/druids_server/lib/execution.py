"""Execution - manages agents, dispatches tool calls, runs programs in-process."""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable
from uuid import UUID

from druids_server.db.models.execution import update_execution
from druids_server.db.models.secret import get_decrypted_secrets
from druids_server.db.session import get_session
from druids_server.lib.agents.base import Agent
from druids_server.lib.agents.config import AgentConfig, create_agent
from druids_server.lib.agents.types import AgentType, agent_class
from druids_server.lib.program_agent import ProgramAgent
from druids_server.lib.caption import CaptionSummarizer
from druids_server.lib.connection import AgentConnection
from druids_server.lib.machine import BRIDGE_PORT, Machine
from druids_server.lib.program_dispatch import extract_agent_tool_schemas
from druids_server.lib.tools import BUILTIN_TOOL_SCHEMAS, BUILTIN_TOOLS
from druids_server.utils import execution_trace


logger = logging.getLogger(__name__)


@dataclass
class ExposedService:
    """A port exposed as a public HTTPS URL on a machine."""

    instance_id: str
    service_name: str
    port: int
    url: str


@dataclass
class Execution:
    """Manages agents, dispatches tool calls, and runs programs in-process.

    Programs call ctx.agent() to create agents, @agent.on() to register
    tool handlers, and ctx.wait() to block until completion. Tool calls
    from agents are dispatched directly to in-memory handlers.
    """

    id: UUID
    slug: str
    user_id: str
    devbox_machine: Machine | None = None
    devbox_id: UUID | None = None
    repo_full_name: str | None = None
    git_branch: str | None = None
    spec: str | None = None
    files: dict[str, str] | None = None  # path -> content, written to each agent sandbox

    # Agent registry
    agents: dict[str, Agent] = field(default_factory=dict)

    # Program's intended agent order (set by /ready from the runtime).
    # Agents may be provisioned concurrently, arriving in arbitrary order.
    _agent_order: list[str] = field(default_factory=list)

    # Archived traces for agents that were removed mid-execution
    _archived_traces: dict[str, list[dict]] = field(default_factory=dict)

    # Exposed HTTP services
    exposed_services: list[ExposedService] = field(default_factory=list)

    # Agent topology: (sender, receiver) pairs that are allowed to communicate
    _topology: set[tuple[str, str]] = field(default_factory=set)

    # Edge topology (list of {"from": ..., "to": ...} dicts, persisted to DB)
    edges: list[dict[str, str]] = field(default_factory=list)

    # Client event handlers registered by the program via @ctx.on_client_event()
    _client_handlers: dict[str, Callable] = field(default_factory=dict)
    _client_event_names: set[str] = field(default_factory=set)

    # Background task running the program function
    _program_task: asyncio.Task | None = field(default=None, repr=False)

    # Time-to-live in seconds. 0 means no timeout (subject to server max).
    ttl: int = 0

    # Lifecycle
    status: str = "created"
    status_error: str | None = None
    _done: asyncio.Event = field(default_factory=asyncio.Event)
    _result: Any = field(default=None, repr=False)
    _failure_reason: str | None = None

    # Caption summarizer (initialized in __post_init__)
    _captioner: CaptionSummarizer = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._captioner = CaptionSummarizer(self._emit_sync)

    # --- Agent queries ---

    def has_agent(self, name: str) -> bool:
        """Check if an agent exists."""
        return name in self.agents

    def all_agent_names(self) -> list[str]:
        """Return all agent names in program creation order."""
        if self._agent_order:
            ordered = [n for n in self._agent_order if n in self.agents]
            extra = [n for n in self.agents if n not in self._agent_order]
            return ordered + extra
        return list(self.agents)

    # --- Lifecycle ---

    async def done(self, result: Any = None) -> None:
        """Signal successful completion."""
        self.status = "completed"
        self._result = result
        self._done.set()

    def fail(self, reason: str) -> None:
        """Signal failure."""
        self.status = "failed"
        self._failure_reason = reason
        execution_trace.error(self.user_id, self.slug, None, reason)
        self._done.set()

    # --- Agent trace ---

    def record_agent_event(self, agent_name: str, params: dict) -> None:
        """Store a raw ACP event and update the agent's trace."""
        agent = self.agents.get(agent_name)
        if not agent:
            return
        agent.record_event(params)

    def get_agent_trace(self, agent_name: str, n: int = 50) -> list[dict]:
        """Return the last `n` trace entries for an agent, serialized as dicts."""
        agent = self.agents.get(agent_name)
        if agent:
            return agent.get_trace(n)
        archived = self._archived_traces.get(agent_name)
        if archived:
            return archived[-n:]
        return []

    def _archive_trace(self, agent_name: str) -> None:
        """Serialize an agent's in-memory trace to the archive before removal."""
        agent = self.agents.get(agent_name)
        if agent:
            self._archived_traces[agent_name] = agent.archive_trace()

    async def run(self) -> None:
        """Wait for the program to signal completion and clean up.

        If a TTL is set (via the request or server config), the execution is
        stopped when the TTL expires.
        """
        self.status = "running"
        devbox_snapshot = self.devbox_machine.snapshot_id if self.devbox_machine else None
        execution_trace.started(self.user_id, self.slug, None, devbox_snapshot)

        try:
            async with get_session() as db:
                await update_execution(db, self.id, status="running")

            try:
                if self.ttl > 0:
                    await asyncio.wait_for(self._done.wait(), timeout=self.ttl)
                else:
                    await self._done.wait()
            except TimeoutError:
                logger.info("Execution %s exceeded TTL of %ds", self.slug, self.ttl)
                execution_trace.error(self.user_id, self.slug, None, f"TTL expired ({self.ttl}s)")
                self.status = "stopped"
                self._failure_reason = f"TTL expired ({self.ttl}s)"

            # Grace period for in-flight tool calls to complete
            await asyncio.sleep(2)

            await self._teardown()
            execution_trace.stopped(self.user_id, self.slug, self.status)
            async with get_session() as db:
                await update_execution(db, self.id, status=self.status, error=self._failure_reason)

        except Exception as e:
            self.status = "error"
            self.status_error = repr(e)
            self._done.set()
            logger.exception("Execution %s failed: %s", self.slug, repr(e))
            execution_trace.error(self.user_id, self.slug, None, repr(e))
            execution_trace.stopped(self.user_id, self.slug, "error")
            await self._teardown()
            async with get_session() as db:
                await update_execution(db, self.id, status="failed", error=repr(e))

    # --- Topology ---

    def connect(self, a: Agent | str, b: Agent | str, *, direction: str = "both") -> None:
        """Declare that agents can communicate.

        Args:
            a: First agent (or agent name).
            b: Second agent (or agent name).
            direction: "both" for bidirectional (default), "forward" for a->b only.
        """
        a_name = a.name if isinstance(a, Agent) else a
        b_name = b.name if isinstance(b, Agent) else b
        self._topology.add((a_name, b_name))
        if direction == "both":
            self._topology.add((b_name, a_name))

    def is_connected(self, sender: Agent | str, receiver: Agent | str) -> bool:
        """Check if sender is allowed to message receiver."""
        s = sender.name if isinstance(sender, Agent) else sender
        r = receiver.name if isinstance(receiver, Agent) else receiver
        return (s, r) in self._topology

    # --- Tool calls ---

    async def call_tool(self, agent_name: str, tool_name: str, args: dict[str, Any]) -> Any:
        """Dispatch a tool call to the appropriate handler."""
        execution_trace.tool_use(self.user_id, self.slug, agent_name, f"druids:{tool_name}", args)

        if tool_name == "expose":
            result = await self._handle_expose(agent_name, args)
        elif tool_name == "message":
            result = await self._handle_message(agent_name, args)
        elif tool_name == "list_agents":
            result = self._handle_list_agents(agent_name)
        else:
            result = await self._dispatch_tool(agent_name, tool_name, args)

        execution_trace.tool_result(self.user_id, self.slug, agent_name, f"druids:{tool_name}", str(result))
        return result

    async def _dispatch_tool(self, agent_name: str, tool_name: str, args: dict[str, Any]) -> Any:
        """Dispatch a program-defined tool call to an in-memory handler."""
        agent = self.agents.get(agent_name)
        if not agent:
            return f"Error: agent '{agent_name}' not found"
        handler = agent._handlers.get(tool_name)
        if not handler:
            return f"Error: no handler for tool '{tool_name}' on agent '{agent_name}'"

        call_args = dict(args)
        if "caller" in inspect.signature(handler).parameters:
            call_args["caller"] = agent

        try:
            result = handler(**call_args)
            if asyncio.iscoroutine(result):
                result = await result
            return result
        except Exception as e:
            logger.exception("Tool '%s' failed for agent '%s'", tool_name, agent_name)
            return f"Error: {e}"

    async def _handle_message(self, sender_name: str, args: dict[str, Any]) -> str:
        """Handle the built-in message tool with topology enforcement."""
        receiver = args.get("receiver", "")
        message = args.get("message", "")
        if receiver not in self.agents:
            return f"Agent '{receiver}' not found. Available: {', '.join(self.agents.keys())}"
        if not self.is_connected(sender_name, receiver):
            reachable = [n for n in self.agents if n != sender_name and self.is_connected(sender_name, n)]
            return f"Agent '{receiver}' not found. Available: {', '.join(reachable)}"
        await self.send(sender_name, receiver, message)
        return f"Message sent to {receiver}."

    def _handle_list_agents(self, agent_name: str) -> str:
        """Handle the built-in list_agents tool with topology enforcement."""
        reachable = [n for n in self.agents if n != agent_name and self.is_connected(agent_name, n)]
        return ", ".join(reachable) if reachable else "No reachable agents."

    async def _handle_expose(self, agent_name: str, args: dict[str, Any]) -> str:
        """Built-in: expose a port on an agent's VM as a public HTTPS URL."""
        agent = self.agents.get(agent_name)
        if not agent or not agent.machine:
            return "Error: no machine available"
        try:
            port_int = int(args.get("port", 0))
        except ValueError:
            return f"Error: invalid port '{args.get('port')}'"
        if not (1 <= port_int <= 65535) or port_int == BRIDGE_PORT:
            return f"Error: port {port_int} is not allowed"
        service_name = args.get("service_name", "default")

        # Already exposed on this machine? Return existing URL.
        for svc in self.exposed_services:
            if svc.instance_id == agent.machine.instance_id and svc.port == port_int:
                return svc.url

        try:
            url = await agent.machine.expose_http_service(service_name, port_int)
        except Exception as e:
            return f"Error: failed to expose port {port_int}: {e}"
        self.exposed_services.append(
            ExposedService(instance_id=agent.machine.instance_id, service_name=service_name, port=port_int, url=url)
        )
        return url

    async def list_tools(self, agent_name: str) -> list[str]:
        """Return tool names available to an agent (built-in + program-defined)."""
        agent = self.agents.get(agent_name)
        program_tools = sorted(agent._handlers.keys()) if agent else []
        return list(BUILTIN_TOOLS) + program_tools

    async def list_tool_schemas(self, agent_name: str) -> list[dict]:
        """Return MCP-compatible tool schemas for an agent (built-in + program-defined)."""
        agent = self.agents.get(agent_name)
        program_schemas = extract_agent_tool_schemas(agent._handlers) if agent else []
        return list(BUILTIN_TOOL_SCHEMAS) + program_schemas

    # --- Client events ---

    def on_client_event(self, event_name: str) -> Callable:
        """Register a handler for client events from the frontend."""

        def decorator(fn: Callable) -> Callable:
            self._client_handlers[event_name] = fn
            self._client_event_names.add(event_name)
            return fn

        return decorator

    def _emit_sync(self, event: str, data: dict[str, Any] | None = None) -> None:
        """Sync emit used internally by CaptionSummarizer."""
        execution_trace.client_event(self.user_id, self.slug, event, data)

    async def emit(self, event: str, data: dict[str, Any] | None = None) -> None:
        """Emit an event to connected clients via the execution trace."""
        self._emit_sync(event, data)

    async def handle_client_event(self, event: str, data: dict[str, Any]) -> Any:
        """Dispatch a client event to a registered handler."""
        handler = self._client_handlers.get(event)
        if not handler:
            return {"error": f"No handler for client event '{event}'"}
        try:
            result = handler(**data)
            if asyncio.iscoroutine(result):
                result = await result
            return result
        except Exception as e:
            return {"error": str(e)}

    def list_client_events(self) -> list[str]:
        """Return client event names."""
        return sorted(self._client_event_names)

    # --- Program execution ---

    async def wait(self) -> None:
        """Block until the execution completes.

        Called by programs after setting up agents and handlers. Pushes
        topology to the trace and blocks on the done event.
        """
        if self._topology:
            self.edges = [{"from": a, "to": b} for a, b in self._topology]
            execution_trace.topology(self.user_id, self.slug, list(self.agents.keys()), self.edges)
        await self._done.wait()

    # --- Stop ---

    async def stop(self, reason: str = "user_request") -> None:
        """Stop all agents, connections, and instances."""
        self.status = "stopped"
        await self._teardown()
        execution_trace.stopped(self.user_id, self.slug, reason)

        try:
            async with get_session() as db:
                await update_execution(db, self.id, status="stopped")
        except Exception:
            logger.warning("Failed to persist stopped status for execution %s", self.slug, exc_info=True)

    async def _teardown(self) -> None:
        """Shared cleanup: cancel program task, shut down agents, stop machines."""
        if self._program_task and not self._program_task.done():
            self._program_task.cancel()
            try:
                await self._program_task
            except (asyncio.CancelledError, Exception):
                pass

        for name in list(self.agents):
            await self.shutdown_agent(name)

        self._done.set()

    async def shutdown_agent(self, agent_name: str) -> None:
        """Shut down a single agent: close connection, stop machine if unshared."""
        self._archive_trace(agent_name)

        agent = self.agents.pop(agent_name, None)
        if not agent:
            return
        execution_trace.agent_disconnected(self.user_id, self.slug, agent_name)

        try:
            await agent.close()
        except Exception:
            logger.warning("Failed to close connection for agent '%s'", agent_name)

        # Stop machine unless another live agent shares it
        shared = any(a.machine is agent.machine for a in self.agents.values())
        if not shared:
            try:
                await agent.machine.stop()
            except Exception:
                logger.warning("Failed to stop machine for agent '%s'", agent_name)

    # --- Agent lifecycle ---

    async def _provision_machine(self, config: AgentConfig) -> Machine:
        """Provision a Machine for an agent."""
        source = self.devbox_machine
        if not source:
            raise ValueError("No machine source for provisioning")

        has_git = bool(config.git and self.repo_full_name)

        metadata = {"druids:agent_name": config.name}
        logger.info("_provision_machine '%s': creating child from %s", config.name, source.instance_id)
        child = await source.create_child(
            metadata=metadata,
            repo_full_name=self.repo_full_name if has_git else None,
            git_branch=self.git_branch if has_git else None,
            git_permissions=config.git if has_git else None,
            ttl_seconds=self.ttl if self.ttl > 0 else None,
        )
        logger.info("_provision_machine '%s': child created, instance=%s", config.name, child.instance_id)
        if has_git:
            logger.info("_provision_machine '%s': running git_pull", config.name)
            await child.git_pull(config.working_directory)
            logger.info("_provision_machine '%s': git_pull complete", config.name)
        if self.files and child.sandbox:
            for path, content in self.files.items():
                await child.sandbox.write_file(path, content)
            logger.info("_provision_machine '%s': wrote %d file(s)", config.name, len(self.files))
        return child

    async def _load_secrets(self) -> dict[str, str]:
        """Load decrypted secrets from the DB for this execution's devbox."""
        if not self.devbox_id:
            return {}
        async with get_session() as db:
            return await get_decrypted_secrets(db, self.devbox_id)

    async def _start_agent(
        self,
        agent_config: AgentConfig,
        machine: Machine,
        *,
        is_shared: bool = False,
        resume_session_id: str | None = None,
    ) -> Agent:
        """Connect an agent on a machine, bind traces, send initial prompt.

        Shared by provision_agent (new machines) and fork_agent (COW clones).
        """
        secrets = await self._load_secrets()
        cls = agent_class(agent_config.agent_type)
        agent = await cls.create(
            agent_config,
            machine,
            is_shared=is_shared,
            slug=self.slug,
            user_id=self.user_id,
            secrets=secrets,
        )

        if resume_session_id:
            agent._resume_session_id = resume_session_id

        self._bind_trace(agent_config.name, agent.connection)
        self.agents[agent_config.name] = agent

        execution_trace.agent_connected(self.user_id, self.slug, agent_config.name, "deferred")

        if agent_config.prompt:
            execution_trace.prompt(self.user_id, self.slug, agent_config.name, agent_config.prompt)
            await agent.prompt(agent_config.prompt)

        return agent

    async def provision_agent(
        self,
        name: str,
        *,
        agent_type: AgentType = "claude",
        model: str | None = None,
        prompt: str | None = None,
        system_prompt: str | None = None,
        git: str | None = None,
        working_directory: str | None = None,
        share_machine_with: str | ProgramAgent | Agent | None = None,
        mcp_servers: dict[str, Any] | None = None,
    ) -> ProgramAgent:
        """Create, provision, and connect an agent. Returns a ProgramAgent wrapper."""
        # Normalize ProgramAgent to name string for _resolve_machine
        share_name: str | None = None
        if isinstance(share_machine_with, ProgramAgent):
            share_name = share_machine_with.name
        elif isinstance(share_machine_with, (str, Agent)):
            share_name = share_machine_with if isinstance(share_machine_with, str) else share_machine_with.name
        agent_config = create_agent(
            name,
            agent_type=agent_type,
            model=model,
            prompt=prompt,
            system_prompt=system_prompt,
            working_directory=working_directory,
            git=git,
            mcp_servers=mcp_servers,
            slug=self.slug,
            user_id=self.user_id,
            secrets=await self._load_secrets(),
            spec=self.spec,
        )

        machine: Machine | None = None
        try:
            t0 = time.monotonic()

            machine = await self._resolve_machine(agent_config, share_name)
            logger.info(
                "Agent '%s': machine ready in %.2fs (instance=%s)", name, time.monotonic() - t0, machine.instance_id
            )
            execution_trace.program_added(self.user_id, self.slug, name, "agent", machine.instance_id)

            agent = await self._start_agent(agent_config, machine, is_shared=bool(share_machine_with))
            logger.info("Agent '%s': ready, total=%.2fs", name, time.monotonic() - t0)
            return ProgramAgent(agent, self)

        except BaseException:
            if machine and not share_machine_with and name not in self.agents:
                try:
                    await machine.stop()
                except Exception:
                    logger.warning("Failed to stop orphaned machine for agent '%s'", name)
            raise

    # Programs call ctx.agent() — alias for provision_agent
    agent = provision_agent

    async def fork_agent(
        self,
        source: Agent,
        name: str,
        *,
        prompt: str | None = None,
        system_prompt: str | None = None,
        model: str | None = None,
        git: str | None = None,
        context: bool = False,
    ) -> Agent:
        """Fork an agent's VM (COW) and create a new agent on the clone.

        MorphCloud branch preserves running processes, so the forked VM has
        the source's bridge and ACP agent still running. We stop the
        inherited bridge, then start a fresh one with the new agent's
        identity. The filesystem (repo, session data) survives because
        only the process is restarted.

        With context=True, the new ACP process resumes the source's session
        from disk via session/resume, preserving conversation history.
        """
        from druids_server.lib.sandbox.docker import DockerSandbox

        if not source.machine:
            raise RuntimeError(f"Agent '{source.name}' has no machine")
        if isinstance(source.machine.sandbox, DockerSandbox):
            raise RuntimeError("fork() requires MorphCloud. Docker containers cannot be forked.")

        resume_session_id = None
        if context:
            if not source.session_id:
                raise RuntimeError(f"Cannot fork with context=True: agent '{source.name}' has no active session")
            resume_session_id = source.session_id

        agent_config = create_agent(
            name,
            agent_type=source.config.agent_type,
            model=model or source.config.model,
            prompt=prompt,
            system_prompt=system_prompt if system_prompt is not None else source.config.system_prompt,
            git=git if git is not None else source.config.git,
            working_directory=source.config.working_directory,
            slug=self.slug,
            user_id=self.user_id,
        )

        machine: Machine | None = None
        try:
            t0 = time.monotonic()
            machine = await source.machine.create_child(
                metadata={"druids:agent_name": name},
                repo_full_name=self.repo_full_name if agent_config.git else None,
                git_branch=self.git_branch if agent_config.git else None,
                git_permissions=agent_config.git,
            )

            # Kill the inherited bridge process from the source agent.
            # MorphCloud branch preserves running processes, so the source's
            # bridge is still running on the forked VM. Stop it so
            # ensure_bridge can start a fresh one with the new identity.
            await machine.exec(
                f"curl -fsS -X POST http://127.0.0.1:{BRIDGE_PORT}/stop 2>/dev/null || true",
                check=False,
            )

            logger.info(
                "Agent '%s': forked from '%s' in %.2fs (instance=%s)",
                name,
                source.name,
                time.monotonic() - t0,
                machine.instance_id,
            )
            execution_trace.program_added(self.user_id, self.slug, name, "agent", machine.instance_id)

            agent = await self._start_agent(agent_config, machine, resume_session_id=resume_session_id)
            logger.info("Agent '%s': fork ready, total=%.2fs", name, time.monotonic() - t0)
            return agent

        except BaseException:
            if machine and name not in self.agents:
                try:
                    await machine.stop()
                except Exception:
                    logger.warning("Failed to stop orphaned fork machine for agent '%s'", name)
            raise

    async def _resolve_machine(self, config: AgentConfig, share_with_name: str | None) -> Machine:
        """Get or provision a machine for an agent."""
        if not share_with_name:
            return await self._provision_machine(config)

        shared = self.agents.get(share_with_name)
        if not shared:
            raise RuntimeError(
                f"Agent '{share_with_name}' not found for share_machine_with on '{config.name}'. "
                f"The shared agent must be fully provisioned before the dependent request."
            )
        return shared.machine

    # --- Trace ---

    def _bind_trace(self, agent_name: str, conn: AgentConnection) -> None:
        """Register the session/update handler on an agent's connection."""
        tool_titles: dict[str, str] = {}
        emitted_tool_use: set[str] = set()

        def _emit_tool_use(tool_call_id: str, title: str, raw_input: dict | None) -> None:
            """Emit tool_use trace event, guarding against double-emission."""
            if tool_call_id in emitted_tool_use:
                return
            emitted_tool_use.add(tool_call_id)
            execution_trace.tool_use(self.user_id, self.slug, agent_name, title, raw_input)
            self._captioner.tool_caption(agent_name, title, raw_input)

        async def on_session_update(params: dict) -> None:
            self.record_agent_event(agent_name, params)

            update = params.get("update", {})
            session_update = update.get("sessionUpdate")

            if session_update == "agent_message_chunk":
                content = update.get("content", {})
                if content.get("type") == "text":
                    text = content.get("text", "")
                    if text:
                        execution_trace.response_chunk(self.user_id, self.slug, agent_name, text)
                        self._captioner.accumulate(agent_name, text)

            elif session_update == "tool_call":
                tool_call_id = update["toolCallId"]
                title = update["title"]
                raw_input = update.get("rawInput") or {}
                tool_titles[tool_call_id] = title
                # The ACP adapter may send multiple tool_call events for
                # the same ID as the input streams in.  Emit once we have
                # real input; _emit_tool_use deduplicates by ID.
                if raw_input:
                    _emit_tool_use(tool_call_id, title, raw_input)

            elif session_update == "tool_call_update":
                tool_call_id = update["toolCallId"]
                if update.get("title"):
                    tool_titles[tool_call_id] = update["title"]
                raw_input = update.get("rawInput")
                if raw_input:
                    _emit_tool_use(tool_call_id, tool_titles.get(tool_call_id, ""), raw_input)
                status = update.get("status")
                if status == "completed":
                    # Fallback: emit if we never got rawInput (no-param tool).
                    _emit_tool_use(tool_call_id, tool_titles.get(tool_call_id, ""), raw_input or {})
                    emitted_tool_use.discard(tool_call_id)
                    title = tool_titles.pop(tool_call_id, "")
                    raw_output = update.get("rawOutput")
                    execution_trace.tool_result(self.user_id, self.slug, agent_name, title, raw_output)

        conn.on("session/update", on_session_update)

    # --- Messaging ---

    async def send(self, sender: Agent | str, receiver: Agent | str, content: str) -> None:
        """Send message to an agent via its connection."""
        sender_name = sender.name if isinstance(sender, Agent) else sender
        receiver_name = receiver.name if isinstance(receiver, Agent) else receiver

        agent = self.agents.get(receiver_name)
        if not agent:
            logger.warning("send: agent '%s' not found, skipping message from '%s'", receiver_name, sender_name)
            return

        formatted = f"[From: {sender_name}] {content}"
        execution_trace.prompt(self.user_id, self.slug, receiver_name, formatted)
        await agent.prompt(formatted)

    async def prompt(self, agent_name: str, text: str) -> None:
        """Send a prompt directly to an agent (fire-and-forget)."""
        agent = self.agents.get(agent_name)
        if not agent:
            logger.warning("prompt: agent '%s' not connected", agent_name)
            return
        execution_trace.prompt(self.user_id, self.slug, agent_name, text)
        await agent.prompt(text)
