"""Task endpoints."""

from __future__ import annotations

import dataclasses
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from orpheus.api.deps import CurrentUser, UserExecutions
from orpheus.api.launch import launch_execution
from orpheus.config import settings
from orpheus.lib.devbox import machine_from_devbox
from orpheus.lib.machine import Machine
from orpheus.lib.spec import build_root_agent, parse_program_spec
from orpheus.db.models.devbox import get_devbox
from orpheus.db.models.execution import get_task_executions, get_user_execution_count, update_execution
from orpheus.db.models.spec import compute_spec_hash, upsert_spec
from orpheus.db.models.task import create_task, get_task_by_slug, get_user_tasks, update_task_status
from orpheus.db.models.user_spec import get_user_specs
from orpheus.db.session import get_session
from orpheus.programs import discover_programs


router = APIRouter()
logger = logging.getLogger(__name__)


class CreateTaskRequest(BaseModel):
    spec: str
    snapshot_id: str | None = None
    repo_full_name: str | None = None  # Auto-detected from cwd by CLI
    git_branch: str | None = None  # Branch to checkout after VM provision
    metadata: dict | None = None  # Arbitrary metadata
    program_filter: list[str] | None = None  # Filter registered programs by label or hash
    programs: list[str] | None = None  # Legacy: filter Python-discovered programs by name
    program_spec: str | None = None  # YAML program spec (bypasses registered programs)
    program_spec_label: str | None = None  # Human-readable label for the spec
    program_params: dict[str, str] | None = None  # Param overrides for YAML spec


@router.post("/tasks", tags=["tasks", "mcp-driver"], operation_id="create_task")
async def create_task_endpoint(
    request: CreateTaskRequest,
    user: CurrentUser,
    executions: UserExecutions,
):
    """Create and start a new task.

    Discovers all programs in programs/ and creates an Execution for each.
    All executions run in parallel from the same snapshot.
    """
    if not settings.enable_task_creation:
        raise HTTPException(403, "Task creation is disabled")

    # Enforce billing: free tier allows N reviews, then require subscription
    if settings.stripe_api_key and user.subscription_status != "active":
        async with get_session() as db:
            count = await get_user_execution_count(db, user.id)
        if count >= settings.free_tier_reviews:
            raise HTTPException(
                402,
                f"Free tier limit reached ({settings.free_tier_reviews} reviews). Please subscribe to continue.",
            )

    # Require repo
    if not request.repo_full_name:
        raise HTTPException(400, "repo_full_name is required")

    # Build devbox Machine: explicit snapshot_id > devbox record
    devbox_machine = None
    if request.snapshot_id:
        devbox_machine = Machine(snapshot_id=request.snapshot_id)
    else:
        async with get_session() as db:
            devbox = await get_devbox(db, user.id, request.repo_full_name)
            if devbox and devbox.snapshot_id:
                devbox_machine = await machine_from_devbox(devbox)

    if not devbox_machine:
        raise HTTPException(
            400, f"No devbox snapshot for {request.repo_full_name}. Run 'orpheus setup start/finish' first."
        )

    repo_name = request.repo_full_name.split("/")[-1]
    metadata = {"repo_full_name": request.repo_full_name, **(request.metadata or {})}
    if request.git_branch:
        metadata["git_branch"] = request.git_branch

    async with get_session() as db:
        task = await create_task(
            db, user_id=user.id, spec=request.spec, snapshot_id=devbox_machine.snapshot_id, metadata=metadata
        )

    execution_slugs = []

    if request.program_spec:
        # YAML spec path: parse spec, build root agent, launch single execution
        try:
            spec_obj = parse_program_spec(request.program_spec, request.program_params)
            root = build_root_agent(spec_obj, repo_name)
        except (ValueError, Exception) as exc:
            logger.warning("Invalid program spec: %s", exc)
            raise HTTPException(400, f"Invalid program spec: {exc}") from exc

        # Register spec in the spec table for ELO tracking
        spec_hash = compute_spec_hash(request.program_spec)
        label = request.program_spec_label or spec_obj.root.name
        async with get_session() as db:
            await upsert_spec(db, spec_hash, label, request.program_spec)

        ex = await launch_execution(
            root,
            task=task,
            user=user,
            devbox_machine=devbox_machine,
            repo_full_name=request.repo_full_name,
            git_branch=request.git_branch,
            task_spec=request.spec,
            program_spec=request.program_spec,
        )
        executions[ex.slug] = ex
        execution_slugs.append(ex.slug)
    else:
        # Check for user-registered specs first
        async with get_session() as db:
            user_specs = await get_user_specs(db, user.id)

        if user_specs:
            # Apply program_filter if provided (match by label or hash)
            if request.program_filter:
                filters = {f.lower() for f in request.program_filter}
                user_specs = [s for s in user_specs if s.label.lower() in filters or s.hash in filters]
                if not user_specs:
                    raise HTTPException(
                        400, f"No registered programs match filter: {', '.join(request.program_filter)}"
                    )

            # User has registered specs: launch one execution per spec
            for spec_row in user_specs:
                try:
                    spec_obj = parse_program_spec(spec_row.yaml, request.program_params)
                    root = build_root_agent(spec_obj, repo_name)
                except (ValueError, Exception) as exc:
                    logger.warning("Skipping invalid user spec %s: %s", spec_row.hash, exc)
                    continue

                ex = await launch_execution(
                    root,
                    task=task,
                    user=user,
                    devbox_machine=devbox_machine,
                    repo_full_name=request.repo_full_name,
                    git_branch=request.git_branch,
                    task_spec=request.spec,
                    program_spec=spec_row.yaml,
                )
                executions[ex.slug] = ex
                execution_slugs.append(ex.slug)

            if not execution_slugs:
                raise HTTPException(400, "All registered specs failed to parse")
        else:
            # Fallback: Python discovery path
            programs = discover_programs()
            if not programs:
                raise HTTPException(500, "No programs found in programs/")

            if request.programs:
                available = {name for name, _ in programs}
                unknown = set(request.programs) - available
                if unknown:
                    raise HTTPException(
                        400,
                        f"Unknown program(s): {', '.join(unknown)}. Available: {', '.join(sorted(available))}",
                    )
                programs = [(name, fn) for name, fn in programs if name in request.programs]

            for _, create_fn in programs:
                root = create_fn(request.spec, repo_name)
                ex = await launch_execution(
                    root,
                    task=task,
                    user=user,
                    devbox_machine=devbox_machine,
                    repo_full_name=request.repo_full_name,
                    git_branch=request.git_branch,
                    task_spec=request.spec,
                )
                executions[ex.slug] = ex
                execution_slugs.append(ex.slug)

    return {
        "task_slug": task.slug,
        "task_id": str(task.id),
        "execution_slugs": execution_slugs,
        "status": "created",
    }


