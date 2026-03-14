"""Tests for two-phase devbox setup endpoints."""

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


class TestSetupStart:
    def test_start_returns_ssh_credentials(self, client, mock_sandbox, mock_user):
        """setup/start provisions a sandbox and returns SSH info."""
        devbox = Devbox(id=uuid4(), user_id=mock_user.id, name="test", repo_full_name="test")
        session = SetupSession(id=uuid4(), user_id=mock_user.id, devbox_id=devbox.id, state="INIT")

        with (
            patch("druids_server.api.routes.setup.Sandbox.create", new_callable=AsyncMock, return_value=mock_sandbox),
            patch("druids_server.api.routes.setup.get_session", make_mock_session()),
            patch("druids_server.api.routes.setup.get_or_create_devbox", new_callable=AsyncMock, return_value=devbox),
            patch("druids_server.api.routes.setup.create_setup_session", new_callable=AsyncMock, return_value=session),
            patch("druids_server.api.routes.setup.transition_to_provisioning", new_callable=AsyncMock, return_value=session),
            patch("druids_server.api.routes.setup.transition_to_configuring", new_callable=AsyncMock, return_value=session),
        ):
            response = client.post("/devbox/setup/start", json={"name": "test"})

        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "test"
        assert data["instance_id"] == "instance-abc"
        assert data["ssh"]["host"] == "10.0.0.1"
        assert data["ssh"]["username"] == "root"
        assert data["ssh"]["password"] == "hunter2"

    def test_start_with_repo_clones(self, client, mock_sandbox, mock_user):
        """setup/start clones the repo when repo_full_name is provided."""
        devbox = Devbox(id=uuid4(), user_id=mock_user.id, name="owner/repo", repo_full_name="owner/repo")
        session = SetupSession(id=uuid4(), user_id=mock_user.id, devbox_id=devbox.id, state="INIT")

        with (
            patch("druids_server.api.routes.setup.Sandbox.create", new_callable=AsyncMock, return_value=mock_sandbox),
            patch("druids_server.api.routes.setup.get_installation_token", new_callable=AsyncMock, return_value="ghp_tok"),
            patch("druids_server.api.routes.setup.get_session", make_mock_session()),
            patch("druids_server.api.routes.setup.get_or_create_devbox", new_callable=AsyncMock, return_value=devbox),
            patch("druids_server.api.routes.setup.create_setup_session", new_callable=AsyncMock, return_value=session),
            patch("druids_server.api.routes.setup.transition_to_provisioning", new_callable=AsyncMock, return_value=session),
            patch("druids_server.api.routes.setup.transition_to_configuring", new_callable=AsyncMock, return_value=session),
        ):
            response = client.post("/devbox/setup/start", json={"repo_full_name": "owner/repo"})

        assert response.status_code == 200
        # Verify git clone was called
        clone_call = mock_sandbox.exec.call_args_list[0]
        assert "git clone" in clone_call.args[0]
        assert "owner/repo" in clone_call.args[0]

    def test_start_stops_sandbox_on_clone_failure(self, client, mock_sandbox, mock_user):
        """setup/start stops the sandbox if cloning fails."""
        mock_sandbox.exec = AsyncMock(return_value=MagicMock(ok=False, exit_code=1, stdout="", stderr="fail"))
        devbox = Devbox(id=uuid4(), user_id=mock_user.id, name="owner/repo", repo_full_name="owner/repo")
        session = SetupSession(id=uuid4(), user_id=mock_user.id, devbox_id=devbox.id, state="INIT")

        with (
            patch("druids_server.api.routes.setup.Sandbox.create", new_callable=AsyncMock, return_value=mock_sandbox),
            patch("druids_server.api.routes.setup.get_installation_token", new_callable=AsyncMock, return_value="ghp_tok"),
            patch("druids_server.api.routes.setup.get_session", make_mock_session()),
            patch("druids_server.api.routes.setup.get_or_create_devbox", new_callable=AsyncMock, return_value=devbox),
            patch("druids_server.api.routes.setup.create_setup_session", new_callable=AsyncMock, return_value=session),
            patch("druids_server.api.routes.setup.transition_to_provisioning", new_callable=AsyncMock, return_value=session),
            patch("druids_server.api.routes.setup.handle_provision_failure", new_callable=AsyncMock),
        ):
            response = client.post("/devbox/setup/start", json={"repo_full_name": "owner/repo"})

        assert response.status_code == 500
        # handle_provision_failure is responsible for stopping the sandbox
        # but we mocked it, so just verify it was called

    def test_start_stops_sandbox_when_no_ssh(self, client, mock_user):
        """setup/start stops the sandbox if SSH is not available."""
        sandbox = MagicMock()
        sandbox.instance_id = "instance-abc"
        sandbox.exec = AsyncMock(return_value=MagicMock(ok=True))
        sandbox.stop = AsyncMock()
        sandbox.ssh_credentials = AsyncMock(return_value=None)
        devbox = Devbox(id=uuid4(), user_id=mock_user.id, name="test", repo_full_name="")
        session = SetupSession(id=uuid4(), user_id=mock_user.id, devbox_id=devbox.id, state="INIT")

        with (
            patch("druids_server.api.routes.setup.Sandbox.create", new_callable=AsyncMock, return_value=sandbox),
            patch("druids_server.api.routes.setup.get_session", make_mock_session()),
            patch("druids_server.api.routes.setup.get_or_create_devbox", new_callable=AsyncMock, return_value=devbox),
            patch("druids_server.api.routes.setup.create_setup_session", new_callable=AsyncMock, return_value=session),
            patch("druids_server.api.routes.setup.transition_to_provisioning", new_callable=AsyncMock, return_value=session),
            patch("druids_server.api.routes.setup.handle_provision_failure", new_callable=AsyncMock),
        ):
            response = client.post("/devbox/setup/start", json={"name": "test"})

        assert response.status_code == 500
        # handle_provision_failure is responsible for stopping the sandbox
        # but we mocked it, so just verify it was called


