from __future__ import annotations

import asyncio
import contextlib
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
from druids.extension import extension_source


async def _noop(_entry: LogEntry) -> None:
    pass
from druids.machines import Image, LocalImage, Machine
from druids.schema import build_tool_definition
from druids.server import Server
from druids.types import ExecResult, ExecutionFailed, ToolCallError


@dataclass
class AgentRecord:
    """Single source of truth for all per-agent state."""

    agent: Agent
    log: AgentEventLog = field(repr=False)
    registered: asyncio.Event = field(default_factory=asyncio.Event)
    _notify: Callable[[LogEntry], Awaitable[None]] = field(
        default=_noop, init=False, repr=False
    )

    async def push(self, entry: LogEntry) -> None:
        """Deliver a log entry to the connected agent."""
        try:
            await self._notify(entry)
        except Exception:
            pass

    async def push_entries(self, entries: list[LogEntry]) -> None:
        for entry in entries:
            await self.push(entry)


_CURRENT_RUNTIME: ContextVar[Runtime | None] = ContextVar(
    "druids_current_runtime", default=None
)

P = ParamSpec("P")
R = TypeVar("R")

_NO_ACTIVE_RUNTIME_ERROR = (
    "No active runtime. Use @agent_runtime, 'async with Runtime(...)', or 'await runtime.start()'."
)


async def _run_until_exit(body: Awaitable[Any] | None, *, timeout: float | None) -> Any:
    if body is None:
        return await current_runtime().wait(timeout=timeout)

    body_task = asyncio.create_task(body)
    exit_task = asyncio.create_task(current_runtime().wait(timeout=timeout))
    try:
        done, _ = await asyncio.wait(
            {body_task, exit_task}, return_when=asyncio.FIRST_COMPLETED
        )
        if body_task in done:
            await body_task
            return await exit_task

        result = await exit_task
        body_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await body_task
        return result
    finally:
        if not body_task.done():
            body_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await body_task
        if not exit_task.done():
            exit_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await exit_task


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
        {
            "name": "set_state",
            "description": "Set a key-value pair in this agent's own state store.",
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {"type": "string"},
                    "value": {"type": "string"},
                },
                "required": ["key", "value"],
            },
        },
        {
            "name": "get_state",
            "description": "Get the value for a key from this agent's own state store. Returns null if the key does not exist.",
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {"type": "string"},
                },
                "required": ["key"],
            },
        },
    ]


