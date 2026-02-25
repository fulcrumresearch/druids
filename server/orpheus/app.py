"""FastAPI application."""

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

from orpheus.api import routes
from orpheus.api.deps import get_executions_registry
from orpheus.config import settings
from orpheus.db.session import init_db


logger = logging.getLogger(__name__)


SHUTDOWN_TIMEOUT_SECONDS: int = 30


async def stop_all_executions() -> None:
    """Stop all active executions, used during server shutdown."""
    registry = get_executions_registry()
    all_executions = [ex for user_execs in registry.values() for ex in user_execs.values()]
    if not all_executions:
        return

    logger.info(f"stopping {len(all_executions)} active execution(s)")
    results = await asyncio.gather(
        *[asyncio.wait_for(ex.stop("server_shutdown"), timeout=SHUTDOWN_TIMEOUT_SECONDS) for ex in all_executions],
        return_exceptions=True,
    )
    failures = sum(1 for r in results if isinstance(r, Exception))
    if failures:
        logger.warning(f"{failures} execution(s) failed to stop cleanly")


def create_app() -> FastAPI:
    """Create the FastAPI application."""
    # Build MCP from routes
    temp_app = FastAPI()
    temp_app.include_router(routes.router, prefix="/api")

    # Execution-level tools only (for spawned agents)
    exec_route_maps = [
        RouteMap(tags={"mcp"}, mcp_type=MCPType.TOOL),
        RouteMap(mcp_type=MCPType.EXCLUDE),
    ]

    # Note: MCP routes still need auth - the agent's auth token is passed via
    # mcp_auth_token in Execution, which is included in the MCP server headers.
    # FastMCP will forward these headers when proxying to the underlying HTTP endpoints.
    exec_mcp_server = FastMCP.from_fastapi(
        app=temp_app,
        route_maps=exec_route_maps,
        httpx_client_kwargs={"base_url": f"http://localhost:{settings.port}"},
    )
    exec_mcp_app = exec_mcp_server.http_app(
        path="/", transport="streamable-http", stateless_http=True, json_response=True
    )

    # All tools: execution + driver (for external agents)
    full_route_maps = [
        RouteMap(tags={"mcp"}, mcp_type=MCPType.TOOL),
        RouteMap(tags={"mcp-driver"}, mcp_type=MCPType.TOOL),
        RouteMap(mcp_type=MCPType.EXCLUDE),
    ]

    full_mcp_server = FastMCP.from_fastapi(
        app=temp_app,
        route_maps=full_route_maps,
        httpx_client_kwargs={"base_url": f"http://localhost:{settings.port}"},
    )
    full_mcp_app = full_mcp_server.http_app(
        path="/", transport="streamable-http", stateless_http=True, json_response=True
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await init_db()

        # Need to manage lifespan for both MCP apps
        async with exec_mcp_app.lifespan(app):
            async with full_mcp_app.lifespan(app):
                yield

        await stop_all_executions()

    app = FastAPI(
        title="Orpheus",
        description="Agent orchestration",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(routes.router, prefix="/api")
    # Mount order: more specific path first
    app.mount("/mcp/exec", exec_mcp_app)
    app.mount("/mcp", full_mcp_app)

    # Serve dashboard static files
    dashboard_dist = Path(__file__).parent.parent.parent / "frontend" / "dist"
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


def main():
    """Run the server."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    uvicorn.run(app, host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
