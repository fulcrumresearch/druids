"""Execution endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from orpheus.api.deps import CurrentUser, UserExecutions
from orpheus.api.helpers.trace_format import merge_response_chunks, normalize_event
from orpheus.lib import execution_trace
from orpheus.lib.morph import aget_instance, get_instance
from orpheus.db.models.execution import get_execution_by_slug
from orpheus.db.models.task import get_task
from orpheus.db.session import get_session


logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/executions/{slug}/diff", tags=["executions", "mcp-driver"], operation_id="get_execution_diff")
async def get_execution_diff(
    slug: str,
    user: CurrentUser,
):
    """Get git diff from an execution's root instance."""
    async with get_session() as db:
        record = await get_execution_by_slug(db, user.id, slug)
        if not record:
            raise HTTPException(404, f"Execution '{slug}' not found")

        task = await get_task(db, record.task_id)
        if not task or task.user_id != user.id:
            raise HTTPException(404, f"Execution '{slug}' not found")

    if not record.root_instance_id:
        raise HTTPException(404, f"Execution '{slug}' has no instance")

    inst = get_instance(record.root_instance_id)
    if not inst:
        raise HTTPException(404, f"Instance {record.root_instance_id} not found or stopped")

    # Get diff from the repo directory (including new untracked files)
    try:
        result = await inst.aexec(
            "sudo -u agent bash -c 'repo=$(ls -d /home/agent/*/ | head -1) && cd \"$repo\" && git add . && git diff --cached HEAD'"
        )
    except Exception as e:
        logger.exception("Failed to retrieve diff from VM for %s", slug)
        raise HTTPException(502, "Failed to retrieve diff from VM") from e
    return {"diff": result.stdout, "execution_id": str(record.id), "execution_slug": record.slug}


@router.get("/executions/{slug}/activity", tags=["executions", "mcp-driver"], operation_id="get_execution_activity")
async def get_execution_activity(
    slug: str,
    user: CurrentUser,
    n: int = 50,
    compact: bool = True,
):
    """Get recent activity from an execution's trace.

    Returns recent events from the normalized trace. Events are already in a
    standard format across all agent backends (claude-code-acp, codex-acp).
    """
    # Look up execution to verify user owns it
    async with get_session() as db:
        record = await get_execution_by_slug(db, user.id, slug)
        if not record:
            raise HTTPException(404, f"Execution with slug '{slug}' not found")

        # Verify user owns this execution via task
        task = await get_task(db, record.task_id)
        if not task or task.user_id != user.id:
            raise HTTPException(404, f"Execution with slug '{slug}' not found")

    # Read recent events from trace
    events = execution_trace.read_tail(str(user.id), slug, n * 10)
    total_events = execution_trace.count_events(str(user.id), slug)

    # Extract unique agent names from events
    agents = sorted(set(e.get("agent") for e in events if e.get("agent")))

    merged_events = merge_response_chunks(events)

    # Filter to interesting event types
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
    user: CurrentUser,
    executions: UserExecutions,
    agent: str | None = None,
):
    """Get SSH credentials for an execution's VM.

    Without ?agent, returns creds for the root instance.
    With ?agent=<name>, returns creds for that agent's VM (requires execution still in memory).
    """
    async with get_session() as db:
        record = await get_execution_by_slug(db, user.id, slug)
        if not record:
            raise HTTPException(404, f"Execution '{slug}' not found")

        task = await get_task(db, record.task_id)
        if not task or task.user_id != user.id:
            raise HTTPException(404, f"Execution '{slug}' not found")

    # If agent specified, look up from in-memory execution
    if agent:
        ex = executions.get(slug)
        if not ex:
            raise HTTPException(404, f"Execution '{slug}' is no longer running (cannot resolve agent)")

        program = ex.programs.get(agent)
        if not program or not program.is_agent:
            available = [name for name, p in ex.programs.items() if p.is_agent]
            raise HTTPException(404, f"Agent '{agent}' not found. Available: {', '.join(available)}")

        if not program.machine:
            raise HTTPException(404, f"Agent '{agent}' has no machine")

        instance_id = program.machine.instance_id
        try:
            ssh_key = await program.machine.ssh_key()
        except Exception as e:
            logger.exception("Failed to retrieve SSH key for agent %s in %s", agent, slug)
            raise HTTPException(502, "Failed to retrieve SSH credentials") from e
    else:
        # Default: root instance from DB
        if not record.root_instance_id:
            raise HTTPException(404, f"Execution '{slug}' has no instance")

        instance_id = record.root_instance_id
        try:
            inst = await aget_instance(record.root_instance_id)
        except Exception:
            raise HTTPException(404, f"Instance {record.root_instance_id} not found or stopped")

        try:
            ssh_key = await inst.assh_key()
        except Exception as e:
            logger.exception("Failed to retrieve SSH key for execution %s", slug)
            raise HTTPException(502, "Failed to retrieve SSH credentials") from e

    return {
        "host": "ssh.cloud.morph.so",
        "username": instance_id,
        "private_key": ssh_key.private_key,
        "password": ssh_key.password,
        "execution_slug": slug,
        "agent": agent,
    }
