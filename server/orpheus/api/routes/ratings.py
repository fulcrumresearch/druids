"""Ratings endpoint for the ELO leaderboard."""

from fastapi import APIRouter, HTTPException

from orpheus.db.models.spec import get_all_specs, get_spec_by_hash
from orpheus.db.session import get_session


router = APIRouter()


@router.get("/ratings", tags=["ratings"], operation_id="get_ratings")
async def get_ratings_endpoint():
    """Get all spec ratings sorted by rating descending."""
    async with get_session() as db:
        specs = await get_all_specs(db)

    return {
        "ratings": [
            {
                "id": str(s.id),
                "hash": s.hash,
                "label": s.label,
                "rating": s.rating,
                "num_comparisons": s.num_comparisons,
                "updated_at": s.updated_at.isoformat() if s.updated_at else None,
            }
            for s in specs
        ]
    }


@router.get("/specs/{spec_hash}", tags=["ratings"], operation_id="get_spec")
async def get_spec_endpoint(spec_hash: str):
    """Get a spec by its content hash, including the full YAML."""
    async with get_session() as db:
        spec = await get_spec_by_hash(db, spec_hash)

    if not spec:
        raise HTTPException(404, "Spec not found")

    return {
        "id": str(spec.id),
        "hash": spec.hash,
        "label": spec.label,
        "yaml": spec.yaml,
        "rating": spec.rating,
        "num_comparisons": spec.num_comparisons,
        "created_at": spec.created_at.isoformat(),
        "updated_at": spec.updated_at.isoformat(),
    }
