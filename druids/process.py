"""Process scopes, handles, the ``@agent_process`` decorator, and ambient helpers.

A process is the unit of composition: an async function decorated with
``@agent_process`` that creates agents, wires them up, and returns a result.
Ambient helpers (``agent()``, ``done()``, ``wait()``, etc.) act on the
current process scope found via a ``ContextVar``.
"""

from __future__ import annotations

import asyncio
import inspect
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import wraps
from pathlib import Path
from typing import Any, Awaitable, Callable, ParamSpec, TypeVar

from druids.agent import Agent
from druids.helpers import kill_agent
from druids.machines import Image, LocalImage, Machine
from druids.runtime import Runtime
from druids.stream import Stream
from druids.types import ExecutionFailed

# ---------------------------------------------------------------------------
# Context variables (read by Runtime.handle_tool_call as well)
# ---------------------------------------------------------------------------

_current_process: ContextVar["ProcessScope | None"] = ContextVar(
    "druids_current_process", default=None
)

_spawn_handle: ContextVar["ProcessHandle | None"] = ContextVar(
    "druids_spawn_handle", default=None
)

P = ParamSpec("P")
R = TypeVar("R")


# ---------------------------------------------------------------------------
# Process scope
# ---------------------------------------------------------------------------


@dataclass
class ProcessScope:
    """Ownership boundary for agents and machines created within a process."""

    parent: "ProcessScope | None"
    runtime: Runtime
    image: Image = field(default_factory=LocalImage)
    agents: list[Agent] = field(default_factory=list)
    machines: list[Machine] = field(default_factory=list)
    events: Stream = field(default_factory=Stream)
    client_handlers: dict[str, Callable[..., Awaitable[Any]]] = field(
        default_factory=dict
    )
    _outcome: asyncio.Future | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self._outcome is None:
            self._outcome = asyncio.get_running_loop().create_future()

    async def cleanup(self) -> None:
        """Tear down agents and machines owned by this scope."""
        eid = self.runtime.execution_id or ""

        # Send shutdown events
        for ag in self.agents:
            rec = self.runtime.records.get(ag.name)
            if rec:
                try:
                    entry = rec.log.emit("shutdown")
                    if entry is not None:
                        await rec.log.broadcast(entry)
                except Exception:
                    pass

        if self.agents:
            await asyncio.sleep(0.2)

        # Kill agents and deregister
        for ag in self.agents:
            await kill_agent(ag, execution_id=eid)
            ag.events.close()
            self.runtime.records.pop(ag.name, None)

        seen: set[int] = set()
        for m in self.machines:
            if id(m) not in seen:
                seen.add(id(m))
                try:
                    await m.stop()
                except Exception:
                    pass

        self.events.close()


# ---------------------------------------------------------------------------
# Process handle (returned by spawn())
# ---------------------------------------------------------------------------


class ProcessHandle:
    """Handle to a spawned process. Provides event stream and control."""

    def __init__(self, events: Stream) -> None:
        self.events = events
        self.task: asyncio.Task | None = None
        self._scope: ProcessScope | None = None

    @property
    def agents(self) -> dict[str, Agent]:
        """Public agents exposed by the child process."""
        if self._scope is None:
            return {}
        return {ag.name: ag for ag in self._scope.agents if ag._public}

    async def call(self, event_name: str, **kwargs: Any) -> Any:
        """Call a client event handler defined by the child process."""
        if self._scope is None:
            raise RuntimeError("Process scope not yet initialized")
        handler = self._scope.client_handlers.get(event_name)
        if handler is None:
            raise ValueError(f"No client event '{event_name}'")
        return await handler(**kwargs)

    def cancel(self) -> None:
        """Cancel the process. Triggers cleanup."""
        if self.task is not None:
            self.task.cancel()


# ---------------------------------------------------------------------------
# @agent_process decorator
# ---------------------------------------------------------------------------


