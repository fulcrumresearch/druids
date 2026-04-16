from __future__ import annotations

import inspect
import shlex
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from druids.events import EventStream
from druids.machines import Machine
from druids.types import ExecResult

if TYPE_CHECKING:
    from druids.runtime import ProcessScope, Runtime


def _agent_extension_path(execution_id: str, agent_name: str) -> str:
    return f"/tmp/druids-extension-{execution_id}-{agent_name}.ts"


def _agent_session_name(execution_id: str, agent_name: str) -> str:
    return f"druids-{execution_id}-{agent_name}"


def _build_agent_launch_command(
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


@dataclass
class Agent:
    name: str
    machine: Machine
    system_prompt: str | None = None
    _handlers: dict[str, Callable[..., Awaitable[Any]]] = field(default_factory=dict)
    _runtime: Runtime | None = field(default=None, init=False, repr=False, compare=False)
    _scope: ProcessScope | None = field(default=None, init=False, repr=False, compare=False)
    _events: EventStream = field(default_factory=EventStream, init=False, repr=False)
    _public: bool = field(default=False, init=False, repr=False)

    @property
    def events(self) -> EventStream:
        """Async-iterable stream of raw agent events."""
        return self._events

    def on(
        self, tool_name: str
    ) -> Callable[[Callable[..., Awaitable[Any]]], Callable[..., Awaitable[Any]]]:
        """Register an async tool handler for this agent."""

        def decorator(
            fn: Callable[..., Awaitable[Any]],
        ) -> Callable[..., Awaitable[Any]]:
            if not inspect.iscoroutinefunction(fn):
                raise TypeError("Tool handlers must be async")
            if "caller" in inspect.signature(fn).parameters:
                raise TypeError("'caller' injection is not supported")

            self._runtime._register_tool_handler(self, tool_name, fn)
            return fn

        return decorator

    async def send(self, message: str) -> None:
        self._runtime._send_message(self.name, message)

    async def exec(
        self, command: str, *, user: str = "agent", timeout: int | None = None
    ) -> ExecResult:
        return await self._runtime._exec_agent(
            self,
            command,
            user=user,
            timeout=timeout,
        )
