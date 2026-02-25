"""Tests for admin usage dashboard."""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from orpheus.api.routes import router
from orpheus.db.models.user import User


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    return app


def _mock_db_user(login: str = "testuser", github_id: int = 12345) -> User:
    return User(id=uuid4(), github_id=github_id, github_login=login, access_token="test_token")


def _patch_auth(db_user: User):
    """Patch get_session and get_user_by_token so the user is authenticated."""
    mock_db = MagicMock()
    mock_db.flush = AsyncMock()
    mock_db.refresh = AsyncMock()

    async def mock_get_user_by_token(db, token):
        return db_user

    @asynccontextmanager
    async def mock_session():
        yield mock_db

    return [
        patch("orpheus.api.deps.get_session", return_value=mock_session()),
        patch("orpheus.api.deps.get_user_by_token", side_effect=mock_get_user_by_token),
    ]


def _mock_admin_db():
    """Create a mock session that returns plausible aggregation results for GET /admin/usage."""
    mock_db = MagicMock()
    mock_db.flush = AsyncMock()
    mock_db.refresh = AsyncMock()

    call_count = 0

    async def mock_execute(stmt):
        nonlocal call_count
        call_count += 1
        result = MagicMock()

        # Queries execute in order:
        # 1. COUNT(*) users total
        # 2. COUNT(*) users subscribed
        # 3. COUNT(DISTINCT repo) devboxes
        # 4. COUNT(*) tasks
        # 5. COUNT(*) GROUP BY status executions
        # 6. SUM tokens
        # 7. recent executions
        if call_count <= 4:
            result.scalar_one.return_value = call_count  # arbitrary counts
        elif call_count == 5:
            result.all.return_value = [("completed", 3), ("running", 1)]
        elif call_count == 6:
            row = MagicMock()
            row.__getitem__ = lambda self, i: [100, 50, 30, 20][i]
            result.one.return_value = row
        elif call_count == 7:
            result.all.return_value = []

        return result

    mock_db.execute = AsyncMock(side_effect=mock_execute)
    return mock_db


class _multi_patch:
    """Combine multiple unittest.mock.patch objects into one context manager."""

    def __init__(self, patches):
        self.patches = patches

    def __enter__(self):
        self.mocks = [p.__enter__() for p in self.patches]
        return self.mocks

    def __exit__(self, *args):
        for p in reversed(self.patches):
            p.__exit__(*args)


class TestAdminUsageAuth:
    """Access control for GET /admin/usage."""

    def test_unauthenticated_returns_401(self):
        app = _make_app()
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/admin/usage")
        assert response.status_code == 401

    def test_non_admin_returns_403(self):
        app = _make_app()
        db_user = _mock_db_user(login="regularuser")
        auth_patches = _patch_auth(db_user)

        with _multi_patch(auth_patches):
            with patch("orpheus.api.deps.settings") as mock_settings:
                mock_settings.github_allowed_users = None
                mock_settings.admin_users = {"kaivuh", "uzay-g", "lenishor"}
                client = TestClient(app, raise_server_exceptions=False)
                response = client.get("/admin/usage", headers={"Authorization": "Bearer test_token"})

        assert response.status_code == 403
        assert "admin" in response.json()["detail"].lower()

    def test_admin_returns_200(self):
        app = _make_app()
        db_user = _mock_db_user(login="kaivuh")
        auth_patches = _patch_auth(db_user)
        mock_db = _mock_admin_db()

        @asynccontextmanager
        async def mock_admin_session():
            yield mock_db

        with _multi_patch(auth_patches):
            with patch("orpheus.api.deps.settings") as mock_settings:
                mock_settings.github_allowed_users = None
                mock_settings.admin_users = {"kaivuh", "uzay-g", "lenishor"}
                with patch("orpheus.api.routes.admin.get_session", return_value=mock_admin_session()):
                    client = TestClient(app, raise_server_exceptions=False)
                    response = client.get("/admin/usage", headers={"Authorization": "Bearer test_token"})

        assert response.status_code == 200
        data = response.json()
        assert "users" in data
        assert "tokens" in data
        assert "recent_executions" in data
        assert "executions" in data
        assert data["executions"]["by_status"]["completed"] == 3


class TestMeIsAdmin:
    """The /me endpoint includes is_admin field."""

    def test_admin_user_has_is_admin_true(self):
        app = _make_app()
        db_user = _mock_db_user(login="kaivuh")
        auth_patches = _patch_auth(db_user)

        with _multi_patch(auth_patches):
            with patch("orpheus.api.deps.settings") as mock_settings:
                mock_settings.github_allowed_users = None
                mock_settings.admin_users = {"kaivuh", "uzay-g", "lenishor"}
                mock_settings.free_tier_reviews = 15
                mock_settings.github_app_slug = "orpheus-dev"
                with patch("orpheus.api.routes.auth.get_user_execution_count", new_callable=AsyncMock, return_value=0):
                    client = TestClient(app, raise_server_exceptions=False)
                    response = client.get("/me", headers={"Authorization": "Bearer test_token"})

        assert response.status_code == 200
        assert response.json()["is_admin"] is True

    def test_regular_user_has_is_admin_false(self):
        app = _make_app()
        db_user = _mock_db_user(login="regularuser")
        auth_patches = _patch_auth(db_user)

        with _multi_patch(auth_patches):
            with patch("orpheus.api.deps.settings") as mock_settings:
                mock_settings.github_allowed_users = None
                mock_settings.admin_users = {"kaivuh", "uzay-g", "lenishor"}
                mock_settings.free_tier_reviews = 15
                mock_settings.github_app_slug = "orpheus-dev"
                with patch("orpheus.api.routes.auth.get_user_execution_count", new_callable=AsyncMock, return_value=0):
                    client = TestClient(app, raise_server_exceptions=False)
                    response = client.get("/me", headers={"Authorization": "Bearer test_token"})

        assert response.status_code == 200
        assert response.json()["is_admin"] is False

    def test_empty_admin_list_means_no_admins(self):
        app = _make_app()
        db_user = _mock_db_user(login="kaivuh")
        auth_patches = _patch_auth(db_user)

        with _multi_patch(auth_patches):
            with patch("orpheus.api.deps.settings") as mock_settings:
                mock_settings.github_allowed_users = None
                mock_settings.admin_users = set()
                mock_settings.free_tier_reviews = 15
                mock_settings.github_app_slug = "orpheus-dev"
                with patch("orpheus.api.routes.auth.get_user_execution_count", new_callable=AsyncMock, return_value=0):
                    client = TestClient(app, raise_server_exceptions=False)
                    response = client.get("/me", headers={"Authorization": "Bearer test_token"})

        assert response.status_code == 200
        assert response.json()["is_admin"] is False
