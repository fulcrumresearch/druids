"""Tests for MCP API routes."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from orpheus.lib.agents.base import Agent
from orpheus.lib.execution import ExposedService
from orpheus.lib.machine import BRIDGE_PORT
from orpheus.lib.program import Program

from tests.api.conftest import SLUG


# --- Send Message Tests ---


class TestSendMessage:
    def test_send_message_success(self, client, mock_execution):
        """Sends message when both programs exist."""
        sender = Agent(name="orchestrator")
        receiver = Agent(name="worker")
        mock_execution.programs = {
            "orchestrator": sender,
            "worker": receiver,
        }

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
        mock_execution.programs = {"worker": Agent(name="worker")}

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
        mock_execution.programs = {"orchestrator": Agent(name="orchestrator")}

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


# --- Spawn Tests ---


class TestSpawn:
    def test_spawn_success(self, client, mock_execution):
        """Returns spawned program info."""
        orchestrator = Agent(name="orchestrator")
        orchestrator.constructors = {"workers": lambda name: Agent(name=name)}
        mock_execution.programs = {"orchestrator": orchestrator}

        new_program = Program(name="worker-1", constructors={"sub": lambda: None})
        mock_execution.spawn.return_value = [new_program]

        response = client.post(
            "/spawn",
            json={
                "execution_slug": SLUG,
                "sender": "orchestrator",
                "constructor_name": "workers",
                "kwargs": {"name": "worker-1"},
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "spawned"
        assert len(data["programs"]) == 1
        assert data["programs"][0]["name"] == "worker-1"
        assert "sub" in data["programs"][0]["constructors"]

    def test_spawn_unknown_sender(self, client, mock_execution):
        """404 for unknown sender."""
        mock_execution.programs = {}

        response = client.post(
            "/spawn",
            json={
                "execution_slug": SLUG,
                "sender": "unknown",
                "constructor_name": "workers",
            },
        )

        assert response.status_code == 404

    def test_spawn_unknown_constructor(self, client, mock_execution):
        """404 for unknown constructor."""
        orchestrator = Agent(name="orchestrator")
        orchestrator.constructors = {"workers": lambda name: Agent(name=name)}
        mock_execution.programs = {"orchestrator": orchestrator}

        response = client.post(
            "/spawn",
            json={
                "execution_slug": SLUG,
                "sender": "orchestrator",
                "constructor_name": "bad",
            },
        )

        assert response.status_code == 404


# --- Programs Tests ---


def _make_agent_with_machine(name, instance_id, bridge_id=None):
    """Create an Agent with a mock Machine for testing."""
    agent = Agent(name=name)
    mock_machine = MagicMock()
    mock_machine.instance_id = instance_id
    mock_machine.bridge_id = bridge_id
    mock_machine.stop = AsyncMock()
    agent.machine = mock_machine
    return agent


class TestGetPrograms:
    def test_get_programs(self, client, mock_execution):
        """Returns all programs with constructors."""
        orchestrator = _make_agent_with_machine("orchestrator", "morph_123", "morph_123")
        worker = _make_agent_with_machine("worker", "morph_456")
        base_program = Program(
            name="base",
            constructors={"pool": lambda: Agent(name="pool-agent")},
        )

        mock_execution.programs = {
            "orchestrator": orchestrator,
            "worker": worker,
            "base": base_program,
        }

        response = client.post("/programs", json={"execution_slug": SLUG})

        assert response.status_code == 200
        data = response.json()
        assert len(data["programs"]) == 3

        programs_by_name = {p["name"]: p for p in data["programs"]}
        assert programs_by_name["orchestrator"]["instance_id"] == "morph_123"
        assert programs_by_name["orchestrator"]["bridge_id"] == "morph_123"
        assert programs_by_name["base"]["constructors"] == ["pool"]

    def test_get_programs_empty(self, client, mock_execution):
        """Returns empty list when no programs."""
        mock_execution.programs = {}

        response = client.post("/programs", json={"execution_slug": SLUG})

        assert response.status_code == 200
        assert response.json() == {"programs": []}


# --- Stop Agent Tests ---


class TestStopAgent:
    def test_stop_agent(self, client, mock_execution):
        """Calls _disconnect_agent and removes from programs."""
        agent = Agent(name="worker")
        mock_execution.programs = {"worker": agent}

        response = client.post(
            "/agents/stop",
            json={"execution_slug": SLUG, "agent_name": "worker"},
        )

        assert response.status_code == 200
        assert response.json() == {"status": "stopped", "agent_name": "worker"}
        mock_execution._disconnect_agent.assert_called_once_with("worker")
        assert "worker" not in mock_execution.programs

    def test_stop_agent_not_found(self, client, mock_execution):
        """404 when agent not found."""
        mock_execution.programs = {}

        response = client.post(
            "/agents/stop",
            json={"execution_slug": SLUG, "agent_name": "nonexistent"},
        )

        assert response.status_code == 404

    def test_stop_non_agent_program(self, client, mock_execution):
        """404 when program is not an Agent."""
        mock_execution.programs = {"base": Program(name="base")}

        response = client.post(
            "/agents/stop",
            json={"execution_slug": SLUG, "agent_name": "base"},
        )

        assert response.status_code == 404


# --- SSH Tests ---


class TestGetAgentSSH:
    def test_get_agent_ssh(self, client, mock_execution, mock_agent):
        """Returns SSH credentials."""
        mock_ssh_key = MagicMock()
        mock_ssh_key.private_key = "-----BEGIN RSA PRIVATE KEY-----\ntest\n-----END RSA PRIVATE KEY-----"
        mock_ssh_key.password = "secret123"
        mock_agent.machine.ssh_key = AsyncMock(return_value=mock_ssh_key)

        mock_execution.programs = {"worker": mock_agent}

        response = client.post(
            "/agents/ssh",
            json={"execution_slug": SLUG, "agent_name": "worker"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["host"] == "ssh.cloud.morph.so"
        assert data["username"] == "morph_123"
        assert "BEGIN RSA PRIVATE KEY" in data["private_key"]
        assert data["password"] == "secret123"

    def test_get_agent_ssh_agent_not_found(self, client, mock_execution):
        """404 when agent not found."""
        mock_execution.programs = {}

        response = client.post(
            "/agents/ssh",
            json={"execution_slug": SLUG, "agent_name": "nonexistent"},
        )

        assert response.status_code == 404

    def test_get_agent_ssh_instance_not_found(self, client, mock_execution):
        """404 when agent has no machine."""
        agent = Agent(name="worker")
        mock_execution.programs = {"worker": agent}

        response = client.post(
            "/agents/ssh",
            json={"execution_slug": SLUG, "agent_name": "worker"},
        )

        assert response.status_code == 404


# --- Expose Port Tests ---


class TestExposePort:
    def test_expose_port_success(self, client, mock_execution, mock_agent):
        """Returns public URL for exposed port."""
        mock_agent.machine.expose_http_service = AsyncMock(return_value="https://worker-8080.morph.so")
        mock_execution.programs = {"worker": mock_agent}

        response = client.post(
            "/agents/expose-port",
            json={"execution_slug": SLUG, "agent_name": "worker", "port": 8080, "service_name": "web"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["url"] == "https://worker-8080.morph.so"
        assert data["port"] == 8080
        assert data["service_name"] == "web"
        assert data["agent_name"] == "worker"
        mock_agent.machine.expose_http_service.assert_called_once_with("web", 8080)

        assert len(mock_execution.exposed_services) == 1
        svc = mock_execution.exposed_services[0]
        assert isinstance(svc, ExposedService)
        assert svc.agent_name == "worker"
        assert svc.service_name == "web"
        assert svc.port == 8080
        assert svc.url == "https://worker-8080.morph.so"

    def test_expose_port_agent_not_found(self, client, mock_execution):
        """404 when agent not found."""
        mock_execution.programs = {}

        response = client.post(
            "/agents/expose-port",
            json={"execution_slug": SLUG, "agent_name": "nonexistent", "port": 8080, "service_name": "web"},
        )

        assert response.status_code == 404

    def test_expose_port_non_agent_program(self, client, mock_execution):
        """404 when program is not an Agent."""
        mock_execution.programs = {"base": Program(name="base")}

        response = client.post(
            "/agents/expose-port",
            json={"execution_slug": SLUG, "agent_name": "base", "port": 8080, "service_name": "web"},
        )

        assert response.status_code == 404

    def test_expose_port_invalid_port(self, client, mock_execution, mock_agent):
        """400 for port outside valid range."""
        mock_execution.programs = {"worker": mock_agent}

        response = client.post(
            "/agents/expose-port",
            json={"execution_slug": SLUG, "agent_name": "worker", "port": 0, "service_name": "web"},
        )

        assert response.status_code == 400
        assert "between 1 and 65535" in response.json()["detail"]

    def test_expose_port_bridge_port_rejected(self, client, mock_execution, mock_agent):
        """400 when port matches the agent bridge port."""
        mock_execution.programs = {"worker": mock_agent}

        response = client.post(
            "/agents/expose-port",
            json={"execution_slug": SLUG, "agent_name": "worker", "port": BRIDGE_PORT, "service_name": "web"},
        )

        assert response.status_code == 400
        assert "reserved for the agent bridge" in response.json()["detail"]

    def test_expose_port_instance_not_found(self, client, mock_execution):
        """404 when agent has no machine."""
        agent = Agent(name="worker")
        mock_execution.programs = {"worker": agent}

        response = client.post(
            "/agents/expose-port",
            json={"execution_slug": SLUG, "agent_name": "worker", "port": 8080, "service_name": "web"},
        )

        assert response.status_code == 404

    def test_expose_port_execution_not_found(self, client, mock_execution):
        """404 when execution slug does not exist."""
        response = client.post(
            "/agents/expose-port",
            json={"execution_slug": "wrong-slug", "agent_name": "worker", "port": 8080, "service_name": "web"},
        )

        assert response.status_code == 404

    def test_expose_port_conflict(self, client, mock_execution, mock_agent):
        """409 when MorphCloud returns a conflict (duplicate service name or port)."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 409
        mock_response.text = "service name or port already in use"
        error = httpx.HTTPStatusError("Conflict", request=MagicMock(), response=mock_response)

        mock_agent.machine.expose_http_service = AsyncMock(side_effect=error)
        mock_execution.programs = {"worker": mock_agent}

        response = client.post(
            "/agents/expose-port",
            json={"execution_slug": SLUG, "agent_name": "worker", "port": 8080, "service_name": "web"},
        )

        assert response.status_code == 409
        assert "service name or port already in use" in response.json()["detail"]

    def test_expose_port_upstream_error(self, client, mock_execution, mock_agent):
        """502 when MorphCloud returns a non-conflict error."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 500
        mock_response.text = "internal server error"
        error = httpx.HTTPStatusError("Server Error", request=MagicMock(), response=mock_response)

        mock_agent.machine.expose_http_service = AsyncMock(side_effect=error)
        mock_execution.programs = {"worker": mock_agent}

        response = client.post(
            "/agents/expose-port",
            json={"execution_slug": SLUG, "agent_name": "worker", "port": 8080, "service_name": "web"},
        )

        assert response.status_code == 502
        assert "internal server error" in response.json()["detail"]


# --- Caller Headers Tests ---


class TestCallerHeaders:
    def test_execution_resolved_from_header(self, client, mock_execution):
        """Header X-Execution-Slug resolves execution when body omits it."""
        mock_execution.programs = {}
        response = client.post(
            "/programs",
            json={},
            headers={"X-Execution-Slug": SLUG},
        )
        assert response.status_code == 200

    def test_header_takes_precedence_over_body(self, client, mock_execution):
        """Header slug wins over body slug."""
        mock_execution.programs = {}
        response = client.post(
            "/programs",
            json={"execution_slug": "wrong-slug"},
            headers={"X-Execution-Slug": SLUG},
        )
        assert response.status_code == 200

    def test_missing_slug_returns_400(self, client):
        """400 when neither header nor body provides execution_slug."""
        response = client.post("/programs", json={})
        assert response.status_code == 400
        assert "execution_slug is required" in response.json()["detail"]

    def test_sender_resolved_from_header(self, client, mock_execution):
        """Header X-Agent-Name resolves sender when body omits it."""
        mock_execution.programs = {"test-agent": Agent(name="test-agent"), "worker": Agent(name="worker")}
        response = client.post(
            "/messages/send",
            json={"receiver": "worker", "message": "hi"},
            headers={"X-Execution-Slug": SLUG, "X-Agent-Name": "test-agent"},
        )
        assert response.status_code == 200
        mock_execution.send.assert_called_once_with("test-agent", "worker", "hi")

    def test_missing_sender_returns_400(self, client, mock_execution):
        """400 when neither header nor body provides sender."""
        mock_execution.programs = {"worker": Agent(name="worker")}
        response = client.post(
            "/messages/send",
            json={"execution_slug": SLUG, "receiver": "worker", "message": "hi"},
        )
        assert response.status_code == 400
        assert "sender is required" in response.json()["detail"]
