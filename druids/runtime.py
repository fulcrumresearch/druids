from __future__ import annotations

import asyncio
import inspect
import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import wraps
from pathlib import Path
from typing import Any, Awaitable, Callable, ParamSpec, TypeVar

from druids.agent import Agent
from druids.helpers import agent_session_name, build_tool_definition, kill_agent
from druids.helpers import launch_agent as _launch_agent_impl
from druids.log import Log
from druids.machines import Image, LocalImage, Machine
from druids.server import Server
from druids.stream import Stream
from druids.types import ExecutionFailed, ToolCallError

# ---------------------------------------------------------------------------
# Context variables
# ---------------------------------------------------------------------------

_current_process: ContextVar[ProcessScope | None] = ContextVar(
    "druids_current_process", default=None
)

_spawn_handle: ContextVar[ProcessHandle | None] = ContextVar(
    "druids_spawn_handle", default=None
)

P = ParamSpec("P")
R = TypeVar("R")


# ---------------------------------------------------------------------------
# Agent record (per-agent bookkeeping inside the runtime)
# ---------------------------------------------------------------------------


@dataclass
class AgentRecord:
    """Single source of truth for all per-agent state inside the runtime."""

    agent: Agent
    log: Log = field(repr=False)
    registered: asyncio.Event = field(default_factory=asyncio.Event)


# ---------------------------------------------------------------------------
# Process scope
# ---------------------------------------------------------------------------


@dataclass
class ProcessScope:
    """Ownership boundary for agents and machines created within a process."""

    parent: ProcessScope | None
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
                        await rec.log.push(entry)
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
# Runtime (singleton infrastructure)
# ---------------------------------------------------------------------------


class Runtime:
    """Manages the server, agent registry, and tool dispatch.

    Created automatically by the root ``@agent_process``. Exists as infrastructure that process scopes
    share.
    """

    def __init__(self, *, log_dir: Path | str | None = None):
        self.execution_id: str | None = None
        self.server_url: str | None = None
        self.records: dict[str, AgentRecord] = {}
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
        if name in self.records:
            raise ValueError(f"Agent '{name}' already exists")
        if machine is not None and image is not None:
            raise ValueError("Pass either machine= or image=, not both")

        spawned_machine: Machine | None = None
        resolved_machine = machine
        if resolved_machine is None:
            resolved_machine = await image.spawn()
            spawned_machine = resolved_machine

        ag = Agent(
            name=name,
            machine=resolved_machine,
            system_prompt=system_prompt,
        )
        ag._runtime = self

        log_path: Path | None = None
        if self.log_dir_root is not None and self.execution_id:
            log_path = self.log_dir_root / self.execution_id / f"{name}.jsonl"
        log = Log(path=log_path)

        self.records[name] = AgentRecord(agent=ag, log=log)
        self._register_builtins(ag)
        log.emit("agent_created", {"agent": name})

        try:
            await self.spawn_agent(ag)
        except Exception:
            self.records.pop(name, None)
            raise

        return ag, spawned_machine

    def _register_builtins(self, ag: Agent) -> None:
        """Register builtin tools (message, send_file, download_file) on an agent."""
        runtime = self

        async def message(receiver: str, message: str) -> str:
            """Send a message to a connected agent."""
            runtime.get_record(receiver)
            runtime.require_connection(ag.name, receiver)
            runtime.send_message(receiver, f"[From: {ag.name}] {message}")
            return f"Message sent to {receiver}."

        async def send_file(receiver: str, path: str, dest_path: str = "") -> str:
            """Send a file to a connected agent."""
            dest = dest_path or path
            receiver_rec = runtime.get_record(receiver)
            runtime.require_connection(ag.name, receiver)
            content = await ag.machine.read_file(path)
            await receiver_rec.agent.machine.write_file(dest, content)
            return f"Sent {len(content)} bytes to {receiver}:{dest}."

        async def download_file(sender: str, path: str, dest_path: str = "") -> str:
            """Download a file from a connected agent."""
            dest = dest_path or path
            sender_rec = runtime.get_record(sender)
            runtime.require_connection(sender, ag.name)
            content = await sender_rec.agent.machine.read_file(path)
            await ag.machine.write_file(dest, content)
            return f"Downloaded {len(content)} bytes from {sender}:{path} to {dest}."

        ag._handlers["message"] = message
        ag._handlers["send_file"] = send_file
        ag._handlers["download_file"] = download_file

    # -- Connections --

    def connect_agents(self, a: Agent, b: Agent, *, direction: str = "both") -> None:
        if direction not in {"both", "forward"}:
            raise ValueError("direction must be 'both' or 'forward'")
        self.edges.add((a.name, b.name))
        if direction == "both":
            self.edges.add((b.name, a.name))

    # -- Tool handler registration --

    def register_tool_handler(
        self,
        agent: Agent,
        tool_name: str,
        fn: Callable[..., Awaitable[Any]],
    ) -> None:
        agent._handlers[tool_name] = fn
        rec = self.records.get(agent.name)
        if rec and rec.registered.is_set():
            tool_def = build_tool_definition(tool_name, fn)
            entry = rec.log.emit("tool_registered", tool_def)
            if entry is not None:
                asyncio.ensure_future(rec.log.push(entry))

    # -- Messaging --

    def send_message(self, agent_name: str, message: str) -> None:
        rec = self.get_record(agent_name)
        entry = rec.log.emit("message", {"text": message})
        if entry is not None:
            asyncio.ensure_future(rec.log.push(entry))

    # -- Agent spawn / registration --

    async def spawn_agent(self, agent: Agent) -> None:
        if not await self.launch_agent(agent):
            return
        rec = self.records[agent.name]
        try:
            await asyncio.wait_for(rec.registered.wait(), timeout=120)
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
        rec = self.records[agent.name]
        rec.log.emit(
            "agent_spawned",
            {
                "agent": agent.name,
                "tmux_session": session_name,
            },
        )
        return True

    def register_agent(self, agent_id: str, execution_id: str) -> list[dict[str, Any]]:
        if execution_id != self.execution_id:
            raise ToolCallError("Execution ID mismatch", status_code=400)
        rec = self.get_record(agent_id)
        rec.registered.set()
        return [
            build_tool_definition(name, fn)
            for name, fn in rec.agent._handlers.items()
        ]

    # -- Tool call dispatch --

    async def handle_tool_call(
        self, agent_id: str, tool_name: str, params: dict[str, Any]
    ) -> Any:
        rec = self.get_record(agent_id)
        handler = rec.agent._handlers.get(tool_name)
        if handler is None:
            raise ToolCallError(
                f"Unknown tool '{tool_name}' for agent '{rec.agent.name}'",
                status_code=404,
            )
        token = _current_process.set(rec.agent._scope)
        try:
            return await handler(**params)
        finally:
            _current_process.reset(token)

    # -- Helpers --

    def get_record(self, name: str) -> AgentRecord:
        rec = self.records.get(name)
        if rec is None:
            raise ToolCallError(f"Unknown agent '{name}'", status_code=404)
        return rec

    def require_connection(self, sender: str, receiver: str) -> None:
        if (sender, receiver) not in self.edges:
            raise ToolCallError(
                f"Agent '{sender}' is not connected to '{receiver}'", status_code=403
            )


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
