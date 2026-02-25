"""Tests for task endpoints."""

from contextlib import contextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from orpheus.api.deps import get_executions_registry
from orpheus.api.routes import router
from orpheus.lib.execution import ExposedService
from orpheus.db.models.execution import ExecutionRecord
from orpheus.db.models.task import Task

from tests.api.conftest import make_mock_session


def _make_launch_mock(exec_records):
    """Build a side_effect for launch_execution that returns mock Executions."""
    if not isinstance(exec_records, list):
        exec_records = [exec_records]
    records_iter = iter(exec_records)

    async def _launch(root, *, task, **kwargs):
        record = next(records_iter)
        mock_ex = MagicMock()
        mock_ex.id = record.id
        mock_ex.slug = record.slug
        mock_ex.task_id = task.id
        return mock_ex

    return _launch


@contextmanager
def _patch_task_creation(programs, task, exec_records):
    """Patch the common dependencies used by POST /tasks."""
    with (
        patch("orpheus.api.routes.tasks.settings") as mock_settings,
        patch("orpheus.api.routes.tasks.discover_programs", return_value=programs),
        patch("orpheus.api.routes.tasks.create_task", new_callable=AsyncMock, return_value=task),
        patch("orpheus.api.routes.tasks.launch_execution", side_effect=_make_launch_mock(exec_records)),
        patch("orpheus.api.routes.tasks.get_session", new=make_mock_session()),
        patch("orpheus.api.routes.tasks.get_user_execution_count", new_callable=AsyncMock, return_value=0),
    ):
        mock_settings.enable_task_creation = True
        mock_settings.free_tier_reviews = 15
        yield


class TestTaskEndpointsAuth:
    """Test that task endpoints require authentication."""

    @pytest.fixture
    def client(self):
        """Create test client without auth."""
        app = FastAPI()
        app.include_router(router)
        return TestClient(app)

    def test_create_task_requires_auth(self, client):
        """POST /tasks returns 401 without auth."""
        response = client.post(
            "/tasks",
            json={"spec": "Add hello world"},
        )
        assert response.status_code == 401

    def test_get_task_requires_auth(self, client):
        """GET /tasks/{slug} returns 401 without auth."""
        response = client.get("/tasks/gentle-nocturne")
        assert response.status_code == 401

    def test_delete_task_requires_auth(self, client):
        """DELETE /tasks/{slug} returns 401 without auth."""
        response = client.delete("/tasks/gentle-nocturne")
        assert response.status_code == 401


