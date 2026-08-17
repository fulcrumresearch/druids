"""The ``Agent`` type.

An Agent owns its own state: a replicated ``Log``, a dict of tool
handlers, a registration ``Event``, and a handle to the process scope
that created it. It is the single source of truth for that agent inside
the runtime; nothing else shadows its fields.
"""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from ramure.helpers.schema import build_tool_definition
from ramure.log import Log
from ramure.machines.base import Machine
from ramure.stream import Stream
from ramure.types import ExecResult

if TYPE_CHECKING:
    from ramure.process import ProcessScope


Handler = Callable[..., Awaitable[Any]]


@dataclass
class Agent:
    name: str
    machine: Machine
    log: Log
    system_prompt: str | None = None
    model: str | None = None
    handlers: dict[str, Handler] = field(default_factory=dict)
    registered: asyncio.Event = field(default_factory=asyncio.Event)
    scope: "ProcessScope | None" = field(default=None, repr=False)
    #: Set by ``ProcessScope.cleanup()`` before the agent is killed, so a
    #: disconnect caused by intentional teardown is not treated as a crash.
    shutting_down: bool = False

    @property
    def events(self) -> Stream:
        """Async-iterable stream of this agent's events (no replication metadata)."""
        return self.log.stream

    def on(self, tool_name: str) -> Callable[[Handler], Handler]:
        """Register an async tool handler for this agent."""

        def decorator(fn: Handler) -> Handler:
            if not inspect.iscoroutinefunction(fn):
                raise TypeError("Tool handlers must be async")
            self.register_handler(tool_name, fn)
            return fn

        return decorator

    def register_handler(self, name: str, fn: Handler) -> None:
        """Install a tool handler. If the agent is already live, announce it."""
        self.handlers[name] = fn
        if self.registered.is_set():
            self.log.emit("tool_registered", build_tool_definition(name, fn))

    async def send(self, text: str) -> None:
        """Deliver a user-style message to this agent."""
        self.log.emit("message", {"text": text})

    async def exec(
        self, command: str, *, user: str = "agent", timeout: int | None = None
    ) -> ExecResult:
        return await self.machine.exec(command, user=user, timeout=timeout)