class Runtime:
    def __init__(self, *, image: Image | None = None, log_dir: Path | str | None = None):
        self.image = image or LocalImage()

        self._execution_id: str | None = None
        self._outcome: asyncio.Future[tuple[str, Any]] | None = None
        self.server_url: str | None = None

        self._records: dict[str, AgentRecord] = {}
        self._machines: list[Machine] = []
        self._edges: set[tuple[str, str]] = set()

        self._log_dir_root = Path(log_dir) if log_dir else None
        self._started = False
        self._shutting_down = False
        self._server: Server | None = None

    @property
    def execution_id(self) -> str | None:
        return self._execution_id

    async def __aenter__(self) -> Runtime:
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    async def start(self) -> None:
        if self._outcome is not None:
            raise RuntimeError("Runtime is already running")
        if self._started:
            raise RuntimeError(
                "Runtime instances are single-use. Create a new Runtime for each execution."
            )
        if _CURRENT_RUNTIME.get() is not None:
            raise RuntimeError("Nested runtimes are not supported")

        self._started = True
        _CURRENT_RUNTIME.set(self)
        self._execution_id = str(uuid.uuid4())
        self._outcome = asyncio.get_running_loop().create_future()
        self._shutting_down = False
        self._server = Server(self)

        try:
            await self._server.start()
        except Exception:
            self._server = None
            self.server_url = None
            self._execution_id = None
            self._outcome = None
            self._clear_current_runtime()
            raise

        self.server_url = f"ws://127.0.0.1:{self._server.port}"

    async def close(self) -> None:
        try:
            await self._shutdown()
        finally:
            self._clear_current_runtime()

    async def agent(
        self,
        name: str,
        *,
        system_prompt: str | None = None,
        image: Image | None = None,
        machine: Machine | None = None,
    ) -> Agent:
        self._require_active()
        if name in self._records:
            raise ValueError(f"Agent '{name}' already exists")

        if machine is not None and image is not None:
            raise ValueError("Pass either machine= or image=, not both")
        resolved_machine = machine
        if resolved_machine is None:
            resolved_machine = await (image or self.image).spawn()
        self._machines.append(resolved_machine)

        ag = Agent(
            name=name,
            machine=resolved_machine,
            system_prompt=system_prompt,
        )
        ag._runtime = self

        log_dir = None
        if self._log_dir_root is not None and self._execution_id:
            log_dir = self._log_dir_root / self._execution_id
        log = AgentEventLog(log_dir=log_dir, agent_name=name)

        self._records[name] = AgentRecord(agent=ag, log=log)
        log.append("agent_created", "server", {"agent": name})

        try:
            await self._spawn_agent(ag)
        except Exception:
            self._records.pop(name, None)
            raise
        return ag

    async def machine(self, image: Image | None = None) -> Machine:
        self._require_active()
        machine = await (image or self.image).spawn()
        self._machines.append(machine)
        return machine

    def connect(self, a: Agent, b: Agent, *, direction: str = "both") -> None:
        self._require_active()
        if a._runtime is not self or b._runtime is not self:
            raise RuntimeError("Agents must belong to the current runtime.")
        if direction not in {"both", "forward"}:
            raise ValueError("direction must be 'both' or 'forward'")
        self._edges.add((a.name, b.name))
        if direction == "both":
            self._edges.add((b.name, a.name))

    def _register_tool_handler(
        self,
        agent: Agent,
        tool_name: str,
        fn: Callable[..., Awaitable[Any]],
    ) -> None:
        self._require_active()
        agent._handlers[tool_name] = fn
        rec = self._records.get(agent.name)
        if rec and rec.registered.is_set() and not self._shutting_down:
            tool_def = build_tool_definition(tool_name, fn)
            entry = rec.log.append("tool_registered", "server", tool_def)
            asyncio.ensure_future(rec.push(entry))

    async def _exec_agent(
        self,
        agent: Agent,
        command: str,
        *,
        user: str = "agent",
        timeout: int | None = None,
    ) -> ExecResult:
        self._require_active()
        return await agent.machine.exec(command, user=user, timeout=timeout)

    def exit(self, result: Any = None) -> None:
        self._require_active()
        assert self._outcome is not None
        for rec in self._records.values():
            rec.log.append("done", "server", {"result": result})
        self._outcome.set_result(("done", result))

    def fail(self, reason: str) -> None:
        self._require_active()
        assert self._outcome is not None
        for rec in self._records.values():
            rec.log.append("failed", "server", {"reason": reason})
        self._outcome.set_result(("failed", reason))

    async def wait(self, *, timeout: float | None = None) -> Any:
        outcome = self._outcome
        if outcome is None:
            raise RuntimeError(_NO_ACTIVE_RUNTIME_ERROR)
        status, value = await (
            asyncio.wait_for(asyncio.shield(outcome), timeout=timeout)
            if timeout is not None
            else asyncio.shield(outcome)
        )
        if status == "failed":
            raise ExecutionFailed(str(value))
        return value

    def _require_active(self) -> None:
        if not self._is_active():
            raise RuntimeError(_NO_ACTIVE_RUNTIME_ERROR)

    def _is_active(self) -> bool:
        outcome = self._outcome
        return outcome is not None and not outcome.done()

    def _clear_current_runtime(self) -> None:
        if _CURRENT_RUNTIME.get() is self:
            _CURRENT_RUNTIME.set(None)

    def _get_record(self, name: str) -> AgentRecord:
        rec = self._records.get(name)
        if rec is None:
            raise ToolCallError(f"Unknown agent '{name}'", status_code=404)
        return rec

    def _get_event_log(self, agent_name: str) -> AgentEventLog:
        return self._get_record(agent_name).log

    def _is_agent_registered(self, name: str) -> bool:
        rec = self._records.get(name)
        return rec is not None and rec.registered.is_set()

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

    def _send_message(self, agent_name: str, message: str) -> None:
        self._require_active()
        rec = self._get_record(agent_name)
        entry = rec.log.append("message", "server", {"text": message})
        asyncio.ensure_future(rec.push(entry))

    def _register_agent(self, agent_id: str, execution_id: str) -> list[dict[str, Any]]:
        if execution_id != self.execution_id:
            raise ToolCallError("Execution ID mismatch", status_code=400)
        rec = self._get_record(agent_id)
        rec.registered.set()
        return _builtin_tools() + [
            build_tool_definition(name, handler)
            for name, handler in rec.agent._handlers.items()
        ]

    async def _handle_tool_call_request(
        self, agent_id: str, tool_name: str, params: dict[str, Any]
    ) -> Any:
        rec = self._get_record(agent_id)

        if tool_name == "message":
            return await self._message(agent_id, params)
        if tool_name == "send_file":
            return await self._send_file(agent_id, params)
        if tool_name == "download_file":
            return await self._download_file(agent_id, params)
        if tool_name == "set_state":
            return self._set_state(agent_id, params)
        if tool_name == "get_state":
            return self._get_state(agent_id, params)
        return await self._invoke_handler(rec, tool_name, params)

    async def _invoke_handler(
        self, rec: AgentRecord, tool_name: str, params: dict[str, Any]
    ) -> Any:
        handler = rec.agent._handlers.get(tool_name)
        if handler is None:
            raise ToolCallError(
                f"Unknown tool '{tool_name}' for agent '{rec.agent.name}'", status_code=404
            )

        _CURRENT_RUNTIME.set(self)
        try:
            return await handler(**params)
        finally:
            if _CURRENT_RUNTIME.get() is self:
                _CURRENT_RUNTIME.set(None)

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

    def _set_state(self, agent_id: str, params: dict[str, Any]) -> str:
        rec = self._get_record(agent_id)
        key = str(params.get("key", ""))
        value = params.get("value", "")
        rec.agent.state[key] = value
        return f"Set state '{key}'."

    def _get_state(self, agent_id: str, params: dict[str, Any]) -> Any:
        rec = self._get_record(agent_id)
        key = str(params.get("key", ""))
        return rec.agent.state.get(key)

    def _require_connection(self, sender: str, receiver: str) -> None:
        if (sender, receiver) not in self._edges:
            raise ToolCallError(
                f"Agent '{sender}' is not connected to '{receiver}'", status_code=403
            )

    async def _shutdown(self) -> None:
        if self._outcome is None:
            return

        self._shutting_down = True

        for rec in self._records.values():
            try:
                entry = rec.log.append("shutdown", "server")
                await rec.push(entry)
            except Exception:
                pass

        await asyncio.sleep(0.2)

        if self._server is not None:
            await self._server.stop()
            self._server = None

        for rec in self._records.values():
            session_name = _agent_session_name(self.execution_id or "", rec.agent.name)
            try:
                await rec.agent.machine.exec(
                    f"tmux kill-session -t {shlex.quote(session_name)} 2>/dev/null || true",
                    timeout=5,
                )
            except Exception:
                pass

        seen: set[int] = set()
        for machine in self._machines:
            if id(machine) in seen:
                continue
            seen.add(id(machine))
            try:
                await machine.stop()
            except Exception:
                pass

        self.server_url = None
        self._execution_id = None
        self._outcome = None


