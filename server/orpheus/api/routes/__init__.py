"""Combined router for all API routes."""

from fastapi import APIRouter

from orpheus.api.routes import (
    admin,
    auth,
    billing,
    bridge,
    executions,
    files,
    mcp,
    oauth,
    programs,
    proxy,
    ratings,
    tasks,
    webhooks,
)


router = APIRouter()

router.include_router(admin.router)
router.include_router(auth.router)
router.include_router(billing.router)
router.include_router(bridge.router)
router.include_router(tasks.router)
router.include_router(executions.router)
router.include_router(files.router)
router.include_router(mcp.router)
router.include_router(oauth.router)
router.include_router(proxy.router)
router.include_router(programs.router)
router.include_router(ratings.router)
router.include_router(webhooks.router)

__all__ = ["router"]
