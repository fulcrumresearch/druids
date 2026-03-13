"""Tests for execution CRUD endpoints (POST/GET/DELETE /executions)."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from druids_server.api.deps import Caller, get_caller, get_executions_registry
from druids_server.api.routes import router
from druids_server.db.models.execution import ExecutionRecord
from druids_server.lib.execution import ExposedService
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.api.conftest import make_mock_session


@pytest.fixture
def mock_user():
    from druids_server.db.models.user import User

    return User(id=uuid4(), github_id=12345)


@pytest.fixture
def app_fixture(mock_user):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_caller] = lambda: Caller(user=mock_user)
    registry = get_executions_registry()
    registry.clear()
    registry[str(mock_user.id)] = {}
    yield app
    app.dependency_overrides.clear()
    registry.clear()


@pytest.fixture
def client(app_fixture):
    return TestClient(app_fixture)


class TestGetExecution:
    def test_get_execution_success(self, client, mock_user):
        exec_id = uuid4()
        record = ExecutionRecord(
            id=exec_id,
            slug="gentle-nocturne",
            user_id=mock_user.id,
            spec="Test spec",
            repo_full_name="user/repo",
            status="running",
            metadata_={"key": "value"},
            branch_name="druids/gentle-nocturne",
            started_at=datetime.now(timezone.utc),
        )

        mock_runtime = MagicMock()
        mock_runtime.agents = {"swe": MagicMock()}
        mock_runtime.all_agent_names = MagicMock(return_value={"swe"})
        mock_runtime.exposed_services = [
            ExposedService(instance_id="inst_123", service_name="web", port=8080, url="https://swe-8080.example.com"),
        ]

        registry = get_executions_registry()
        registry[str(mock_user.id)]["gentle-nocturne"] = mock_runtime

        with (
            patch("druids_server.api.routes.executions.get_session", new=make_mock_session()),
            patch(
                "druids_server.api.routes.executions.get_execution_by_slug", new_callable=AsyncMock, return_value=record
            ),
        ):
            response = client.get("/executions/gentle-nocturne")

        assert response.status_code == 200
        data = response.json()
        assert data["execution_id"] == str(exec_id)
        assert data["execution_slug"] == "gentle-nocturne"
        assert data["spec"] == "Test spec"
        assert data["status"] == "running"
        assert data["agents"] == ["swe"]
        assert data["exposed_services"] == [
            {"instance_id": "inst_123", "service_name": "web", "port": 8080, "url": "https://swe-8080.example.com"},
        ]

    def test_get_execution_not_found(self, client):
        with (
            patch("druids_server.api.routes.executions.get_session", new=make_mock_session()),
            patch(
                "druids_server.api.routes.executions.get_execution_by_slug", new_callable=AsyncMock, return_value=None
            ),
        ):
            response = client.get("/executions/nonexistent-slug")

        assert response.status_code == 404
        assert "not found" in response.json()["detail"]


class TestListExecutions:
    def test_list_executions_success(self, client, mock_user):
        records = [
            ExecutionRecord(
                id=uuid4(),
                slug="gentle-nocturne",
                user_id=mock_user.id,
                spec="First execution",
                status="running",
                started_at=datetime.now(timezone.utc),
            ),
            ExecutionRecord(
                id=uuid4(),
                slug="cosmic-waltz",
                user_id=mock_user.id,
                spec="Second execution",
                status="completed",
                started_at=datetime.now(timezone.utc),
            ),
        ]

        with (
            patch("druids_server.api.routes.executions.get_session", new=make_mock_session()),
            patch(
                "druids_server.api.routes.executions.get_user_executions", new_callable=AsyncMock, return_value=records
            ),
        ):
            response = client.get("/executions")

        assert response.status_code == 200
        data = response.json()
        assert len(data["executions"]) == 2
        assert data["executions"][0]["spec"] == "First execution"
        assert data["executions"][1]["spec"] == "Second execution"


class TestExecutionAgentEndpoints:
    def test_runtime_ready_execution_not_running(self, client):
        response = client.post("/executions/missing-slug/ready", json={})

        assert response.status_code == 404
        assert response.json()["detail"] == "Execution 'missing-slug' is not running"

    def test_list_agent_tools_agent_not_found(self, client, mock_user):
        mock_runtime = MagicMock()
        mock_runtime.agents = {}
        registry = get_executions_registry()
        registry[str(mock_user.id)]["active-slug"] = mock_runtime

        response = client.get("/executions/active-slug/agents/swe/tools")

        assert response.status_code == 404
        assert response.json()["detail"] == "Agent 'swe' not found"

    def test_call_agent_tool_uses_fresh_default_args_per_request(self, client, mock_user):
        captured_args: list[dict[str, object]] = []

        async def _call_tool(_agent_name: str, _tool_name: str, args: dict[str, object]):
            captured_args.append(args.copy())
            args["mutated"] = True
            return {"ok": True}

        mock_runtime = MagicMock()
        mock_runtime.agents = {"swe": MagicMock()}
        mock_runtime.call_tool = AsyncMock(side_effect=_call_tool)

        registry = get_executions_registry()
        registry[str(mock_user.id)]["active-slug"] = mock_runtime

        first = client.post("/executions/active-slug/agents/swe/tools/bash", json={})
        second = client.post("/executions/active-slug/agents/swe/tools/bash", json={})

        assert first.status_code == 200
        assert second.status_code == 200
        assert captured_args == [{}, {}]

    def test_runtime_ready_defaults_client_events_to_empty_list(self, client, mock_user):
        mock_runtime = MagicMock()
        mock_runtime._client_event_names = set()

        registry = get_executions_registry()
        registry[str(mock_user.id)]["active-slug"] = mock_runtime

        response = client.post("/executions/active-slug/ready", json={})

        assert response.status_code == 200
        assert response.json()["status"] == "ready"
        assert mock_runtime._client_event_names == set()
