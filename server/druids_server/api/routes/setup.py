"""Devbox setup endpoints.

Two flows:

1. CLI flow (existing): ``setup/start`` provisions a sandbox and returns SSH
   credentials. The user SSHes in, configures manually, then calls
   ``setup/finish`` to snapshot. No agent involved.

2. Wizard flow (new): ``setup/wizard/start`` provisions a sandbox, launches a
   Claude agent on it, and returns a session slug. The frontend connects via
   SSE to stream agent messages and tool calls. The agent interactively
   configures the environment with user guidance.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from druids_server.api.deps import Caller, require_driver
from druids_server.api.github import get_installation_token
from druids_server.db.models.devbox import get_devbox, get_or_create_devbox, get_user_devboxes, resolve_devbox
from druids_server.db.session import get_session
from druids_server.lib.machine import Machine
from druids_server.lib.sandbox.base import Sandbox
from druids_server.lib.setup_session import (
    SetupSession,
    emit_event,
    flush_text_buffer,
    get_launch_lock,
    launch_setup_session,
    setup_registry,
    subscribe,
    unsubscribe,
)


router = APIRouter(dependencies=[Depends(require_driver)])
logger = logging.getLogger(__name__)

# ===========================================================================
# CLI flow (unchanged)
# ===========================================================================


class StartRequest(BaseModel):
    name: str | None = None
    repo_full_name: str | None = None
    vcpus: int | None = None
    memory_mb: int | None = None
    disk_mb: int | None = None


class FinishRequest(BaseModel):
    name: str | None = None
    repo_full_name: str | None = None


@router.post("/devbox/setup/start", tags=["devbox"], operation_id="setup_start")
async def setup_start(request: StartRequest, caller: Caller):
    """Start devbox setup by provisioning a sandbox.

    Creates a sandbox, optionally clones a repo, and returns SSH credentials
    so the user can configure the environment interactively. The sandbox stays
    running until `setup/finish` is called.
    """
    repo = request.repo_full_name
    devbox_name = request.name or repo or "default"

    sandbox = await Sandbox.create()

    if repo:
        try:
            await _clone_repo(sandbox, repo, "/home/agent/repo")
        except Exception:
            logger.exception("Failed to clone %s", repo)
            await sandbox.stop()
            raise HTTPException(502, "Failed to clone repository")

    ssh = await sandbox.ssh_credentials()
    if not ssh:
        await sandbox.stop()
        raise HTTPException(500, "Sandbox does not support SSH")

    async with get_session() as db:
        if repo:
            devbox = await get_or_create_devbox(db, caller.user.id, repo)
        else:
            devbox = await get_or_create_devbox(db, caller.user.id, devbox_name)
        devbox.name = devbox_name
        if repo:
            devbox.repo_full_name = repo
        devbox.instance_id = sandbox.instance_id
        devbox.snapshot_id = None
        devbox.setup_completed_at = None
        devbox.vcpus = request.vcpus
        devbox.memory_mb = request.memory_mb
        devbox.disk_mb = request.disk_mb
        devbox.updated_at = datetime.now(timezone.utc)
        db.add(devbox)

    logger.info("setup_start: sandbox=%s for '%s'", sandbox.instance_id, devbox_name)

    return {
        "name": devbox_name,
        "instance_id": sandbox.instance_id,
        "ssh": {
            "host": ssh.host,
            "port": ssh.port,
            "username": ssh.username,
            "private_key": ssh.private_key,
            "password": ssh.password,
        },
    }


@router.post("/devbox/setup/finish", tags=["devbox"], operation_id="setup_finish")
async def setup_finish(request: FinishRequest, caller: Caller):
    """Finish devbox setup by snapshotting and stopping the sandbox.

    Looks up the running sandbox from the devbox record, scrubs any git
    token from the remote URL, snapshots the environment, stops the sandbox,
    and updates the devbox with the snapshot ID.
    """
    devbox_name = request.name
    repo = request.repo_full_name

    async with get_session() as db:
        devbox = await resolve_devbox(db, caller.user.id, name=devbox_name, repo_full_name=repo)
        if not devbox:
            raise HTTPException(404, "Devbox not found")
        if not devbox.instance_id:
            raise HTTPException(400, "No running sandbox for this devbox. Run setup/start first.")

        instance_id = devbox.instance_id
        resolved_repo = devbox.repo_full_name or None
        resolved_name = devbox.name

    sandbox = await Sandbox.get(instance_id, owned=True)
    try:
        if resolved_repo:
            await sandbox.exec(
                f"cd /home/agent/repo && git remote set-url origin https://github.com/{resolved_repo}.git",
                user="agent",
            )

        snapshot_id = await sandbox.snapshot()
        logger.info("setup_finish: snapshot=%s for '%s'", snapshot_id, resolved_name)
    finally:
        await sandbox.stop()

    async with get_session() as db:
        devbox = await resolve_devbox(db, caller.user.id, name=devbox_name, repo_full_name=repo)
        if devbox:
            devbox.snapshot_id = snapshot_id
            devbox.instance_id = None
            devbox.setup_completed_at = datetime.now(timezone.utc)
            devbox.updated_at = datetime.now(timezone.utc)
            db.add(devbox)

    return {"name": resolved_name, "snapshot_id": snapshot_id}


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
                "has_snapshot": d.snapshot_id is not None,
                "setup_slug": d.setup_slug,
                "instance_id": d.instance_id,
                "vcpus": d.vcpus,
                "memory_mb": d.memory_mb,
                "disk_mb": d.disk_mb,
                "created_at": d.created_at.isoformat() if d.created_at else None,
                "updated_at": d.updated_at.isoformat() if d.updated_at else None,
            }
            for d in devboxes
        ],
    }


# ===========================================================================
# Wizard flow
# ===========================================================================


class WizardStartRequest(BaseModel):
    repo_full_name: str
    mode: Literal["setup", "modify"] = "setup"
    vcpus: int | None = None
    memory_mb: int | None = None
    disk_mb: int | None = None


class WizardStartResponse(BaseModel):
    slug: str
    status: Literal["started", "resumed"]
    mode: Literal["setup", "modify"] = "setup"


class WizardSaveRequest(BaseModel):
    repo_full_name: str


class WizardSaveResponse(BaseModel):
    snapshot_id: str
    repo_full_name: str


class WizardResetRequest(BaseModel):
    repo_full_name: str


class WizardMessageRequest(BaseModel):
    text: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _configure_devbox_shell(sandbox: Sandbox) -> None:
    """Configure shell so SSH login drops into agent user in the repo directory.

    Idempotent: checks for a sentinel comment before appending.
    """
    switch_cmd = """grep -q '# DO NOT REMOVE' {file} 2>/dev/null || cat >> {file} << 'EOF'
