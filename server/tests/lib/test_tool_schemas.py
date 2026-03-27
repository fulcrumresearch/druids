"""Tests for tool schema extraction and the druids MCP tools integration."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4


import pytest
from druids_server.lib.acp import ACPConfig
from druids_server.lib.agents import create_agent
from druids_server.lib.agents.base import Agent
from druids_server.lib.agents.config import AgentConfig
from druids_server.lib.execution import Execution
from druids_server.lib.program_dispatch import extract_agent_tool_schemas, extract_tool_schema
from druids_server.lib.tools import BUILTIN_TOOL_SCHEMAS


# ---------------------------------------------------------------------------
# Schema extraction tests
# ---------------------------------------------------------------------------


class TestExtractToolSchema:
    def test_extracts_name_and_description(self):
        async def handler(summary: str = ""):
            """Submit for review. Include what you changed."""

        schema = extract_tool_schema("submit_for_review", handler)
        assert schema["name"] == "submit_for_review"
        assert "Submit for review" in schema["description"]

    def test_extracts_string_param(self):
        def handler(summary: str = ""):
            """Do something."""

        schema = extract_tool_schema("test", handler)
        props = schema["inputSchema"]["properties"]
        assert "summary" in props
        assert props["summary"]["type"] == "string"

    def test_extracts_int_param(self):
        def handler(count: int = 0):
            """Do something."""

        schema = extract_tool_schema("test", handler)
        props = schema["inputSchema"]["properties"]
        assert props["count"]["type"] == "integer"

    def test_required_params(self):
        def handler(name: str, optional: str = "default"):
            """Do something."""

        schema = extract_tool_schema("test", handler)
        assert "name" in schema["inputSchema"]["required"]
        assert "optional" not in schema["inputSchema"]["required"]

    def test_no_params(self):
        def handler():
            """List things."""

        schema = extract_tool_schema("list", handler)
        assert schema["inputSchema"]["properties"] == {}
        assert schema["inputSchema"]["required"] == []

    def test_no_docstring(self):
        def handler(x: str = ""):
            pass

        schema = extract_tool_schema("test", handler)
        assert schema["description"] == ""

    def test_unannotated_param_defaults_to_string(self):
        def handler(value=""):
            """Do something."""

        schema = extract_tool_schema("test", handler)
        props = schema["inputSchema"]["properties"]
        assert props["value"]["type"] == "string"


class TestExtractAgentToolSchemas:
    def test_extracts_all_handlers(self):
        # Use handlers dict directly (Agent.on() populates _handlers)
        handlers = {}

        def on_commit(message: str = ""):
            """Commit staged changes."""

        def on_surface(title: str = "", body: str = ""):
            """Surface a decision."""

        handlers["commit"] = on_commit
        handlers["surface"] = on_surface

        schemas = extract_agent_tool_schemas(handlers)
        assert len(schemas) == 2
        names = {s["name"] for s in schemas}
        assert names == {"commit", "surface"}


# ---------------------------------------------------------------------------
# Built-in tool schemas
# ---------------------------------------------------------------------------


class TestBuiltinToolSchemas:
    def test_builtin_schemas_defined(self):
        names = {s["name"] for s in BUILTIN_TOOL_SCHEMAS}
        assert names == {"expose", "message", "list_agents", "send_file", "download_file"}

    def test_expose_schema(self):
        schema = next(s for s in BUILTIN_TOOL_SCHEMAS if s["name"] == "expose")
        assert "port" in schema["inputSchema"]["properties"]
        assert "service_name" in schema["inputSchema"]["properties"]
        assert "service_name" in schema["inputSchema"]["required"]
        assert "port" in schema["inputSchema"]["required"]

    def test_message_schema(self):
        schema = next(s for s in BUILTIN_TOOL_SCHEMAS if s["name"] == "message")
        assert "receiver" in schema["inputSchema"]["properties"]
        assert "message" in schema["inputSchema"]["properties"]
        assert set(schema["inputSchema"]["required"]) == {"receiver", "message"}

    def test_list_agents_schema(self):
        schema = next(s for s in BUILTIN_TOOL_SCHEMAS if s["name"] == "list_agents")
        assert schema["inputSchema"]["properties"] == {}


# ---------------------------------------------------------------------------
# Execution.list_tool_schemas
# ---------------------------------------------------------------------------


def _make_execution(**kwargs) -> Execution:
    defaults = {
        "id": uuid4(),
        "slug": "test-slug",
        "user_id": "user-1",
    }
    defaults.update(kwargs)
    return Execution(**defaults)


def _make_mock_agent(name: str) -> Agent:
    """Create a mock Agent with the minimum fields for testing."""
    config = AgentConfig(name=name)
    machine = MagicMock()
    machine.instance_id = "inst_1"
    conn = MagicMock()
    conn.prompt_nowait = AsyncMock()
    conn.close = AsyncMock()
    return Agent(
        config=config,
        machine=machine,
        bridge_id="inst_1:7462",
        bridge_token="tok",
        session_id="sess-1",
        connection=conn,
    )


class TestListToolSchemas:
    @pytest.mark.asyncio
    async def test_returns_builtins_when_no_handlers(self):
        ex = _make_execution()
        schemas = await ex.list_tool_schemas("builder")
        names = {s["name"] for s in schemas}
        assert {"expose", "message", "list_agents"} <= names

    @pytest.mark.asyncio
    async def test_includes_program_defined_schemas(self):
        ex = _make_execution()
        agent = _make_mock_agent("builder")
        ex.agents["builder"] = agent

        @agent.on("submit_for_review")
        def on_submit(summary: str = ""):
            """Submit for review."""

        schemas = await ex.list_tool_schemas("builder")
        names = {s["name"] for s in schemas}
        assert "submit_for_review" in names
        assert "expose" in names


# ---------------------------------------------------------------------------
# _create_acp_session includes druids MCP server
# ---------------------------------------------------------------------------


def _make_config(name: str = "test-agent", system_prompt: str | None = None) -> AgentConfig:
    return AgentConfig(name=name, system_prompt=system_prompt)


def _mock_conn() -> MagicMock:
    conn = MagicMock()
    conn.start = AsyncMock()
    conn.new_session = AsyncMock(return_value="sess-1")
    conn.session_id = "sess-1"
    conn.on = MagicMock()
    conn.prompt = AsyncMock()
    conn.prompt_nowait = AsyncMock()
    conn.set_model = AsyncMock()
    return conn


class TestCreateSessionDruidsMCP:
    @pytest.mark.asyncio
    async def test_druids_mcp_server_included(self):
        """_create_acp_session includes the druids MCP server in mcp_servers."""
        config = _make_config()
        acp = ACPConfig(env={"DRUIDS_ACCESS_TOKEN": "test-token"})

        mock = _mock_conn()
        await Agent._create_acp_session(config, acp, "test-slug", mock)

        mock.new_session.assert_called_once()
        call_kwargs = mock.new_session.call_args[1]
        mcp_servers = call_kwargs["mcp_servers"]
        assert mcp_servers is not None
        assert len(mcp_servers) >= 1

        druids_server = next(s for s in mcp_servers if s["name"] == "druids")
        assert "/amcp/" in druids_server["url"]
        assert "Authorization" in druids_server["headers"]
        assert "Bearer test-token" in druids_server["headers"]["Authorization"]

    @pytest.mark.asyncio
    async def test_druids_mcp_plus_program_mcp(self):
        """_create_acp_session includes both druids and program-provided MCP servers."""
        config = _make_config()
        acp = ACPConfig(
            env={"DRUIDS_ACCESS_TOKEN": "test-token"},
            mcp_servers={"slack": {"url": "https://slack.mcp/sse", "headers": {"Authorization": "Bearer xoxb"}}},
        )

        mock = _mock_conn()
        await Agent._create_acp_session(config, acp, "test-slug", mock)

        call_kwargs = mock.new_session.call_args[1]
        mcp_servers = call_kwargs["mcp_servers"]
        names = [s["name"] for s in mcp_servers]
        assert "druids" in names
        assert "slack" in names


# ---------------------------------------------------------------------------
# Preamble removal: system prompt should NOT include druids tool preamble
# ---------------------------------------------------------------------------


class TestPreambleRemoved:
    def test_system_prompt_not_prepended(self):
        """create_agent() does not prepend DRUIDS_TOOLS_PREAMBLE to system_prompt."""
        spec = create_agent(
            "root",
            system_prompt="Be thorough.",
            slug="test-slug",
            user_id="user-1",
        )

        assert spec.system_prompt == "Be thorough."
        assert "druids tool" not in spec.system_prompt
        assert "druids tools" not in spec.system_prompt

    def test_no_system_prompt_stays_none(self):
        """create_agent() does not set system_prompt when none is provided."""
        spec = create_agent(
            "root",
            slug="test-slug",
            user_id="user-1",
        )

        assert spec.system_prompt is None
