"""ProgramContext and Agent -- typed interfaces for Druids programs.

These are Protocol classes that describe the public API available to programs.
The server's real Execution and Agent classes satisfy these protocols
structurally. Program authors import these for type checking and autocomplete;
at runtime, the server provides the concrete implementations.
"""

from __future__ import annotations

from typing import Any, Callable, Literal, Protocol, runtime_checkable


GitPermission = Literal["read", "post", "write"]


@runtime_checkable
class Agent(Protocol):
    """An agent running on a remote VM.

    Returned by ``ctx.agent()``. Use ``on()`` to register tool handlers and
    ``send()`` to deliver messages.
    """

    name: str

    def on(self, tool_name: str) -> Callable:
        """Register a tool handler for this agent.

        Usage::

            @agent.on("submit")
            async def handle_submit(summary: str = ""):
                ctx.done(summary)
        """
        ...

    async def send(self, message: str) -> None:
        """Send a message to this agent (fire-and-forget)."""
        ...

    async def exec(self, command: str, *, user: str = "agent", timeout: int | None = None) -> Any:
        """Run a shell command on this agent's VM."""
        ...

    async def expose(self, name: str, port: int) -> str:
        """Expose a port on this agent's VM as a public HTTPS URL."""
        ...

    async def fork(
        self,
        name: str,
        *,
        prompt: str | None = None,
        system_prompt: str | None = None,
        model: str | None = None,
        git: str | None = None,
        context: bool = False,
    ) -> Agent:
        """Fork this agent's VM (COW) and create a new agent on the clone."""
        ...


@runtime_checkable
class ProgramContext(Protocol):
    """The ``ctx`` object passed to program functions.

    Programs are async functions that receive ``ctx`` as the first argument.
    CLI key-value args (``spec=...``, ``pr_number=...``) arrive as kwargs.
    Server-side state (repo, slug) lives on ``ctx``.

    Usage::

        from druids.lib import ProgramContext, Agent

        async def program(ctx: ProgramContext, spec: str = "", **kwargs: str):
            agent: Agent = await ctx.agent("worker", prompt=spec, git="write")

            @agent.on("submit")
            async def on_submit(summary: str = ""):
                ctx.done(summary)
    """

    slug: str
    repo_full_name: str | None

    async def agent(
        self,
        name: str,
        *,
        prompt: str | None = None,
        system_prompt: str | None = None,
        model: str = "claude",
        git: GitPermission | None = None,
        working_directory: str | None = None,
        share_machine_with: Agent | None = None,
        mcp_servers: dict[str, Any] | None = None,
    ) -> Agent:
        """Create an agent on a remote VM.

        Returns immediately. VM provisioning runs in the background.
        Tool handlers registered via ``agent.on()`` take effect before
        the agent's prompt fires.
        """
        ...

    def done(self, result: Any = None) -> None:
        """Signal successful completion."""
        ...

    def fail(self, reason: str) -> None:
        """Signal failure."""
        ...

    def on_client_event(self, event_name: str) -> Callable:
        """Register a handler for client events (decorator)."""
        ...

    def emit(self, event: str, data: dict[str, Any] | None = None) -> None:
        """Emit an event to connected clients."""
        ...

    async def send(self, sender: Agent | str, receiver: Agent | str, content: str) -> None:
        """Send a message from one agent to another."""
        ...

    async def prompt(self, agent: Agent | str, text: str) -> None:
        """Send a prompt directly to an agent."""
        ...
