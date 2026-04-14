from __future__ import annotations

import asyncio
import inspect
import json
import shlex
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from druids.extension import extension_source
from druids.machines import DockerMachine, Image, LocalImage, Machine
from druids.schema import build_tool_definition
from druids.server import AgentChannel, OrchestratorServer, SSEEvent
from druids.types import ExecResult, ExecutionFailed, LaunchError, ToolCallError, to_jsonable


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
            "name": "list_agents",
            "description": "List all agent names in this execution.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
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
class Agent:
    name: str
    _ctx: Context
    machine: Machine
    prompt: str | None = None
    system_prompt: str | None = None
    _handlers: dict[str, Callable[..., Any]] = field(default_factory=dict)
    _registered: bool = False
    _launched: bool = False
    _initial_prompt_sent: bool = False

    def on(self, tool_name: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Register a tool handler for this agent."""

        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            self._handlers[tool_name] = fn
            self._ctx._log_event("tool_registered", agent=self.name, tool=tool_name)
            if self._ctx._entered and self._registered and not self._ctx._shutting_down:
                self._ctx._push_event(self.name, "new_tool", build_tool_definition(tool_name, fn))
            return fn

        return decorator

    async def send(self, message: str) -> None:
        """Send a message to this agent."""
        self._ctx._ensure_entered()
        self._ctx._send_message(self.name, message)

    async def exec(self, command: str, *, user: str = "agent", timeout: int | None = None) -> ExecResult:
        """Run a shell command on this agent's machine."""
        self._ctx._ensure_entered()
        return await self.machine.exec(command, user=user, timeout=timeout)


class Context:
    def __init__(
        self,
        *,
        image: Image | None = None,
        server_url: str | None = None,
        launch_mode: str = "auto",
    ):
        if launch_mode not in {"auto", "always", "manual"}:
            raise ValueError("launch_mode must be 'auto', 'always', or 'manual'")
        self.image = image or LocalImage()
        self._configured_server_url = server_url
        self.launch_mode = launch_mode

        self.execution_id: str | None = None
        self.server_url: str | None = None
        self.log_path: Path | None = None

        self._agents: dict[str, Agent] = {}
        self._channels: dict[str, AgentChannel] = {}
        self._machines: list[Machine] = []
        self._machine_ids: set[int] = set()
        self._machine_images: dict[int, Image] = {}
        self._edges: set[tuple[str, str]] = set()

        self._status = "pending"
        self._result: Any = None
        self._failure_reason: str | None = None

        self._entered = False
        self._shutting_down = False
        self._server: OrchestratorServer | None = None
        self._completion_future: asyncio.Future[Any] | None = None
        self._server_stop_event: asyncio.Event | None = None
        self._no_active_tool_calls: asyncio.Event | None = None
        self._active_tool_calls = 0
        self._log_handle = None

    @property
    def agents(self) -> dict[str, Agent]:
        return dict(self._agents)

    @property
    def status(self) -> str:
        return self._status

    async def __aenter__(self) -> Context:
        if self._entered:
            raise RuntimeError("Context is already running")

        self.execution_id = str(uuid.uuid4())
        self._completion_future = asyncio.get_running_loop().create_future()
        self._server_stop_event = asyncio.Event()
        self._no_active_tool_calls = asyncio.Event()
        self._no_active_tool_calls.set()
        self._entered = True
        self._status = "running"
        self._shutting_down = False

        self._open_log()
        self._log_event("execution_started")
        await self._start_server()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self._shutdown()

    async def agent(
        self,
        name: str,
        *,
        prompt: str | None = None,
        system_prompt: str | None = None,
        image: Image | None = None,
        machine: Machine | None = None,
    ) -> Agent:
        self._ensure_entered()
        if name in self._agents:
            raise ValueError(f"Agent '{name}' already exists")

        resolved_machine, machine_image = await self._resolve_machine(machine, image)
        agent = Agent(
            name=name,
            _ctx=self,
            machine=resolved_machine,
            prompt=prompt,
            system_prompt=system_prompt,
        )
        self._agents[name] = agent
        self._channels[name] = AgentChannel()
        self._log_event("agent_created", agent=name)

        try:
            await self._spawn_agent(agent, machine_image=machine_image)
        except Exception:
            self._agents.pop(name, None)
            self._channels.pop(name, None)
            raise
        return agent

    async def machine(self, image: Image | None = None) -> Machine:
        self._ensure_entered()
        resolved_image = image or self.image
        machine = await resolved_image.spawn()
        self._track_machine(machine, resolved_image)
        return machine

    def connect(self, a: Agent | str, b: Agent | str, *, direction: str = "both") -> None:
        if direction not in {"both", "forward"}:
            raise ValueError("direction must be 'both' or 'forward'")
        a_name = a.name if isinstance(a, Agent) else a
        b_name = b.name if isinstance(b, Agent) else b
        self._edges.add((a_name, b_name))
        if direction == "both":
            self._edges.add((b_name, a_name))
        self._log_event("agents_connected", sender=a_name, receiver=b_name, direction=direction)

    def is_connected(self, sender: Agent | str, receiver: Agent | str) -> bool:
        sender_name = sender.name if isinstance(sender, Agent) else sender
        receiver_name = receiver.name if isinstance(receiver, Agent) else receiver
        return (sender_name, receiver_name) in self._edges

    async def done(self, result: Any = None) -> None:
        self._ensure_entered()
        if self._completion_future is None or self._completion_future.done():
            return
        self._status = "completed"
        self._result = result
        self._log_event("done", result=to_jsonable(result))
        self._completion_future.set_result(result)

    async def fail(self, reason: str) -> None:
        self._ensure_entered()
        if self._completion_future is None or self._completion_future.done():
            return
        self._status = "failed"
        self._failure_reason = reason
        self._log_event("fail", reason=reason)
        self._completion_future.set_result(None)

    async def wait(self, *, timeout: float | None = None) -> Any:
        self._ensure_entered()
        if self._completion_future is None:
            raise RuntimeError("Context is not running")
        await (
            asyncio.wait_for(asyncio.shield(self._completion_future), timeout=timeout)
            if timeout is not None
            else asyncio.shield(self._completion_future)
        )
        if self._status == "failed":
            raise ExecutionFailed(self._failure_reason or "Execution failed")
        return self._result

    async def _resolve_machine(self, machine: Machine | None, image: Image | None) -> tuple[Machine, Image | None]:
        if machine is not None and image is not None:
            raise ValueError("Pass either machine= or image=, not both")

        if machine is not None:
            tracked_image = self._machine_images.get(id(machine))
            self._track_machine(machine, tracked_image)
            return machine, tracked_image

        resolved_image = image or self.image
        resolved_machine = await resolved_image.spawn()
        self._track_machine(resolved_machine, resolved_image)
        return resolved_machine, resolved_image

    def _track_machine(self, machine: Machine, image: Image | None = None) -> None:
        machine_id = id(machine)
        if machine_id not in self._machine_ids:
            self._machine_ids.add(machine_id)
            self._machines.append(machine)
        if image is not None:
            self._machine_images[machine_id] = image

    def _open_log(self) -> None:
        execution_id = self.execution_id or "unknown"
        log_dir = Path.cwd() / "logs" / execution_id
        log_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = log_dir / "orchestrator.jsonl"
        self._log_handle = self.log_path.open("a", encoding="utf-8")

    def _log_event(self, event: str, **fields: Any) -> None:
        if self._log_handle is None:
            return
        payload = {
            "ts": time.time(),
            "event": event,
            "execution_id": self.execution_id,
            **{key: to_jsonable(value) for key, value in fields.items()},
        }
        self._log_handle.write(json.dumps(payload) + "\n")
        self._log_handle.flush()

    async def _start_server(self) -> None:
        bind_host, bind_port = self._resolve_server_binding()
        self._server = OrchestratorServer(self, bind_host, bind_port)
        await self._server.start()
        if self._configured_server_url is None:
            self.server_url = f"http://127.0.0.1:{self._server.port}"
        else:
            self.server_url = self._configured_server_url
        self._log_event("server_started", url=self.server_url)

    def _resolve_server_binding(self) -> tuple[str, int]:
        if self._configured_server_url is None:
            return ("127.0.0.1", 0)
        parsed = urlparse(self._configured_server_url)
        hostname = parsed.hostname or "0.0.0.0"
        if hostname not in {"127.0.0.1", "0.0.0.0", "localhost"}:
            hostname = "0.0.0.0"
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        return (hostname, port)

    def _ensure_entered(self) -> None:
        if not self._entered:
            raise RuntimeError("Context is not running. Use 'async with Context(...) as ctx'.")

    async def _spawn_agent(self, agent: Agent, *, machine_image: Image | None) -> None:
        self._log_event("agent_spawned", agent=agent.name)
        launched = await self._launch_agent(agent, machine_image=machine_image)
        agent._launched = launched
        if launched:
            channel = self._channels[agent.name]
            try:
                await asyncio.wait_for(channel.registered.wait(), timeout=120)
            except asyncio.TimeoutError as exc:
                raise LaunchError(
                    f"Agent '{agent.name}' did not register within 120s. "
                    f"Check tmux session: druids-{self.execution_id}-{agent.name}"
                ) from exc
            if agent.prompt is not None and not agent._initial_prompt_sent:
                agent._initial_prompt_sent = True
                self._send_message(agent.name, agent.prompt)

    async def _launch_agent(self, agent: Agent, *, machine_image: Image | None) -> bool:
        if self.launch_mode == "manual":
            self._log_event("agent_launch_skipped", agent=agent.name, reason="manual_mode")
            return False

        pi_command = shutil.which("pi")
        tmux_command = shutil.which("tmux")
        if not pi_command or not tmux_command:
            if self.launch_mode == "always":
                raise LaunchError("pi and tmux must both be available to launch agents")
            self._log_event("agent_launch_skipped", agent=agent.name, reason="missing_pi_or_tmux")
            return False

        extension_path = f"/tmp/druids-extension-{self.execution_id}-{agent.name}.ts"
        await agent.machine.write_file(extension_path, extension_source())

        env = {
            "DRUIDS_SERVER_URL": self._agent_server_url(agent.machine, machine_image=machine_image),
            "DRUIDS_EXECUTION_ID": self.execution_id or "",
            "DRUIDS_AGENT_ID": agent.name,
            "DRUIDS_SYSTEM_PROMPT": agent.system_prompt or "",
        }
        env_prefix = " ".join(f"{key}={shlex.quote(value)}" for key, value in env.items())
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
            raise LaunchError(result.stderr.strip() or result.stdout.strip() or f"Failed to launch agent '{agent.name}'")
        self._log_event("agent_process_started", agent=agent.name, tmux_session=session_name)
        return True

    def _agent_server_url(self, machine: Machine, *, machine_image: Image | None) -> str:
        if self._configured_server_url:
            return self._configured_server_url
        if machine_image is not None:
            return machine_image.server_url_for(self._server.port if self._server is not None else 0)
        if isinstance(machine, DockerMachine):
            return f"http://host.docker.internal:{self._server.port if self._server is not None else 0}"
        return self.server_url or ""

    def _push_event(self, agent_name: str, event: str, data: dict[str, Any]) -> None:
        channel = self._channels.get(agent_name)
        if channel is None:
            raise ToolCallError(f"Unknown agent '{agent_name}'", status_code=404)
        channel.publish(SSEEvent(event=event, data=data))

    def _send_message(self, agent_name: str, message: str) -> None:
        if agent_name not in self._agents:
            raise ToolCallError(f"Unknown agent '{agent_name}'", status_code=404)
        self._push_event(agent_name, "message", {"text": message})

    def _register_agent(self, agent_id: str, execution_id: str) -> list[dict[str, Any]]:
        if execution_id != self.execution_id:
            raise ToolCallError("Execution ID mismatch", status_code=400)
        agent = self._agents.get(agent_id)
        if agent is None:
            raise ToolCallError(f"Unknown agent '{agent_id}'", status_code=404)

        agent._registered = True
        self._channels[agent_id].registered.set()
        tools = _builtin_tools() + [build_tool_definition(name, handler) for name, handler in agent._handlers.items()]
        self._log_event("agent_registered", agent=agent_id, tool_count=len(tools))

        if agent.prompt is not None and not agent._initial_prompt_sent and not agent._launched:
            agent._initial_prompt_sent = True
            self._send_message(agent_id, agent.prompt)
        return tools

    def _subscribe_events(self, agent_id: str) -> asyncio.Queue[SSEEvent]:
        if agent_id not in self._agents:
            raise ToolCallError(f"Unknown agent '{agent_id}'", status_code=404)
        return self._channels[agent_id].subscribe()

    def _unsubscribe_events(self, agent_id: str, subscription: asyncio.Queue[SSEEvent]) -> None:
        channel = self._channels.get(agent_id)
        if channel is not None:
            channel.unsubscribe(subscription)

    async def _handle_tool_call_request(self, agent_id: str, tool_name: str, params: dict[str, Any]) -> Any:
        agent = self._agents.get(agent_id)
        if agent is None:
            raise ToolCallError(f"Unknown agent '{agent_id}'", status_code=404)

        self._active_tool_calls += 1
        if self._no_active_tool_calls is not None:
            self._no_active_tool_calls.clear()
        self._log_event("tool_call_dispatched", agent=agent_id, tool=tool_name, params=params)
        try:
            if tool_name in {"message", "list_agents", "send_file", "download_file"}:
                result = await self._run_builtin_tool(agent_id, tool_name, params)
            else:
                result = await self._invoke_handler(agent, tool_name, params)
            self._log_event("tool_call_result", agent=agent_id, tool=tool_name, result=result)
            return result
        finally:
            self._active_tool_calls -= 1
            if self._active_tool_calls == 0 and self._no_active_tool_calls is not None:
                self._no_active_tool_calls.set()

    async def _invoke_handler(self, agent: Agent, tool_name: str, params: dict[str, Any]) -> Any:
        handler = agent._handlers.get(tool_name)
        if handler is None:
            raise ToolCallError(f"Unknown tool '{tool_name}' for agent '{agent.name}'", status_code=404)

        bound_params = dict(params)
        if "caller" in inspect.signature(handler).parameters:
            bound_params["caller"] = agent

        result = handler(**bound_params)
        if inspect.isawaitable(result):
            result = await result
        return result

    async def _run_builtin_tool(self, caller_name: str, tool_name: str, params: dict[str, Any]) -> Any:
        if tool_name == "list_agents":
            return sorted(self._agents.keys())

        if tool_name == "message":
            receiver = str(params.get("receiver", ""))
            message = str(params.get("message", ""))
            self._require_agent(receiver)
            self._require_connection(caller_name, receiver)
            self._send_message(receiver, f"[From: {caller_name}] {message}")
            self._log_event("message_routed", sender=caller_name, receiver=receiver, text=message)
            return f"Message sent to {receiver}."

        if tool_name == "send_file":
            receiver = str(params.get("receiver", ""))
            path = str(params.get("path", ""))
            dest_path = str(params.get("dest_path") or path)
            self._require_agent(receiver)
            self._require_connection(caller_name, receiver)
            content = await self._agents[caller_name].machine.read_file(path)
            await self._agents[receiver].machine.write_file(dest_path, content)
            self._log_event("file_routed", sender=caller_name, receiver=receiver, path=path, dest_path=dest_path)
            return f"Sent {len(content)} bytes to {receiver}:{dest_path}."

        if tool_name == "download_file":
            sender = str(params.get("sender", ""))
            path = str(params.get("path", ""))
            dest_path = str(params.get("dest_path") or path)
            self._require_agent(sender)
            self._require_connection(sender, caller_name)
            content = await self._agents[sender].machine.read_file(path)
            await self._agents[caller_name].machine.write_file(dest_path, content)
            self._log_event("file_routed", sender=sender, receiver=caller_name, path=path, dest_path=dest_path)
            return f"Downloaded {len(content)} bytes from {sender}:{path} to {dest_path}."

        raise ToolCallError(f"Unknown builtin tool '{tool_name}'", status_code=404)

    def _require_agent(self, name: str) -> None:
        if name not in self._agents:
            raise ToolCallError(f"Unknown agent '{name}'", status_code=404)

    def _require_connection(self, sender: str, receiver: str) -> None:
        if not self.is_connected(sender, receiver):
            raise ToolCallError(f"Agent '{sender}' is not connected to '{receiver}'", status_code=403)

    async def _shutdown(self) -> None:
        if not self._entered:
            return

        self._shutting_down = True
        if self._status == "running":
            self._status = "cancelled"

        if self._no_active_tool_calls is not None:
            try:
                await asyncio.wait_for(self._no_active_tool_calls.wait(), timeout=30)
            except asyncio.TimeoutError:
                self._log_event("active_tool_calls_shutdown_timeout")

        for agent_name in list(self._agents):
            try:
                self._push_event(agent_name, "shutdown", {})
            except Exception:
                pass

        if self._server_stop_event is not None:
            self._server_stop_event.set()

        await asyncio.sleep(0.2)

        if self._server is not None:
            await self._server.stop()
            self._server = None

        for agent in self._agents.values():
            if not agent._launched:
                continue
            session_name = f"druids-{self.execution_id}-{agent.name}"
            try:
                await agent.machine.exec(
                    f"tmux kill-session -t {shlex.quote(session_name)} 2>/dev/null || true",
                    timeout=5,
                )
            except Exception:
                pass

        for machine in self._machines:
            try:
                await machine.stop()
            except Exception:
                self._log_event("machine_stop_failed")

        self._log_event("shutdown_complete", status=self._status)
        if self._log_handle is not None:
            self._log_handle.close()
            self._log_handle = None
        self._entered = False