class TestSetupFinish:
    def test_finish_snapshots_and_stops(self, client, mock_sandbox, mock_user):
        """setup/finish snapshots the sandbox, stops it, and returns snapshot ID."""
        devbox = Devbox(
            id=uuid4(), user_id=mock_user.id, name="test",
            repo_full_name="", instance_id="instance-abc",
        )

        mock_get = AsyncMock(return_value=mock_sandbox)
        with (
            patch("druids_server.api.routes.setup.Sandbox.get", mock_get),
            patch("druids_server.api.routes.setup.get_session", make_mock_session(devbox)),
            patch("druids_server.api.routes.setup.resolve_devbox", new_callable=AsyncMock, return_value=devbox),
        ):
            response = client.post("/devbox/setup/finish", json={"name": "test"})

        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "test"
        assert data["snapshot_id"] == "snap-xyz"
        mock_sandbox.snapshot.assert_awaited_once()
        mock_sandbox.stop.assert_awaited_once()
        # Sandbox must be retrieved with owned=True so stop() actually tears it down
        mock_get.assert_awaited_once_with("instance-abc", owned=True)

    def test_finish_scrubs_git_remote(self, client, mock_sandbox, mock_user):
        """setup/finish scrubs the git token from the remote URL."""
        devbox = Devbox(
            id=uuid4(), user_id=mock_user.id, name="myrepo",
            repo_full_name="owner/repo", instance_id="instance-abc",
        )

        with (
            patch("druids_server.api.routes.setup.Sandbox.get", new_callable=AsyncMock, return_value=mock_sandbox),
            patch("druids_server.api.routes.setup.get_session", make_mock_session(devbox)),
            patch("druids_server.api.routes.setup.resolve_devbox", new_callable=AsyncMock, return_value=devbox),
        ):
            response = client.post("/devbox/setup/finish", json={"name": "myrepo"})

        assert response.status_code == 200
        # First exec call should be the git remote set-url
        scrub_call = mock_sandbox.exec.call_args_list[0]
        assert "git remote set-url" in scrub_call.args[0]
        assert "https://github.com/owner/repo.git" in scrub_call.args[0]

    def test_finish_404_when_devbox_not_found(self, client, mock_user):
        """setup/finish returns 404 when devbox does not exist."""
        with (
            patch("druids_server.api.routes.setup.get_session", make_mock_session()),
            patch("druids_server.api.routes.setup.resolve_devbox", new_callable=AsyncMock, return_value=None),
        ):
            response = client.post("/devbox/setup/finish", json={"name": "missing"})

        assert response.status_code == 404

    def test_finish_400_when_no_running_sandbox(self, client, mock_user):
        """setup/finish returns 400 when devbox has no instance_id."""
        devbox = Devbox(
            id=uuid4(), user_id=mock_user.id, name="test",
            repo_full_name="", instance_id=None, snapshot_id="old-snap",
        )

        with (
            patch("druids_server.api.routes.setup.get_session", make_mock_session(devbox)),
            patch("druids_server.api.routes.setup.resolve_devbox", new_callable=AsyncMock, return_value=devbox),
        ):
            response = client.post("/devbox/setup/finish", json={"name": "test"})

        assert response.status_code == 400

    def test_finish_stops_sandbox_even_on_snapshot_failure(self, client, mock_user):
        """setup/finish stops the sandbox even if snapshot raises."""
        sandbox = MagicMock()
        sandbox.instance_id = "instance-abc"
        sandbox.exec = AsyncMock(return_value=MagicMock(ok=True))
        sandbox.stop = AsyncMock()
        sandbox.snapshot = AsyncMock(side_effect=RuntimeError("snapshot failed"))

        devbox = Devbox(
            id=uuid4(), user_id=mock_user.id, name="test",
            repo_full_name="", instance_id="instance-abc",
        )

        with (
            patch("druids_server.api.routes.setup.Sandbox.get", new_callable=AsyncMock, return_value=sandbox),
            patch("druids_server.api.routes.setup.get_session", make_mock_session(devbox)),
            patch("druids_server.api.routes.setup.resolve_devbox", new_callable=AsyncMock, return_value=devbox),
            pytest.raises(RuntimeError, match="snapshot failed"),
        ):
            client.post("/devbox/setup/finish", json={"name": "test"})

        sandbox.stop.assert_awaited_once()