# DO NOT REMOVE: switches to agent user for Druids
if [[ $- == *i* ]]; then
    exec su - agent
fi
EOF"""
    await sandbox.exec(switch_cmd.format(file="/root/.bash_profile"), user="root")
    await sandbox.exec(switch_cmd.format(file="/root/.bashrc"), user="root")
    cd_line = "cd /home/agent/repo"
    await sandbox.exec(
        f'grep -qF "{cd_line}" /home/agent/.bashrc 2>/dev/null || echo "{cd_line}" >> /home/agent/.bashrc',
        user="root",
    )


def _lookup_session(slug: str) -> tuple[str, SetupSession] | None:
    """Return (repo_full_name, session) for the given slug, or None."""
    for repo_full_name, session in setup_registry.items():
        if session.slug == slug:
            return repo_full_name, session
    return None


async def _persist_instance_id(user_id: UUID, repo_full_name: str, instance_id: str) -> None:
    """Update the devbox record with a new instance_id."""
    async with get_session() as db:
        devbox = await get_or_create_devbox(db, user_id, repo_full_name)
        devbox.instance_id = instance_id
        devbox.updated_at = datetime.now(timezone.utc)
        db.add(devbox)


async def _ensure_sandbox(
    repo_full_name: str,
    instance_id: str | None,
    snapshot_id: str | None,
    modify: bool,
    *,
    vcpus: int | None = None,
    memory_mb: int | None = None,
    disk_mb: int | None = None,
) -> Sandbox:
    """Provision or attach to a sandbox VM.

    Args:
        repo_full_name: Used for metadata and error messages.
        instance_id: Existing instance to attach to, or None to provision.
        snapshot_id: Snapshot to fork from (modify mode only).
        modify: If True, fork from snapshot_id.
        vcpus: Override vCPU count (None = default).
        memory_mb: Override memory in MB (None = default).
        disk_mb: Override disk in MB (None = default).

    Returns:
        A live Sandbox ready for use.
    """
    metadata = {"druids:setup": "true", "druids:repo": repo_full_name}
    resource_kwargs = {"vcpus": vcpus, "memory_mb": memory_mb, "disk_mb": disk_mb}

    if modify:
        try:
            return await Sandbox.create(snapshot_id=snapshot_id, metadata=metadata, **resource_kwargs)
        except Exception:
            logger.exception("Failed to provision modify VM for %s", repo_full_name)
            raise HTTPException(503, "Failed to start setup wizard. Please try again.")

    if not instance_id:
        try:
            return await Sandbox.create(metadata=metadata, **resource_kwargs)
        except Exception:
            logger.exception("Failed to provision VM for %s", repo_full_name)
            raise HTTPException(503, "Failed to start setup wizard. Please try again.")

    try:
        return await Sandbox.get(instance_id)
    except Exception:
        logger.warning("Instance %s is gone for %s, re-provisioning", instance_id, repo_full_name)
        try:
            return await Sandbox.create(metadata=metadata, **resource_kwargs)
        except Exception:
            logger.exception("Failed to re-provision VM for %s", repo_full_name)
            raise HTTPException(503, "Failed to start setup wizard. Please try again.")


async def _clone_repo(sandbox: Sandbox, repo_full_name: str, working_dir: str) -> None:
    """Clone a repo onto the sandbox using a credential helper to avoid leaking tokens.

    The token is written into a short-lived helper script rather than
    interpolated into the git clone command line, keeping it out of
    process listings and shell history.
    """
    gh_token = await get_installation_token(repo_full_name)

    # Write a one-shot credential helper via heredoc. The token does not
    # appear as a git CLI argument, so it stays out of `ps` output and
    # is not stored in .git/config as part of the remote URL.
    result = await sandbox.exec(
        "cat > /tmp/git-cred-helper.sh <<'CRED_HELPER'\n"
        "#!/bin/sh\n"
        f"printf 'protocol=https\\nhost=github.com\\nusername=x-access-token\\npassword={gh_token}\\n'\n"
        "CRED_HELPER\n"
        "chmod 700 /tmp/git-cred-helper.sh",
        user="root",
    )
    if not result.ok:
        raise RuntimeError(f"Failed to write credential helper: {result.stderr}")

    result = await sandbox.exec(
        f'test -d {working_dir}/.git || git -c credential.helper="/tmp/git-cred-helper.sh" '
        f"clone https://github.com/{repo_full_name}.git {working_dir}",
        user="root",
        timeout=120,
    )
    if not result.ok:
        raise RuntimeError(f"git clone failed (exit {result.exit_code}): {result.stderr}")

    await sandbox.exec("rm -f /tmp/git-cred-helper.sh", user="root")
    await sandbox.exec(f"chown -R agent:agent {working_dir}", user="root")


# ---------------------------------------------------------------------------
# POST /setup/wizard/start
# ---------------------------------------------------------------------------


@router.post("/setup/wizard/start", tags=["setup-wizard"], response_model=WizardStartResponse)
async def wizard_start(body: WizardStartRequest, caller: Caller):
    """Start or resume a setup wizard session for a repository.

    Handles all devbox states:
    - No instance -> provision VM, clone repo, launch wizard
    - Instance exists, no session -> launch wizard on existing VM
    - Instance exists, session alive -> return slug as "resumed"
    - Instance exists, session dead (server restart) -> clear slug, re-launch
    """
    repo_full_name = body.repo_full_name
    user_id_str = str(caller.user.id)

    async with get_launch_lock(repo_full_name):
        return await _wizard_start_locked(body, caller, user_id_str)


async def _wizard_start_locked(body: WizardStartRequest, caller: Caller, user_id_str: str):
    """Inner body of wizard_start, called while holding the per-repo launch lock."""
    repo_full_name = body.repo_full_name
    working_dir = "/home/agent/repo"

    # --- DB: read/create devbox, check state ---
    async with get_session() as db:
        devbox = await get_or_create_devbox(db, caller.user.id, repo_full_name)
        devbox.name = devbox.name or repo_full_name

        if devbox.snapshot_id and body.mode != "modify":
            raise HTTPException(409, "Already set up. Use mode='modify' to change, or reset first.")
        if not devbox.snapshot_id and body.mode == "modify":
            raise HTTPException(400, "No snapshot exists. Run setup first.")

        # Check for a live in-memory session.
        if devbox.setup_slug:
            existing = setup_registry.get(repo_full_name)
            if existing and existing.slug == devbox.setup_slug:
                if existing.user_id != user_id_str:
                    raise HTTPException(409, "Setup in progress by another user")
                return WizardStartResponse(slug=devbox.setup_slug, status="resumed", mode=existing.mode)

            # setup_slug is set but session is gone (server restart). Clear it.
            devbox.setup_slug = None
            devbox.updated_at = datetime.now(timezone.utc)
            db.add(devbox)

        instance_id = devbox.instance_id
        snapshot_id = devbox.snapshot_id

    # --- Ensure we have a live VM ---
    sandbox = await _ensure_sandbox(
        repo_full_name,
        instance_id,
        snapshot_id,
        modify=body.mode == "modify",
        vcpus=body.vcpus,
        memory_mb=body.memory_mb,
        disk_mb=body.disk_mb,
    )
    if sandbox.instance_id != instance_id:
        await _persist_instance_id(caller.user.id, repo_full_name, sandbox.instance_id)

    # --- Clone and configure (idempotent, skip for modify mode) ---
    if body.mode != "modify":
        try:
            await _clone_repo(sandbox, repo_full_name, working_dir)
        except Exception:
            logger.exception("Failed to clone %s", repo_full_name)
            raise HTTPException(503, "Failed to start setup wizard. Please try again.")

        try:
            await _configure_devbox_shell(sandbox)
        except Exception:
            logger.exception("Failed to configure VM shell for %s", repo_full_name)
            raise HTTPException(503, "Failed to start setup wizard. Please try again.")

    # --- Launch the wizard ---
    machine = Machine(sandbox=sandbox, snapshot_id=snapshot_id)
    try:
        session = await launch_setup_session(
            user_id=user_id_str,
            repo_full_name=repo_full_name,
            machine=machine,
            mode=body.mode,
        )
    except Exception:
        logger.exception("Failed to launch setup session for %s", repo_full_name)
        raise HTTPException(503, "Failed to start setup wizard. Please try again.")

    # Store in registry and persist slug to DB.
    setup_registry[repo_full_name] = session

    async with get_session() as db:
        devbox = await get_or_create_devbox(db, caller.user.id, repo_full_name)
        devbox.setup_slug = session.slug
        devbox.updated_at = datetime.now(timezone.utc)
        db.add(devbox)

    return WizardStartResponse(slug=session.slug, status="started", mode=body.mode)


# ---------------------------------------------------------------------------
# GET /setup/wizard/{slug}/chat  (SSE)
# ---------------------------------------------------------------------------


@router.get("/setup/wizard/{slug}/chat", tags=["setup-wizard"])
async def wizard_chat(slug: str, request: Request, caller: Caller):
    """SSE stream of chat events for a setup session.

    On connect: replay historical events with id > Last-Event-ID, then
    block on subscriber queue for new events. Sends keepalive comments
    every 30 seconds.
    """
    found = _lookup_session(slug)
    if found is None:
        raise HTTPException(404, "Session not found")

    _repo, session = found
    if session.user_id != str(caller.user.id):
        raise HTTPException(403, "Access denied")

    raw_last_id = request.headers.get("last-event-id", "-1")
    try:
        last_event_id = int(raw_last_id)
    except ValueError:
        last_event_id = -1

    async def event_generator():
        queue = subscribe(session)
        try:
            # Replay history.
            for event in session.events:
                if event["id"] > last_event_id:
                    yield _format_sse(event)

            # Stream new events.
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield _format_sse(event)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            unsubscribe(session, queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


def _format_sse(event: dict) -> str:
    """Format a session event as an SSE frame.

    Ephemeral events omit the ``id`` line so the client's Last-Event-ID
    is not advanced.
    """
    event_type = event["event"]
    data = json.dumps(event["data"])
    if event.get("ephemeral"):
        return f"event: {event_type}\ndata: {data}\n\n"
    return f"id: {event['id']}\nevent: {event_type}\ndata: {data}\n\n"


# ---------------------------------------------------------------------------
# POST /setup/wizard/{slug}/message
# ---------------------------------------------------------------------------


@router.post("/setup/wizard/{slug}/message", tags=["setup-wizard"])
async def wizard_message(slug: str, body: WizardMessageRequest, caller: Caller):
    """Send a user message to a running setup wizard session."""
    found = _lookup_session(slug)
    if found is None:
        raise HTTPException(404, "Session not found")

    _repo, session = found
    if session.user_id != str(caller.user.id):
        raise HTTPException(403, "Access denied")

    if session.status != "running":
        raise HTTPException(409, "Session is not running")

    flush_text_buffer(session)
    emit_event(session, "message", {"role": "user", "text": body.text})
    await session.conn.prompt_nowait(body.text)
    return {}


# ---------------------------------------------------------------------------
# POST /setup/wizard/{slug}/interrupt
# ---------------------------------------------------------------------------


@router.post("/setup/wizard/{slug}/interrupt", tags=["setup-wizard"])
async def wizard_interrupt(slug: str, caller: Caller):
    """Interrupt the running setup wizard agent."""
    found = _lookup_session(slug)
    if found is None:
        raise HTTPException(404, "Session not found")

    _repo, session = found
    if session.user_id != str(caller.user.id):
        raise HTTPException(403, "Access denied")

    if session.status != "running":
        raise HTTPException(409, "Session is not running")

    await session.conn.cancel()
    return {}


# ---------------------------------------------------------------------------
# POST /setup/wizard/save
# ---------------------------------------------------------------------------


@router.post("/setup/wizard/save", tags=["setup-wizard"], response_model=WizardSaveResponse)
async def wizard_save(body: WizardSaveRequest, caller: Caller):
    """Save devbox state by creating a snapshot."""
    repo_full_name = body.repo_full_name

    async with get_session() as db:
        devbox = await get_devbox(db, caller.user.id, repo_full_name)
        if not devbox or not devbox.instance_id:
            raise HTTPException(400, "No active setup for this repo.")

        instance_id = devbox.instance_id
        old_snapshot_id = devbox.snapshot_id

    # Scrub git auth token from remote URL before snapshotting.
    sandbox = await Sandbox.get(instance_id)
    try:
        await sandbox.exec(
            f"cd /home/agent/repo && git remote set-url origin https://github.com/{repo_full_name}.git",
            user="agent",
        )
    except Exception:
        logger.warning("Failed to scrub git remote for %s", repo_full_name, exc_info=True)

    # Snapshot.
    try:
        snapshot_id = await sandbox.snapshot()
    except Exception:
        logger.exception("Failed to snapshot %s", repo_full_name)
        raise HTTPException(503, "Failed to create snapshot")

    # Update DB.
    async with get_session() as db:
        devbox = await get_devbox(db, caller.user.id, repo_full_name)
        if devbox:
            devbox.snapshot_id = snapshot_id
            devbox.setup_slug = None
            devbox.setup_completed_at = datetime.now(timezone.utc)
            devbox.updated_at = datetime.now(timezone.utc)
            db.add(devbox)

    # Stop the agent connection and VM.
    session = setup_registry.pop(repo_full_name, None)
    if session:
        try:
            await session.conn.close()
        except Exception:
            logger.warning("Failed to stop agent connection for %s", repo_full_name, exc_info=True)

    try:
        await sandbox.stop()
    except Exception:
        logger.warning("Failed to stop VM %s after save", instance_id, exc_info=True)

    # Delete old snapshot.
    if old_snapshot_id:
        try:
            old_sandbox = await Sandbox.get(old_snapshot_id)
            await old_sandbox.stop()
        except Exception:
            logger.warning("Failed to delete old snapshot %s", old_snapshot_id, exc_info=True)

    return WizardSaveResponse(snapshot_id=snapshot_id, repo_full_name=repo_full_name)


# ---------------------------------------------------------------------------
# POST /setup/wizard/reset
# ---------------------------------------------------------------------------


@router.post("/setup/wizard/reset", tags=["setup-wizard"])
async def wizard_reset(body: WizardResetRequest, caller: Caller):
    """Reset setup by clearing devbox state so the user can redo it.

    Clears instance_id, setup_slug, snapshot_id, and setup_completed_at.
    Removes any in-memory session and makes a best-effort attempt to stop the VM.
    """
    repo_full_name = body.repo_full_name

    async with get_session() as db:
        devbox = await get_devbox(db, caller.user.id, repo_full_name)
        if not devbox:
            raise HTTPException(404, "No setup found for this repo.")

        old_instance_id = devbox.instance_id
        devbox.instance_id = None
        devbox.setup_slug = None
        devbox.snapshot_id = None
        devbox.setup_completed_at = None
        devbox.updated_at = datetime.now(timezone.utc)
        db.add(devbox)

    # Remove from in-memory registry.
    setup_registry.pop(repo_full_name, None)

    # Best-effort stop the VM.
    if old_instance_id:
        try:
            sandbox = await Sandbox.get(old_instance_id, owned=True)
            await sandbox.stop()
        except Exception:
            logger.warning("Failed to stop instance %s during reset", old_instance_id, exc_info=True)

    return {}
