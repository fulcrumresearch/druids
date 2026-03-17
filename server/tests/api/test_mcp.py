"""Tests for MCP API routes."""

from unittest.mock import AsyncMock, MagicMock

from tests.api.conftest import SLUG


# --- Send Message Tests ---


class TestSendMessage:
    def test_send_message_success(self, client, mock_execution):
        """Sends message when both agents exist."""
        mock_execution.agents = {
            "orchestrator": MagicMock(),
            "worker": MagicMock(),
        }
        mock_execution.has_agent.side_effect = lambda name: name in mock_execution.agents

        response = client.post(
            "/messages/send",
            json={
                "execution_slug": SLUG,
                "sender": "orchestrator",
                "receiver": "worker",
                "message": "hello",
            },
        )

        assert response.status_code == 200
        assert response.json() == {"status": "sent", "recipient": "worker"}
        mock_execution.send.assert_called_once_with("orchestrator", "worker", "hello")

    def test_send_message_execution_not_found(self, client, mock_execution):
        """404 when execution not found."""
        response = client.post(
            "/messages/send",
            json={
                "execution_slug": "bad-id",
                "sender": "orchestrator",
                "receiver": "worker",
                "message": "hello",
            },
        )

        assert response.status_code == 404
        assert "Execution" in response.json()["detail"]

    def test_send_message_sender_not_found(self, client, mock_execution):
        """404 when sender not found."""
        mock_execution.agents = {"worker": MagicMock()}
        mock_execution.has_agent.side_effect = lambda name: name in mock_execution.agents

        response = client.post(
            "/messages/send",
            json={
                "execution_slug": SLUG,
                "sender": "orchestrator",
                "receiver": "worker",
                "message": "hello",
            },
        )

        assert response.status_code == 404
        assert "Sender" in response.json()["detail"]

    def test_send_message_receiver_not_found(self, client, mock_execution):
        """404 when receiver not found."""
        mock_execution.agents = {"orchestrator": MagicMock()}
        mock_execution.has_agent.side_effect = lambda name: name in mock_execution.agents

        response = client.post(
            "/messages/send",
            json={
                "execution_slug": SLUG,
                "sender": "orchestrator",
                "receiver": "worker",
                "message": "hello",
            },
        )

        assert response.status_code == 404
        assert "Receiver" in response.json()["detail"]


# --- Stop Agent Tests ---


class TestStopAgent:
    def test_stop_agent(self, client, mock_execution):
        """Calls shutdown_agent and returns success."""
        mock_execution.agents = {"worker": MagicMock()}
        mock_execution.has_agent.side_effect = lambda name: name in mock_execution.agents

        response = client.post(
            "/agents/stop",
            json={"execution_slug": SLUG, "agent_name": "worker"},
        )

        assert response.status_code == 200
        assert response.json() == {"status": "stopped", "agent_name": "worker"}
        mock_execution.shutdown_agent.assert_called_once_with("worker")

    def test_stop_agent_not_found(self, client, mock_execution):
        """404 when agent not found."""
        mock_execution.agents = {}
        mock_execution.has_agent.side_effect = lambda name: False

        response = client.post(
            "/agents/stop",
            json={"execution_slug": SLUG, "agent_name": "nonexistent"},
        )

        assert response.status_code == 404


# --- SSH Tests ---


class TestGetAgentSSH:
    def test_get_agent_ssh(self, client, mock_execution, mock_agent):
        """Returns SSH credentials."""
        from druids_server.lib.sandbox.base import SSHCredentials

        mock_creds = SSHCredentials(
            host="ssh.example.com",
            port=22,
            username="instance_1",
            private_key="-----BEGIN RSA PRIVATE KEY-----\ntest\n-----END RSA PRIVATE KEY-----",
            password="secret123",
        )
        mock_agent.machine.ssh_credentials = AsyncMock(return_value=mock_creds)

        mock_execution.agents = {"worker": mock_agent}
        mock_execution.has_agent.side_effect = lambda name: name in mock_execution.agents

        response = client.post(
            "/agents/ssh",
            json={"execution_slug": SLUG, "agent_name": "worker"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["host"] == "ssh.example.com"
        assert data["port"] == 22
        assert data["username"] == "instance_1"
        assert "BEGIN RSA PRIVATE KEY" in data["private_key"]
        assert data["password"] == "secret123"

    def test_get_agent_ssh_agent_not_found(self, client, mock_execution):
        """404 when agent not found."""
        mock_execution.agents = {}
        mock_execution.has_agent.side_effect = lambda name: False

        response = client.post(
            "/agents/ssh",
            json={"execution_slug": SLUG, "agent_name": "nonexistent"},
        )

        assert response.status_code == 404


class TestRequiredFields:
    def test_send_message_missing_required_fields(self, client, mock_execution):
        """422 when required fields are missing from request body."""
        response = client.post(
            "/messages/send",
            json={"receiver": "worker", "message": "hi"},
        )
        assert response.status_code == 422