@router.get("/tasks/{slug}", tags=["tasks", "mcp-driver"], operation_id="get_task")
async def get_task_endpoint(
    slug: str,
    user: CurrentUser,
    executions: UserExecutions,
):
    """Get task status by slug."""
    async with get_session() as db:
        task = await get_task_by_slug(db, user.id, slug)
        if not task:
            raise HTTPException(404, f"Task '{slug}' not found")

        task_executions = await get_task_executions(db, task.id)

    # Find runtime executions for this task
    runtime_executions = {ex.slug: ex for slug, ex in executions.items() if ex.task_id == task.id}

    return {
        "task_id": str(task.id),
        "task_slug": task.slug,
        "spec": task.spec,
        "snapshot_id": task.snapshot_id,
        "is_active": task.is_active,
        "metadata": task.metadata_,
        "created_at": task.created_at.isoformat(),
        "executions": [
            {
                "id": str(ex.id),
                "slug": ex.slug,
                "program_name": ex.program_name,
                "status": ex.status,
                "branch_name": ex.branch_name,
                "pr_url": ex.pr_url,
                "programs": list(runtime_executions[ex.slug].programs.keys()) if ex.slug in runtime_executions else [],
                "connections": list(runtime_executions[ex.slug].connections.keys())
                if ex.slug in runtime_executions
                else [],
                "exposed_services": [dataclasses.asdict(svc) for svc in runtime_executions[ex.slug].exposed_services]
                if ex.slug in runtime_executions
                else [],
            }
            for ex in task_executions
        ],
    }


@router.delete("/tasks/{slug}", tags=["tasks", "mcp-driver"], operation_id="delete_task")
async def delete_task_endpoint(
    slug: str,
    user: CurrentUser,
    executions: UserExecutions,
):
    """Stop and deactivate a task by slug."""
    async with get_session() as db:
        task = await get_task_by_slug(db, user.id, slug)
        if not task:
            raise HTTPException(404, f"Task '{slug}' not found")

    # Stop all in-memory executions for this task
    for ex_slug, ex in list(executions.items()):
        if ex.task_id == task.id:
            await ex.stop()
            del executions[ex_slug]

    # Mark task as inactive and stop any DB execution records still marked active.
    # This catches executions that are no longer in memory (e.g. after server restart)
    # but still have status "running"/"starting" in the DB, which would otherwise
    # block future executions (zombie prevention).
    async with get_session() as db:
        await update_task_status(db, task.id, is_active=False)
        task_executions = await get_task_executions(db, task.id)
        for ex_record in task_executions:
            if ex_record.status in ("running", "starting"):
                await update_execution(db, ex_record.id, status="stopped")

    return {"status": "stopped", "task_id": str(task.id), "task_slug": task.slug}


@router.get("/tasks", tags=["tasks", "mcp-driver"], operation_id="list_tasks")
async def list_tasks_endpoint(
    user: CurrentUser,
    active_only: bool = True,
):
    """List all tasks for the current user with their executions."""
    async with get_session() as db:
        tasks = await get_user_tasks(db, user.id, active_only=active_only)

        # Include executions for each task
        result = []
        for task in tasks:
            task_data = {
                "id": str(task.id),
                "slug": task.slug,
                "spec": task.spec[:100] + "..." if len(task.spec) > 100 else task.spec,
                "is_active": task.is_active,
                "metadata": task.metadata_,
                "created_at": task.created_at.isoformat(),
            }
            task_executions = await get_task_executions(db, task.id)
            task_data["executions"] = [
                {
                    "id": str(ex.id),
                    "slug": ex.slug,
                    "program_name": ex.program_name,
                    "root_instance_id": ex.root_instance_id,
                    "status": ex.status,
                    "branch_name": ex.branch_name,
                    "pr_url": ex.pr_url,
                    "started_at": ex.started_at.isoformat() if ex.started_at else None,
                }
                for ex in task_executions
            ]
            result.append(task_data)

    return {"tasks": result}
