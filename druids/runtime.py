from __future__ import annotations

import asyncio
import inspect
import shlex
import shutil
import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, ParamSpec, TypeVar

from druids.agent import (
    Agent,
    _agent_extension_path,
    _agent_session_name,
    _build_agent_launch_command,
)
from druids.event_log import AgentEventLog, LogEntry
from druids.events import EventStream
from druids.extension import extension_source
from druids.machines import Image, LocalImage, Machine
from druids.schema import build_tool_definition
from druids.server import Server
from druids.types import ExecResult, ExecutionFailed, ToolCallError


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
    log: AgentEventLog = field(repr=False)
    registered: asyncio.Event = field(default_factory=asyncio.Event)
    _notify: Callable[[LogEntry], Awaitable[None]] | None = field(
        default=None, init=False, repr=False
    )

    async def push(self, entry: LogEntry) -> None:
        if self._notify is not None:
            try:
                await self._notify(entry)
            except Exception:
                pass

    async def push_entries(self, entries: list[LogEntry]) -> None:
        for entry in entries:
            await self.push(entry)


# ---------------------------------------------------------------------------
# Process scope
# ---------------------------------------------------------------------------

@dataclass
class ProcessScope:
    """Ownership boundary for agents and machines created within a process."""

    parent: ProcessScope | None
    runtime: Runtime
    agents: list[Agent] = field(default_factory=list)
    machines: list[Machine] = field(default_factory=list)
    events: EventStream = field(default_factory=EventStream)
    client_handlers: dict[str, Callable[..., Awaitable[Any]]] = field(default_factory=dict)
    _outcome: asyncio.Future | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self._outcome is None:
            self._outcome = asyncio.get_running_loop().create_future()

    async def cleanup(self) -> None:
        """Tear down agents and machines owned by this scope."""
        # Send shutdown events
        for ag in self.agents:
            rec = self.runtime._records.get(ag.name)
            if rec:
                try:
                    entry = rec.log.append("shutdown", "server")
                    await rec.push(entry)
                except Exception:
                    pass

        if self.agents:
            await asyncio.sleep(0.2)

        # Kill tmux sessions and deregister
        for ag in self.agents:
            session = _agent_session_name(self.runtime.execution_id or "", ag.name)
            try:
                await ag.machine.exec(
                    f"tmux kill-session -t {shlex.quote(session)} 2>/dev/null || true",
                    timeout=5,
                )
            except Exception:
                pass
            self.runtime._records.pop(ag.name, None)

        # Close agent event streams
        for ag in self.agents:
            ag._events.close()

        # Stop machines spawned by this scope
        seen: set[int] = set()
        for m in self.machines:
            if id(m) not in seen:
                seen.add(id(m))
                try:
                    await m.stop()
                except Exception:
                    pass

        # Close process event stream
        self.events.close()


# ---------------------------------------------------------------------------
# Process handle (returned by spawn())
# ---------------------------------------------------------------------------

class ProcessHandle:
    """Handle to a spawned process. Provides event stream and control."""

    def __init__(self, events: EventStream) -> None:
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
# Builtin agent tools
# ---------------------------------------------------------------------------

def _builtin_tools() -> list[dict[str, Any]]:
    return [
        {
            "name": "message",
            "description": "Send a message to a connected agent.",
            "parameters": {
                "type": "object",
                "properties": {
                    "receiver": {"type": "string"},
                    "message": {"type": "string"},
                },
                "required": ["receiver", "message"],
            },
        },
        {
            "name": "send_file",
            "description": "Send a file to a connected agent.",
            "parameters": {
                "type": "object",
                "properties": {
                    "receiver": {"type": "string"},
                    "path": {"type": "string"},
                    "dest_path": {"type": "string"},
                },
                "required": ["receiver", "path"],
            },
        },
        {
            "name": "download_file",
            "description": "Download a file from a connected agent.",
            "parameters": {
                "type": "object",
                "properties": {
                    "sender": {"type": "string"},
                    "path": {"type": "string"},
                    "dest_path": {"type": "string"},
                },
                "required": ["sender", "path"],
            },
        },
    ]


# ---------------------------------------------------------------------------
# Runtime (singleton infrastructure)
# ---------------------------------------------------------------------------

