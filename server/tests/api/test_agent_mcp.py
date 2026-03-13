"""Tests for the per-agent MCP endpoint."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from druids_server.api.deps import CallerIdentity, get_caller, get_executions_registry
from druids_server.api.routes import router
from druids_server.db.models.user import User
from druids_server.lib.tools import BUILTIN_TOOL_SCHEMAS
from fastapi import FastAPI
from fastapi.testclient import TestClient


SLUG = "test-exec"


@pytest.fixture
def mock_user():
    return User(id=uuid4(), github_id=12345, access_token="test_token")


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


@pytest.fixture
def client(mock_user, mock_execution):
    app = FastAPI()
    app.include_router(router)

    registry = get_executions_registry()
    registry.clear()
    user_id = str(mock_user.id)
    registry[user_id] = {mock_execution.slug: mock_execution}
    app.dependency_overrides[get_caller] = lambda: CallerIdentity(user=mock_user)

    yield TestClient(app)

    app.dependency_overrides.clear()
    registry.clear()


class TestMCPInitialize:
    def test_initialize(self, client):
        resp = client.post(
            f"/executions/{SLUG}/agents/builder/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["jsonrpc"] == "2.0"
        assert data["id"] == 1
        result = data["result"]
        assert "protocolVersion" in result
        assert result["capabilities"]["tools"] == {}
        assert result["serverInfo"]["name"] == "druids"


class TestMCPToolsList:
    def test_tools_list(self, client, mock_execution):
        resp = client.post(
            f"/executions/{SLUG}/agents/builder/mcp",
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
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

        # Each tool has name, description, inputSchema
        for tool in tools:
            assert "name" in tool
            assert "description" in tool
            assert "inputSchema" in tool
            assert tool["inputSchema"]["type"] == "object"


class TestMCPToolsCall:
    def test_tools_call_success(self, client, mock_execution):
        resp = client.post(
            f"/executions/{SLUG}/agents/builder/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "submit_for_review", "arguments": {"summary": "All done."}},
            },
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
            f"/executions/{SLUG}/agents/builder/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "nonexistent", "arguments": {}},
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        result = data["result"]
        assert result["isError"] is True
        assert "Error" in result["content"][0]["text"]


class TestMCPNotification:
    def test_notification_returns_202(self, client):
        resp = client.post(
            f"/executions/{SLUG}/agents/builder/mcp",
            json={"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
        )
        assert resp.status_code == 202


class TestMCPUnknownMethod:
    def test_unknown_method(self, client):
        resp = client.post(
            f"/executions/{SLUG}/agents/builder/mcp",
            json={"jsonrpc": "2.0", "id": 5, "method": "unknown/method", "params": {}},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "error" in data
        assert data["error"]["code"] == -32601


class TestMCPNotFound:
    def test_execution_not_found(self, client):
        resp = client.post(
            "/executions/nonexistent/agents/builder/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        )
        assert resp.status_code == 404

    def test_agent_not_found(self, client):
        resp = client.post(
            f"/executions/{SLUG}/agents/nonexistent/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        )
        assert resp.status_code == 404


class TestMCPAgentAuth:
    """Agent authorization: agents can only access their own MCP endpoint."""

    @pytest.fixture
    def client_with_caller(self, mock_user, mock_execution):
        """Client factory that injects a specific CallerIdentity."""

        def _make(identity: CallerIdentity):
            app = FastAPI()
            app.include_router(router)

            registry = get_executions_registry()
            registry.clear()
            user_id = str(mock_user.id)
            registry[user_id] = {mock_execution.slug: mock_execution}
            app.dependency_overrides[get_caller] = lambda: identity
            return TestClient(app), app

        yield _make

    def test_same_agent_allowed(self, mock_user, client_with_caller):
        """Agent accessing its own endpoint succeeds."""
        tc, app = client_with_caller(
            CallerIdentity(
                user=mock_user,
                scope="agent",
                execution_slug=SLUG,
                agent_name="builder",
            )
        )
        resp = tc.post(
            f"/executions/{SLUG}/agents/builder/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        )
        assert resp.status_code == 200
        app.dependency_overrides.clear()

    def test_different_agent_rejected(self, mock_user, client_with_caller):
        """Agent A cannot access agent B's MCP endpoint."""
        tc, app = client_with_caller(
            CallerIdentity(
                user=mock_user,
                scope="agent",
                execution_slug=SLUG,
                agent_name="attacker",
            )
        )
        resp = tc.post(
            f"/executions/{SLUG}/agents/builder/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        )
        assert resp.status_code == 403
        app.dependency_overrides.clear()

    def test_different_execution_rejected(self, mock_user, client_with_caller):
        """Agent from execution X cannot access MCP endpoint in execution Y."""
        tc, app = client_with_caller(
            CallerIdentity(
                user=mock_user,
                scope="agent",
                execution_slug="other-exec",
                agent_name="builder",
            )
        )
        resp = tc.post(
            f"/executions/{SLUG}/agents/builder/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        )
        assert resp.status_code == 403
        app.dependency_overrides.clear()

    def test_driver_caller_allowed(self, client):
        """Non-agent caller (driver) can access any MCP endpoint."""
        resp = client.post(
            f"/executions/{SLUG}/agents/builder/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        )
        assert resp.status_code == 200


class TestMCPMalformedInput:
    """Malformed JSON and empty body return proper JSON-RPC errors."""

    def test_malformed_json(self, client):
        """Garbage JSON body returns parse error, not 500."""
        resp = client.post(
            f"/executions/{SLUG}/agents/builder/mcp",
            content=b"not valid json{{{",
            headers={"content-type": "application/json"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["error"]["code"] == -32700
        assert "Parse error" in data["error"]["message"]

    def test_empty_body(self, client):
        """Empty POST body returns parse error, not crash."""
        resp = client.post(
            f"/executions/{SLUG}/agents/builder/mcp",
            content=b"",
            headers={"content-type": "application/json"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "error" in data

    def test_json_array_body(self, client):
        """JSON array (not object) returns invalid request error."""
        resp = client.post(
            f"/executions/{SLUG}/agents/builder/mcp",
            json=[1, 2, 3],
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["error"]["code"] == -32600
        assert "expected JSON object" in data["error"]["message"]
