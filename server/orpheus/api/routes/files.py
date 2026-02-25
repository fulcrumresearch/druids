"""File transfer endpoints.

These are regular HTTP endpoints (not MCP tools) called by the CLI.
Download returns raw bytes. Upload accepts multipart form data.
File contents never pass through model context.
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from orpheus.api.deps import CurrentUser, UserExecutions
from orpheus.config import settings
from orpheus.lib.devbox import InstanceNotFound, resolve_instance


router = APIRouter(prefix="/files", tags=["files"])
logger = logging.getLogger(__name__)


@router.get("/download")
async def download_file(
    user: CurrentUser,
    executions: UserExecutions,
    path: str = Query(..., description="Remote file path"),
    repo: str | None = Query(None, description="Repo full name (devbox target)"),
    execution_slug: str | None = Query(None, description="Execution slug"),
    agent_name: str | None = Query(None, description="Agent name"),
):
    """Download a file from a VM. Returns raw file content."""
    if not settings.enable_task_creation:
        raise HTTPException(403, "File transfer is disabled")

    try:
        inst = await resolve_instance(user, executions, repo=repo, execution_slug=execution_slug, agent_name=agent_name)
    except InstanceNotFound as e:
        raise HTTPException(404, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e

    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp_path = tmp.name
    try:
        await inst.adownload(path, tmp_path)
    except (IOError, OSError) as e:
        Path(tmp_path).unlink(missing_ok=True)
        raise HTTPException(404, f"File not found: {path}") from e

    return FileResponse(
        tmp_path,
        filename=Path(path).name,
        media_type="application/octet-stream",
        background=BackgroundTask(Path(tmp_path).unlink, missing_ok=True),
    )


@router.post("/upload")
async def upload_file(
    user: CurrentUser,
    executions: UserExecutions,
    file: UploadFile = File(...),
    path: str = Query(..., description="Remote file path"),
    repo: str | None = Query(None, description="Repo full name (devbox target)"),
    execution_slug: str | None = Query(None, description="Execution slug"),
    agent_name: str | None = Query(None, description="Agent name"),
):
    """Upload a file to a VM. Accepts multipart form data."""
    if not settings.enable_task_creation:
        raise HTTPException(403, "File transfer is disabled")

    try:
        inst = await resolve_instance(user, executions, repo=repo, execution_slug=execution_slug, agent_name=agent_name)
    except InstanceNotFound as e:
        raise HTTPException(404, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e

    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name
    try:
        await inst.aupload(tmp_path, path)
        # MorphCloud SFTP connects as root, so uploaded files are root-owned.
        # Chown to agent so the agent process can read/write them.
        await inst.aexec(f"chown agent:agent {path}")
    except (IOError, OSError) as e:
        Path(tmp_path).unlink(missing_ok=True)
        logger.exception("Failed to upload file to %s", path)
        raise HTTPException(502, "Failed to upload file") from e
    Path(tmp_path).unlink()

    return {"status": "uploaded", "path": path}
