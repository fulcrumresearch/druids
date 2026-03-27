"""Combined router for all API routes."""

from fastapi import APIRouter

from druids_server.api.routes import (
    bridge,
    executions,
    mcp,
    me,
    programs,
    runtime,
    secrets,
    setup,
)


def create_router() -> APIRouter:
    """Build the combined API router."""
    router = APIRouter()
    router.include_router(me.router)
    router.include_router(bridge.router)
    router.include_router(secrets.router)
    router.include_router(setup.router)
    router.include_router(executions.router)
    router.include_router(programs.router)
    router.include_router(runtime.router)
    router.include_router(mcp.router)
    return router


router = create_router()

__all__ = ["router"]