class TestCreateTask:
    """Tests for POST /tasks endpoint."""

    def test_create_task_disabled(self, client):
        """POST /tasks returns 403 when task creation is disabled."""
        with patch("orpheus.api.routes.tasks.settings") as mock_settings:
            mock_settings.enable_task_creation = False
            response = client.post(
                "/tasks",
                json={"spec": "hello", "repo_full_name": "user/repo"},
            )
            assert response.status_code == 403

    def test_create_task_success(self, client, mock_user):
        """Creates and starts tasks for all discovered programs."""
        mock_root = MagicMock()
        mock_root.name = "test_program"
        mock_root.is_agent = False
        mock_create_fn = MagicMock(return_value=mock_root)
        mock_programs = [("test_program", mock_create_fn)]

        task_id = uuid4()
        mock_task = Task(
            id=task_id,
            slug="gentle-nocturne",
            user_id=mock_user.id,
            spec="Add hello world endpoint",
            snapshot_id=None,
            is_active=True,
            metadata_={},
            created_at=datetime.now(timezone.utc),
        )

        exec_id = uuid4()
        mock_exec_record = ExecutionRecord(
            id=exec_id,
            slug="gentle-nocturne-test_program",
            task_id=task_id,
            program_name="test_program",
        )

        with _patch_task_creation(mock_programs, mock_task, mock_exec_record):
            response = client.post(
                "/tasks",
                json={
                    "spec": "Add hello world endpoint",
                    "snapshot_id": "snap-123",
                    "repo_full_name": "user/repo",
                },
            )

            assert response.status_code == 200
            data = response.json()
            assert "task_id" in data
            assert "task_slug" in data
            assert "execution_slugs" in data
            assert data["status"] == "created"
            assert len(data["execution_slugs"]) == 1
            assert "test_program" in data["execution_slugs"][0]

            mock_create_fn.assert_called_once_with("Add hello world endpoint", "repo")

    def test_create_task_with_snapshot(self, client, mock_user):
        """Creates tasks with explicit snapshot_id."""
        mock_root = MagicMock()
        mock_root.name = "test_program"
        mock_root.is_agent = False
        mock_create_fn = MagicMock(return_value=mock_root)
        mock_programs = [("test_program", mock_create_fn)]

        task_id = uuid4()
        mock_task = Task(
            id=task_id,
            slug="gentle-nocturne",
            user_id=mock_user.id,
            spec="Update feature",
            snapshot_id="snap-123",
            is_active=True,
            metadata_={},
            created_at=datetime.now(timezone.utc),
        )

        mock_exec_record = ExecutionRecord(
            id=uuid4(),
            slug="gentle-nocturne-test_program",
            task_id=task_id,
            program_name="test_program",
        )

        with _patch_task_creation(mock_programs, mock_task, mock_exec_record):
            response = client.post(
                "/tasks",
                json={
                    "spec": "Update feature",
                    "snapshot_id": "snap-123",
                    "repo_full_name": "user/repo",
                },
            )

            assert response.status_code == 200
            mock_create_fn.assert_called_once_with("Update feature", "repo")

    def test_create_task_multiple_programs(self, client, mock_user):
        """Creates an execution for each discovered program."""
        mock_root_1 = MagicMock()
        mock_root_1.name = "task"
        mock_root_1.is_agent = False
        mock_root_2 = MagicMock()
        mock_root_2.name = "orchestrator"
        mock_root_2.is_agent = False
        mock_create_1 = MagicMock(return_value=mock_root_1)
        mock_create_2 = MagicMock(return_value=mock_root_2)
        mock_programs = [("task", mock_create_1), ("orchestrator", mock_create_2)]

        task_id = uuid4()
        mock_task = Task(
            id=task_id,
            slug="gentle-nocturne",
            user_id=mock_user.id,
            spec="Build feature",
            snapshot_id=None,
            is_active=True,
            metadata_={},
            created_at=datetime.now(timezone.utc),
        )

        exec_records = [
            ExecutionRecord(id=uuid4(), slug="gentle-nocturne-task", task_id=task_id, program_name="task"),
            ExecutionRecord(
                id=uuid4(), slug="gentle-nocturne-orchestrator", task_id=task_id, program_name="orchestrator"
            ),
        ]

        with _patch_task_creation(mock_programs, mock_task, exec_records):
            response = client.post(
                "/tasks",
                json={
                    "spec": "Build feature",
                    "snapshot_id": "snap-123",
                    "repo_full_name": "user/repo",
                },
            )

            assert response.status_code == 200
            data = response.json()
            assert len(data["execution_slugs"]) == 2

    def test_create_task_with_git_branch(self, client, mock_user):
        """Accepts git_branch parameter without validation error."""
        mock_root = MagicMock()
        mock_root.name = "test_program"
        mock_root.is_agent = False
        mock_create_fn = MagicMock(return_value=mock_root)
        mock_programs = [("test_program", mock_create_fn)]

        task_id = uuid4()
        mock_task = Task(
            id=task_id,
            slug="gentle-nocturne",
            user_id=mock_user.id,
            spec="Test spec with branch",
            snapshot_id="snap-123",
            is_active=True,
            metadata_={},
            created_at=datetime.now(timezone.utc),
        )

        mock_exec_record = ExecutionRecord(
            id=uuid4(),
            slug="gentle-nocturne-test_program",
            task_id=task_id,
            program_name="test_program",
        )

        with _patch_task_creation(mock_programs, mock_task, mock_exec_record):
            response = client.post(
                "/tasks",
                json={
                    "spec": "Test spec with branch",
                    "snapshot_id": "snap-123",
                    "repo_full_name": "user/repo",
                    "git_branch": "feature-branch",
                },
            )

            assert response.status_code == 200
            data = response.json()
            assert "task_id" in data
            assert "task_slug" in data


