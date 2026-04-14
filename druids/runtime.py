from __future__ import annotations

import asyncio
import inspect
import shlex
import shutil
import uuid
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from functools import wraps
from typing import Any, Awaitable, Callable, Literal, ParamSpec, TypeVar

from druids.extension import extension_source
from druids.machines import Image, LocalImage, Machine
from druids.schema import build_tool_definition
from druids.server import AgentChannel, OrchestratorServer, SSEEvent
from druids.types import ExecResult, ExecutionFailed, ToolCallError


_CURRENT_RUNTIME: ContextVar[Runtime | None] = ContextVar(
    "druids_current_runtime", default=None
)

P = ParamSpec("P")
R = TypeVar("R")


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


@dataclass
class ExecutionState:
    id: str
    outcome: asyncio.Future[tuple[str, Any]]

    @property
    def status(self) -> Literal["running", "done", "failed"]:
        if not self.outcome.done():
            return "running"
        outcome, _ = self.outcome.result()
        return outcome

    def exit(self, result: Any = None) -> None:
        if not self.outcome.done():
            self.outcome.set_result(("done", result))

    def fail(self, reason: str) -> None:
        if not self.outcome.done():
            self.outcome.set_result(("failed", reason))

    async def wait(self, *, timeout: float | None = None) -> Any:
        outcome, value = await (
            asyncio.wait_for(asyncio.shield(self.outcome), timeout=timeout)
            if timeout is not None
            else asyncio.shield(self.outcome)
        )
        if outcome == "failed":
            raise ExecutionFailed(str(value))
        return value


@dataclass
class Agent:
    name: str
    machine: Machine
    system_prompt: str | None = None
    _handlers: dict[str, Callable[..., Awaitable[Any]]] = field(default_factory=dict)
    _channel: AgentChannel = field(default_factory=AgentChannel, repr=False)

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
            self._handlers[tool_name] = fn

            runtime = _CURRENT_RUNTIME.get()
            if (
                runtime is not None
                and runtime.execution is not None
                and self._channel.registered.is_set()
                and not runtime._shutting_down
            ):
                runtime._push_event(
                    self.name, "new_tool", build_tool_definition(tool_name, fn)
                )
            return fn

        return decorator

    async def send(self, message: str) -> None:
        current_runtime()._send_message(self.name, message)

    async def exec(
        self, command: str, *, user: str = "agent", timeout: int | None = None
    ) -> ExecResult:
        current_runtime()._require_agent(self.name)
        return await self.machine.exec(command, user=user, timeout=timeout)


