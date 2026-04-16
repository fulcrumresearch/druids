from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from druids.machines import Machine
from druids.types import ExecResult

if TYPE_CHECKING:
    from druids.runtime import ProcessScope, Runtime


@dataclass
class Agent:
    name: str
    machine: Machine
    system_prompt: str | None = None
    _handlers: dict[str, Callable[..., Awaitable[Any]]] = field(default_factory=dict)
    _runtime: Runtime | None = field(default=None, init=False, repr=False, compare=False)
    _scope: ProcessScope | None = field(default=None, init=False, repr=False)
    _public: bool = field(default=False, init=False, repr=False)

    @property
    def events(self):
        """Async-iterable log of raw agent events."""
        return self._runtime.get_record(self.name).log

    def on(
        self, tool_name: str
    ) -> Callable[[Callable[..., Awaitable[Any]]], Callable[..., Awaitable[Any]]]:
        """Register an async tool handler for this agent."""

        def decorator(
            fn: Callable[..., Awaitable[Any]],
        ) -> Callable[..., Awaitable[Any]]:
            if not inspect.iscoroutinefunction(fn):
                raise TypeError("Tool handlers must be async")
            self._runtime.register_tool_handler(self, tool_name, fn)
            return fn

        return decorator

    async def send(self, message: str) -> None:
        self._runtime.send_message(self.name, message)

    async def exec(
        self, command: str, *, user: str = "agent", timeout: int | None = None
    ) -> ExecResult:
        return await self.machine.exec(command, user=user, timeout=timeout)