class TestGetTask:
    """Tests for GET /tasks/{slug} endpoint."""

    def test_get_task_success(self, client, mock_user):
        """Returns task status."""
        task_id = uuid4()
        mock_task = Task(
            id=task_id,
            slug="gentle-nocturne",
            user_id=mock_user.id,
            spec="Test spec",
            snapshot_id=None,
            is_active=True,
            metadata_={"key": "value"},
            created_at=datetime.now(timezone.utc),
        )

        # Add a mock execution to the registry
        registry = get_executions_registry()
        user_id = str(mock_user.id)

        exec_slug = "gentle-nocturne-task"
        mock_execution = MagicMock()
        mock_execution.slug = exec_slug
        mock_execution.programs = {"swe": MagicMock()}
        mock_execution.connections = {"conn1": MagicMock(), "conn2": MagicMock()}
        mock_execution.exposed_services = [
            ExposedService(agent_name="swe", service_name="web", port=8080, url="https://swe-8080.morph.so"),
        ]
        mock_execution.task_id = task_id
        registry[user_id][exec_slug] = mock_execution

        mock_exec_records = [
            ExecutionRecord(
                id=uuid4(),
                slug=exec_slug,
                task_id=task_id,
                program_name="task",
                status="running",
            )
        ]

        with (
            patch("orpheus.api.routes.tasks.get_task_by_slug", new_callable=AsyncMock, return_value=mock_task),
            patch(
                "orpheus.api.routes.tasks.get_task_executions", new_callable=AsyncMock, return_value=mock_exec_records
            ),
            patch("orpheus.api.routes.tasks.get_session", new=make_mock_session()),
        ):
            response = client.get("/tasks/gentle-nocturne")

            assert response.status_code == 200
            data = response.json()
            assert data["task_id"] == str(task_id)
            assert data["task_slug"] == "gentle-nocturne"
            assert data["spec"] == "Test spec"
            assert data["is_active"] is True
            assert data["metadata"] == {"key": "value"}
            assert len(data["executions"]) == 1
            assert data["executions"][0]["programs"] == ["swe"]
            assert data["executions"][0]["exposed_services"] == [
                {"agent_name": "swe", "service_name": "web", "port": 8080, "url": "https://swe-8080.morph.so"},
            ]

    def test_get_task_not_found(self, client):
        """Returns 404 for nonexistent task."""
        with (
            patch("orpheus.api.routes.tasks.get_task_by_slug", new_callable=AsyncMock, return_value=None),
            patch("orpheus.api.routes.tasks.get_session", new=make_mock_session()),
        ):
            response = client.get("/tasks/nonexistent-slug")

            assert response.status_code == 404
            assert "not found" in response.json()["detail"]


