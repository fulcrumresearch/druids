"""Tests for authentication."""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from orpheus.api.auth import AuthError, GitHubUser, get_github_user
from orpheus.api.deps import get_executions_registry
from orpheus.api.routes import router


# ---------------------------------------------------------------------------
# Auth service tests
# ---------------------------------------------------------------------------


class TestGetGitHubUser:
    @pytest.mark.asyncio
    async def test_valid_token(self):
        """Returns GitHubUser for valid token."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "id": 12345,
            "login": "testuser",
            "name": "Test User",
            "email": "test@example.com",
        }

        with patch("orpheus.api.auth.httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_class.return_value.__aenter__.return_value = mock_client

            user = await get_github_user("valid_token")

            assert user.id == 12345
            assert user.login == "testuser"
            assert user.name == "Test User"
            assert user.email == "test@example.com"

            mock_client.get.assert_called_once()
            call_args = mock_client.get.call_args
            assert call_args[0][0] == "https://api.github.com/user"
            assert "Bearer valid_token" in call_args[1]["headers"]["Authorization"]

    @pytest.mark.asyncio
    async def test_invalid_token(self):
        """Raises AuthError for invalid/revoked token."""
        mock_response = MagicMock()
        mock_response.status_code = 401

        with patch("orpheus.api.auth.httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_class.return_value.__aenter__.return_value = mock_client

            with pytest.raises(AuthError, match="Invalid or revoked token"):
                await get_github_user("bad_token")

    @pytest.mark.asyncio
    async def test_github_api_error(self):
        """Raises AuthError for GitHub API errors."""
        mock_response = MagicMock()
        mock_response.status_code = 500

        with patch("orpheus.api.auth.httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_class.return_value.__aenter__.return_value = mock_client

            with pytest.raises(AuthError, match="GitHub API error: 500"):
                await get_github_user("token")


# ---------------------------------------------------------------------------
# /me endpoint tests
# ---------------------------------------------------------------------------


class TestMeEndpoint:
    def test_me_authenticated(self, app_with_auth, mock_user):
        """Returns user info when authenticated."""
        app, user = app_with_auth

        # Override the get_current_user dependency
        from orpheus.api.deps import get_current_user

        app.dependency_overrides[get_current_user] = lambda: user
        client = TestClient(app)

        response = client.get("/me")

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == str(user.id)
        assert data["github_id"] == user.github_id

        app.dependency_overrides.clear()

    def test_me_unauthenticated(self, app_with_auth):
        """Returns 401 when not authenticated."""
        app, _ = app_with_auth
        client = TestClient(app)

        # No token provided
        response = client.get("/me")

        assert response.status_code == 401
        assert response.json()["detail"] == "Not authenticated"


# ---------------------------------------------------------------------------
# Protected route tests
# ---------------------------------------------------------------------------


class TestProtectedRoutes:
    """Tests that MCP routes require authentication."""

    @pytest.fixture
    def app(self):
        """Create test app without auth override."""
        app = FastAPI()
        app.include_router(router)
        return app

    @pytest.fixture
    def client(self, app):
        """Create test client."""
        return TestClient(app)

    def test_send_message_requires_auth(self, client):
        """POST /messages/send returns 401 without auth."""
        response = client.post(
            "/messages/send",
            json={
                "execution_slug": "exec-123",
                "sender": "a",
                "receiver": "b",
                "message": "hello",
            },
        )
        assert response.status_code == 401

    def test_download_file_requires_auth(self, client):
        """GET /files/download returns 401 without auth."""
        response = client.get("/files/download", params={"path": "/test", "repo": "owner/repo"})
        assert response.status_code == 401

    def test_upload_file_requires_auth(self, client):
        """POST /files/upload returns 401 without auth."""
        response = client.post(
            "/files/upload",
            params={"path": "/test", "repo": "owner/repo"},
            files={"file": ("test.txt", b"test content")},
        )
        assert response.status_code == 401

    def test_remote_exec_requires_auth(self, client):
        """POST /remote-exec returns 401 without auth."""
        response = client.post(
            "/remote-exec",
            json={"repo": "owner/repo", "command": "echo hello"},
        )
        assert response.status_code == 401

    def test_spawn_requires_auth(self, client):
        """POST /spawn returns 401 without auth."""
        response = client.post(
            "/spawn",
            json={
                "execution_slug": "exec-123",
                "sender": "orchestrator",
                "constructor_name": "workers",
            },
        )
        assert response.status_code == 401

    def test_programs_requires_auth(self, client):
        """POST /programs returns 401 without auth."""
        response = client.post(
            "/programs",
            json={"execution_id": "exec-123"},
        )
        assert response.status_code == 401

    def test_stop_agent_requires_auth(self, client):
        """POST /agents/stop returns 401 without auth."""
        response = client.post(
            "/agents/stop",
            json={"execution_slug": "exec-123", "agent_name": "worker"},
        )
        assert response.status_code == 401

    def test_agents_ssh_requires_auth(self, client):
        """POST /agents/ssh returns 401 without auth."""
        response = client.post(
            "/agents/ssh",
            json={"execution_slug": "exec-123", "agent_name": "worker"},
        )
        assert response.status_code == 401


# ---------------------------------------------------------------------------
# User-scoped execution tests
# ---------------------------------------------------------------------------


class TestUserScopedExecutions:
    """Tests that users can only access their own executions."""

    def test_user_cannot_see_other_user_executions(self, app_with_auth, mock_user):
        """User cannot access executions belonging to another user."""
        app, user = app_with_auth

        # Create execution under a different user
        registry = get_executions_registry()
        other_user_id = str(uuid4())
        registry[other_user_id] = {}

        mock_execution = MagicMock()
        mock_execution.id = "exec-other"
        mock_execution.programs = {}
        registry[other_user_id]["exec-other"] = mock_execution

        # Override auth to return our user
        from orpheus.api.deps import get_current_user

        app.dependency_overrides[get_current_user] = lambda: user
        client = TestClient(app)

        # Try to access the other user's execution
        response = client.post(
            "/programs",
            json={"execution_slug": "exec-other"},
        )

        # Should return 404 because execution is not in user's scope
        assert response.status_code == 404
        assert "not found" in response.json()["detail"]

        app.dependency_overrides.clear()
