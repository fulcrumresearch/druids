"""Unix-socket control server for CLI interaction.

The runtime listens on ``~/.ramure/runtimes/{execution_id}.sock``.
The CLI connects, sends one line of JSON, reads one line back, closes.

Commands:

- ``{"cmd":"status"}`` -> ``{agents, connections, program, pid, started_at}``
- ``{"cmd":"agent", "name":<str>}`` -> ``{name, machine, tmux_session}``
- ``{"cmd":"send", "agent":<str>, "text":<str>}`` -> ``{"ok":true}``
- ``{"cmd":"ssh_credentials", "name":<str>}`` -> ``{"credentials": {host, port, username, private_key, password} | null}``
- ``{"cmd":"endpoints"}`` -> ``{"endpoints": [{name, description, parameters}, ...]}``
- ``{"cmd":"call", "endpoint":<str>, "kwargs":<dict>, "caller":<str>}`` -> ``{"ok":true, "result":<jsonable>}``

Errors come back as ``{"error":<msg>}``.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ramure.context import _invoke_in_scope
from ramure.helpers import agent_session_name
from ramure.helpers.schema import build_tool_definition
from ramure.types import to_jsonable

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
        if cmd == "ssh_credentials":
            return await self._cmd_ssh_credentials(msg.get("name", ""))
        if cmd == "endpoints":
            return self._cmd_endpoints()
        if cmd == "call":
            return await self._cmd_call(
                msg.get("endpoint", ""),
                msg.get("kwargs") or {},
                msg.get("caller") or "external",
            )
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

    async def _cmd_ssh_credentials(self, name: str) -> dict[str, Any]:
        """Return SSH credentials for an agent's machine, or ``None``.

        Backends that don't expose SSH (e.g. :class:`LocalMachine`) return
        ``None`` from ``ssh_credentials()``; we pass that through so the CLI
        can fall back to a local shell / local tmux attach without a second
        round-trip.
        """
        ag = self.runtime.agents.get(name)
        if ag is None:
            return {"error": f"unknown agent '{name}'"}
        creds = await ag.machine.ssh_credentials()
        if creds is None:
            return {"credentials": None}
        return {
            "credentials": {
                "host": creds.host,
                "port": creds.port,
                "username": creds.username,
                "private_key": creds.private_key,
                "password": creds.password,
            }
        }

    def _cmd_endpoints(self) -> dict[str, Any]:
        """List endpoints exposed by the root @agent_process.

        Reuses :func:`build_tool_definition` so the schema matches
        what agents see for their own tools -- the CLI can render
        endpoints without a parallel code path.
        """
        scope = self.runtime.root_scope
        if scope is None:
            return {"endpoints": []}
        return {
            "endpoints": [
                build_tool_definition(name, fn)
                for name, fn in scope.endpoints.items()
            ]
        }

    async def _cmd_call(
        self, endpoint: str, kwargs: dict[str, Any], caller: str
    ) -> dict[str, Any]:
        """Invoke an exposed endpoint on the root scope.

        Goes through the same ``_invoke_in_scope`` path that
        ``handle.call`` uses, so the endpoint runs inside the root
        scope and ``emit()``/``done()``/``fail()`` inside the
        handler land where the program author expects.

        Logs a pair of ``endpoint_called`` / ``endpoint_returned``
        entries on the runtime log so the call is visible to any
        downstream observer (CLI tail, status file, etc.). The
        ``caller`` field distinguishes external invocations from
        in-runtime ones (which currently don't go through this
        path, but the convention lines up for when they do).
        """
        scope = self.runtime.root_scope
        if scope is None:
            return {"error": "root scope not available"}
        handler = scope.endpoints.get(endpoint)
        if handler is None:
            return {"error": f"unknown endpoint '{endpoint}'"}
        if not isinstance(kwargs, dict):
            return {"error": "kwargs must be an object"}

        log = self.runtime.log
        if log is not None:
            log.emit(
                "endpoint_called",
                {"endpoint": endpoint, "kwargs": to_jsonable(kwargs), "caller": caller},
            )

        started = time.monotonic()
        try:
            result = await _invoke_in_scope(scope, handler, kwargs)
            ok, error = True, None
        except Exception as exc:  # noqa: BLE001 -- handler errors must reach the caller
            result, ok, error = None, False, str(exc) or type(exc).__name__

        if log is not None:
            log.emit(
                "endpoint_returned",
                {
                    "endpoint": endpoint,
                    "caller": caller,
                    "ok": ok,
                    "duration_ms": int((time.monotonic() - started) * 1000),
                    **({"error": error} if error else {}),
                },
            )
        if not ok:
            return {"error": error}
        return {"ok": True, "result": to_jsonable(result)}

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
