"""Health check endpoint."""

from __future__ import annotations

from fastapi import APIRouter


router = APIRouter()


@router.get("/health", tags=["health"], operation_id="health_check")
async def health_check():
    """Health check endpoint that returns 200 OK."""
    return {"status": "ok"}
