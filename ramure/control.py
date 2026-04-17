"""Unix-socket control server for CLI interaction.

The runtime listens on ``~/.ramure/runtimes/{execution_id}.sock``.
The CLI connects, sends one line of JSON, reads one line back, closes.

Commands:

- ``{"cmd":"status"}`` -> ``{agents, connections, program, pid, started_at}``
- ``{"cmd":"agent", "name":<str>}`` -> ``{name, machine, tmux_session}``
- ``{"cmd":"send", "agent":<str>, "text":<str>}`` -> ``{"ok":true}``

Errors come back as ``{"error":<msg>}``.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ramure.helpers import agent_session_name

if TYPE_CHECKING:
    from ramure.runtime import Runtime


SOCKET_DIR = Path.home() / ".ramure" / "runtimes"


def socket_path(execution_id: str) -> Path:
    return SOCKET_DIR / f"{execution_id}.sock"


class ControlServer:
    def __init__(self, runtime: Runtime) -> None:
        self.runtime = runtime
        self._server: asyncio.base_events.Server | None = None
        self._path: Path | None = None

    async def start(self) -> None:
        assert self.runtime.execution_id
        SOCKET_DIR.mkdir(parents=True, exist_ok=True)
        self._path = socket_path(self.runtime.execution_id)
        # Clean up a stale socket from a crashed previous run.
        try:
            self._path.unlink()
        except FileNotFoundError:
            pass
        self._server = await asyncio.start_unix_server(self._handle, path=str(self._path))

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        if self._path is not None:
            try:
                self._path.unlink()
            except FileNotFoundError:
                pass
            self._path = None

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            line = await reader.readline()
            if not line:
                return
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                reply = {"error": "invalid json"}
            else:
                reply = await self._dispatch(msg)
            writer.write((json.dumps(reply) + "\n").encode())
            await writer.drain()
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    async def _dispatch(self, msg: dict[str, Any]) -> dict[str, Any]:
        cmd = msg.get("cmd")
        if cmd == "status":
            return self._cmd_status()
        if cmd == "agent":
            return self._cmd_agent(msg.get("name", ""))
        if cmd == "send":
            return await self._cmd_send(msg.get("agent", ""), msg.get("text", ""))
        return {"error": f"unknown cmd '{cmd}'"}

    # -- handlers --

    def _cmd_status(self) -> dict[str, Any]:
        rt = self.runtime
        return {
            "execution_id": rt.execution_id,
            "pid": os.getpid(),
            "program": _program_name(),
            "started_at": rt.started_at,
            "server_url": rt.server_url,
            "agents": [self._agent_info(ag.name) for ag in rt.agents.values()],
            "connections": [
                {"a": a, "b": b} for (a, b) in sorted(rt.edges)
            ],
        }

    def _cmd_agent(self, name: str) -> dict[str, Any]:
        if name not in self.runtime.agents:
            return {"error": f"unknown agent '{name}'"}
        return self._agent_info(name)

    async def _cmd_send(self, name: str, text: str) -> dict[str, Any]:
        ag = self.runtime.agents.get(name)
        if ag is None:
            return {"error": f"unknown agent '{name}'"}
        if not text:
            return {"error": "missing text"}
        await ag.send(text)
        return {"ok": True}

    def _agent_info(self, name: str) -> dict[str, Any]:
        ag = self.runtime.agents[name]
        return {
            "name": name,
            "machine": ag.machine.describe(),
            "tmux_session": agent_session_name(self.runtime.execution_id or "", name),
        }


def _program_name() -> str:
    argv0 = sys.argv[0] if sys.argv else ""
    if argv0 and Path(argv0).exists():
        return str(Path(argv0).resolve())
    return argv0 or "<unknown>"
