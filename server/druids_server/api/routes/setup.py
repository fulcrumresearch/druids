"""Devbox setup endpoints: two-phase start/finish flow."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from druids_server.api.deps import Caller, require_driver
from druids_server.api.github import get_installation_token
from druids_server.db.models.devbox import get_or_create_devbox, get_user_devboxes, resolve_devbox
from druids_server.db.session import get_session
from druids_server.lib.sandbox.base import Sandbox


router = APIRouter(dependencies=[Depends(require_driver)])
logger = logging.getLogger(__name__)


class StartRequest(BaseModel):
    name: str | None = None
    repo_full_name: str | None = None


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
            gh_token = await get_installation_token(repo)
        except Exception:
            logger.exception("Failed to get installation token for %s", repo)
            await sandbox.stop()
            raise HTTPException(502, "Failed to get GitHub credentials for this repo")

        clone_url = f"https://x-access-token:{gh_token}@github.com/{repo}.git"
        result = await sandbox.exec(
            f"git clone --depth 1 {clone_url} /home/agent/repo",
            user="root",
            timeout=120,
        )
        if not result.ok:
            logger.error(
                "git clone failed (exit %d): stdout=%s stderr=%s", result.exit_code, result.stdout, result.stderr
            )
            await sandbox.stop()
            raise HTTPException(500, "Failed to clone repository")
        await sandbox.exec("chown -R agent:agent /home/agent/repo", user="root")

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
                "ready": d.snapshot_id is not None,
                "created_at": d.created_at.isoformat() if d.created_at else None,
                "updated_at": d.updated_at.isoformat() if d.updated_at else None,
            }
            for d in devboxes
        ],
    }
