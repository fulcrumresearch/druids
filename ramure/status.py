"""STATUS.md writer for a live ramure execution.

Each runtime owns one :class:`StatusWriter`. It subscribes to the
runtime log, watches for state changes, and rewrites
``{log_dir_root}/{execution_id}/STATUS.md`` as the program evolves.

The file is a *view*, not a source of truth. Authoritative reads
still go through the control socket. The point of STATUS.md is
**orientation**: an LLM (or a human) dropped into a directory with
this file should understand what's running and how to interact
with it without prior knowledge of ramure.

Update strategy: a single coalescing task. Structural events
trigger an immediate flush; everything else is debounced to at
most one rewrite per :data:`_DEBOUNCE_S` seconds.

Atomic write-then-rename keeps concurrent readers (an agent
reading the file as we update it) from ever seeing half a file.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ramure.helpers.schema import build_tool_definition

if TYPE_CHECKING:
    from ramure.log import LogEntry
    from ramure.runtime import Runtime


_DEBOUNCE_S = 0.5

# Event types that mean "topology or affordance changed; render now."
# Anything else (e.g. an agent message) bumps activity but doesn't
# warrant an immediate flush -- the debounced timer picks it up.
_STRUCTURAL_EVENTS = frozenset(
    {
        "execution_started",
        "execution_ended",
        "agent_created",
        "agent_ended",
        "agent_spawned",
        "connection_added",
        "endpoint_called",
        "endpoint_returned",
    }
)

_RECENT_EVENTS = 20
_RECENT_CALLS = 10


class StatusWriter:
    """Continuously-updated STATUS.md alongside the runtime's log.

    Owned by :class:`ramure.runtime.Runtime`. ``start()`` subscribes
    to the runtime log and spawns the writer task. ``stop(...)``
    cancels the task and writes one final snapshot stamped with the
    terminal status (``done`` / ``failed``).
    """

    def __init__(self, runtime: Runtime, path: Path, summary: str | None = None) -> None:
        self.runtime = runtime
        self.path = path
        self.summary = summary
        self._task: asyncio.Task | None = None
        self._wake: asyncio.Event = asyncio.Event()
        self._urgent: bool = False
        self._unsubscribe: Any = None
        self._stopped: bool = False

    async def start(self) -> None:
        """Subscribe to the runtime log and spawn the writer task."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.runtime.log is not None:
            self._unsubscribe = self.runtime.log.subscribe(self._on_log_entry)
        self._task = asyncio.create_task(self._run())
        # Render once up-front so an early reader sees a meaningful file.
        self._render_to_disk(status="live")

    async def stop(self, *, status: str = "done") -> None:
        """Cancel the task and write a final snapshot.

        ``status`` becomes the ``Status:`` header in the final file
        (``live`` / ``done`` / ``failed``). The file is left in
        place so the log directory becomes a self-describing
        post-mortem artifact.
        """
        if self._stopped:
            return
        self._stopped = True
        if self._unsubscribe is not None:
            try:
                self._unsubscribe()
            except Exception:
                pass
            self._unsubscribe = None
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        # Final render outside the loop -- captures any state that
        # changed between the last debounced flush and shutdown.
        self._render_to_disk(status=status)

    async def _on_log_entry(self, entry: LogEntry) -> None:
        """Subscriber callback. Wakes the writer task.

        Cheap on purpose: we don't render here. The writer task
        decides whether to flush immediately or wait out the
        debounce window. This keeps log emission off the hot path
        of file I/O.
        """
        if entry.type in _STRUCTURAL_EVENTS:
            self._urgent = True
        self._wake.set()

    async def _run(self) -> None:
        """Coalesce wake events into at most one render per debounce window."""
        try:
            while True:
                await self._wake.wait()
                self._wake.clear()
                # Structural events flush immediately; everything else
                # waits out the debounce so a burst (e.g. tool messages
                # during a chatty turn) collapses into a single write.
                if not self._urgent:
                    await asyncio.sleep(_DEBOUNCE_S)
                self._urgent = False
                try:
                    self._render_to_disk(status="live")
                except Exception:
                    # Never let a render error tear down the writer.
                    # Worst case the file goes briefly stale; the
                    # next event re-tries. The runtime log itself
                    # is unaffected.
                    pass
        except asyncio.CancelledError:
            raise

    # -----------------------------------------------------------------
    # Rendering
    # -----------------------------------------------------------------

    def _render_to_disk(self, *, status: str) -> None:
        body = self._render(status=status)
        # Write-then-rename so a reader never sees a half-written
        # file. Same-directory tempfile guarantees os.replace is
        # atomic on the same filesystem.
        fd, tmp = tempfile.mkstemp(prefix=".STATUS.", suffix=".md", dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "w") as fh:
                fh.write(body)
            os.replace(tmp, self.path)
        except Exception:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass
            raise

    def _render(self, *, status: str) -> str:
        rt = self.runtime
        eid = rt.execution_id or "<unset>"
        eid8 = eid[:8]
        started = (
            datetime.fromtimestamp(rt.started_at).isoformat(timespec="seconds")
            if rt.started_at
            else "?"
        )

        lines: list[str] = []
        lines.append(f"# ramure execution {eid}")
        lines.append("")
        lines.append(
            "This file is a continuously-updated digest of a live ramure "
            "execution. ramure is a Python library for running multi-agent "
            "programs. Authoritative state lives in the control socket; "
            "use the `ramure` CLI to interact."
        )
        lines.append("")
        lines.append(f"- Status: {status}")
        lines.append(f"- Program: {_program_name()}")
        lines.append(f"- Started: {started}")
        lines.append(f"- PID: {os.getpid()}")
        if self.summary:
            lines.append("")
            lines.append("## Summary")
            lines.append("")
            lines.append(self.summary.strip())

        # Affordances ----------------------------------------------------
        endpoints = self._endpoints()
        lines.append("")
        lines.append("## Affordances")
        lines.append("")
        if endpoints:
            lines.append(
                "Call from outside with `ramure call <name> k=v ...`. "
                "Arguments are parsed as JSON, falling back to strings."
            )
            lines.append("")
            for ep in endpoints:
                lines.append(f"- `{_signature(ep)}`")
                doc = (ep.get("description") or "").strip()
                first = doc.splitlines()[0] if doc else ""
                if first:
                    lines.append(f"  - {first}")
        else:
            lines.append("_(no endpoints exposed)_")

        # Agents ---------------------------------------------------------
        lines.append("")
        lines.append("## Agents")
        lines.append("")
        if rt.agents:
            for ag in rt.agents.values():
                m = ag.machine.describe()
                bits = [m.get("kind", "?")]
                if "workdir" in m:
                    bits.append(f"workdir={m['workdir']}")
                tmux = f"ramure-{eid8}-{ag.name}"
                lines.append(f"- `{ag.name}` [{' '.join(bits)}] tmux=`{tmux}`")
        else:
            lines.append("_(none yet)_")

        # Connections ----------------------------------------------------
        if rt.edges:
            lines.append("")
            lines.append("## Connections")
            lines.append("")
            for a, b in sorted(rt.edges):
                lines.append(f"- {a} -> {b}")

        # Recent endpoint calls -----------------------------------------
        calls = self._recent_calls()
        if calls:
            lines.append("")
            lines.append(f"## Recent endpoint calls (last {len(calls)})")
            lines.append("")
            for line in calls:
                lines.append(f"- {line}")

        # Recent events --------------------------------------------------
        events = self._recent_events()
        if events:
            lines.append("")
            lines.append(f"## Recent events (last {len(events)})")
            lines.append("")
            for line in events:
                lines.append(f"- {line}")

        # Footer ---------------------------------------------------------
        lines.append("")
        lines.append("## How to interact")
        lines.append("")
        lines.append(f"- `ramure status -i {eid8}` — full snapshot")
        lines.append(f"- `ramure send <agent> \"<msg>\" -i {eid8}` — message an agent")
        lines.append(
            f"- `ramure call <endpoint> k=v ... -i {eid8}` — call an exposed endpoint"
        )
        lines.append(f"- `ramure connect <agent> -i {eid8}` — attach to an agent's tmux")
        lines.append("")

        return "\n".join(lines)

    def _endpoints(self) -> list[dict[str, Any]]:
        scope = self.runtime.root_scope
        if scope is None:
            return []
        return [
            build_tool_definition(name, fn) for name, fn in scope.endpoints.items()
        ]

    def _recent_calls(self) -> list[str]:
        log = self.runtime.log
        if log is None:
            return []
        # Pair endpoint_called with its endpoint_returned by walking the
        # tail. We only render returned calls (or their pending shape if
        # somehow a call hasn't returned -- shouldn't happen, but be
        # defensive). Most recent first.
        entries = list(log._entries[-200:])
        pairs: list[str] = []
        # Index returns by (endpoint, ts) so we can match the most
        # recent return after each call.
        returns_by_endpoint: dict[str, list[Any]] = {}
        for e in entries:
            if e.type == "endpoint_returned":
                returns_by_endpoint.setdefault(e.data.get("endpoint", "?"), []).append(e)
        for e in reversed(entries):
            if e.type != "endpoint_called":
                continue
            ep_name = e.data.get("endpoint", "?")
            kwargs = e.data.get("kwargs") or {}
            caller = e.data.get("caller", "?")
            ret = None
            bucket = returns_by_endpoint.get(ep_name) or []
            # Pick the first return strictly after this call's seq.
            for r in bucket:
                if r.seq > e.seq:
                    ret = r
                    break
            outcome = "..." if ret is None else ("ok" if ret.data.get("ok") else f"error: {ret.data.get('error', '?')}" )
            args_str = ", ".join(f"{k}={_short_repr(v)}" for k, v in kwargs.items())
            ts = datetime.fromtimestamp(e.ts).strftime("%H:%M:%S")
            pairs.append(f"{ts}  {ep_name}({args_str}) by {caller} -> {outcome}")
            if len(pairs) >= _RECENT_CALLS:
                break
        return pairs

    def _recent_events(self) -> list[str]:
        log = self.runtime.log
        if log is None:
            return []
        out: list[str] = []
        # Endpoint_called/endpoint_returned already render in their own
        # section; skip them here so the events list stays focused on
        # agent/connection lifecycle.
        skip = {"endpoint_called", "endpoint_returned"}
        for e in reversed(log._entries[-200:]):
            if e.type in skip:
                continue
            ts = datetime.fromtimestamp(e.ts).strftime("%H:%M:%S")
            d = e.data if isinstance(e.data, dict) else {}
            tag = d.get("agent") or d.get("execution_id") or ""
            extra = f" {tag}" if tag else ""
            out.append(f"{ts}  {e.type}{extra}")
            if len(out) >= _RECENT_EVENTS:
                break
        return out


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _signature(ep: dict[str, Any]) -> str:
    """Render an endpoint as a Python-ish signature.

    Mirrors :func:`ramure.cli._format_endpoint_signature` but
    duplicated here to keep the status writer independent of the
    CLI module. The schema is the same shape either way.
    """
    name = ep.get("name", "?")
    params = ep.get("parameters") or {}
    props = params.get("properties") or {}
    required = set(params.get("required") or [])
    bits: list[str] = []
    for pname, pschema in props.items():
        ptype = pschema.get("type", "any")
        if pname in required:
            bits.append(f"{pname}: {ptype}")
        else:
            default = pschema.get("default")
            bits.append(f"{pname}: {ptype} = {default!r}")
    return f"{name}({', '.join(bits)})"


def _short_repr(value: Any, limit: int = 60) -> str:
    """Compact ``repr`` for logging endpoint kwargs in STATUS.md.

    Truncates anything past ``limit`` characters with an ellipsis.
    """
    r = repr(value)
    if len(r) > limit:
        return r[: limit - 1] + "…"
    return r


def _program_name() -> str:
    import sys

    argv0 = sys.argv[0] if sys.argv else ""
    if argv0 and Path(argv0).exists():
        return str(Path(argv0).resolve())
    return argv0 or "<unknown>"
