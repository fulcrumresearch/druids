"""User program registration endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from orpheus.api.deps import CurrentUser
from orpheus.db.models.spec import compute_spec_hash, get_spec_by_hash, upsert_spec
from orpheus.db.models.user_spec import get_user_specs, register_user_spec, unregister_user_spec
from orpheus.db.session import get_session


router = APIRouter()
logger = logging.getLogger(__name__)


class AddProgramRequest(BaseModel):
    yaml: str
    label: str | None = None


@router.post("/user/programs", tags=["programs"], operation_id="add_program")
async def add_program_endpoint(request: AddProgramRequest, user: CurrentUser):
    """Upload a YAML program spec and register it for the current user.

    Hashes the YAML, upserts into the global spec table, then registers the
    program for the user in the junction table.
    """
    yaml_str = request.yaml.strip()
    if not yaml_str:
        raise HTTPException(400, "YAML body is required")

    spec_hash = compute_spec_hash(yaml_str)
    label = request.label or ""

    async with get_session() as db:
        spec = await upsert_spec(db, spec_hash, label, yaml_str)
        await register_user_spec(db, user.id, spec.id)

    return {
        "id": str(spec.id),
        "hash": spec.hash,
        "label": spec.label,
        "rating": spec.rating,
        "num_comparisons": spec.num_comparisons,
        "created_at": spec.created_at.isoformat(),
    }


@router.get("/user/programs", tags=["programs"], operation_id="list_programs")
async def list_programs_endpoint(user: CurrentUser):
    """List all programs registered by the current user."""
    async with get_session() as db:
        specs = await get_user_specs(db, user.id)

    return {
        "programs": [
            {
                "id": str(s.id),
                "hash": s.hash,
                "label": s.label,
                "yaml": s.yaml,
                "rating": s.rating,
                "num_comparisons": s.num_comparisons,
                "created_at": s.created_at.isoformat(),
                "updated_at": s.updated_at.isoformat(),
            }
            for s in specs
        ]
    }


@router.delete("/user/programs/{spec_hash}", tags=["programs"], operation_id="remove_program")
async def remove_program_endpoint(spec_hash: str, user: CurrentUser):
    """Unregister a program for the current user. Does not delete the global spec row."""
    async with get_session() as db:
        spec = await get_spec_by_hash(db, spec_hash)
        if not spec:
            raise HTTPException(404, "Program not found")

        removed = await unregister_user_spec(db, user.id, spec.id)

    if not removed:
        raise HTTPException(404, "Program not registered for this user")

    return {"status": "removed", "hash": spec_hash}
