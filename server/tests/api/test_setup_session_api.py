"""Tests for setup session REST API endpoints."""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from druids_server.api.deps import Caller, get_caller
from druids_server.api.routes import router
from druids_server.db.models.devbox import Devbox
from druids_server.db.models.setup_session import SetupSession
from druids_server.db.models.user import User
from druids_server.lib.sandbox.base import SSHCredentials


@pytest.fixture
def mock_user():
    return User(id=uuid4(), github_id=12345)


@pytest.fixture
def mock_sandbox():
    sandbox = MagicMock()
    sandbox.instance_id = "instance-abc"
    sandbox.exec = AsyncMock(return_value=MagicMock(ok=True, exit_code=0, stdout="", stderr=""))
    sandbox.stop = AsyncMock()
    sandbox.snapshot = AsyncMock(return_value="snap-xyz")
    sandbox.ssh_credentials = AsyncMock(return_value=SSHCredentials(
        host="10.0.0.1",
        port=22,
        username="root",
        private_key="-----BEGIN RSA PRIVATE KEY-----\nfake\n-----END RSA PRIVATE KEY-----",
        password="hunter2",
    ))
    return sandbox


def make_mock_session(devbox=None):
    """Return an async context manager that yields a mock db session."""
    mock_db = MagicMock()
    mock_db.add = MagicMock()
    mock_db.flush = AsyncMock()
    mock_db.refresh = AsyncMock()

    result = MagicMock()
    result.scalar_one_or_none.return_value = devbox
    result.scalars.return_value.first.return_value = devbox
    mock_db.execute = AsyncMock(return_value=result)

    @asynccontextmanager
    async def _session():
        yield mock_db

    return _session


@pytest.fixture
def app(mock_user):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_caller] = lambda: Caller(user=mock_user)
    yield app
    app.dependency_overrides.clear()


@pytest.fixture
def client(app):
    return TestClient(app)


