"""Driver-facing execution endpoints.

These endpoints are called by the druids CLI or dashboard to create, list,
update, and inspect executions. Runtime and agent-facing endpoints live in
the sibling `runtime` module.
"""

from __future__ import annotations

import ast
import asyncio
import dataclasses
import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from starlette.websockets import WebSocketState

from druids_server.api.deps import (
    Caller,
    UserExecutions,
    _get_local_user,
    get_caller,
    get_executions_registry,
    require_driver,
)
from druids_server.api.helpers.execution_stream import iter_execution_stream
from druids_server.api.helpers.trace_format import merge_response_chunks, normalize_event
from druids_server.config import settings
from druids_server.db.models.devbox import resolve_devbox
from druids_server.db.models.execution import (
    create_execution,
    get_execution_by_slug,
    get_user_executions,
    update_execution,
)
from druids_server.db.session import get_session
from druids_server.lib.execution import Execution
from druids_server.lib.machine import Machine
from druids_server.lib.sandbox.base import Sandbox
from druids_server.utils import execution_trace


logger = logging.getLogger(__name__)

router = APIRouter()


def _clamp_ttl(requested: int) -> int:
    """Clamp a user-requested TTL to the server max.

    Returns the effective TTL in seconds. 0 means no timeout.
    """
    server_max = settings.max_execution_ttl
    if requested > 0 and server_max > 0:
        return min(requested, server_max)
    if requested > 0:
        return requested
    return server_max


# ---------------------------------------------------------------------------
# Shared helpers (imported by runtime.py and mcp.py)
# ---------------------------------------------------------------------------


def _get_agent_machine(executions: dict, slug: str, agent_name: str | None = None):
    """Resolve an agent's machine from the in-memory execution registry.

    If agent_name is None, picks the first agent that has a machine.
    """
    ex = executions.get(slug)
    if not ex:
        raise HTTPException(404, f"Execution '{slug}' is no longer running")

    if agent_name:
        agent = ex.agents.get(agent_name)
        if not agent:
            available = list(ex.agents.keys())
            raise HTTPException(404, f"Agent '{agent_name}' not found. Available: {', '.join(available)}")
        return agent.machine

    for name, agent in ex.agents.items():
        return agent.machine

    raise HTTPException(404, f"No agents with machines in execution '{slug}'")


def _get_runtime_execution(executions: UserExecutions, slug: str) -> Execution:
    """Return a running execution by slug or raise 404."""
    ex = executions.get(slug)
    if not ex:
        raise HTTPException(404, f"Execution '{slug}' is not running")
    return ex


def _get_runtime_agent(ex: Execution, agent_name: str, detail: str | None = None):
    """Return an agent from an execution or raise a 404."""
    agent = ex.agents.get(agent_name)
    if not agent:
        raise HTTPException(404, detail or f"Agent '{agent_name}' not found")
    return agent


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


class CreateExecutionRequest(BaseModel):
    program_source: str
    devbox_name: str | None = None
    repo_full_name: str | None = None  # used only to find the devbox when devbox_name is not set
    git_branch: str | None = None
    args: dict[str, str] | None = None
    ttl: int = 0  # seconds, 0 = use server default


