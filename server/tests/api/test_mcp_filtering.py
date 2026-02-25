"""Tests for MCP tool filtering between /mcp/ and /mcp/exec/ endpoints."""

import pytest
from fastapi import FastAPI
from fastmcp import FastMCP
from fastmcp.server.openapi import MCPType, RouteMap
from orpheus.api import routes


class TestMCPToolTagging:
    """Tests that routes are correctly tagged for MCP filtering."""

    def test_execution_tools_have_mcp_tag(self):
        """Verify execution-level routes are tagged with 'mcp'."""
        # Build a simple FastAPI app with the routes
        temp_app = FastAPI()
        temp_app.include_router(routes.router)

        # Check that execution tools have the 'mcp' tag
        execution_tool_paths = [
            "/messages/send",
            "/spawn",
            "/programs",
            "/agents/stop",
            "/agents/ssh",
        ]

        for route in temp_app.routes:
            if hasattr(route, "path") and route.path in execution_tool_paths:
                assert "mcp" in route.tags, f"Route {route.path} should have 'mcp' tag"

    def test_driver_tools_have_mcp_driver_tag(self):
        """Verify driver-level routes are tagged with 'mcp-driver'."""
        temp_app = FastAPI()
        temp_app.include_router(routes.router)

        # Check that driver tools have the 'mcp-driver' tag
        driver_tool_paths = [
            "/tasks",
            "/tasks/{slug}",
            "/executions/{slug}/diff",
        ]

        for route in temp_app.routes:
            if hasattr(route, "path") and route.path in driver_tool_paths:
                assert "mcp-driver" in route.tags, f"Route {route.path} should have 'mcp-driver' tag"

    def test_send_message_available_to_agents(self):
        """Verify send_message is an execution tool (available to agents and driver via 'driver' sender)."""
        temp_app = FastAPI()
        temp_app.include_router(routes.router)

        send_route = None
        for route in temp_app.routes:
            if hasattr(route, "path") and route.path == "/messages/send":
                send_route = route
                break

        assert send_route is not None, "send_message endpoint should exist"
        assert "mcp" in send_route.tags, "send_message should have 'mcp' tag"

    @pytest.mark.asyncio
    async def test_exec_mcp_server_has_fewer_tools(self):
        """Verify exec MCP server exposes fewer tools than full MCP server."""
        temp_app = FastAPI()
        temp_app.include_router(routes.router)

        # Create exec MCP server (execution tools only)
        exec_route_maps = [
            RouteMap(tags={"mcp"}, mcp_type=MCPType.TOOL),
            RouteMap(mcp_type=MCPType.EXCLUDE),
        ]
        exec_mcp_server = FastMCP.from_fastapi(
            app=temp_app,
            route_maps=exec_route_maps,
            httpx_client_kwargs={"base_url": "http://localhost:8000"},
        )

        # Create full MCP server (execution + driver tools)
        full_route_maps = [
            RouteMap(tags={"mcp"}, mcp_type=MCPType.TOOL),
            RouteMap(tags={"mcp-driver"}, mcp_type=MCPType.TOOL),
            RouteMap(mcp_type=MCPType.EXCLUDE),
        ]
        full_mcp_server = FastMCP.from_fastapi(
            app=temp_app,
            route_maps=full_route_maps,
            httpx_client_kwargs={"base_url": "http://localhost:8000"},
        )

        # Get tools (returns a dict of tool_name -> Tool)
        exec_tools = await exec_mcp_server.get_tools()
        full_tools = await full_mcp_server.get_tools()

        exec_tool_names = set(exec_tools.keys())
        full_tool_names = set(full_tools.keys())

        # Verify exec tools are a subset of full tools
        assert exec_tool_names.issubset(full_tool_names), "Exec tools should be a subset of full tools"

        # Verify full has more tools
        assert len(full_tool_names) > len(exec_tool_names), (
            f"Full MCP should have more tools ({len(full_tool_names)}) than exec MCP ({len(exec_tool_names)})"
        )

        # Verify specific tools
        # Execution tools should be in both
        assert "send_message" in exec_tool_names
        assert "send_message" in full_tool_names

        # Driver tools should only be in full
        create_task_tools = [k for k in full_tool_names if "create_task" in k]
        assert len(create_task_tools) > 0, "create_task should be in full tools"
        for tool_name in create_task_tools:
            assert tool_name not in exec_tool_names, f"{tool_name} should NOT be in exec tools"


class TestMCPUrlUpdate:
    """Tests that create_task_endpoint uses the correct MCP URL."""

    def test_launch_execution_uses_exec_mcp_url(self):
        """Verify that launch_execution builds the mcp_url with /mcp/exec/."""
        import inspect

        from orpheus.api.launch import launch_execution

        source = inspect.getsource(launch_execution)
        assert "/mcp/exec/" in source, "launch_execution should use /mcp/exec/ URL"
