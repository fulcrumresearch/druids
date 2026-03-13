"""FastAPI application."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastmcp import FastMCP
from fastmcp.server.openapi import MCPType, RouteMap

from druids_server.api import routes
from druids_server.api.deps import get_executions_registry
from druids_server.config import settings
from druids_server.db.session import init_db
from druids_server.paths import DASHBOARD_DIST


logger = logging.getLogger(__name__)


SHUTDOWN_TIMEOUT_SECONDS: int = 30
LOG_DIR = Path(__file__).parent.parent / "logs"
LOG_FILE = LOG_DIR / "druids.log"


def _configure_logging() -> None:
    """Set up logging to both stdout and an append-only file."""
    fmt = "%(asctime)s %(name)s %(levelname)s %(message)s"
    logging.basicConfig(level=logging.INFO, format=fmt)

    LOG_DIR.mkdir(exist_ok=True)
    file_handler = logging.FileHandler(LOG_FILE)
    file_handler.setFormatter(logging.Formatter(fmt))
    file_handler.setLevel(logging.INFO)
    logging.getLogger().addHandler(file_handler)


async def stop_all_executions() -> None:
    """Stop all active executions, used during server shutdown."""
    registry = get_executions_registry()
    all_executions = [ex for user_execs in registry.values() for ex in user_execs.values()]
    if not all_executions:
        return

    logger.info("Stopping %d active execution(s)", len(all_executions))
    results = await asyncio.gather(
        *[asyncio.wait_for(ex.stop("server_shutdown"), timeout=SHUTDOWN_TIMEOUT_SECONDS) for ex in all_executions],
        return_exceptions=True,
    )
    failures = sum(1 for r in results if isinstance(r, Exception))
    if failures:
        logger.warning("%d execution(s) failed to stop cleanly", failures)


def create_app() -> FastAPI:
    """Create the FastAPI application."""
    _configure_logging()
    logger.info("Druids server starting (host=%s, port=%d)", settings.host, settings.port)

    # Build MCP from routes (driver-only, for CLI and external agents)
    temp_app = FastAPI()
    temp_app.include_router(routes.router, prefix="/api")

    mcp_route_maps = [
        RouteMap(tags={"mcp-driver"}, mcp_type=MCPType.TOOL),
        RouteMap(mcp_type=MCPType.EXCLUDE),
    ]

    mcp_server = FastMCP.from_fastapi(
        app=temp_app,
        route_maps=mcp_route_maps,
        httpx_client_kwargs={"base_url": f"http://localhost:{settings.port}"},
    )
    mcp_app = mcp_server.http_app(path="/", transport="streamable-http", stateless_http=True, json_response=True)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await init_db()

        async with mcp_app.lifespan(app):
            yield

        await stop_all_executions()

    app = FastAPI(
        title="Druids",
        description="Agent orchestration",
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(routes.router, prefix="/api")
    app.mount("/mcp", mcp_app)

    # Serve dashboard static files
    dashboard_dist = DASHBOARD_DIST
    if dashboard_dist.is_dir():
        app.mount("/assets", StaticFiles(directory=dashboard_dist / "assets"), name="dashboard-assets")
        index_html = dashboard_dist / "index.html"

        @app.get("/{path:path}")
        async def serve_dashboard(path: str):
            """Serve the dashboard SPA for any unmatched GET path."""
            if path.startswith("api/") or path.startswith("mcp/"):
                raise HTTPException(404, "Not found")
            file_path = dashboard_dist / path
            if path and file_path.is_file():
                return FileResponse(file_path)
            return FileResponse(index_html)

    return app


app = create_app()


def _build_client_wheel() -> None:
    """Build the client wheel so VMs get the latest CLI code.

    When running from a monorepo checkout, builds from ../client.
    When pip-installed, the wheel is already bundled -- this is a no-op.
    """
    import subprocess

    from druids_server.paths import CLIENT_WHEEL_DIR, ROOT_DIR

    # If we already have a bundled wheel, skip building
    if list(CLIENT_WHEEL_DIR.glob("druids-*.whl")):
        return

    client_dir = ROOT_DIR / "client"
    if not (client_dir / "pyproject.toml").exists():
        return
    logger.info("Building client wheel from %s", client_dir)
    subprocess.run(["uv", "build"], cwd=client_dir, check=True, capture_output=True)
    logger.info("Client wheel built")


def main():
    """Run the server."""
    _build_client_wheel()
    uvicorn.run(app, host=settings.host, port=settings.port, log_config=None)


if __name__ == "__main__":
    main()