@router.post(
    "/executions",
    tags=["executions", "mcp-driver"],
    operation_id="create_execution",
    dependencies=[Depends(require_driver)],
)
async def create_execution_endpoint(
    request: CreateExecutionRequest,
    caller: Caller,
    executions: UserExecutions,
):
    """Create and start a new execution.

    Validates the program source syntax, provisions a sandbox, deploys the
    execution runtime, and launches it. The runtime runs the program, which
    calls back to the server API to create agents and register tools.
    """
    if not request.repo_full_name and not request.devbox_name:
        raise HTTPException(400, "Either repo_full_name or devbox_name is required")

    # Validate syntax only -- never exec() program source on the server
    try:
        ast.parse(request.program_source)
    except SyntaxError:
        raise HTTPException(400, "Invalid program source")

    # Resolve devbox: by name first, then by repo
    async with get_session() as db:
        devbox = await resolve_devbox(
            db,
            caller.user.id,
            name=request.devbox_name,
            repo_full_name=request.repo_full_name,
        )
        if not devbox:
            label = request.devbox_name or request.repo_full_name
            raise HTTPException(404, f"No devbox for '{label}'. Run 'druids setup' first.")

    devbox_machine = Machine(snapshot_id=devbox.snapshot_id)
    if devbox.instance_id:
        try:
            sandbox = await Sandbox.get(devbox.instance_id)
            devbox_machine.sandbox = sandbox
        except Exception:
            logger.info("Devbox instance %s not found, using snapshot only", devbox.instance_id)

    program_args = request.args or {}
    repo_full_name = devbox.repo_full_name or None

    # Create execution record
    async with get_session() as db:
        record = await create_execution(
            db,
            user_id=caller.user.id,
            spec=program_args.get("spec", ""),
            repo_full_name=repo_full_name or "",
            metadata=program_args,
        )

    user_id_str = str(caller.user.id)

    ex = Execution(
        id=record.id,
        slug=record.slug,
        user_id=user_id_str,
        devbox_machine=devbox_machine,
        devbox_id=devbox.id,
        repo_full_name=repo_full_name,
        git_branch=request.git_branch,
        spec=program_args.get("spec"),
        ttl=_clamp_ttl(request.ttl),
    )
    executions[ex.slug] = ex

    async def run_and_cleanup():
        try:
            # Run program in-process. exec() the source to get the program
            # function, then run it as a background task. The program creates
            # agents (which provision VMs) and registers tool handlers.
            namespace: dict[str, Any] = {}
            try:
                exec(request.program_source, namespace)  # noqa: S102
            except Exception as e:
                ex.fail(f"Program load error: {e}")
                return

            program_fn = namespace.get("program")
            if not callable(program_fn):
                ex.fail("Program source does not define a callable 'program'")
                return

            async def _run_program():
                try:
                    await program_fn(ex, **program_args)
                except Exception as e:
                    logger.exception("Program failed for %s", ex.slug)
                    if not ex._done.is_set():
                        ex.fail(f"Program error: {e}")

            ex._program_task = asyncio.create_task(_run_program())

            await ex.run()
        except Exception:
            logger.exception("Execution %s failed during setup", ex.slug)
            if not ex._done.is_set():
                ex.fail("Execution setup failed")
            async with get_session() as db:
                await update_execution(db, ex.id, status="failed")
        finally:
            # Persist graph topology before removing from memory
            try:
                async with get_session() as db:
                    await update_execution(
                        db,
                        ex.id,
                        agents=list(ex.agents.keys()),
                        edges=ex.edges,
                    )
            except Exception:
                logger.warning("Failed to persist agents/edges for %s", ex.slug)
            executions.pop(ex.slug, None)

    asyncio.create_task(run_and_cleanup())

    return {
        "execution_slug": ex.slug,
        "execution_id": str(record.id),
    }


@router.get("/executions/{slug}", tags=["executions", "mcp-driver"], operation_id="get_execution")
async def get_execution_endpoint(
    slug: str,
    caller: Caller,
    executions: UserExecutions,
):
    """Get execution status by slug."""
    async with get_session() as db:
        record = await get_execution_by_slug(db, caller.user.id, slug)
        if not record:
            raise HTTPException(404, f"Execution '{slug}' not found")

    runtime = executions.get(slug)

    return {
        "execution_id": str(record.id),
        "execution_slug": record.slug,
        "spec": record.spec,
        "repo_full_name": record.repo_full_name,
        "status": record.status,
        "error": record.error,
        "metadata": record.metadata_,
        "branch_name": record.branch_name,
        "pr_url": record.pr_url,
        "started_at": record.started_at.isoformat() if record.started_at else None,
        "stopped_at": record.stopped_at.isoformat() if record.stopped_at else None,
        "agents": list(runtime.all_agent_names()) if runtime else record.agents_,
        "exposed_services": [dataclasses.asdict(svc) for svc in runtime.exposed_services] if runtime else [],
        "client_events": runtime.list_client_events() if runtime else [],
        "edges": runtime.edges if runtime else record.edges_,
    }


class UpdateExecutionRequest(BaseModel):
    status: str  # "completed", "failed", "stopped"
    result: Any = None
    reason: str | None = None


@router.patch("/executions/{slug}", tags=["executions", "mcp-driver"], operation_id="update_execution")
async def update_execution_endpoint(
    slug: str,
    request: UpdateExecutionRequest,
    caller: Caller,
    executions: UserExecutions,
):
    """Update execution status (complete, fail, or stop)."""
    if request.status not in ("completed", "failed", "stopped"):
        raise HTTPException(400, "Status must be 'completed', 'failed', or 'stopped'")

    ex = executions.get(slug)
    if not ex:
        # Not running in memory -- update DB record directly if it exists
        async with get_session() as db:
            record = await get_execution_by_slug(db, caller.user.id, slug)
            if not record:
                raise HTTPException(404, f"Execution '{slug}' not found")
            await update_execution(db, record.id, status=request.status)
        return {"status": request.status, "execution_slug": slug}

    if request.status == "completed":
        ex.done(request.result)
    elif request.status == "failed":
        logger.warning("Execution %s failed: %s", slug, request.reason)
        ex.fail(request.reason or "unknown")
    elif request.status == "stopped":
        await ex.stop()
        del executions[slug]

    return {"status": request.status, "execution_slug": slug}


