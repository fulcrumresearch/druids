"""Secret management endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from druids_server.api.deps import Caller, require_driver
from druids_server.db.models.devbox import resolve_devbox
from druids_server.db.models.secret import delete_secret, get_secrets, set_secret
from druids_server.db.session import get_session


router = APIRouter(dependencies=[Depends(require_driver)])
logger = logging.getLogger(__name__)


async def _require_devbox(caller, devbox_name, repo_full_name):
    """Resolve a devbox or raise 404."""
    if not devbox_name and not repo_full_name:
        raise HTTPException(400, "Either devbox_name or repo_full_name is required")
    async with get_session() as db:
        devbox = await resolve_devbox(db, caller.user.id, name=devbox_name, repo_full_name=repo_full_name)
    if not devbox:
        label = devbox_name or repo_full_name
        raise HTTPException(404, f"No devbox for '{label}'")
    return devbox


class SetSecretsRequest(BaseModel):
    devbox_name: str | None = None
    repo_full_name: str | None = None
    secrets: dict[str, str]


class DeleteSecretRequest(BaseModel):
    devbox_name: str | None = None
    repo_full_name: str | None = None
    name: str


@router.post("/secrets", tags=["secrets"], operation_id="set_secrets")
async def set_secrets_endpoint(request: SetSecretsRequest, caller: Caller):
    """Set one or more secrets on a devbox. Creates or updates each key."""
    devbox = await _require_devbox(caller, request.devbox_name, request.repo_full_name)
    async with get_session() as db:
        for name, value in request.secrets.items():
            await set_secret(db, devbox.id, name, value)
    return {"status": "ok", "count": len(request.secrets)}


@router.get("/secrets", tags=["secrets"], operation_id="list_secrets")
async def list_secrets_endpoint(
    caller: Caller,
    devbox_name: str | None = None,
    repo_full_name: str | None = None,
):
    """List secret names for a devbox. Values are not returned."""
    devbox = await _require_devbox(caller, devbox_name, repo_full_name)
    async with get_session() as db:
        secrets = await get_secrets(db, devbox.id)
    return {"secrets": [{"name": s.name, "updated_at": s.updated_at.isoformat()} for s in secrets]}


@router.delete("/secrets", tags=["secrets"], operation_id="delete_secret")
async def delete_secret_endpoint(request: DeleteSecretRequest, caller: Caller):
    """Delete a secret from a devbox."""
    devbox = await _require_devbox(caller, request.devbox_name, request.repo_full_name)
    async with get_session() as db:
        deleted = await delete_secret(db, devbox.id, request.name)
    if not deleted:
        raise HTTPException(404, f"Secret '{request.name}' not found")
    return {"status": "ok", "name": request.name}