class TestSetupSessionAPI:
    """Test session REST API endpoints."""

    def test_create_session(self, client, mock_sandbox, mock_user):
        """POST /setup/sessions creates a session and starts provisioning."""
        devbox = Devbox(id=uuid4(), user_id=mock_user.id, name="test", repo_full_name="owner/repo")
        session = SetupSession(
            id=uuid4(), user_id=mock_user.id, devbox_id=devbox.id, state="INIT",
        )

        with (
            patch("druids_server.api.routes.setup.Sandbox.create", new_callable=AsyncMock, return_value=mock_sandbox),
            patch("druids_server.api.routes.setup.get_installation_token", new_callable=AsyncMock, return_value="ghp_tok"),
            patch("druids_server.api.routes.setup.get_session", make_mock_session()),
            patch("druids_server.api.routes.setup.get_or_create_devbox", new_callable=AsyncMock, return_value=devbox),
            patch("druids_server.api.routes.setup.create_setup_session", new_callable=AsyncMock, return_value=session),
            patch("druids_server.api.routes.setup.transition_to_provisioning", new_callable=AsyncMock, return_value=session),
            patch("druids_server.api.routes.setup.transition_to_configuring", new_callable=AsyncMock, return_value=session),
        ):
            response = client.post("/setup/sessions", json={"repo_full_name": "owner/repo"})

        assert response.status_code == 200
        data = response.json()
        assert "session_id" in data
        assert data["instance_id"] == "instance-abc"

    def test_get_session(self, client, mock_user):
        """GET /setup/sessions/:id returns session state and metadata."""
        session_id = uuid4()
        devbox = Devbox(
            id=uuid4(), user_id=mock_user.id, name="test",
            repo_full_name="owner/repo",
        )
        session = SetupSession(
            id=session_id,
            user_id=mock_user.id,
            devbox_id=devbox.id,
            state="CONFIGURING",
            instance_id="instance-abc",
        )

        mock_db = MagicMock()
        result = MagicMock()
        result.scalar_one_or_none.side_effect = [session, devbox]
        mock_db.execute = AsyncMock(return_value=result)

        @asynccontextmanager
        async def _session():
            yield mock_db

        with (
            patch("druids_server.api.routes.setup.get_session", _session),
            patch("druids_server.api.routes.setup.Sandbox.get", new_callable=AsyncMock, side_effect=Exception("no sandbox")),
        ):
            response = client.get(f"/setup/sessions/{session_id}")

        assert response.status_code == 200
        data = response.json()
        assert data["session_id"] == str(session_id)
        assert data["state"] == "CONFIGURING"
        assert data["status"] == "configuring"
        assert data["repo_full_name"] == "owner/repo"
        assert data["error_message"] is None

    def test_get_session_404_when_not_found(self, client):
        """GET /setup/sessions/:id returns 404 when session doesn't exist."""
        session_id = uuid4()

        mock_db = MagicMock()
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=result)

        @asynccontextmanager
        async def _session():
            yield mock_db

        with patch("druids_server.api.routes.setup.get_session", _session):
            response = client.get(f"/setup/sessions/{session_id}")

        assert response.status_code == 404

    def test_get_session_400_invalid_uuid(self, client):
        """GET /setup/sessions/:id returns 400 when session_id is not a valid UUID."""
        response = client.get("/setup/sessions/not-a-uuid")
        assert response.status_code == 400

    def test_get_session_with_error_state(self, client, mock_user):
        """GET /setup/sessions/:id returns error message when in ERROR state."""
        session_id = uuid4()
        devbox = Devbox(
            id=uuid4(), user_id=mock_user.id, name="test",
            repo_full_name="owner/repo",
        )
        session = SetupSession(
            id=session_id,
            user_id=mock_user.id,
            devbox_id=devbox.id,
            state="ERROR",
            error_message="Clone failed: repository not found",
            failed_step="PROVISIONING",
        )

        mock_db = MagicMock()
        result = MagicMock()
        result.scalar_one_or_none.side_effect = [session, devbox]
        mock_db.execute = AsyncMock(return_value=result)

        @asynccontextmanager
        async def _session():
            yield mock_db

        with patch("druids_server.api.routes.setup.get_session", _session):
            response = client.get(f"/setup/sessions/{session_id}")

        assert response.status_code == 200
        data = response.json()
        assert data["state"] == "ERROR"
        assert data["status"] == "error"
        assert data["error_message"] == "Clone failed: repository not found"
        assert data["failed_step"] == "PROVISIONING"

    def test_get_session_includes_ssh_info_when_available(self, client, mock_user, mock_sandbox):
        """GET /setup/sessions/:id includes SSH info when sandbox is running."""
        session_id = uuid4()
        devbox = Devbox(
            id=uuid4(), user_id=mock_user.id, name="test",
            repo_full_name="owner/repo",
        )
        session = SetupSession(
            id=session_id,
            user_id=mock_user.id,
            devbox_id=devbox.id,
            state="CONFIGURING",
            instance_id="instance-abc",
        )

        mock_db = MagicMock()
        result = MagicMock()
        result.scalar_one_or_none.side_effect = [session, devbox]
        mock_db.execute = AsyncMock(return_value=result)

        @asynccontextmanager
        async def _session():
            yield mock_db

        with (
            patch("druids_server.api.routes.setup.get_session", _session),
            patch("druids_server.api.routes.setup.Sandbox.get", new_callable=AsyncMock, return_value=mock_sandbox),
        ):
            response = client.get(f"/setup/sessions/{session_id}")

        assert response.status_code == 200
        data = response.json()
        assert data["ssh_info"] == "ssh root@10.0.0.1"

    def test_get_session_no_ssh_when_completed(self, client, mock_user):
        """GET /setup/sessions/:id does not include SSH info when session is completed."""
        session_id = uuid4()
        devbox = Devbox(
            id=uuid4(), user_id=mock_user.id, name="test",
            repo_full_name="owner/repo",
        )
        session = SetupSession(
            id=session_id,
            user_id=mock_user.id,
            devbox_id=devbox.id,
            state="COMPLETED",
            instance_id=None,
        )

        mock_db = MagicMock()
        result = MagicMock()
        result.scalar_one_or_none.side_effect = [session, devbox]
        mock_db.execute = AsyncMock(return_value=result)

        @asynccontextmanager
        async def _session():
            yield mock_db

        with patch("druids_server.api.routes.setup.get_session", _session):
            response = client.get(f"/setup/sessions/{session_id}")

        assert response.status_code == 200
        data = response.json()
        assert data["ssh_info"] is None

    def test_snapshot_session(self, client, mock_sandbox, mock_user):
        """POST /setup/sessions/:id/snapshot creates snapshot and transitions to COMPLETED."""
        session_id = uuid4()
        devbox = Devbox(
            id=uuid4(),
            user_id=mock_user.id,
            name="test",
            repo_full_name="",
            instance_id="instance-abc",
        )
        session = SetupSession(
            id=session_id,
            user_id=mock_user.id,
            devbox_id=devbox.id,
            state="CONFIGURING",
            instance_id="instance-abc",
        )

        mock_db = MagicMock()
        mock_db.add = MagicMock()
        mock_db.flush = AsyncMock()
        mock_db.refresh = AsyncMock()

        result = MagicMock()
        result.scalar_one_or_none.side_effect = [session, devbox, devbox, devbox]
        result.scalars.return_value.first.return_value = devbox
        mock_db.execute = AsyncMock(return_value=result)

        @asynccontextmanager
        async def _session():
            yield mock_db

        with (
            patch("druids_server.api.routes.setup.get_session", _session),
            patch("druids_server.api.routes.setup.Sandbox.get", new_callable=AsyncMock, return_value=mock_sandbox),
            patch("druids_server.api.routes.setup.resolve_devbox", new_callable=AsyncMock, return_value=devbox),
            patch("druids_server.api.routes.setup.transition_to_saving", new_callable=AsyncMock, return_value=session),
            patch("druids_server.api.routes.setup.transition_to_completed", new_callable=AsyncMock, return_value=session),
        ):
            response = client.post(f"/setup/sessions/{session_id}/snapshot")

        assert response.status_code == 200
        data = response.json()
        assert data["snapshot_id"] == "snap-xyz"

    def test_snapshot_session_404_when_not_found(self, client):
        """POST /setup/sessions/:id/snapshot returns 404 when session doesn't exist."""
        session_id = uuid4()

        mock_db = MagicMock()
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=result)

        @asynccontextmanager
        async def _session():
            yield mock_db

        with patch("druids_server.api.routes.setup.get_session", _session):
            response = client.post(f"/setup/sessions/{session_id}/snapshot")

        assert response.status_code == 404

    def test_snapshot_session_400_when_no_instance(self, client, mock_user):
        """POST /setup/sessions/:id/snapshot returns 400 when no sandbox is running."""
        session_id = uuid4()
        devbox = Devbox(
            id=uuid4(),
            user_id=mock_user.id,
            name="test",
            instance_id=None,
        )
        session = SetupSession(
            id=session_id,
            user_id=mock_user.id,
            devbox_id=devbox.id,
            state="CONFIGURING",
        )

        mock_db = MagicMock()
        result = MagicMock()
        result.scalar_one_or_none.side_effect = [session, devbox]
        mock_db.execute = AsyncMock(return_value=result)

        @asynccontextmanager
        async def _session():
            yield mock_db

        with patch("druids_server.api.routes.setup.get_session", _session):
            response = client.post(f"/setup/sessions/{session_id}/snapshot")

        assert response.status_code == 400