def current_runtime() -> Runtime:
    runtime = _CURRENT_RUNTIME.get()
    if runtime is None or not runtime._is_active():
        raise RuntimeError(_NO_ACTIVE_RUNTIME_ERROR)
    return runtime


async def agent(
    name: str,
    *,
    system_prompt: str | None = None,
    image: Image | None = None,
    machine: Machine | None = None,
) -> Agent:
    return await current_runtime().agent(
        name,
        system_prompt=system_prompt,
        image=image,
        machine=machine,
    )


async def machine(image: Image | None = None) -> Machine:
    return await current_runtime().machine(image=image)


def connect(a: Agent, b: Agent, *, direction: str = "both") -> None:
    current_runtime().connect(a, b, direction=direction)


def exit(result: Any = None) -> None:
    current_runtime().exit(result)


def fail(reason: str) -> None:
    current_runtime().fail(reason)


def agent_runtime(
    fn: Callable[P, Awaitable[R]] | None = None,
    *,
    image: Image | None = None,
    timeout: float | None = None,
) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R]]] | Callable[P, Awaitable[Any]]:
    from functools import wraps

    def decorate(coro_fn: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
        if not inspect.iscoroutinefunction(coro_fn):
            raise TypeError("@agent_runtime requires an async function")

        @wraps(coro_fn)
        async def wrapped(*args: P.args, **kwargs: P.kwargs) -> Any:
            async with Runtime(image=image):
                return await _run_until_exit(
                    coro_fn(*args, **kwargs),
                    timeout=timeout,
                )

        return wrapped

    if fn is None:
        return decorate
    return decorate(fn)