@router.get("/executions", tags=["executions", "mcp-driver"], operation_id="list_executions")
async def list_executions_endpoint(
    caller: Caller,
    active_only: bool = True,
):
    """List all executions for the current user."""
    async with get_session() as db:
        records = await get_user_executions(db, caller.user.id, active_only=active_only)

    return {
        "executions": [
            {
                "id": str(record.id),
                "slug": record.slug,
                "spec": record.spec[:100] + "..." if len(record.spec) > 100 else record.spec,
                "repo_full_name": record.repo_full_name,
                "status": record.status,
                "error": record.error,
                "metadata": record.metadata_,
                "branch_name": record.branch_name,
                "pr_url": record.pr_url,
                "started_at": record.started_at.isoformat() if record.started_at else None,
            }
            for record in records
        ],
    }


@router.get("/executions/{slug}/diff", tags=["executions", "mcp-driver"], operation_id="get_execution_diff")
async def get_execution_diff(
    slug: str,
    caller: Caller,
    executions: UserExecutions,
    agent: str | None = None,
):
    """Get git diff from an execution's VM."""
    async with get_session() as db:
        record = await get_execution_by_slug(db, caller.user.id, slug)
        if not record:
            raise HTTPException(404, f"Execution '{slug}' not found")

    machine = _get_agent_machine(executions, slug, agent)

    try:
        result = await machine.sandbox.exec(
            "sudo -u agent bash -c 'repo=$(ls -d /home/agent/*/ | head -1) && cd \"$repo\" && git add . && git diff --cached HEAD'"
        )
    except Exception:
        logger.exception("Failed to retrieve diff from VM for %s", slug)
        raise HTTPException(502, "Failed to retrieve diff from VM")
    return {"diff": result.stdout, "execution_id": str(record.id), "execution_slug": record.slug}


@router.get("/executions/{slug}/activity", tags=["executions", "mcp-driver"], operation_id="get_execution_activity")
async def get_execution_activity(
    slug: str,
    caller: Caller,
    n: int = 50,
    compact: bool = True,
):
    """Get recent activity from an execution's trace."""
    async with get_session() as db:
        record = await get_execution_by_slug(db, caller.user.id, slug)
        if not record:
            raise HTTPException(404, f"Execution '{slug}' not found")

    events = execution_trace.read_tail(str(caller.user.id), slug, n * 10)
    total_events = execution_trace.count_events(str(caller.user.id), slug)

    agents = sorted(set(e.get("agent") for e in events if e.get("agent")))

    merged_events = merge_response_chunks(events)

    interesting_types = {
        "tool_use",
        "tool_result",
        "prompt",
        "response_chunk",
        "connected",
        "disconnected",
        "error",
    }
    recent_activity = [e for e in merged_events if e.get("type") in interesting_types]
    recent_activity = recent_activity[-n:]
    recent_activity = [normalize_event(e, compact) for e in recent_activity]

    return {
        "execution_slug": slug,
        "agents": agents,
        "event_count": total_events,
        "recent_activity": recent_activity,
    }


@router.get("/executions/{slug}/ssh", tags=["executions"], operation_id="get_execution_ssh")
async def get_execution_ssh(
    slug: str,
    caller: Caller,
    executions: UserExecutions,
    agent: str | None = None,
):
    """Get SSH credentials for an execution's VM."""
    async with get_session() as db:
        record = await get_execution_by_slug(db, caller.user.id, slug)
        if not record:
            raise HTTPException(404, f"Execution '{slug}' not found")

    ex = executions.get(slug)
    if not ex:
        raise HTTPException(404, f"Execution '{slug}' is no longer running")

    machine = _get_agent_machine(executions, slug, agent)

    # Resolve agent type and session ID from the agent
    agent_type = "unknown"
    session_id = None
    agent_obj = ex.agents.get(agent) if agent else None
    if agent_obj:
        agent_type = agent_obj.config.agent_type
        session_id = agent_obj.session_id

    try:
        creds = await machine.ssh_credentials()
    except Exception:
        logger.exception("Failed to retrieve SSH credentials for execution %s", slug)
        raise HTTPException(502, "Failed to retrieve SSH credentials")

    if not creds:
        raise HTTPException(501, "SSH access is not available for this sandbox backend")

    return {
        "host": creds.host,
        "port": creds.port,
        "username": creds.username,
        "private_key": creds.private_key,
        "password": creds.password,
        "execution_slug": slug,
        "agent": agent,
        "backend": agent_type,
        "session_id": session_id,
    }


