"""Tests for the per-agent MCP endpoint (/amcp/).

The endpoint is a standalone ASGI app (not a FastAPI route). Agent identity
comes from a JWT in the Authorization header, resolved via a contextvar.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from druids_server.api.routes.agent_mcp import agent_mcp_lifespan, create_agent_mcp_app
from druids_server.lib.tools import BUILTIN_TOOL_SCHEMAS
from starlette.applications import Starlette
from starlette.routing import Mount
from starlette.testclient import TestClient


SLUG = "test-exec"
MCP_HEADERS = {"Authorization": "Bearer test-jwt", "Accept": "application/json"}

# Full initialize params required by the MCP SDK.
INIT_PARAMS = {
    "protocolVersion": "2025-03-26",
    "capabilities": {},
    "clientInfo": {"name": "test", "version": "1.0"},
}


@pytest.fixture
def mock_execution():
    ex = MagicMock()
    ex.id = uuid4()
    ex.slug = SLUG
    ex.agents = {"builder": MagicMock()}
    ex.has_agent = MagicMock(side_effect=lambda name: name in ex.agents)
    ex.list_tool_schemas = AsyncMock(
        return_value=list(BUILTIN_TOOL_SCHEMAS)
        + [
            {
                "name": "submit_for_review",
                "description": "Submit for review.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"summary": {"type": "string"}},
                    "required": [],
                },
            },
        ]
    )
    ex.call_tool = AsyncMock(return_value="Tool result text")
    return ex


@asynccontextmanager
async def _test_lifespan(app):
    async with agent_mcp_lifespan():
        yield


def _build_app():
    """Starlette app that manages the MCP session manager lifespan."""
    return Starlette(
        routes=[Mount("/amcp", app=create_agent_mcp_app())],
        lifespan=_test_lifespan,
    )


@pytest.fixture
def client(execution_registry, mock_user, mock_execution):
    """TestClient with a valid JWT that maps to mock_execution / builder."""
    user_id = str(mock_user.id)
    execution_registry[user_id] = {mock_execution.slug: mock_execution}

    caller_info = {"sub": user_id, "execution_slug": SLUG, "agent_name": "builder"}

    with patch("druids_server.api.routes.agent_mcp.validate_token", return_value=caller_info):
        with TestClient(_build_app()) as tc:
            yield tc


# -- Protocol basics ----------------------------------------------------------


class TestMCPInitialize:
    def test_initialize(self, client):
        resp = client.post(
            "/amcp/",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": INIT_PARAMS},
            headers=MCP_HEADERS,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["jsonrpc"] == "2.0"
        assert data["id"] == 1
        result = data["result"]
        assert "protocolVersion" in result
        assert "tools" in result["capabilities"]
        assert result["serverInfo"]["name"] == "druids"


class TestMCPToolsList:
    def test_tools_list(self, client, mock_execution):
        resp = client.post(
            "/amcp/",
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            headers=MCP_HEADERS,
        )
        assert resp.status_code == 200
        data = resp.json()
        result = data["result"]
        tools = result["tools"]
        names = {t["name"] for t in tools}
        assert "expose" in names
        assert "message" in names
        assert "list_agents" in names
        assert "submit_for_review" in names

        for tool in tools:
            assert "name" in tool
            assert "description" in tool
            assert "inputSchema" in tool
            assert tool["inputSchema"]["type"] == "object"


class TestMCPToolsCall:
    def test_tools_call_success(self, client, mock_execution):
        resp = client.post(
            "/amcp/",
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "submit_for_review", "arguments": {"summary": "All done."}},
            },
            headers=MCP_HEADERS,
        )
        assert resp.status_code == 200
        data = resp.json()
        result = data["result"]
        assert result["content"][0]["type"] == "text"
        assert "Tool result text" in result["content"][0]["text"]
        mock_execution.call_tool.assert_called_once_with("builder", "submit_for_review", {"summary": "All done."})

    def test_tools_call_error(self, client, mock_execution):
        mock_execution.call_tool = AsyncMock(side_effect=RuntimeError("No handler"))

        resp = client.post(
            "/amcp/",
            json={
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "nonexistent", "arguments": {}},
            },
            headers=MCP_HEADERS,
        )
        assert resp.status_code == 200
        data = resp.json()
        result = data["result"]
        assert "Error" in result["content"][0]["text"]


class TestMCPNotification:
    def test_notification_returns_202(self, client):
        resp = client.post(
            "/amcp/",
            json={"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
            headers=MCP_HEADERS,
        )
        assert resp.status_code == 202


class TestMCPUnknownMethod:
    def test_unknown_method(self, client):
        resp = client.post(
            "/amcp/",
            json={"jsonrpc": "2.0", "id": 5, "method": "unknown/method", "params": {}},
            headers=MCP_HEADERS,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "error" in data
        assert data["error"]["code"] < 0


# -- Identity / auth ---------------------------------------------------------


class TestMCPNoAuth:
    """Without a valid JWT the caller contextvar is unset -> empty tool list."""

    def test_no_auth_header_returns_empty_tools(self, execution_registry, mock_user, mock_execution):
        user_id = str(mock_user.id)
        execution_registry[user_id] = {mock_execution.slug: mock_execution}

        with TestClient(_build_app()) as tc:
            resp = tc.post(
                "/amcp/",
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
                headers={"Accept": "application/json"},
            )
            assert resp.status_code == 200
            assert resp.json()["result"]["tools"] == []

    def test_invalid_token_returns_empty_tools(self, execution_registry, mock_user, mock_execution):
        user_id = str(mock_user.id)
        execution_registry[user_id] = {mock_execution.slug: mock_execution}

        with patch(
            "druids_server.api.routes.agent_mcp.validate_token",
            side_effect=ValueError("bad token"),
        ):
            with TestClient(_build_app()) as tc:
                resp = tc.post(
                    "/amcp/",
                    json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
                    headers=MCP_HEADERS,
                )
                assert resp.status_code == 200
                assert resp.json()["result"]["tools"] == []

    def test_nonexistent_execution_returns_empty_tools(self, execution_registry, mock_user, mock_execution):
        """JWT references an execution slug not in the registry."""
        user_id = str(mock_user.id)
        execution_registry[user_id] = {mock_execution.slug: mock_execution}

        caller_info = {"sub": user_id, "execution_slug": "no-such-exec", "agent_name": "builder"}

        with patch("druids_server.api.routes.agent_mcp.validate_token", return_value=caller_info):
            with TestClient(_build_app()) as tc:
                resp = tc.post(
                    "/amcp/",
                    json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
                    headers=MCP_HEADERS,
                )
                assert resp.status_code == 200
                assert resp.json()["result"]["tools"] == []


# -- Malformed input ----------------------------------------------------------


class TestMCPMalformedInput:
    """Malformed JSON and empty body return JSON-RPC errors at the HTTP level."""

    def test_malformed_json(self, client):
        resp = client.post(
            "/amcp/",
            content=b"not valid json{{{",
            headers={"Content-Type": "application/json", **MCP_HEADERS},
        )
        assert resp.status_code == 400
        data = resp.json()
        assert data["error"]["code"] == -32700

    def test_empty_body(self, client):
        resp = client.post(
            "/amcp/",
            content=b"",
            headers={"Content-Type": "application/json", **MCP_HEADERS},
        )
        assert resp.status_code == 400
        data = resp.json()
        assert "error" in data

    def test_json_array_body(self, client):
        resp = client.post(
            "/amcp/",
            json=[1, 2, 3],
            headers=MCP_HEADERS,
        )
        assert resp.status_code == 400
        data = resp.json()
        assert "error" in data
