"""Tests for AgentConnection: system prompt and model selection."""

from unittest.mock import AsyncMock, patch

import pytest
from druids_server.lib.connection import AgentConnection


@pytest.fixture
def conn():
    """Create an AgentConnection with mocked transport."""
    with patch.object(AgentConnection, "__post_init__"):
        c = AgentConnection.__new__(AgentConnection)
        c.bridge_id = "bridge-1"
        c.bridge_token = "token-1"
        c.session_id = ""
        c.connection = AsyncMock()
        c.connection.send_request = AsyncMock(return_value={"sessionId": "sess-1"})
        c._handlers = {}
        return c


class TestSetModel:
    @pytest.mark.asyncio
    async def test_sends_session_set_model(self, conn):
        """set_model sends session/set_model RPC with session and model IDs."""
        conn.session_id = "sess-1"
        await conn.set_model("claude-opus-4-6")

        conn.connection.send_request.assert_called_once_with(
            "session/set_model",
            {"sessionId": "sess-1", "modelId": "claude-opus-4-6"},
        )

    @pytest.mark.asyncio
    async def test_raises_without_session(self, conn):
        """set_model raises RuntimeError if called before new_session."""
        conn.session_id = ""
        with pytest.raises(RuntimeError, match="Cannot set model before session"):
            await conn.set_model("claude-opus-4-6")


class TestNewSessionSystemPrompt:
    @pytest.mark.asyncio
    async def test_meta_set_with_append_when_system_prompt_provided(self, conn):
        """_meta.systemPrompt is set in append mode when system_prompt is given."""
        await conn.new_session(cwd="/workspace", system_prompt="Be concise.")

        call_args = conn.connection.send_request.call_args
        method, params = call_args[0]
        assert method == "session/new"
        assert params["_meta"] == {"systemPrompt": {"append": "Be concise."}}

    @pytest.mark.asyncio
    async def test_meta_absent_when_no_system_prompt(self, conn):
        """_meta is None when system_prompt is not provided."""
        await conn.new_session(cwd="/workspace")

        call_args = conn.connection.send_request.call_args
        method, params = call_args[0]
        assert method == "session/new"
        assert params.get("_meta") is None

    @pytest.mark.asyncio
    async def test_meta_absent_when_system_prompt_is_empty(self, conn):
        """_meta is None when system_prompt is empty string."""
        await conn.new_session(cwd="/workspace", system_prompt="")

        call_args = conn.connection.send_request.call_args
        method, params = call_args[0]
        assert params.get("_meta") is None

    @pytest.mark.asyncio
    async def test_session_id_returned(self, conn):
        """Session ID is still extracted from response."""
        result = await conn.new_session(cwd="/workspace", system_prompt="test")
        assert result == "sess-1"
        assert conn.session_id == "sess-1"