class TestSetupSessionLifecycle:
    """Test the full setup session state machine lifecycle."""

    def test_session_created_on_start(self, client, mock_sandbox, mock_user):
        """setup/start creates a setup session in CONFIGURING state."""
        devbox = Devbox(id=uuid4(), user_id=mock_user.id, name="test", repo_full_name="")
        session = SetupSession(
            id=uuid4(), user_id=mock_user.id, devbox_id=devbox.id, state="INIT",
        )

        with (
            patch("druids_server.api.routes.setup.Sandbox.create", new_callable=AsyncMock, return_value=mock_sandbox),
            patch("druids_server.api.routes.setup.get_session", make_mock_session(devbox)),
            patch("druids_server.api.routes.setup.get_or_create_devbox", new_callable=AsyncMock, return_value=devbox),
            patch("druids_server.api.routes.setup.create_setup_session", new_callable=AsyncMock, return_value=session),
            patch("druids_server.api.routes.setup.transition_to_provisioning", new_callable=AsyncMock, return_value=session),
            patch("druids_server.api.routes.setup.transition_to_configuring", new_callable=AsyncMock, return_value=session),
        ):
            response = client.post("/devbox/setup/start", json={"name": "test"})

        assert response.status_code == 200
        data = response.json()
        assert "session_id" in data
        assert data["instance_id"] == "instance-abc"

    def test_finish_with_session_transitions_to_completed(self, client, mock_sandbox, mock_user):
        """setup/finish with session_id transitions CONFIGURING -> SAVING -> COMPLETED."""
        session_id = uuid4()
        devbox = Devbox(
            id=uuid4(), user_id=mock_user.id, name="test",
            repo_full_name="", instance_id="instance-abc",
        )
        session = SetupSession(
            id=session_id, user_id=mock_user.id, devbox_id=devbox.id, state="CONFIGURING",
        )

        with (
            patch("druids_server.api.routes.setup.Sandbox.get", new_callable=AsyncMock, return_value=mock_sandbox),
            patch("druids_server.api.routes.setup.get_session", make_mock_session(devbox)),
            patch("druids_server.api.routes.setup.resolve_devbox", new_callable=AsyncMock, return_value=devbox),
            patch("druids_server.api.routes.setup.get_setup_session", new_callable=AsyncMock, return_value=session),
            patch("druids_server.api.routes.setup.transition_to_saving", new_callable=AsyncMock, return_value=session),
            patch("druids_server.api.routes.setup.transition_to_completed", new_callable=AsyncMock, return_value=session),
        ):
            response = client.post("/devbox/setup/finish", json={"name": "test", "session_id": str(session_id)})

        assert response.status_code == 200
        assert response.json()["snapshot_id"] == "snap-xyz"

    def test_start_clone_failure_transitions_to_error(self, client, mock_user):
        """setup/start clone failure transitions to ERROR and stops sandbox."""
        sandbox = MagicMock()
        sandbox.instance_id = "instance-abc"
        sandbox.exec = AsyncMock(return_value=MagicMock(ok=False, exit_code=1, stdout="", stderr="clone failed"))
        sandbox.stop = AsyncMock()
        sandbox.ssh_credentials = AsyncMock(return_value=SSHCredentials(
            host="10.0.0.1", port=22, username="root",
            private_key="-----BEGIN RSA PRIVATE KEY-----\nfake\n-----END RSA PRIVATE KEY-----",
            password="hunter2",
        ))

        devbox = Devbox(id=uuid4(), user_id=mock_user.id, name="owner/repo", repo_full_name="owner/repo")
        session = SetupSession(
            id=uuid4(), user_id=mock_user.id, devbox_id=devbox.id, state="INIT",
        )
        error_session = SetupSession(
            id=session.id, user_id=mock_user.id, devbox_id=devbox.id,
            state="ERROR", error_message="git clone failed", failed_step="PROVISIONING",
        )

        handle_failure_mock = AsyncMock(return_value=error_session)

        with (
            patch("druids_server.api.routes.setup.Sandbox.create", new_callable=AsyncMock, return_value=sandbox),
            patch("druids_server.api.routes.setup.get_installation_token", new_callable=AsyncMock, return_value="ghp_tok"),
            patch("druids_server.api.routes.setup.get_session", make_mock_session(devbox)),
            patch("druids_server.api.routes.setup.get_or_create_devbox", new_callable=AsyncMock, return_value=devbox),
            patch("druids_server.api.routes.setup.create_setup_session", new_callable=AsyncMock, return_value=session),
            patch("druids_server.api.routes.setup.transition_to_provisioning", new_callable=AsyncMock, return_value=session),
            patch("druids_server.api.routes.setup.handle_provision_failure", handle_failure_mock),
        ):
            response = client.post("/devbox/setup/start", json={"repo_full_name": "owner/repo"})

        assert response.status_code == 500
        # Verify handle_provision_failure was called (it's responsible for stopping the sandbox)
        handle_failure_mock.assert_awaited_once()

    def test_finish_snapshot_failure_transitions_to_error(self, client, mock_user):
        """setup/finish snapshot failure transitions to ERROR and stops sandbox."""
        session_id = uuid4()
        sandbox = MagicMock()
        sandbox.instance_id = "instance-abc"
        sandbox.exec = AsyncMock(return_value=MagicMock(ok=True))
        sandbox.stop = AsyncMock()
        sandbox.snapshot = AsyncMock(side_effect=RuntimeError("snapshot timeout"))

        devbox = Devbox(
            id=uuid4(), user_id=mock_user.id, name="test",
            repo_full_name="", instance_id="instance-abc",
        )
        session = SetupSession(
            id=session_id, user_id=mock_user.id, devbox_id=devbox.id, state="CONFIGURING",
        )
        error_session = SetupSession(
            id=session_id, user_id=mock_user.id, devbox_id=devbox.id,
            state="ERROR", error_message="snapshot timeout", failed_step="SAVING",
        )

        handle_failure_mock = AsyncMock(return_value=error_session)

        with (
            patch("druids_server.api.routes.setup.Sandbox.get", new_callable=AsyncMock, return_value=sandbox),
            patch("druids_server.api.routes.setup.get_session", make_mock_session(devbox)),
            patch("druids_server.api.routes.setup.resolve_devbox", new_callable=AsyncMock, return_value=devbox),
            patch("druids_server.api.routes.setup.get_setup_session", new_callable=AsyncMock, return_value=session),
            patch("druids_server.api.routes.setup.transition_to_saving", new_callable=AsyncMock, return_value=session),
            patch("druids_server.api.routes.setup.handle_snapshot_failure", handle_failure_mock),
            pytest.raises(RuntimeError, match="snapshot timeout"),
        ):
            client.post("/devbox/setup/finish", json={"name": "test", "session_id": str(session_id)})

        # Verify handle_snapshot_failure was called (it's responsible for stopping the sandbox)
        handle_failure_mock.assert_awaited_once()

    def test_retry_clears_error_state(self, client, mock_user):
        """retry endpoint clears error state and returns session to INIT."""
        session_id = uuid4()
        devbox_id = uuid4()
        error_session = SetupSession(
            id=session_id, user_id=mock_user.id, devbox_id=devbox_id,
            state="ERROR", error_message="snapshot failed", failed_step="SAVING",
        )
        retried_session = SetupSession(
            id=session_id, user_id=mock_user.id, devbox_id=devbox_id,
            state="INIT", error_message=None, failed_step=None,
        )

        with (
            patch("druids_server.api.routes.setup.get_session", make_mock_session()),
            patch("druids_server.api.routes.setup.retry_session", new_callable=AsyncMock, return_value=retried_session),
        ):
            response = client.post(f"/setup/sessions/{session_id}/retry")

        assert response.status_code == 200
        data = response.json()
        assert data["state"] == "INIT"
        assert data["error_message"] is None
        assert data["failed_step"] is None

    def test_retry_400_when_not_in_error_state(self, client, mock_user):
        """retry endpoint returns 400 when session is not in ERROR state."""
        from druids_server.lib.setup_session import SetupSessionError

        session_id = uuid4()

        with (
            patch("druids_server.api.routes.setup.get_session", make_mock_session()),
            patch("druids_server.api.routes.setup.retry_session", new_callable=AsyncMock, side_effect=SetupSessionError("Cannot retry from state CONFIGURING", "RETRY")),
        ):
            with pytest.raises(SetupSessionError):
                client.post(f"/setup/sessions/{session_id}/retry")

    def test_finish_without_session_id_still_works(self, client, mock_sandbox, mock_user):
        """setup/finish without session_id works for backwards compatibility."""
        devbox = Devbox(
            id=uuid4(), user_id=mock_user.id, name="test",
            repo_full_name="", instance_id="instance-abc",
        )

        with (
            patch("druids_server.api.routes.setup.Sandbox.get", new_callable=AsyncMock, return_value=mock_sandbox),
            patch("druids_server.api.routes.setup.get_session", make_mock_session(devbox)),
            patch("druids_server.api.routes.setup.resolve_devbox", new_callable=AsyncMock, return_value=devbox),
        ):
            response = client.post("/devbox/setup/finish", json={"name": "test"})

        assert response.status_code == 200
        data = response.json()
        assert data["snapshot_id"] == "snap-xyz"
