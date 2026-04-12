from __future__ import annotations

import inspect
import json
import shlex
import shutil
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from druids.extension import extension_source
from druids.machines import DockerMachine, Image, LocalImage, Machine, ManagedMachine
from druids.schema import build_tool_definition
from druids.server import AgentChannel, OrchestratorServer, SSEEvent
from druids.types import ExecutionFailed, LaunchError, ToolCallError, to_jsonable


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


class _ConcreteImage(Image):
    def __init__(self, machine: Machine):
        self.machine = machine
        self._used = False

    def spawn(self) -> Machine:
        if self._used:
            return self.machine
        self._used = True
        return self.machine


@dataclass
class Agent:
    name: str
    _ctx: Context
    _machine: ManagedMachine
    prompt: str | None = None
    system_prompt: str | None = None
    _handlers: dict[str, Callable[..., Any]] = field(default_factory=dict)
    _spawned: bool = False
    _registered: bool = False
    _initial_prompt_sent: bool = False

    @property
    def machine(self) -> Machine:
        return self._machine

    def on(self, tool_name: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            self._handlers[tool_name] = fn
            self._ctx._handle_tool_registered(self, tool_name, fn)
            return fn

        return decorator

    def send(self, message: str) -> None:
        self._ctx._ensure_running()
        self._ctx._send_message_to_agent(self.name, message)

    def exec(self, command: str, *, user: str = "agent", timeout: int | None = None):
        self._ctx._ensure_agent_spawned(self)
        return self._machine.exec(command, user=user, timeout=timeout)


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

        self.execution_id = str(uuid.uuid4())
        self._agents: dict[str, Agent] = {}
        self._channels: dict[str, AgentChannel] = {}
        self._machines: list[ManagedMachine] = []
        self._edges: set[tuple[str, str]] = set()

        self._status = "pending"
        self._result: Any = None
        self._failure_reason: str | None = None
        self._running = False
        self._finished = False

        self._lock = threading.RLock()
        self._completion_event = threading.Event()
        self._server_stop_event = threading.Event()
        self._active_tool_calls = 0
        self._active_tool_calls_condition = threading.Condition(self._lock)
        self._handler_local = threading.local()

        self._tool_executor = ThreadPoolExecutor(max_workers=16, thread_name_prefix="druids-tool")
        self._server: OrchestratorServer | None = None
        self.server_url: str | None = server_url
        self.log_path: Path | None = None
        self._log_handle = None
        self._log_lock = threading.Lock()
        self._pending_logs: list[dict[str, Any]] = []

    @property
    def agents(self) -> dict[str, Agent]:
        return dict(self._agents)

    @property
    def status(self) -> str:
        return self._status

    def machine(self, image: Image | None = None) -> Machine:
        machine = ManagedMachine(image or self.image)
        self._machines.append(machine)
        if self._running:
            machine.ensure_started()
        return machine

    def agent(
        self,
        name: str,
        *,
        prompt: str | None = None,
        system_prompt: str | None = None,
        machine: Machine | None = None,
        image: Image | None = None,
    ) -> Agent:
        with self._lock:
            if name in self._agents:
                raise ValueError(f"Agent '{name}' already exists")
            machine_ref = self._coerce_machine(machine, image)
            agent = Agent(
                name=name,
                _ctx=self,
                _machine=machine_ref,
                prompt=prompt,
                system_prompt=system_prompt,
            )
            self._agents[name] = agent
            self._channels[name] = AgentChannel()
            self._log_event("agent_created", agent=name)

        if self._running:
            self._spawn_agent(agent)
        return agent

    def connect(self, a: Agent | str, b: Agent | str, *, direction: str = "both") -> None:
        if direction not in {"both", "forward"}:
            raise ValueError("direction must be 'both' or 'forward'")
        a_name = a.name if isinstance(a, Agent) else a
        b_name = b.name if isinstance(b, Agent) else b
        with self._lock:
            self._edges.add((a_name, b_name))
            if direction == "both":
                self._edges.add((b_name, a_name))
            self._log_event("agents_connected", sender=a_name, receiver=b_name, direction=direction)

    def is_connected(self, sender: Agent | str, receiver: Agent | str) -> bool:
        sender_name = sender.name if isinstance(sender, Agent) else sender
        receiver_name = receiver.name if isinstance(receiver, Agent) else receiver
        return (sender_name, receiver_name) in self._edges

    def done(self, result: Any = None) -> None:
        with self._lock:
            if self._finished:
                return
            self._status = "completed"
            self._result = result
            self._finished = True
            self._log_event("done", result=to_jsonable(result))
            self._completion_event.set()

    def fail(self, reason: str) -> None:
        with self._lock:
            if self._finished:
                return
            self._status = "failed"
            self._failure_reason = reason
            self._finished = True
            self._log_event("fail", reason=reason)
            self._completion_event.set()

    def run(self, *, timeout: float | None = None) -> Any:
        with self._lock:
            if self._status != "pending":
                raise RuntimeError("Context.run() may only be called once")
            self._status = "running"
            self._running = True
            self._open_log()
            self._start_server()

        try:
            for agent in list(self._agents.values()):
                self._spawn_agent(agent)

            completed = self._completion_event.wait(timeout=timeout)
            if not completed:
                raise TimeoutError("Timed out waiting for ctx.done() / ctx.fail()")

            self._wait_for_active_tool_calls()
            if self._status == "failed":
                raise ExecutionFailed(self._failure_reason or "Execution failed")
            return self._result
        finally:
            self._shutdown()

    def _coerce_machine(self, machine: Machine | None, image: Image | None) -> ManagedMachine:
        if machine is not None and image is not None:
            raise ValueError("Pass either machine= or image=, not both")
        if isinstance(machine, ManagedMachine):
            return machine
        if machine is not None:
            managed = ManagedMachine(_ConcreteImage(machine))
            managed.ensure_started()
            self._machines.append(managed)
            return managed
        managed = ManagedMachine(image or self.image)
        self._machines.append(managed)
        return managed

    def _open_log(self) -> None:
        log_dir = Path.cwd() / "logs" / self.execution_id
        log_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = log_dir / "orchestrator.jsonl"
        self._log_handle = self.log_path.open("a", encoding="utf-8")
        if self._pending_logs:
            for payload in self._pending_logs:
                self._log_handle.write(json.dumps(payload) + "\n")
            self._log_handle.flush()
            self._pending_logs.clear()

    def _log_event(self, event: str, **fields: Any) -> None:
        payload = {
            "ts": time.time(),
            "event": event,
            "execution_id": self.execution_id,
            **{key: to_jsonable(value) for key, value in fields.items()},
        }
        if self._log_handle is None:
            self._pending_logs.append(payload)
            return
        with self._log_lock:
            self._log_handle.write(json.dumps(payload) + "\n")
            self._log_handle.flush()

    def _start_server(self) -> None:
        bind_host, bind_port, public_url = self._resolve_server_binding()
        self._server = OrchestratorServer(self, bind_host, bind_port, public_url)
        self._server.start()
        if self._configured_server_url is None:
            self.server_url = f"http://127.0.0.1:{self._server.port}"
        else:
            self.server_url = self._configured_server_url
        self._log_event("server_started", bind_host=bind_host, bind_port=self._server.port, public_url=self.server_url)

    def _resolve_server_binding(self) -> tuple[str, int, str]:
        if self._configured_server_url is None:
            return ("127.0.0.1", 0, "")

        parsed = urlparse(self._configured_server_url)
        hostname = parsed.hostname or "0.0.0.0"
        if hostname not in {"127.0.0.1", "0.0.0.0", "localhost"}:
            hostname = "0.0.0.0"
        if parsed.port is not None:
            port = parsed.port
        elif parsed.scheme == "https":
            port = 443
        else:
            port = 80
        return (hostname, port, self._configured_server_url)

    def _ensure_running(self) -> None:
        if not self._running:
            raise RuntimeError("Context is not running. Call ctx.run() first.")

    def _ensure_agent_spawned(self, agent: Agent) -> None:
        if not agent._spawned:
            self._spawn_agent(agent)

    def _spawn_agent(self, agent: Agent) -> None:
        with self._lock:
            if agent._spawned:
                return
            agent._machine.ensure_started()
            agent._spawned = True
            self._log_event("agent_spawned", agent=agent.name)
        launched = self._maybe_launch_agent_process(agent)
        if launched:
            channel = self._channels[agent.name]
            if not channel.registered.wait(timeout=120):
                raise LaunchError(
                    f"Agent '{agent.name}' did not register within 120s. "
                    f"Check tmux session: druids-{self.execution_id}-{agent.name}"
                )

    def _maybe_launch_agent_process(self, agent: Agent) -> bool:
        """Launch the agent's pi process. Returns True if launched."""
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
        agent.machine.write_file(extension_path, extension_source())

        env = {
            "DRUIDS_SERVER_URL": self._agent_server_url(agent.machine),
            "DRUIDS_EXECUTION_ID": self.execution_id,
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
        result = agent.machine.exec(command)
        if not result.ok:
            raise LaunchError(result.stderr.strip() or result.stdout.strip() or f"Failed to launch agent '{agent.name}'")
        self._log_event("agent_process_started", agent=agent.name, tmux_session=session_name)
        return True

    def _agent_server_url(self, machine: Machine) -> str:
        if self._configured_server_url:
            return self._configured_server_url
        if isinstance(getattr(machine, "backend", None), DockerMachine):
            return f"http://host.docker.internal:{self._server.port if self._server else 0}"
        return self.server_url or ""

    def _handle_tool_registered(self, agent: Agent, tool_name: str, handler: Callable[..., Any]) -> None:
        if self._running and agent._registered:
            self._push_event(agent.name, "new_tool", build_tool_definition(tool_name, handler))
        self._log_event("tool_registered", agent=agent.name, tool=tool_name)

    def _push_event(self, agent_name: str, event: str, data: dict[str, Any]) -> None:
        channel = self._channels.get(agent_name)
        if channel is None:
            raise ToolCallError(f"Unknown agent '{agent_name}'", status_code=404)
        channel.publish(SSEEvent(event=event, data=data))

    def _send_message_to_agent(self, agent_name: str, message: str) -> None:
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

        if agent.prompt is not None and not agent._initial_prompt_sent:
            agent._initial_prompt_sent = True
            self._send_message_to_agent(agent_id, agent.prompt)
        return tools

    def _subscribe_events(self, agent_id: str):
        if agent_id not in self._agents:
            raise ToolCallError(f"Unknown agent '{agent_id}'", status_code=404)
        return self._channels[agent_id].subscribe()

    def _unsubscribe_events(self, agent_id: str, subscription) -> None:
        channel = self._channels.get(agent_id)
        if channel is not None:
            channel.unsubscribe(subscription)

    def _handle_tool_call_request(self, agent_id: str, tool_name: str, params: dict[str, Any]) -> Any:
        agent = self._agents.get(agent_id)
        if agent is None:
            raise ToolCallError(f"Unknown agent '{agent_id}'", status_code=404)

        with self._active_tool_calls_condition:
            self._active_tool_calls += 1

        self._log_event("tool_call_dispatched", agent=agent_id, tool=tool_name, params=params)
        try:
            if tool_name in {"message", "list_agents", "send_file", "download_file"}:
                result = self._run_builtin_tool(agent_id, tool_name, params)
            else:
                future = self._tool_executor.submit(self._invoke_handler, agent, tool_name, params)
                result = future.result()
            self._log_event("tool_call_result", agent=agent_id, tool=tool_name, result=result)
            return result
        finally:
            with self._active_tool_calls_condition:
                self._active_tool_calls -= 1
                self._active_tool_calls_condition.notify_all()

    def _invoke_handler(self, agent: Agent, tool_name: str, params: dict[str, Any]) -> Any:
        handler = agent._handlers.get(tool_name)
        if handler is None:
            raise ToolCallError(f"Unknown tool '{tool_name}' for agent '{agent.name}'", status_code=404)

        self._handler_local.in_handler = True
        try:
            bound_params = dict(params)
            if "caller" in inspect.signature(handler).parameters:
                bound_params["caller"] = agent
            return handler(**bound_params)
        finally:
            self._handler_local.in_handler = False

    def _run_builtin_tool(self, caller_name: str, tool_name: str, params: dict[str, Any]) -> Any:
        if tool_name == "list_agents":
            return sorted(self._agents.keys())

        if tool_name == "message":
            receiver = str(params.get("receiver", ""))
            message = str(params.get("message", ""))
            self._require_agent(receiver)
            self._require_connection(caller_name, receiver)
            self._send_message_to_agent(receiver, f"[From: {caller_name}] {message}")
            self._log_event("message_routed", sender=caller_name, receiver=receiver, text=message)
            return f"Message sent to {receiver}."

        if tool_name == "send_file":
            receiver = str(params.get("receiver", ""))
            path = str(params.get("path", ""))
            dest_path = str(params.get("dest_path") or path)
            self._require_agent(receiver)
            self._require_connection(caller_name, receiver)
            content = self._agents[caller_name].machine.read_file(path)
            self._agents[receiver].machine.write_file(dest_path, content)
            self._log_event("file_routed", sender=caller_name, receiver=receiver, path=path, dest_path=dest_path)
            return f"Sent {len(content)} bytes to {receiver}:{dest_path}."

        if tool_name == "download_file":
            sender = str(params.get("sender", ""))
            path = str(params.get("path", ""))
            dest_path = str(params.get("dest_path") or path)
            self._require_agent(sender)
            self._require_connection(sender, caller_name)
            content = self._agents[sender].machine.read_file(path)
            self._agents[caller_name].machine.write_file(dest_path, content)
            self._log_event("file_routed", sender=sender, receiver=caller_name, path=path, dest_path=dest_path)
            return f"Downloaded {len(content)} bytes from {sender}:{path} to {dest_path}."

        raise ToolCallError(f"Unknown builtin tool '{tool_name}'", status_code=404)

    def _require_agent(self, name: str) -> None:
        if name not in self._agents:
            raise ToolCallError(f"Unknown agent '{name}'", status_code=404)

    def _require_connection(self, sender: str, receiver: str) -> None:
        if not self.is_connected(sender, receiver):
            raise ToolCallError(f"Agent '{sender}' is not connected to '{receiver}'", status_code=403)

    def _wait_for_active_tool_calls(self) -> None:
        with self._active_tool_calls_condition:
            while self._active_tool_calls > 0:
                self._active_tool_calls_condition.wait(timeout=0.1)

    def _shutdown(self) -> None:
        with self._lock:
            if not self._running:
                return
            self._running = False

        for agent_name in list(self._agents):
            try:
                self._push_event(agent_name, "shutdown", {})
            except Exception:
                pass

        self._server_stop_event.set()
        if self._server is not None:
            self._server.stop()
            self._server = None

        # Kill tmux sessions
        for agent in self._agents.values():
            session_name = f"druids-{self.execution_id}-{agent.name}"
            try:
                agent.machine.exec(f"tmux kill-session -t {shlex.quote(session_name)} 2>/dev/null || true", timeout=5)
            except Exception:
                pass

        seen: set[int] = set()
        for machine in self._machines:
            if id(machine) in seen:
                continue
            seen.add(id(machine))
            try:
                machine.stop()
            except Exception:
                self._log_event("machine_stop_failed")

        self._tool_executor.shutdown(wait=True, cancel_futures=False)
        self._log_event("shutdown_complete", status=self._status)
        if self._log_handle is not None:
            self._log_handle.close()
            self._log_handle = None