class Runtime:
    def __init__(self, *, image: Image | None = None):
        self.image = image or LocalImage()

        self.execution: ExecutionState | None = None
        self.server_url: str | None = None

        self._agents: dict[str, Agent] = {}
        self._machines: list[Machine] = []
        self._edges: set[tuple[str, str]] = set()

        self._started = False
        self._shutting_down = False
        self._server: OrchestratorServer | None = None
        self._runtime_token: Token[Runtime | None] | None = None

    @property
    def execution_id(self) -> str | None:
        execution = self.execution
        return execution.id if execution is not None else None

    async def __aenter__(self) -> Runtime:
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    async def start(self) -> None:
        if self.execution is not None:
            raise RuntimeError("Runtime is already running")
        if self._started:
            raise RuntimeError(
                "Runtime instances are single-use. Create a new Runtime for each execution."
            )
        if _CURRENT_RUNTIME.get() is not None:
            raise RuntimeError("Nested runtimes are not supported")

        self._started = True
        self._runtime_token = _CURRENT_RUNTIME.set(self)
        self.execution = ExecutionState(
            id=str(uuid.uuid4()),
            outcome=asyncio.get_running_loop().create_future(),
        )
        self._shutting_down = False
        self._server = OrchestratorServer(self)

        try:
            await self._server.start()
        except Exception:
            self._server = None
            self.server_url = None
            self.execution = None
            self._deactivate()
            raise

        self.server_url = f"http://127.0.0.1:{self._server.port}"

    async def close(self) -> None:
        try:
            await self._shutdown()
        finally:
            self._deactivate()

    async def run(
        self,
        setup: Callable[[], Awaitable[None]] | None = None,
        *,
        timeout: float | None = None,
    ) -> Any:
        async with self:
            if setup is not None:
                await setup()
            return await self.wait(timeout=timeout)

    async def agent(
        self,
        name: str,
        *,
        system_prompt: str | None = None,
        image: Image | None = None,
        machine: Machine | None = None,
    ) -> Agent:
        self._require_execution()
        if name in self._agents:
            raise ValueError(f"Agent '{name}' already exists")

        agent = Agent(
            name=name,
            machine=await self._resolve_machine(machine, image),
            system_prompt=system_prompt,
        )
        self._agents[name] = agent
        await self._spawn_agent(agent)
        return agent

    async def machine(self, image: Image | None = None) -> Machine:
        self._require_execution()
        machine = await (image or self.image).spawn()
        self._machines.append(machine)
        return machine

    def connect(self, a: Agent, b: Agent, *, direction: str = "both") -> None:
        self._require_agent(a.name)
        self._require_agent(b.name)
        if direction not in {"both", "forward"}:
            raise ValueError("direction must be 'both' or 'forward'")
        self._edges.add((a.name, b.name))
        if direction == "both":
            self._edges.add((b.name, a.name))

    def exit(self, result: Any = None) -> None:
        self._require_execution().exit(result)

    def fail(self, reason: str) -> None:
        self._require_execution().fail(reason)

    async def wait(self, *, timeout: float | None = None) -> Any:
        return await self._require_execution().wait(timeout=timeout)

    async def _resolve_machine(
        self, machine: Machine | None, image: Image | None
    ) -> Machine:
        if machine is not None and image is not None:
            raise ValueError("Pass either machine= or image=, not both")
        if machine is not None:
            self._machines.append(machine)
            return machine
        machine = await (image or self.image).spawn()
        self._machines.append(machine)
        return machine

    def _require_execution(self) -> ExecutionState:
        execution = self.execution
        if execution is None:
            raise RuntimeError(
                "No active runtime. Use @agent_runtime, 'async with Runtime(...)', or 'await runtime.start()'."
            )
        return execution

    def _deactivate(self) -> None:
        if self._runtime_token is None:
            return
        _CURRENT_RUNTIME.reset(self._runtime_token)
        self._runtime_token = None

    async def _spawn_agent(self, agent: Agent) -> None:
        if not await self._launch_agent(agent):
            return
        try:
            await asyncio.wait_for(agent._channel.registered.wait(), timeout=120)
        except asyncio.TimeoutError as exc:
            raise RuntimeError(
                f"Agent '{agent.name}' did not register within 120s. "
                f"Check tmux session: druids-{self.execution_id}-{agent.name}"
            ) from exc

    async def _launch_agent(self, agent: Agent) -> bool:
        pi_command = shutil.which("pi")
        tmux_command = shutil.which("tmux")
        if not pi_command or not tmux_command:
            raise RuntimeError("pi and tmux must both be available to launch agents")

        extension_path = f"/tmp/druids-extension-{self.execution_id}-{agent.name}.ts"
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
        env_prefix = " ".join(
            f"{key}={shlex.quote(value)}" for key, value in env.items()
        )
        pi_invocation = f"env {env_prefix} {shlex.quote(pi_command)} --extension {shlex.quote(extension_path)}"
        session_name = f"druids-{self.execution_id}-{agent.name}"
        command = (
            f"{shlex.quote(tmux_command)} has-session -t {shlex.quote(session_name)} 2>/dev/null && "
            f"{shlex.quote(tmux_command)} kill-session -t {shlex.quote(session_name)}; "
            f"{shlex.quote(tmux_command)} new-session -d -s {shlex.quote(session_name)} "
            f"/bin/bash -lc {shlex.quote(pi_invocation)}"
        )
        result = await agent.machine.exec(command)
        if not result.ok:
            raise RuntimeError(
                result.stderr.strip()
                or result.stdout.strip()
                or f"Failed to launch agent '{agent.name}'"
            )
        return True

    def _push_event(self, agent_name: str, event: str, data: dict[str, Any]) -> None:
        agent = self._agents.get(agent_name)
        if agent is None:
            raise ToolCallError(f"Unknown agent '{agent_name}'", status_code=404)
        agent._channel.publish(SSEEvent(event=event, data=data))

    def _send_message(self, agent_name: str, message: str) -> None:
        self._require_agent(agent_name)
        self._push_event(agent_name, "message", {"text": message})

    def _register_agent(self, agent_id: str, execution_id: str) -> list[dict[str, Any]]:
        if execution_id != self.execution_id:
            raise ToolCallError("Execution ID mismatch", status_code=400)
        agent = self._agents.get(agent_id)
        if agent is None:
            raise ToolCallError(f"Unknown agent '{agent_id}'", status_code=404)

        agent._channel.registered.set()
        return _builtin_tools() + [
            build_tool_definition(name, handler)
            for name, handler in agent._handlers.items()
        ]

    def _subscribe_events(self, agent_id: str) -> asyncio.Queue[SSEEvent]:
        agent = self._agents.get(agent_id)
        if agent is None:
            raise ToolCallError(f"Unknown agent '{agent_id}'", status_code=404)
        return agent._channel.subscribe()

    def _unsubscribe_events(
        self, agent_id: str, subscription: asyncio.Queue[SSEEvent]
    ) -> None:
        agent = self._agents.get(agent_id)
        if agent is not None:
            agent._channel.unsubscribe(subscription)

    async def _handle_tool_call_request(
        self, agent_id: str, tool_name: str, params: dict[str, Any]
    ) -> Any:
        agent = self._agents.get(agent_id)
        if agent is None:
            raise ToolCallError(f"Unknown agent '{agent_id}'", status_code=404)

        if tool_name == "message":
            return await self._message(agent_id, params)
        if tool_name == "send_file":
            return await self._send_file(agent_id, params)
        if tool_name == "download_file":
            return await self._download_file(agent_id, params)
        return await self._invoke_handler(agent, tool_name, params)

    async def _invoke_handler(
        self, agent: Agent, tool_name: str, params: dict[str, Any]
    ) -> Any:
        handler = agent._handlers.get(tool_name)
        if handler is None:
            raise ToolCallError(
                f"Unknown tool '{tool_name}' for agent '{agent.name}'", status_code=404
            )

        token = _CURRENT_RUNTIME.set(self)
        try:
            return await handler(**params)
        finally:
            _CURRENT_RUNTIME.reset(token)

    async def _message(self, sender: str, params: dict[str, Any]) -> str:
        receiver = str(params.get("receiver", ""))
        message = str(params.get("message", ""))
        self._require_agent(receiver)
        self._require_connection(sender, receiver)
        self._send_message(receiver, f"[From: {sender}] {message}")
        return f"Message sent to {receiver}."

    async def _send_file(self, sender: str, params: dict[str, Any]) -> str:
        receiver = str(params.get("receiver", ""))
        path = str(params.get("path", ""))
        dest_path = str(params.get("dest_path") or path)
        self._require_agent(receiver)
        self._require_connection(sender, receiver)
        content = await self._agents[sender].machine.read_file(path)
        await self._agents[receiver].machine.write_file(dest_path, content)
        return f"Sent {len(content)} bytes to {receiver}:{dest_path}."

    async def _download_file(self, requester: str, params: dict[str, Any]) -> str:
        sender = str(params.get("sender", ""))
        path = str(params.get("path", ""))
        dest_path = str(params.get("dest_path") or path)
        self._require_agent(sender)
        self._require_connection(sender, requester)
        content = await self._agents[sender].machine.read_file(path)
        await self._agents[requester].machine.write_file(dest_path, content)
        return f"Downloaded {len(content)} bytes from {sender}:{path} to {dest_path}."

    def _require_agent(self, name: str) -> None:
        if name not in self._agents:
            raise ToolCallError(f"Unknown agent '{name}'", status_code=404)

    def _require_connection(self, sender: str, receiver: str) -> None:
        if (sender, receiver) not in self._edges:
            raise ToolCallError(
                f"Agent '{sender}' is not connected to '{receiver}'", status_code=403
            )

    async def _shutdown(self) -> None:
        if self.execution is None:
            return

        self._shutting_down = True

        for agent_name in list(self._agents):
            try:
                self._push_event(agent_name, "shutdown", {})
            except Exception:
                pass

        await asyncio.sleep(0.2)

        if self._server is not None:
            await self._server.stop()
            self._server = None

        for agent in self._agents.values():
            session_name = f"druids-{self.execution_id}-{agent.name}"
            try:
                await agent.machine.exec(
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
        self.execution = None


def current_runtime() -> Runtime:
    runtime = _CURRENT_RUNTIME.get()
    if runtime is None:
        raise RuntimeError(
            "No active runtime. Use @agent_runtime, 'async with Runtime(...)', or 'await runtime.start()'."
        )
    return runtime


def current_execution() -> ExecutionState:
    return current_runtime()._require_execution()


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


async def wait(*, timeout: float | None = None) -> Any:
    return await current_runtime().wait(timeout=timeout)


def agent_runtime(
    fn: Callable[P, Awaitable[R]] | None = None,
    *,
    image: Image | None = None,
) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R]]] | Callable[P, Awaitable[R]]:
    def decorate(coro_fn: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
        if not inspect.iscoroutinefunction(coro_fn):
            raise TypeError("@agent_runtime requires an async function")

        @wraps(coro_fn)
        async def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
            async with Runtime(image=image):
                return await coro_fn(*args, **kwargs)

        return wrapped

    if fn is None:
        return decorate
    return decorate(fn)
