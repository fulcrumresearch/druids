"""Program endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from druids_server.api.deps import Caller, require_driver
from druids_server.db.models.program import get_program, get_user_programs
from druids_server.db.session import get_session


router = APIRouter()


@router.get(
    "/programs",
    tags=["programs", "mcp-driver"],
    operation_id="list_programs",
    dependencies=[Depends(require_driver)],
)
async def list_programs(caller: Caller):
    """List all saved programs for the current user."""
    async with get_session() as db:
        programs = await get_user_programs(db, caller.user.id)

    return {
        "programs": [
            {
                "id": str(p.id),
                "source_hash": p.source_hash,
                "created_at": p.created_at.isoformat() if p.created_at else None,
            }
            for p in programs
        ],
    }


@router.get(
    "/programs/{program_id}",
    tags=["programs", "mcp-driver"],
    operation_id="get_program",
    dependencies=[Depends(require_driver)],
)
async def get_program_endpoint(program_id: str, caller: Caller):
    """Get a program by ID, including its source code."""
    from uuid import UUID

    try:
        pid = UUID(program_id)
    except ValueError:
        raise HTTPException(400, "Invalid program ID")

    async with get_session() as db:
        program = await get_program(db, pid)
        if not program:
            raise HTTPException(404, "Program not found")
        if program.user_id != caller.user.id:
            raise HTTPException(404, "Program not found")

    return {
        "id": str(program.id),
        "source": program.source,
        "source_hash": program.source_hash,
        "created_at": program.created_at.isoformat() if program.created_at else None,
    }