def agent_process(
    fn: Callable[P, Awaitable[R]] | None = None,
    *,
    image: Image | None = None,
    timeout: float | None = None,
    log_dir: Path | str | None = None,
) -> (
    Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[Any]]]
    | Callable[P, Awaitable[Any]]
):
    def decorate(coro_fn: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[Any]]:
        if not inspect.iscoroutinefunction(coro_fn):
            raise TypeError("@agent_process requires an async function")

        @wraps(coro_fn)
        async def wrapped(*args: P.args, **kwargs: P.kwargs) -> Any:
            parent = _current_process.get()
            is_root = parent is None

            if is_root:
                runtime = Runtime(
                    log_dir=Path(log_dir) if log_dir else None,
                )
                await runtime.start()
            else:
                runtime = parent.runtime

            resolved_image = image or (parent.image if parent else LocalImage())

            # If spawned, the handle's event stream becomes the scope's stream
            handle = _spawn_handle.get()

            scope = ProcessScope(parent=parent, runtime=runtime, image=resolved_image)

            if handle is not None:
                scope.events = handle.events
                handle._scope = scope

            token = _current_process.set(scope)
            try:
                if timeout is not None:
                    result = await asyncio.wait_for(
                        coro_fn(*args, **kwargs), timeout=timeout
                    )
                else:
                    result = await coro_fn(*args, **kwargs)
                scope.events.emit("done", result)
                return result
            except asyncio.CancelledError:
                scope.events.emit("cancelled", None)
                raise
            except Exception as e:
                scope.events.emit("failed", str(e))
                raise
            finally:
                _current_process.reset(token)
                await scope.cleanup()
                if is_root:
                    await runtime.close()

        return wrapped

    if fn is None:
        return decorate
    return decorate(fn)


# ---------------------------------------------------------------------------
# Ambient functions
# ---------------------------------------------------------------------------


def _require_scope() -> ProcessScope:
    scope = _current_process.get()
    if scope is None:
        raise RuntimeError("No active process. Use @agent_process.")
    return scope


def current_runtime() -> Runtime:
    return _require_scope().runtime


async def agent(
    name: str,
    *,
    system_prompt: str | None = None,
    image: Image | None = None,
    machine: Machine | None = None,
) -> Agent:
    scope = _require_scope()
    resolved_image = (image or scope.image) if machine is None else None
    ag, spawned_machine = await scope.runtime.create_agent(
        name,
        system_prompt=system_prompt,
        image=resolved_image,
        machine=machine,
    )
    ag._scope = scope
    scope.agents.append(ag)
    if spawned_machine is not None:
        scope.machines.append(spawned_machine)
    return ag


async def machine(image: Image | None = None) -> Machine:
    scope = _require_scope()
    m = await (image or scope.image).spawn()
    scope.machines.append(m)
    return m


def connect(a: Agent, b: Agent, *, direction: str = "both") -> None:
    scope = _require_scope()
    scope.runtime.connect_agents(a, b, direction=direction)


def done(result: Any = None) -> None:
    """Signal that this process completed successfully."""
    scope = _require_scope()
    if not scope._outcome.done():
        scope._outcome.set_result(result)


def fail(reason: str) -> None:
    """Signal that this process failed."""
    scope = _require_scope()
    if not scope._outcome.done():
        scope._outcome.set_exception(ExecutionFailed(reason))


async def wait() -> Any:
    """Block until done() or fail() is called. Returns the done value, raises on fail."""
    scope = _require_scope()
    return await scope._outcome


def emit(event_type: str, data: Any = None) -> None:
    """Emit an event into this process's event stream."""
    scope = _require_scope()
    scope.events.emit(event_type, data)


def public(ag: Agent) -> None:
    """Expose an agent so the parent process can interact with it."""
    ag._public = True


def client_event(fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
    """Register a handler that the parent process can call via handle.call()."""
    scope = _require_scope()
    scope.client_handlers[fn.__name__] = fn
    return fn


def spawn(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> ProcessHandle:
    """Run a process function in a background task. Returns a handle."""
    events = Stream()
    handle = ProcessHandle(events=events)

    async def run() -> None:
        token = _spawn_handle.set(handle)
        try:
            await fn(*args, **kwargs)
        except asyncio.CancelledError:
            raise
        except Exception:
            pass  # "failed" event already emitted by @agent_process decorator
        finally:
            _spawn_handle.reset(token)
            if not events.closed:
                events.close()

    handle.task = asyncio.create_task(run())
    return handle