# ---------------------------------------------------------------------------
# SSE streaming
# ---------------------------------------------------------------------------


@router.get("/executions/{slug}/stream", tags=["executions"], operation_id="stream_execution")
async def stream_execution(
    slug: str,
    request: Request,
    caller: Caller,
    executions: UserExecutions,
    raw: bool = False,
):
    """Stream execution trace events as SSE.

    Tails the JSONL trace file and yields new events as they appear. Supports
    resumption via the Last-Event-ID header (value is a line number in the
    trace file). Pass raw=true to skip response chunk merging.
    """
    async with get_session() as db:
        record = await get_execution_by_slug(db, caller.user.id, slug)
        if not record:
            raise HTTPException(404, "Execution not found")

    last_event_id = int(request.headers.get("Last-Event-ID", "0"))

    async def generate():
        async for item in iter_execution_stream(
            str(caller.user.id),
            slug,
            executions,
            start_line=last_event_id,
            raw=raw,
            is_disconnected=request.is_disconnected,
        ):
            if item.kind == "activity":
                if item.activity is None:
                    continue
                yield (f"id: {item.activity.event_id}\nevent: activity\ndata: {json.dumps(item.activity.payload)}\n\n")
            elif item.kind == "done":
                yield "event: done\ndata: {}\n\n"
            elif item.kind == "keepalive":
                yield ": keepalive\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# Chat / message
# ---------------------------------------------------------------------------


class ChatMessageRequest(BaseModel):
    text: str


@router.post(
    "/executions/{slug}/agents/{agent_name}/message",
    tags=["executions"],
    operation_id="send_agent_message",
)
async def send_agent_message(
    slug: str,
    agent_name: str,
    body: ChatMessageRequest,
    caller: Caller,
    executions: UserExecutions,
):
    """Send a chat message to an agent in a running execution."""
    ex = _get_runtime_execution(executions, slug)
    _get_runtime_agent(ex, agent_name)
    await ex.prompt(agent_name, body.text)
    return {"status": "sent"}


# ---------------------------------------------------------------------------
# WebSocket for bidirectional execution <-> client communication
# ---------------------------------------------------------------------------


@router.websocket("/executions/{slug}/ws")
async def execution_websocket(
    websocket: WebSocket,
    slug: str,
    token: str | None = Query(default=None),
):
    """Bidirectional WebSocket for execution-client communication.

    Streams execution trace events (including client_event) to the client.
    Accepts JSON messages from the client and relays them to the runtime
    sandbox's event handlers.

    Client sends: {"event": "name", "data": {...}}
    Server sends trace events and: {"type": "event_result", "event": "name", "result": ...}
    """
    await websocket.accept()

    user = await _get_local_user()

    user_id = str(user.id)
    registry = get_executions_registry()
    executions = registry.get(user_id, {})
    ex = executions.get(slug)
    if not ex:
        await websocket.close(code=4004, reason="Execution not found or not running")
        return

    async def send_loop():
        """Tail the execution trace and send events to the client."""
        async for item in iter_execution_stream(user_id, slug, executions):
            if item.kind == "activity":
                if item.activity is None:
                    continue
                await websocket.send_json(item.activity.payload)
            elif item.kind == "done":
                await websocket.send_json({"type": "done"})
            elif item.kind == "keepalive":
                await websocket.send_json({"type": "keepalive"})

    async def recv_loop():
        """Receive events from the client and dispatch to runtime handlers."""
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_json({"type": "event_error", "error": "Invalid JSON"})
                continue

            event_name = msg.get("event")
            if not event_name:
                await websocket.send_json({"type": "event_error", "error": "Missing 'event' field"})
                continue

            try:
                result = await asyncio.wait_for(ex.handle_client_event(event_name, msg.get("data", {})), timeout=30)
                await websocket.send_json({"type": "event_result", "event": event_name, "result": result})
            except TimeoutError:
                await websocket.send_json(
                    {"type": "event_error", "event": event_name, "error": "Execution is not responding"}
                )
            except Exception as exc:
                await websocket.send_json({"type": "event_error", "event": event_name, "error": str(exc)})

    try:
        await asyncio.gather(send_loop(), recv_loop())
    except WebSocketDisconnect:
        pass
    except Exception:
        if websocket.client_state == WebSocketState.CONNECTED:
            await websocket.close(code=1011)
