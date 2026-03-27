"""Tests for two-phase devbox setup endpoints."""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from druids_server.db.models.devbox import Devbox
from druids_server.lib.sandbox.base import SSHCredentials

from tests.api.conftest import make_mock_session


@pytest.fixture
def mock_sandbox():
    sandbox = MagicMock()
    sandbox.instance_id = "instance-abc"
    sandbox.exec = AsyncMock(return_value=MagicMock(ok=True, exit_code=0, stdout="", stderr=""))
    sandbox.stop = AsyncMock()
    sandbox.snapshot = AsyncMock(return_value="snap-xyz")
    sandbox.ssh_credentials = AsyncMock(
        return_value=SSHCredentials(
            host="10.0.0.1",
            port=22,
            username="root",
            private_key="-----BEGIN RSA PRIVATE KEY-----\nfake\n-----END RSA PRIVATE KEY-----",
            password="hunter2",
        )
    )
    return sandbox


class TestSetupStart:
    def test_start_returns_ssh_credentials(self, authed_client, mock_sandbox, mock_user):
        """setup/start provisions a sandbox and returns SSH info."""
        devbox = Devbox(id=uuid4(), user_id=mock_user.id, name="test", repo_full_name="test")

        with (
            patch("druids_server.api.routes.setup.Sandbox.create", new_callable=AsyncMock, return_value=mock_sandbox),
            patch("druids_server.api.routes.setup.get_session", make_mock_session()),
            patch("druids_server.api.routes.setup.get_or_create_devbox", new_callable=AsyncMock, return_value=devbox),
        ):
            response = authed_client.post("/devbox/setup/start", json={"name": "test"})

        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "test"
        assert data["instance_id"] == "instance-abc"
        assert data["ssh"]["host"] == "10.0.0.1"
        assert data["ssh"]["username"] == "root"
        assert data["ssh"]["password"] == "hunter2"

    def test_start_with_resources(self, authed_client, mock_sandbox, mock_user):
        """setup/start forwards resource params to Sandbox.create and stores them on devbox."""
        devbox = Devbox(id=uuid4(), user_id=mock_user.id, name="test", repo_full_name="test")

        mock_create = AsyncMock(return_value=mock_sandbox)
        with (
            patch("druids_server.api.routes.setup.Sandbox.create", mock_create),
            patch("druids_server.api.routes.setup.get_session", make_mock_session()),
            patch("druids_server.api.routes.setup.get_or_create_devbox", new_callable=AsyncMock, return_value=devbox),
        ):
            response = authed_client.post(
                "/devbox/setup/start",
                json={"name": "test", "vcpus": 4, "memory_mb": 8192, "disk_mb": 20480},
            )

        assert response.status_code == 200
        mock_create.assert_awaited_once()
        call_kwargs = mock_create.call_args.kwargs
        assert call_kwargs["vcpus"] == 4
        assert call_kwargs["memory_mb"] == 8192
        assert call_kwargs["disk_mb"] == 20480
        assert devbox.vcpus == 4
        assert devbox.memory_mb == 8192
        assert devbox.disk_mb == 20480

    def test_start_without_resources_passes_none(self, authed_client, mock_sandbox, mock_user):
        """setup/start passes None for resources when not specified."""
        devbox = Devbox(id=uuid4(), user_id=mock_user.id, name="test", repo_full_name="test")

        mock_create = AsyncMock(return_value=mock_sandbox)
        with (
            patch("druids_server.api.routes.setup.Sandbox.create", mock_create),
            patch("druids_server.api.routes.setup.get_session", make_mock_session()),
            patch("druids_server.api.routes.setup.get_or_create_devbox", new_callable=AsyncMock, return_value=devbox),
        ):
            response = authed_client.post("/devbox/setup/start", json={"name": "test"})

        assert response.status_code == 200
        call_kwargs = mock_create.call_args.kwargs
        assert call_kwargs["vcpus"] is None
        assert call_kwargs["memory_mb"] is None
        assert call_kwargs["disk_mb"] is None

    def test_start_with_repo_clones(self, authed_client, mock_sandbox, mock_user):
        """setup/start clones the repo when repo_full_name is provided."""
        devbox = Devbox(id=uuid4(), user_id=mock_user.id, name="owner/repo", repo_full_name="owner/repo")

        with (
            patch("druids_server.api.routes.setup.Sandbox.create", new_callable=AsyncMock, return_value=mock_sandbox),
            patch(
                "druids_server.api.routes.setup.get_installation_token", new_callable=AsyncMock, return_value="ghp_tok"
            ),
            patch("druids_server.api.routes.setup.get_session", make_mock_session()),
            patch("druids_server.api.routes.setup.get_or_create_devbox", new_callable=AsyncMock, return_value=devbox),
        ):
            response = authed_client.post("/devbox/setup/start", json={"repo_full_name": "owner/repo"})

        assert response.status_code == 200
        # First exec is the credential helper, second is git clone
        clone_call = mock_sandbox.exec.call_args_list[1]
        assert "clone" in clone_call.args[0]
        assert "owner/repo" in clone_call.args[0]

    def test_start_stops_sandbox_on_clone_failure(self, authed_client, mock_sandbox, mock_user):
        """setup/start stops the sandbox if cloning fails."""
        # Credential helper write succeeds, git clone fails
        mock_sandbox.exec = AsyncMock(
            side_effect=[
                MagicMock(ok=True, exit_code=0, stdout="", stderr=""),
                MagicMock(ok=False, exit_code=1, stdout="", stderr="fail"),
            ]
        )

        with (
            patch("druids_server.api.routes.setup.Sandbox.create", new_callable=AsyncMock, return_value=mock_sandbox),
            patch(
                "druids_server.api.routes.setup.get_installation_token", new_callable=AsyncMock, return_value="ghp_tok"
            ),
            patch("druids_server.api.routes.setup.get_session", make_mock_session()),
        ):
            response = authed_client.post("/devbox/setup/start", json={"repo_full_name": "owner/repo"})

        assert response.status_code == 502
        mock_sandbox.stop.assert_awaited_once()

    def test_start_stops_sandbox_when_no_ssh(self, authed_client, mock_user):
        """setup/start stops the sandbox if SSH is not available."""
        sandbox = MagicMock()
        sandbox.instance_id = "instance-abc"
        sandbox.exec = AsyncMock(return_value=MagicMock(ok=True))
        sandbox.stop = AsyncMock()
        sandbox.ssh_credentials = AsyncMock(return_value=None)

        with (
            patch("druids_server.api.routes.setup.Sandbox.create", new_callable=AsyncMock, return_value=sandbox),
            patch("druids_server.api.routes.setup.get_session", make_mock_session()),
        ):
            response = authed_client.post("/devbox/setup/start", json={"name": "test"})

        assert response.status_code == 500
        sandbox.stop.assert_awaited_once()


