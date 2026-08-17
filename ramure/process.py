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

from ramure.agent import Agent
from ramure.context import _current_process, _invoke_in_scope
from ramure.helpers import kill_agent
from ramure.machines.base import Image, Machine
from ramure.machines.local import LocalImage
from ramure.runtime import Runtime
from ramure.stream import Stream
from ramure.types import ExecutionFailed

_spawn_handle: ContextVar["ProcessHandle | None"] = ContextVar(
    "ramure_spawn_handle", default=None
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
    endpoints: dict[str, Callable[..., Awaitable[Any]]] = field(
        default_factory=dict
    )
    _outcome: asyncio.Future | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self._outcome is None:
            self._outcome = asyncio.get_running_loop().create_future()

    def fail_external(self, reason: str) -> None:
        """Resolve this scope's outcome as failed, from outside its context.

        Called by the runtime when an agent owned by this scope dies
        unexpectedly. Semantics match the ambient ``fail()``: a pending
        ``wait()`` raises ``ExecutionFailed``, and the ``@agent_process``
        decorator emits the ``failed`` event on the way out. A scope whose
        outcome is already resolved is left untouched.
        """
        if self._outcome is not None and not self._outcome.done():
            self._outcome.set_exception(ExecutionFailed(reason))
            # The process may finish without ever awaiting the outcome
            # (e.g. it loops over events instead of calling wait()).
            # Mark the exception as retrieved so garbage collection does
            # not log a spurious "exception was never retrieved" warning;
            # a later ``wait()`` still raises.
            self._outcome.exception()

    async def cleanup(self) -> None:
        """Tear down agents and machines owned by this scope."""
        eid = self.runtime.execution_id or ""

        # Emit a shutdown entry to each agent's log. Delivery is scheduled
        # internally by ``emit``; we then proceed to kill the tmux session.
        # Any client-side cleanup pi does is best-effort.
        for ag in self.agents:
            ag.shutting_down = True
            try:
                ag.log.emit("shutdown")
            except Exception:
                pass

        # Kill agents and deregister
        for ag in self.agents:
            await kill_agent(ag, execution_id=eid)
            ag.log.close()
            self.runtime.agents.pop(ag.name, None)

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
        self._scope_ready: asyncio.Event = asyncio.Event()

    def _bind_scope(self, scope: ProcessScope) -> None:
        self._scope = scope
        self._scope_ready.set()

    async def _await_scope(self) -> ProcessScope:
        await self._scope_ready.wait()
        assert self._scope is not None
        return self._scope

    @property
    def agents(self) -> dict[str, Agent]:
        """Agents owned by the child process (populated once it starts)."""
        if self._scope is None:
            return {}
        return {ag.name: ag for ag in self._scope.agents}

    async def call(self, name: str, **kwargs: Any) -> Any:
        """Call an endpoint defined by the child process via ``@expose``."""
        scope = await self._await_scope()
        handler = scope.endpoints.get(name)
        if handler is None:
            raise ValueError(f"No endpoint '{name}'")
        return await _invoke_in_scope(scope, handler, kwargs)

    async def attach(
        self,
        ag: Agent,
        *,
        only: list[str] | None = None,
        prefix: str = "",
    ) -> None:
        """Register this process's endpoints as tools on ``ag``.

        The agent can then invoke them like any other tool. Each call runs
        inside the child process's scope, so ``emit()``/``done()``/``fail()``
        inside an endpoint affect the child, not the caller.
        """
        scope = await self._await_scope()
        names = list(scope.endpoints.keys()) if only is None else list(only)
        for name in names:
            handler = scope.endpoints.get(name)
            if handler is None:
                raise ValueError(f"No endpoint '{name}'")
            tool_name = f"{prefix}{name}"
            ag.register_handler(tool_name, _make_endpoint_tool(scope, handler))

    def cancel(self) -> None:
        """Cancel the process. Triggers cleanup."""
        if self.task is not None:
            self.task.cancel()


def _make_endpoint_tool(
    scope: ProcessScope,
    handler: Callable[..., Awaitable[Any]],
) -> Callable[..., Awaitable[Any]]:
    @wraps(handler)
    async def tool(**kwargs: Any) -> Any:
        return await _invoke_in_scope(scope, handler, kwargs)

    return tool


# ---------------------------------------------------------------------------
# @agent_process decorator
# ---------------------------------------------------------------------------


def agent_process(
    fn: Callable[P, Awaitable[R]] | None = None,
    *,
    image: Image | None = None,
    timeout: float | None = None,
    log_dir: Path | str | None = None,
    host: str = "127.0.0.1",
    port: int = 0,
    base_url: str | None = None,
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
                    host=host,
                    port=port,
                    base_url=base_url,
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
                handle._bind_scope(scope)

            if is_root:
                # External callers (the control socket / `ramure call`)
                # dispatch into the root scope's exposed endpoints.
                runtime.root_scope = scope

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
                # Fall back to the exception class name when the message is
                # empty — otherwise bare exceptions like asyncio.TimeoutError
                # (which carries no args) would show up as `("failed", "")`
                # on the event stream, giving the supervisor no reason.
                scope.events.emit("failed", str(e) or type(e).__name__)
                raise
            finally:
                _current_process.reset(token)
                await scope.cleanup()
                if is_root:
                    runtime.root_scope = None
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
    model: str | None = None,
    image: Image | None = None,
    machine: Machine | None = None,
) -> Agent:
    scope = _require_scope()
    resolved_image = (image or scope.image) if machine is None else None
    ag, spawned_machine = await scope.runtime.create_agent(
        name,
        system_prompt=system_prompt,
        model=model,
        image=resolved_image,
        machine=machine,
    )
    ag.scope = scope
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


def bubble(
    handle: "ProcessHandle", *, source: str | None = None
) -> Callable[[], None]:
    """Forward events from ``handle`` onto the current process's stream.

    A supervisor process that spawns children usually wants every
    child's events to show up on its own event stream, so a single
    ``async for`` on ``spawn(supervisor).events`` sees the whole
    picture. ``bubble`` wires that up::

        pool   = spawn(worker_pool, ...)
        env    = spawn(invariant_maintainer, name='env',   ...)
        goals  = spawn(invariant_maintainer, name='goals', ...)

        bubble(pool,  source='pool')
        bubble(env,   source='env')
        bubble(goals, source='goals')

    Each forwarded event has its :attr:`Event.source` tagged so
    downstream consumers can tell which child emitted it. The
    application ``data`` payload is passed through unchanged.

    If the child event already has a ``source`` (for example it was
    itself bubbled up from a grandchild), we preserve that original
    source. You see where an event really originated, not where it
    passed through. Pass ``source=None`` (the default) to forward
    whatever source the child already had.

    Synchronous under the hood (uses :meth:`Stream.subscribe`); no
    background task is spawned. Returns an idempotent unsubscribe
    callable.
    """
    scope = _require_scope()
    dst = scope.events

    def _fwd(ev: Any) -> None:
        # Preserve the deepest known source: if the child already
        # tagged it (grandchild -> child -> us), keep the
        # grandchild's source. Otherwise fall back to ours.
        dst.emit(ev.type, ev.data, source=ev.source or source)

    return handle.events.subscribe(_fwd)


def expose(fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
    """Register an async function as an endpoint of this process.

    The parent can invoke it via ``handle.call(name, ...)`` or install it
    as a tool on an agent via ``handle.attach(agent)``.
    """
    if not inspect.iscoroutinefunction(fn):
        raise TypeError("@expose requires an async function")
    scope = _require_scope()
    scope.endpoints[fn.__name__] = fn
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
