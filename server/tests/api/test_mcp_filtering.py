"""Tests for MCP tool filtering -- driver-only MCP server."""

import pytest
from druids_server.api import routes
from fastmcp import FastMCP
from fastmcp.server.openapi import MCPType, RouteMap

from tests.api.conftest import make_api_app


class TestMCPToolTagging:
    """Tests that routes are correctly tagged for MCP filtering."""

    def test_driver_tools_have_mcp_driver_tag(self):
        """Verify driver-level routes are tagged with 'mcp-driver'."""
        temp_app = make_api_app()

        driver_tool_paths = [
            "/messages/send",
            "/agents/stop",
            "/agents/ssh",
            "/executions/{slug}/diff",
        ]

        for route in temp_app.routes:
            if hasattr(route, "path") and route.path in driver_tool_paths:
                assert "mcp-driver" in route.tags, f"Route {route.path} should have 'mcp-driver' tag"

    @pytest.mark.asyncio
    async def test_mcp_server_exposes_driver_tools(self):
        """Verify MCP server exposes driver-tagged tools."""
        temp_app = make_api_app()

        route_maps = [
            RouteMap(tags={"mcp-driver"}, mcp_type=MCPType.TOOL),
            RouteMap(mcp_type=MCPType.EXCLUDE),
        ]
        mcp_server = FastMCP.from_fastapi(
            app=temp_app,
            route_maps=route_maps,
            httpx_client_kwargs={"base_url": "http://localhost:8000"},
        )

        tools = await mcp_server.get_tools()
        tool_names = set(tools.keys())

        assert "send_message" in tool_names
        assert "stop_agent" in tool_names
        assert "get_agent_ssh" in tool_names

    @pytest.mark.asyncio
    async def test_no_agent_visible_mcp_tools(self):
        """Verify there are no 'mcp'-only tagged routes (agent-visible tools were removed)."""
        temp_app = make_api_app()

        # An MCP server that only picks up 'mcp' (not 'mcp-driver') should get nothing
        route_maps = [
            RouteMap(tags={"mcp"}, mcp_type=MCPType.TOOL),
            RouteMap(mcp_type=MCPType.EXCLUDE),
        ]
        mcp_server = FastMCP.from_fastapi(
            app=temp_app,
            route_maps=route_maps,
            httpx_client_kwargs={"base_url": "http://localhost:8000"},
        )

        tools = await mcp_server.get_tools()
        assert len(tools) == 0, f"No routes should have bare 'mcp' tag, but found: {list(tools.keys())}"
