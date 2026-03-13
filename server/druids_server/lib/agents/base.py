"""Agent -- a live, provisioned ACP agent with runtime state."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable, Self

from druids_server.config import settings
from druids_server.lib.acp import ACPConfig
from druids_server.lib.agents.config import AgentConfig
from druids_server.lib.connection import AgentConnection, _log_task_exception
from druids_server.lib.trace import Trace, trace_entry_to_dict
from druids_server.utils.forwarding_tokens import mint_token


if TYPE_CHECKING:
    from druids_server.lib.machine import Machine


logger = logging.getLogger(__name__)


@dataclass
class Agent:
    """A live agent in an execution. All fields are present and valid.

    Constructed via the `create` classmethod, which runs every provisioning
    step and returns a fully-initialized instance. Subclasses register
    themselves via `__init_subclass__` and are looked up with `for_type`.

    Session creation is deferred until the first prompt. This ensures that
    when ACP fetches `tools/list` during `session/new`, any tool handlers
    registered by the program via `@agent.on()` are already in place.
    """

    config: AgentConfig
    machine: Machine
    bridge_id: str
    bridge_token: str
    session_id: str
    connection: AgentConnection
    trace: Trace = field(default_factory=Trace)
    raw_events: list[dict] = field(default_factory=list)

    # Tool handlers registered by the program via @agent.on("tool_name")
    _handlers: dict[str, Callable] = field(default_factory=dict)

    # Deferred session creation: stored by create(), used by _ensure_session()
    _acp_config: ACPConfig | None = field(default=None, repr=False)
    _slug: str = field(default="", repr=False)
    _session_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    @property
    def name(self) -> str:
        """Agent name, delegated to config."""
        return self.config.name

    @classmethod
    def auth_method(cls) -> str | None:
        """ACP auth method passed to conn.start(). Overridden by subclasses."""
        return None

    # --- Program API ---

    def on(self, tool_name: str) -> Callable:
        """Register a tool handler for this agent.

        If the handler function has a `caller` parameter, the runtime
        automatically injects the Agent that invoked the tool.
        """

        def decorator(fn: Callable) -> Callable:
            self._handlers[tool_name] = fn
            return fn

        return decorator

    # --- Construction ---

    @classmethod
    async def create(
        cls,
        config: AgentConfig,
        machine: Machine,
        *,
        is_shared: bool,
        slug: str,
        user_id: str,
        secrets: dict[str, str] | None = None,
        resume_session_id: str | None = None,
    ) -> Self:
        """Provision and connect an agent. Returns a fully-initialized instance.

        The ACP connection is established but the session is NOT created yet.
        Session creation is deferred until the first prompt so that program-
        defined tool handlers are visible to ACP during `tools/list`.

        Args:
            config: Agent configuration.
            machine: Machine to run on.
            is_shared: Whether the machine is shared with another agent.
            slug: Execution slug.
            user_id: User ID.
            secrets: Decrypted secrets for env injection.
            resume_session_id: If set, pass --resume to the ACP process
                so it resumes an existing conversation session (used by fork
                with context=True).
        """
        await cls._prepare_machine(config, machine, is_shared)

        acp = cls.build_acp(config, slug=slug, user_id=user_id, secrets=secrets)
        if resume_session_id:
            acp.command_args = [*acp.command_args, "--resume", resume_session_id]

        bridge_id, bridge_token = await machine.ensure_bridge(
            acp,
            config.working_directory,
        )

        conn = await cls._open_connection(
            config,
            bridge_id,
            bridge_token,
        )

        return cls(
            config=config,
            machine=machine,
            bridge_id=bridge_id,
            bridge_token=bridge_token,
            session_id="",
            connection=conn,
            _acp_config=acp,
            _slug=slug,
        )

    @classmethod
    def build_acp(
        cls,
        config: AgentConfig,
        *,
        slug: str,
        user_id: str,
        secrets: dict[str, str] | None = None,
    ) -> ACPConfig:
        """Build the ACP process config for the bridge. Subclasses must override."""
        raise NotImplementedError(f"{cls.__name__} must override build_acp")

    @staticmethod
    def _build_base_env(
        config: AgentConfig,
        *,
        slug: str,
        user_id: str,
        secrets: dict[str, str] | None = None,
    ) -> tuple[dict[str, str], dict[str, Any]]:
        """Compute base env vars and resolved MCP servers common to all agent types."""
        access_token = mint_token(user_id, slug, config.name)
        env: dict[str, str] = {
            "DRUIDS_AGENT_NAME": config.name,
            "DRUIDS_EXECUTION_SLUG": slug,
            "DRUIDS_ACCESS_TOKEN": access_token,
        }
        if secrets:
            env.update(secrets)
        mcp: dict[str, Any] = dict(config.mcp_servers) if config.mcp_servers else {}
        return env, mcp

    @classmethod
    async def _prepare_machine(
        cls,
        config: AgentConfig,
        machine: Machine,
        is_shared: bool,
    ) -> None:
        """Write configs to a machine before starting the bridge."""
        if not is_shared:
            await machine.write_cli_config(str(settings.base_url))

    @classmethod
    async def _open_connection(
        cls,
        config: AgentConfig,
        bridge_id: str,
        bridge_token: str,
    ) -> AgentConnection:
        """Open an ACP connection through the bridge relay.

        Initializes the ACP protocol but does NOT create a session.
        Session creation is deferred to `_ensure_session()`.
        """
        conn = AgentConnection(bridge_id, bridge_token)
        await conn.start(auth_method=cls.auth_method())
        return conn

    async def _ensure_session(self) -> None:
        """Create the ACP session if not already created.

        Called lazily before the first prompt. By this point, program code
        has had a chance to register tool handlers via `@agent.on()`, so
        ACP's `tools/list` call during `session/new` will see them.
        """
        if self.session_id:
            return
        async with self._session_lock:
            if self.session_id:
                return
            self.session_id = await self._create_acp_session(
                self.config, self._acp_config, self._slug, self.connection,
            )
            logger.info("Agent '%s': session created (deferred), session_id=%s", self.name, self.session_id)

    @classmethod
    async def _create_acp_session(
        cls,
        config: AgentConfig,
        acp: ACPConfig,
        slug: str,
        conn: AgentConnection,
    ) -> str:
        """Create an ACP session with MCP servers. Returns the session ID."""
        base_url = str(settings.base_url)
        mcp_servers = []

        druids_mcp_url = f"{base_url}/api/executions/{slug}/agents/{config.name}/mcp"
        mcp_servers.append(
            {
                "name": "druids",
                "url": druids_mcp_url,
                "headers": {"Authorization": f"Bearer {acp.env.get('DRUIDS_ACCESS_TOKEN', '')}"},
            }
        )
        logger.info("Agent '%s' druids MCP: %s", config.name, druids_mcp_url)

        if acp.mcp_servers:
            for name, srv_config in acp.mcp_servers.items():
                mcp_servers.append({"name": name, **srv_config})
                logger.info("Agent '%s' additional MCP: %s", config.name, name)

        logger.info("Creating session for '%s' with %d MCP server(s)", config.name, len(mcp_servers))
        await conn.new_session(
            cwd=config.working_directory,
            mcp_servers=mcp_servers,
            system_prompt=config.system_prompt,
        )

        if config.model:
            await conn.set_model(config.model)

        return conn.session_id

    # --- Instance methods ---

    def record_event(self, params: dict) -> None:
        """Store a raw ACP event and update the trace."""
        self.raw_events.append({"ts": datetime.now(timezone.utc).isoformat(), "params": params})
        self.trace.ingest(params)

    def archive_trace(self, n: int = 500) -> list[dict]:
        """Serialize the in-memory trace to a list of dicts."""
        return [trace_entry_to_dict(e) for e in self.trace.tail(n)]

    def get_trace(self, n: int = 50) -> list[dict]:
        """Return the last n trace entries, serialized as dicts."""
        return [trace_entry_to_dict(e) for e in self.trace.tail(n)]

    async def prompt(self, text: str) -> None:
        """Send a prompt to this agent.

        Creates the ACP session on first call (deferred from create()),
        then sends the prompt as a fire-and-forget background task.
        """

        async def _prompt_with_session():
            await self._ensure_session()
            await self.connection.prompt(text)

        task = asyncio.create_task(_prompt_with_session())
        task.add_done_callback(_log_task_exception)

    async def close(self) -> None:
        """Close this agent's connection."""
        await self.connection.close()
