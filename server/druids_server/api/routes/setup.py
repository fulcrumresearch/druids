"""Devbox setup endpoints: two-phase start/finish flow."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from druids_server.api.deps import Caller, require_driver
from druids_server.api.github import get_installation_token
from druids_server.db.models.devbox import get_or_create_devbox, get_user_devboxes, resolve_devbox
from druids_server.db.models.setup_session import create_setup_session, get_setup_session
from druids_server.db.session import get_session
from druids_server.lib.sandbox.base import Sandbox
from druids_server.lib.setup_session import (
    handle_provision_failure,
    handle_snapshot_failure,
    retry_session,
    transition_to_completed,
    transition_to_configuring,
    transition_to_provisioning,
    transition_to_saving,
)


router = APIRouter(dependencies=[Depends(require_driver)])
logger = logging.getLogger(__name__)


class StartRequest(BaseModel):
    name: str | None = None
    repo_full_name: str | None = None


class FinishRequest(BaseModel):
    session_id: str | None = None
    name: str | None = None
    repo_full_name: str | None = None


@router.post("/devbox/setup/start", tags=["devbox"], operation_id="setup_start")
async def setup_start(request: StartRequest, caller: Caller):
    """Start devbox setup by provisioning a sandbox.

    Creates a sandbox, optionally clones a repo, and returns SSH credentials
    so the user can configure the environment interactively. The sandbox stays
    running until `setup/finish` is called.

    This endpoint creates a setup session in INIT state, then transitions through
    PROVISIONING -> CONFIGURING. On error, the session transitions to ERROR state
    and the sandbox is stopped.
    """
    repo = request.repo_full_name
    devbox_name = request.name or repo or "default"

    # Create devbox and setup session
    async with get_session() as db:
        if repo:
            devbox = await get_or_create_devbox(db, caller.user.id, repo)
        else:
            devbox = await get_or_create_devbox(db, caller.user.id, devbox_name)
        devbox.name = devbox_name
        if repo:
            devbox.repo_full_name = repo
        devbox.instance_id = None
        devbox.snapshot_id = None
        devbox.setup_completed_at = None
        devbox.updated_at = datetime.now(timezone.utc)
        db.add(devbox)
        await db.flush()
        await db.refresh(devbox)

        session = await create_setup_session(db, caller.user.id, devbox.id)
        session_id = session.id

    # Create sandbox
    sandbox = None
    try:
        sandbox = await Sandbox.create()

        async with get_session() as db:
            await transition_to_provisioning(db, session_id, sandbox)

        # Clone repo if provided
        if repo:
            try:
                gh_token = await get_installation_token(repo)
            except Exception as ex:
                logger.exception("Failed to get installation token for %s", repo)
                async with get_session() as db:
                    await handle_provision_failure(db, session_id, ex, sandbox)
                raise HTTPException(502, "Failed to get GitHub credentials for this repo")

            clone_url = f"https://x-access-token:{gh_token}@github.com/{repo}.git"
            result = await sandbox.exec(
                f"git clone --depth 1 {clone_url} /home/agent/repo", user="root", timeout=120,
            )
            if not result.ok:
                error_msg = f"git clone failed (exit {result.exit_code}): {result.stderr}"
                logger.error(error_msg)
                async with get_session() as db:
                    await handle_provision_failure(db, session_id, Exception(error_msg), sandbox)
                raise HTTPException(500, "Failed to clone repository")
            await sandbox.exec("chown -R agent:agent /home/agent/repo", user="root")

        # Get SSH credentials
        ssh = await sandbox.ssh_credentials()
        if not ssh:
            async with get_session() as db:
                await handle_provision_failure(db, session_id, Exception("SSH not supported"), sandbox)
            raise HTTPException(500, "Sandbox does not support SSH")

        # Transition to CONFIGURING and update devbox
        async with get_session() as db:
            await transition_to_configuring(db, session_id)
            devbox_record = await get_or_create_devbox(db, caller.user.id, devbox_name if not repo else repo)
            devbox_record.instance_id = sandbox.instance_id
            devbox_record.updated_at = datetime.now(timezone.utc)
            db.add(devbox_record)

        logger.info("setup_start: sandbox=%s session=%s for '%s'", sandbox.instance_id, session_id, devbox_name)

        return {
            "name": devbox_name,
            "session_id": str(session_id),
            "instance_id": sandbox.instance_id,
            "ssh": {
                "host": ssh.host,
                "port": ssh.port,
                "username": ssh.username,
                "private_key": ssh.private_key,
                "password": ssh.password,
            },
        }
    except HTTPException:
        raise
    except Exception as ex:
        logger.exception("Unexpected error during setup_start")
        async with get_session() as db:
            await handle_provision_failure(db, session_id, ex, sandbox)
        raise HTTPException(500, "Setup failed")


@router.post("/devbox/setup/finish", tags=["devbox"], operation_id="setup_finish")
async def setup_finish(request: FinishRequest, caller: Caller):
    """Finish devbox setup by snapshotting and stopping the sandbox.

    Looks up the running sandbox from the devbox record, scrubs any git
    token from the remote URL, snapshots the environment, stops the sandbox,
    and updates the devbox with the snapshot ID.

    This endpoint transitions the session through SAVING -> COMPLETED.
    On error, the session transitions to ERROR state and the sandbox is stopped.
    """
    from uuid import UUID as UUIDType

    devbox_name = request.name
    repo = request.repo_full_name
    session_id_str = request.session_id

    # Look up devbox and optionally session
    async with get_session() as db:
        devbox = await resolve_devbox(db, caller.user.id, name=devbox_name, repo_full_name=repo)
        if not devbox:
            raise HTTPException(404, "Devbox not found")
        if not devbox.instance_id:
            raise HTTPException(400, "No running sandbox for this devbox. Run setup/start first.")

        instance_id = devbox.instance_id
        resolved_repo = devbox.repo_full_name or None
        resolved_name = devbox.name

        # Get session if provided, otherwise allow finish without session (legacy)
        session_id = None
        if session_id_str:
            try:
                session_id = UUIDType(session_id_str)
                session = await get_setup_session(db, session_id)
                if not session:
                    raise HTTPException(404, "Setup session not found")
                if session.state not in ("CONFIGURING", "VERIFYING"):
                    raise HTTPException(400, f"Cannot finish from state {session.state}")
            except ValueError:
                raise HTTPException(400, "Invalid session_id format")

    sandbox = await Sandbox.get(instance_id, owned=True)
    stopped = False
    try:
        # Transition to SAVING if we have a session
        if session_id:
            async with get_session() as db:
                await transition_to_saving(db, session_id)

        # Scrub git token
        if resolved_repo:
            await sandbox.exec(
                f"cd /home/agent/repo && git remote set-url origin https://github.com/{resolved_repo}.git",
                user="agent",
            )

        # Create snapshot
        snapshot_id = await sandbox.snapshot()
        logger.info("setup_finish: snapshot=%s for '%s'", snapshot_id, resolved_name)
    except Exception as ex:
        stopped = True
        if session_id:
            async with get_session() as db:
                await handle_snapshot_failure(db, session_id, ex, sandbox)
        else:
            await sandbox.stop()
        raise
    finally:
        if not stopped:
            await sandbox.stop()

    # Update devbox and transition session to COMPLETED
    async with get_session() as db:
        devbox = await resolve_devbox(db, caller.user.id, name=devbox_name, repo_full_name=repo)
        if devbox:
            devbox.snapshot_id = snapshot_id
            devbox.instance_id = None
            devbox.setup_completed_at = datetime.now(timezone.utc)
            devbox.updated_at = datetime.now(timezone.utc)
            db.add(devbox)

        if session_id:
            await transition_to_completed(db, session_id)

    return {"name": resolved_name, "snapshot_id": snapshot_id}


@router.post("/setup/sessions", tags=["devbox"], operation_id="create_setup_session")
async def create_setup_session_endpoint(request: StartRequest, caller: Caller):
    """Create a new setup session.

    This is a convenience endpoint that wraps /devbox/setup/start.
    It creates a session and begins provisioning a sandbox.
    """
    return await setup_start(request, caller)


@router.get("/setup/sessions/{session_id}", tags=["devbox"], operation_id="get_setup_session")
async def get_setup_session_endpoint(session_id: str, caller: Caller):
    """Get a setup session by ID.

    Returns the current state, error information, and related metadata
    for tracking the setup wizard progress.
    """
    from uuid import UUID as UUIDType

    try:
        session_uuid = UUIDType(session_id)
    except ValueError:
        raise HTTPException(400, "Invalid session_id format")

    async with get_session() as db:
        session = await get_setup_session(db, session_uuid)
        if not session:
            raise HTTPException(404, "Setup session not found")

        # Get devbox to include repo info
        from druids_server.db.models.devbox import Devbox
        from sqlalchemy import select

        result = await db.execute(select(Devbox).where(Devbox.id == session.devbox_id))
        devbox = result.scalar_one_or_none()

    # Format SSH info if instance is running
    ssh_info = None
    if session.instance_id and session.state in ("PROVISIONING", "CONFIGURING", "VERIFYING"):
        try:
            sandbox = await Sandbox.get(session.instance_id, owned=False)
            ssh_creds = await sandbox.ssh_credentials()
            if ssh_creds:
                ssh_info = f"ssh {ssh_creds.username}@{ssh_creds.host}"
        except Exception:
            logger.warning("Failed to get SSH info for session %s", session_id)

    return {
        "session_id": str(session.id),
        "state": session.state,
        "status": session.state.lower(),
        "repo_full_name": devbox.repo_full_name if devbox else None,
        "ssh_info": ssh_info,
        "error_message": session.error_message,
        "failed_step": session.failed_step,
        "created_at": session.created_at.isoformat(),
        "updated_at": session.updated_at.isoformat(),
        "completed_at": session.completed_at.isoformat() if session.completed_at else None,
    }


@router.post("/setup/sessions/{session_id}/snapshot", tags=["devbox"], operation_id="snapshot_setup_session")
async def snapshot_setup_session(session_id: str, caller: Caller):
    """Snapshot a setup session.

    This triggers the snapshot creation for a session in CONFIGURING or VERIFYING state.
    It transitions the session through SAVING -> COMPLETED and stops the sandbox.
    """
    from uuid import UUID as UUIDType

    try:
        session_uuid = UUIDType(session_id)
    except ValueError:
        raise HTTPException(400, "Invalid session_id format")

    async with get_session() as db:
        session = await get_setup_session(db, session_uuid)
        if not session:
            raise HTTPException(404, "Setup session not found")

        # Get devbox to find the instance
        from druids_server.db.models.devbox import Devbox
        from sqlalchemy import select

        result = await db.execute(select(Devbox).where(Devbox.id == session.devbox_id))
        devbox = result.scalar_one_or_none()
        if not devbox:
            raise HTTPException(404, "Devbox not found")

        if not devbox.instance_id:
            raise HTTPException(400, "No running sandbox to snapshot")

        instance_id = devbox.instance_id
        resolved_repo = devbox.repo_full_name or None
        resolved_name = devbox.name

    # Call finish with the session_id
    finish_request = FinishRequest(
        session_id=session_id,
        name=resolved_name,
        repo_full_name=resolved_repo,
    )
    return await setup_finish(finish_request, caller)


@router.post("/setup/sessions/{session_id}/retry", tags=["devbox"], operation_id="retry_setup_session")
async def retry_setup_session(session_id: str, caller: Caller):
    """Retry a failed setup session.

    Clears the error state and returns the session to INIT so it can be
    retried from the beginning. The session must be in ERROR state.
    """
    from uuid import UUID as UUIDType

    try:
        session_uuid = UUIDType(session_id)
    except ValueError:
        raise HTTPException(400, "Invalid session_id format")

    async with get_session() as db:
        session = await retry_session(db, session_uuid)

    return {
        "session_id": str(session.id),
        "state": session.state,
        "error_message": None,
        "failed_step": None,
    }


@router.get("/devboxes", tags=["devbox"], operation_id="list_devboxes")
async def list_devboxes(caller: Caller):
    """List all devboxes for the current user."""
    async with get_session() as db:
        devboxes = await get_user_devboxes(db, caller.user.id)

    return {
        "devboxes": [
            {
                "name": d.name,
                "repo_full_name": d.repo_full_name or None,
                "snapshot_id": d.snapshot_id,
                "ready": d.snapshot_id is not None,
                "created_at": d.created_at.isoformat() if d.created_at else None,
                "updated_at": d.updated_at.isoformat() if d.updated_at else None,
            }
            for d in devboxes
        ],
    }