class TestSetupFinish:
    def test_finish_snapshots_and_stops(self, authed_client, mock_sandbox, mock_user):
        """setup/finish snapshots the sandbox, stops it, and returns snapshot ID."""
        devbox = Devbox(
            id=uuid4(),
            user_id=mock_user.id,
            name="test",
            repo_full_name="",
            instance_id="instance-abc",
        )

        mock_get = AsyncMock(return_value=mock_sandbox)
        with (
            patch("druids_server.api.routes.setup.Sandbox.get", mock_get),
            patch(
                "druids_server.api.routes.setup.get_session",
                make_mock_session(scalar_one_or_none=devbox, scalars_first=devbox),
            ),
            patch("druids_server.api.routes.setup.resolve_devbox", new_callable=AsyncMock, return_value=devbox),
        ):
            response = authed_client.post("/devbox/setup/finish", json={"name": "test"})

        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "test"
        assert data["snapshot_id"] == "snap-xyz"
        mock_sandbox.snapshot.assert_awaited_once()
        mock_sandbox.stop.assert_awaited_once()
        # Sandbox must be retrieved with owned=True so stop() actually tears it down
        mock_get.assert_awaited_once_with("instance-abc", owned=True)

    def test_finish_scrubs_git_remote(self, authed_client, mock_sandbox, mock_user):
        """setup/finish scrubs the git token from the remote URL."""
        devbox = Devbox(
            id=uuid4(),
            user_id=mock_user.id,
            name="myrepo",
            repo_full_name="owner/repo",
            instance_id="instance-abc",
        )

        with (
            patch("druids_server.api.routes.setup.Sandbox.get", new_callable=AsyncMock, return_value=mock_sandbox),
            patch(
                "druids_server.api.routes.setup.get_session",
                make_mock_session(scalar_one_or_none=devbox, scalars_first=devbox),
            ),
            patch("druids_server.api.routes.setup.resolve_devbox", new_callable=AsyncMock, return_value=devbox),
        ):
            response = authed_client.post("/devbox/setup/finish", json={"name": "myrepo"})

        assert response.status_code == 200
        # First exec call should be the git remote set-url
        scrub_call = mock_sandbox.exec.call_args_list[0]
        assert "git remote set-url" in scrub_call.args[0]
        assert "https://github.com/owner/repo.git" in scrub_call.args[0]

    def test_finish_404_when_devbox_not_found(self, authed_client, mock_user):
        """setup/finish returns 404 when devbox does not exist."""
        with (
            patch("druids_server.api.routes.setup.get_session", make_mock_session()),
            patch("druids_server.api.routes.setup.resolve_devbox", new_callable=AsyncMock, return_value=None),
        ):
            response = authed_client.post("/devbox/setup/finish", json={"name": "missing"})

        assert response.status_code == 404

    def test_finish_400_when_no_running_sandbox(self, authed_client, mock_user):
        """setup/finish returns 400 when devbox has no instance_id."""
        devbox = Devbox(
            id=uuid4(),
            user_id=mock_user.id,
            name="test",
            repo_full_name="",
            instance_id=None,
            snapshot_id="old-snap",
        )

        with (
            patch(
                "druids_server.api.routes.setup.get_session",
                make_mock_session(scalar_one_or_none=devbox, scalars_first=devbox),
            ),
            patch("druids_server.api.routes.setup.resolve_devbox", new_callable=AsyncMock, return_value=devbox),
        ):
            response = authed_client.post("/devbox/setup/finish", json={"name": "test"})

        assert response.status_code == 400

    def test_finish_stops_sandbox_even_on_snapshot_failure(self, authed_client, mock_user):
        """setup/finish stops the sandbox even if snapshot raises."""
        sandbox = MagicMock()
        sandbox.instance_id = "instance-abc"
        sandbox.exec = AsyncMock(return_value=MagicMock(ok=True))
        sandbox.stop = AsyncMock()
        sandbox.snapshot = AsyncMock(side_effect=RuntimeError("snapshot failed"))

        devbox = Devbox(
            id=uuid4(),
            user_id=mock_user.id,
            name="test",
            repo_full_name="",
            instance_id="instance-abc",
        )

        with (
            patch("druids_server.api.routes.setup.Sandbox.get", new_callable=AsyncMock, return_value=sandbox),
            patch(
                "druids_server.api.routes.setup.get_session",
                make_mock_session(scalar_one_or_none=devbox, scalars_first=devbox),
            ),
            patch("druids_server.api.routes.setup.resolve_devbox", new_callable=AsyncMock, return_value=devbox),
            pytest.raises(RuntimeError, match="snapshot failed"),
        ):
            authed_client.post("/devbox/setup/finish", json={"name": "test"})

        sandbox.stop.assert_awaited_once()