class Runtime:
    """Manages the server, agent registry, and tool dispatch.

    Created automatically by the root ``@agent_process``. Not intended for
    direct use in normal code; exists as infrastructure that process scopes
    share.
    """

    def __init__(self, *, image: Image | None = None, log_dir: Path | str | None = None):
        self.image = image or LocalImage()
        self._execution_id: str | None = None
        self.server_url: str | None = None
        self._records: dict[str, AgentRecord] = {}
        self._edges: set[tuple[str, str]] = set()
        self._log_dir_root = Path(log_dir) if log_dir else None
        self._server: Server | None = None

    @property
    def execution_id(self) -> str | None:
        return self._execution_id

    async def start(self) -> None:
        self._execution_id = str(uuid.uuid4())
        self._server = Server(self)
        try:
            await self._server.start()
        except Exception:
            self._server = None
            self.server_url = None
            self._execution_id = None
            raise
        self.server_url = f"ws://127.0.0.1:{self._server.port}"

    async def close(self) -> None:
        if self._server is not None:
            await self._server.stop()
            self._server = None
        self.server_url = None
        self._execution_id = None

    # -- Agent creation (called by ambient agent()) --

    async def _create_agent(
        self,
        name: str,
        *,
        system_prompt: str | None = None,
        image: Image | None = None,
        machine: Machine | None = None,
        scope: ProcessScope,
    ) -> tuple[Agent, Machine | None]:
        """Create, register, and launch an agent.

        Returns ``(agent, spawned_machine)`` where *spawned_machine* is the
        machine that was created (so the scope can track it), or ``None`` if
        an existing machine was passed in.
        """
        if name in self._records:
            raise ValueError(f"Agent '{name}' already exists")
        if machine is not None and image is not None:
            raise ValueError("Pass either machine= or image=, not both")

        spawned_machine: Machine | None = None
        resolved_machine = machine
        if resolved_machine is None:
            resolved_machine = await (image or self.image).spawn()
            spawned_machine = resolved_machine

        ag = Agent(
            name=name,
            machine=resolved_machine,
            system_prompt=system_prompt,
        )
        ag._runtime = self
        ag._scope = scope

        log_dir = None
        if self._log_dir_root is not None and self._execution_id:
            log_dir = self._log_dir_root / self._execution_id
        log = AgentEventLog(
            log_dir=log_dir,
            agent_name=name,
            on_append=lambda entry: ag._events.emit(entry.type, entry.data),
        )

        self._records[name] = AgentRecord(agent=ag, log=log)
        log.append("agent_created", "server", {"agent": name})

        try:
            await self._spawn_agent(ag)
        except Exception:
            self._records.pop(name, None)
            raise

        return ag, spawned_machine

    # -- Connections --

    def _connect(self, a: Agent, b: Agent, *, direction: str = "both") -> None:
        if direction not in {"both", "forward"}:
            raise ValueError("direction must be 'both' or 'forward'")
        self._edges.add((a.name, b.name))
        if direction == "both":
            self._edges.add((b.name, a.name))

    # -- Tool handler registration --

    def _register_tool_handler(
        self,
        agent: Agent,
        tool_name: str,
        fn: Callable[..., Awaitable[Any]],
    ) -> None:
        # Capture the scope at registration time so that done()/fail()
        # resolve the correct process when the handler is invoked later.
        fn._handler_scope = _current_process.get()  # type: ignore[attr-defined]
        agent._handlers[tool_name] = fn
        rec = self._records.get(agent.name)
        if rec and rec.registered.is_set():
            tool_def = build_tool_definition(tool_name, fn)
            entry = rec.log.append("tool_registered", "server", tool_def)
            asyncio.ensure_future(rec.push(entry))

    # -- Exec --

    async def _exec_agent(
        self,
        agent: Agent,
        command: str,
        *,
        user: str = "agent",
        timeout: int | None = None,
    ) -> ExecResult:
        return await agent.machine.exec(command, user=user, timeout=timeout)

    # -- Messaging --

    def _send_message(self, agent_name: str, message: str) -> None:
        rec = self._get_record(agent_name)
        entry = rec.log.append("message", "server", {"text": message})
        asyncio.ensure_future(rec.push(entry))

    # -- Agent spawn / registration --

    async def _spawn_agent(self, agent: Agent) -> None:
        if not await self._launch_agent(agent):
            return
        rec = self._records[agent.name]
        try:
            await asyncio.wait_for(rec.registered.wait(), timeout=120)
        except asyncio.TimeoutError as exc:
            raise RuntimeError(
                f"Agent '{agent.name}' did not register within 120s. "
                f"Check tmux session: {_agent_session_name(self.execution_id or '', agent.name)}"
            ) from exc

    async def _launch_agent(self, agent: Agent) -> bool:
        pi_command = shutil.which("pi")
        tmux_command = shutil.which("tmux")
        if not pi_command or not tmux_command:
            raise RuntimeError("pi and tmux must both be available to launch agents")

        extension_path = _agent_extension_path(self.execution_id or "", agent.name)
        await agent.machine.write_file(extension_path, extension_source())

        server_url = self.server_url
        if server_url is None:
            raise RuntimeError("Server is not running")

        env = {
            "DRUIDS_SERVER_URL": server_url,
            "DRUIDS_EXECUTION_ID": self.execution_id or "",
            "DRUIDS_AGENT_ID": agent.name,
            "DRUIDS_SYSTEM_PROMPT": agent.system_prompt or "",
        }
        session_name = _agent_session_name(self.execution_id or "", agent.name)
        command = _build_agent_launch_command(
            pi_command=pi_command,
            tmux_command=tmux_command,
            extension_path=extension_path,
            env=env,
            session_name=session_name,
        )
        result = await agent.machine.exec(command)
        if not result.ok:
            raise RuntimeError(
                result.stderr.strip()
                or result.stdout.strip()
                or f"Failed to launch agent '{agent.name}'"
            )

        rec = self._records[agent.name]
        rec.log.append("agent_spawned", "server", {
            "agent": agent.name,
            "tmux_session": session_name,
        })
        return True

    def _register_agent(self, agent_id: str, execution_id: str) -> list[dict[str, Any]]:
        if execution_id != self.execution_id:
            raise ToolCallError("Execution ID mismatch", status_code=400)
        rec = self._get_record(agent_id)
        rec.registered.set()
        return _builtin_tools() + [
            build_tool_definition(name, handler)
            for name, handler in rec.agent._handlers.items()
        ]

    # -- Tool call dispatch --

    async def _handle_tool_call_request(
        self, agent_id: str, tool_name: str, params: dict[str, Any]
    ) -> Any:
        self._get_record(agent_id)

        if tool_name == "message":
            return await self._message(agent_id, params)
        if tool_name == "send_file":
            return await self._send_file(agent_id, params)
        if tool_name == "download_file":
            return await self._download_file(agent_id, params)
        return await self._invoke_handler(self._get_record(agent_id), tool_name, params)

    async def _invoke_handler(
        self, rec: AgentRecord, tool_name: str, params: dict[str, Any]
    ) -> Any:
        handler = rec.agent._handlers.get(tool_name)
        if handler is None:
            raise ToolCallError(
                f"Unknown tool '{tool_name}' for agent '{rec.agent.name}'", status_code=404
            )

        # Use the scope captured at handler registration time, falling back
        # to the agent's own scope.
        scope = getattr(handler, "_handler_scope", None) or rec.agent._scope
        token = _current_process.set(scope)
        try:
            return await handler(**params)
        finally:
            _current_process.reset(token)

    # -- Builtin tool implementations --

    async def _message(self, sender: str, params: dict[str, Any]) -> str:
        receiver = str(params.get("receiver", ""))
        message = str(params.get("message", ""))
        self._get_record(receiver)
        self._require_connection(sender, receiver)
        self._send_message(receiver, f"[From: {sender}] {message}")
        return f"Message sent to {receiver}."

    async def _send_file(self, sender: str, params: dict[str, Any]) -> str:
        sender_rec = self._get_record(sender)
        receiver = str(params.get("receiver", ""))
        path = str(params.get("path", ""))
        dest_path = str(params.get("dest_path") or path)
        receiver_rec = self._get_record(receiver)
        self._require_connection(sender, receiver)
        content = await sender_rec.agent.machine.read_file(path)
        await receiver_rec.agent.machine.write_file(dest_path, content)
        return f"Sent {len(content)} bytes to {receiver}:{dest_path}."

    async def _download_file(self, requester: str, params: dict[str, Any]) -> str:
        requester_rec = self._get_record(requester)
        sender = str(params.get("sender", ""))
        path = str(params.get("path", ""))
        dest_path = str(params.get("dest_path") or path)
        sender_rec = self._get_record(sender)
        self._require_connection(sender, requester)
        content = await sender_rec.agent.machine.read_file(path)
        await requester_rec.agent.machine.write_file(dest_path, content)
        return f"Downloaded {len(content)} bytes from {sender}:{path} to {dest_path}."

    # -- Helpers --

    def _get_record(self, name: str) -> AgentRecord:
        rec = self._records.get(name)
        if rec is None:
            raise ToolCallError(f"Unknown agent '{name}'", status_code=404)
        return rec

    def _require_connection(self, sender: str, receiver: str) -> None:
        if (sender, receiver) not in self._edges:
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
) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[Any]]] | Callable[P, Awaitable[Any]]:
    from functools import wraps

    def decorate(coro_fn: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[Any]]:
        if not inspect.iscoroutinefunction(coro_fn):
            raise TypeError("@agent_process requires an async function")

        @wraps(coro_fn)
        async def wrapped(*args: P.args, **kwargs: P.kwargs) -> Any:
            parent = _current_process.get()
            is_root = parent is None

            if is_root:
                runtime = Runtime(
                    image=image,
                    log_dir=Path(log_dir) if log_dir else None,
                )
                await runtime.start()
            else:
                runtime = parent.runtime

            # If spawned, the handle's event stream becomes the scope's stream
            handle = _spawn_handle.get()

            scope = ProcessScope(parent=parent, runtime=runtime)

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
    ag, spawned_machine = await scope.runtime._create_agent(
        name,
        system_prompt=system_prompt,
        image=image,
        machine=machine,
        scope=scope,
    )
    scope.agents.append(ag)
    if spawned_machine is not None:
        scope.machines.append(spawned_machine)
    return ag


async def machine(image: Image | None = None) -> Machine:
    scope = _require_scope()
    m = await (image or scope.runtime.image).spawn()
    scope.machines.append(m)
    return m


def connect(a: Agent, b: Agent, *, direction: str = "both") -> None:
    scope = _require_scope()
    scope.runtime._connect(a, b, direction=direction)


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
    events = EventStream()
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
