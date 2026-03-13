"""Combined router for all API routes."""

from fastapi import APIRouter

from druids_server.api.routes import (
    agent_mcp,
    bridge,
    executions,
    mcp,
    me,
    runtime,
    secrets,
    setup,
)


router = APIRouter()

router.include_router(me.router)
router.include_router(agent_mcp.router)
router.include_router(bridge.router)
router.include_router(secrets.router)
router.include_router(setup.router)
router.include_router(executions.router)
router.include_router(runtime.router)
router.include_router(mcp.router)

__all__ = ["router"]