class TestDeleteTask:
    """Tests for DELETE /tasks/{slug} endpoint."""

    def test_delete_task_success(self, client, mock_user):
        """Stops and removes a task."""
        task_id = uuid4()
        mock_task = Task(
            id=task_id,
            slug="gentle-nocturne",
            user_id=mock_user.id,
            spec="Test spec",
            snapshot_id=None,
            is_active=True,
            metadata_={},
            created_at=datetime.now(timezone.utc),
        )

        # Add a mock execution
        registry = get_executions_registry()
        user_id = str(mock_user.id)

        mock_execution = MagicMock()
        mock_execution.stop = AsyncMock()
        mock_execution.task_id = task_id
        exec_slug = "gentle-nocturne-task"
        registry[user_id][exec_slug] = mock_execution

        with (
            patch("orpheus.api.routes.tasks.get_task_by_slug", new_callable=AsyncMock, return_value=mock_task),
            patch("orpheus.api.routes.tasks.update_task_status", new_callable=AsyncMock),
            patch("orpheus.api.routes.tasks.get_task_executions", new_callable=AsyncMock, return_value=[]),
            patch("orpheus.api.routes.tasks.update_execution", new_callable=AsyncMock),
            patch("orpheus.api.routes.tasks.get_session", new=make_mock_session()),
        ):
            response = client.delete("/tasks/gentle-nocturne")

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "stopped"
            assert data["task_id"] == str(task_id)
            assert data["task_slug"] == "gentle-nocturne"

            mock_execution.stop.assert_called_once()
            assert exec_slug not in registry[user_id]

    def test_delete_task_not_found(self, client):
        """Returns 404 for nonexistent task."""
        with (
            patch("orpheus.api.routes.tasks.get_task_by_slug", new_callable=AsyncMock, return_value=None),
            patch("orpheus.api.routes.tasks.get_session", new=make_mock_session()),
        ):
            response = client.delete("/tasks/nonexistent-slug")

            assert response.status_code == 404
            assert "not found" in response.json()["detail"]


class TestTaskIsolation:
    """Tests that users can only access their own tasks."""

    def test_user_cannot_access_other_user_task(self, client):
        """User cannot get task belonging to another user (slug lookup is scoped to user)."""
        with (
            patch("orpheus.api.routes.tasks.get_task_by_slug", new_callable=AsyncMock, return_value=None),
            patch("orpheus.api.routes.tasks.get_session", new=make_mock_session()),
        ):
            response = client.get("/tasks/other-users-task")

            assert response.status_code == 404
            assert "not found" in response.json()["detail"]


class TestListTasks:
    """Tests for GET /tasks endpoint."""

    def test_list_tasks_success(self, client, mock_user):
        """Lists all tasks for the current user."""
        mock_tasks = [
            Task(
                id=uuid4(),
                slug="gentle-nocturne",
                user_id=mock_user.id,
                spec="First task",
                snapshot_id=None,
                is_active=True,
                metadata_={},
                created_at=datetime.now(timezone.utc),
            ),
            Task(
                id=uuid4(),
                slug="cosmic-waltz",
                user_id=mock_user.id,
                spec="Second task",
                snapshot_id="snap-123",
                is_active=False,
                metadata_={"key": "value"},
                created_at=datetime.now(timezone.utc),
            ),
        ]

        with (
            patch("orpheus.api.routes.tasks.get_user_tasks", new_callable=AsyncMock, return_value=mock_tasks),
            patch("orpheus.api.routes.tasks.get_task_executions", new_callable=AsyncMock, return_value=[]),
            patch("orpheus.api.routes.tasks.get_session", new=make_mock_session()),
        ):
            response = client.get("/tasks")

            assert response.status_code == 200
            data = response.json()
            assert len(data["tasks"]) == 2
            assert data["tasks"][0]["spec"] == "First task"
            assert data["tasks"][1]["spec"] == "Second task"

    def test_list_tasks_active_only(self, client, mock_user):
        """Lists only active tasks when active_only=true."""
        mock_tasks = [
            Task(
                id=uuid4(),
                slug="gentle-nocturne",
                user_id=mock_user.id,
                spec="Active task",
                snapshot_id=None,
                is_active=True,
                metadata_={},
                created_at=datetime.now(timezone.utc),
            ),
        ]

        with (
            patch(
                "orpheus.api.routes.tasks.get_user_tasks", new_callable=AsyncMock, return_value=mock_tasks
            ) as mock_get,
            patch("orpheus.api.routes.tasks.get_task_executions", new_callable=AsyncMock, return_value=[]),
            patch("orpheus.api.routes.tasks.get_session", new=make_mock_session()),
        ):
            response = client.get("/tasks?active_only=true")

            assert response.status_code == 200
            mock_get.assert_called_once()
            call_kwargs = mock_get.call_args
            assert call_kwargs[1]["active_only"] is True
