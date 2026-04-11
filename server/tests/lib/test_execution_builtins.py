"""Tests for built-in tool handlers in Execution (send_file, download_file)."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from druids_server.lib.agents.config import AgentConfig
from druids_server.lib.agents.base import Agent
from druids_server.lib.execution import Execution


def _make_execution(**kwargs) -> Execution:
    defaults = {
        "id": uuid4(),
        "slug": "test-slug",
        "user_id": "user-1",
    }
    defaults.update(kwargs)
    return Execution(**defaults)


def _make_mock_agent(name: str, instance_id: str = "inst_1") -> Agent:
    """Create a mock Agent with sandbox-backed machine."""
    config = AgentConfig(name=name)
    sandbox = MagicMock()
    sandbox.read_file = AsyncMock(return_value=b"file contents")
    sandbox.write_file = AsyncMock()
    machine = MagicMock()
    machine.instance_id = instance_id
    machine.sandbox = sandbox
    conn = MagicMock()
    conn.close = AsyncMock()
    return Agent(
        config=config,
        machine=machine,
        bridge_id=f"{instance_id}:7462",
        bridge_token="tok",
        session_id="sess-1",
        connection=conn,
    )


class TestSendFile:
    @pytest.mark.asyncio
    async def test_send_file_success(self):
        """send_file reads from sender sandbox and writes to receiver sandbox."""
        ex = _make_execution()
        sender = _make_mock_agent("alice", "inst_a")
        receiver = _make_mock_agent("bob", "inst_b")
        ex.agents["alice"] = sender
        ex.agents["bob"] = receiver
        ex.connect("alice", "bob")

        result = await ex._handle_send_file("alice", {
            "receiver": "bob",
            "path": "/home/agent/file.txt",
            "dest_path": "/home/agent/file.txt",
        })

        assert "sent to bob" in result.lower()
        sender.machine.sandbox.read_file.assert_called_once_with("/home/agent/file.txt")
        receiver.machine.sandbox.write_file.assert_called_once_with("/home/agent/file.txt", b"file contents")

    @pytest.mark.asyncio
    async def test_send_file_default_dest_path(self):
        """send_file uses source path as dest_path when dest_path is not provided."""
        ex = _make_execution()
        sender = _make_mock_agent("alice", "inst_a")
        receiver = _make_mock_agent("bob", "inst_b")
        ex.agents["alice"] = sender
        ex.agents["bob"] = receiver
        ex.connect("alice", "bob")

        result = await ex._handle_send_file("alice", {
            "receiver": "bob",
            "path": "/home/agent/data.csv",
        })

        assert "sent to bob" in result.lower()
        receiver.machine.sandbox.write_file.assert_called_once_with("/home/agent/data.csv", b"file contents")

    @pytest.mark.asyncio
    async def test_send_file_missing_receiver(self):
        """send_file returns error when receiver is missing."""
        ex = _make_execution()
        result = await ex._handle_send_file("alice", {"path": "/home/agent/file.txt"})
        assert "Error" in result
        assert "receiver" in result

    @pytest.mark.asyncio
    async def test_send_file_missing_path(self):
        """send_file returns error when path is missing."""
        ex = _make_execution()
        result = await ex._handle_send_file("alice", {"receiver": "bob"})
        assert "Error" in result
        assert "path" in result

    @pytest.mark.asyncio
    async def test_send_file_not_connected(self):
        """send_file returns error when agents are not connected."""
        ex = _make_execution()
        sender = _make_mock_agent("alice", "inst_a")
        receiver = _make_mock_agent("bob", "inst_b")
        ex.agents["alice"] = sender
        ex.agents["bob"] = receiver
        # No connect() call

        result = await ex._handle_send_file("alice", {
            "receiver": "bob",
            "path": "/home/agent/file.txt",
        })

        assert "Error" in result

    @pytest.mark.asyncio
    async def test_send_file_unknown_receiver(self):
        """send_file returns error when receiver agent doesn't exist."""
        ex = _make_execution()
        sender = _make_mock_agent("alice", "inst_a")
        ex.agents["alice"] = sender

        result = await ex._handle_send_file("alice", {
            "receiver": "unknown",
            "path": "/home/agent/file.txt",
        })

        assert "Error" in result


class TestDownloadFile:
    @pytest.mark.asyncio
    async def test_download_file_success(self):
        """download_file reads from sender sandbox and writes to requester sandbox."""
        ex = _make_execution()
        requester = _make_mock_agent("alice", "inst_a")
        sender = _make_mock_agent("bob", "inst_b")
        ex.agents["alice"] = requester
        ex.agents["bob"] = sender
        ex.connect("alice", "bob")

        result = await ex._handle_download_file("alice", {
            "sender": "bob",
            "path": "/home/agent/results.json",
            "dest_path": "/home/agent/results.json",
        })

        assert "downloaded from bob" in result.lower()
        sender.machine.sandbox.read_file.assert_called_once_with("/home/agent/results.json")
        requester.machine.sandbox.write_file.assert_called_once_with("/home/agent/results.json", b"file contents")

    @pytest.mark.asyncio
    async def test_download_file_missing_sender(self):
        """download_file returns error when sender is missing."""
        ex = _make_execution()
        result = await ex._handle_download_file("alice", {"path": "/home/agent/file.txt"})
        assert "Error" in result
        assert "sender" in result

    @pytest.mark.asyncio
    async def test_download_file_not_connected(self):
        """download_file returns error when agents are not connected."""
        ex = _make_execution()
        requester = _make_mock_agent("alice", "inst_a")
        sender = _make_mock_agent("bob", "inst_b")
        ex.agents["alice"] = requester
        ex.agents["bob"] = sender
        # No connect() call

        result = await ex._handle_download_file("alice", {
            "sender": "bob",
            "path": "/home/agent/file.txt",
        })

        assert "Error" in result


class TestCallToolDispatch:
    @pytest.mark.asyncio
    async def test_call_tool_routes_send_file(self):
        """call_tool routes 'send_file' to _handle_send_file."""
        ex = _make_execution()
        sender = _make_mock_agent("alice", "inst_a")
        receiver = _make_mock_agent("bob", "inst_b")
        ex.agents["alice"] = sender
        ex.agents["bob"] = receiver
        ex.connect("alice", "bob")

        result = await ex.call_tool("alice", "send_file", {
            "receiver": "bob",
            "path": "/home/agent/file.txt",
        })

        assert "sent to bob" in result.lower()

    @pytest.mark.asyncio
    async def test_call_tool_routes_download_file(self):
        """call_tool routes 'download_file' to _handle_download_file."""
        ex = _make_execution()
        requester = _make_mock_agent("alice", "inst_a")
        sender = _make_mock_agent("bob", "inst_b")
        ex.agents["alice"] = requester
        ex.agents["bob"] = sender
        ex.connect("alice", "bob")

        result = await ex.call_tool("alice", "download_file", {
            "sender": "bob",
            "path": "/home/agent/file.txt",
        })

        assert "downloaded from bob" in result.lower()
