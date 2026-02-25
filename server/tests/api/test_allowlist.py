"""Tests for GitHub user allowlist."""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from orpheus.api.auth import GitHubUser
from orpheus.api.routes import router
from orpheus.db.models.user import User


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    return app


def _mock_github_user(login: str = "testuser", github_id: int = 12345) -> GitHubUser:
    return GitHubUser(id=github_id, login=login, name="Test User", email="test@example.com")


def _mock_db_user(login: str | None = "testuser", github_id: int = 12345) -> User:
    return User(id=uuid4(), github_id=github_id, github_login=login, access_token="test_token")


def _patch_db_and_github(db_user: User | None, github_user: GitHubUser | None = None):
    """Return a context manager that patches get_session and get_github_user.

    When db_user is provided, get_user_by_token returns it (simulating a cached token).
    When github_user is provided, get_github_user returns it (simulating a fresh GitHub validation).
    """
    mock_db = MagicMock()
    mock_db.flush = AsyncMock()
    mock_db.refresh = AsyncMock()

    async def mock_get_user_by_token(db, token):
        return db_user

    async def mock_get_or_create_user(db, github_id, access_token, github_login=None):
        return _mock_db_user(login=github_login, github_id=github_id)

    patches = [
        patch("orpheus.api.deps.get_session", return_value=_async_cm(mock_db)),
        patch("orpheus.api.deps.get_user_by_token", side_effect=mock_get_user_by_token),
        patch("orpheus.api.deps.get_or_create_user", side_effect=mock_get_or_create_user),
    ]

    if github_user is not None:
        patches.append(patch("orpheus.api.deps.get_github_user", AsyncMock(return_value=github_user)))
    else:
        # If no github_user, make get_github_user raise AuthError by default
        from orpheus.api.auth import AuthError

        patches.append(patch("orpheus.api.deps.get_github_user", AsyncMock(side_effect=AuthError("no mock"))))

    return _multi_patch(patches)


class _async_cm:
    """Simple async context manager wrapping a value."""

    def __init__(self, val):
        self.val = val

    async def __aenter__(self):
        return self.val

    async def __aexit__(self, *args):
        pass


class _multi_patch:
    """Combine multiple unittest.mock.patch objects into one context manager."""

    def __init__(self, patches):
        self.patches = patches
        self.mocks = []

    def __enter__(self):
        self.mocks = [p.__enter__() for p in self.patches]
        return self.mocks

    def __exit__(self, *args):
        for p in reversed(self.patches):
            p.__exit__(*args)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestAllowlistUserOnList:
    """User on allowlist can authenticate (200 on /me)."""

    def test_user_on_allowlist_gets_200(self):
        app = _make_app()
        github_user = _mock_github_user(login="alice")
        db_user = _mock_db_user(login="alice")

        with _patch_db_and_github(db_user=db_user, github_user=github_user):
            with patch("orpheus.api.deps.settings") as mock_settings:
                mock_settings.github_allowed_users = {"alice"}
                client = TestClient(app)
                response = client.get("/me", headers={"Authorization": "Bearer test_token"})

        assert response.status_code == 200


class TestAllowlistUserNotOnList:
    """User NOT on allowlist gets 403 on /me."""

    def test_user_not_on_allowlist_gets_403(self):
        app = _make_app()
        db_user = _mock_db_user(login="eve")

        with _patch_db_and_github(db_user=db_user):
            with patch("orpheus.api.deps.settings") as mock_settings:
                mock_settings.github_allowed_users = {"alice", "bob"}
                client = TestClient(app)
                response = client.get("/me", headers={"Authorization": "Bearer test_token"})

        assert response.status_code == 403
        assert "allowlist" in response.json()["detail"].lower()


class TestAllowlistCaseInsensitive:
    """Case insensitivity: allowlist has 'alice', user login is 'Alice'."""

    def test_case_insensitive_match(self):
        app = _make_app()
        # DB stores the login as returned by GitHub (mixed case)
        db_user = _mock_db_user(login="Alice")

        with _patch_db_and_github(db_user=db_user):
            with patch("orpheus.api.deps.settings") as mock_settings:
                # Allowlist is lowercased by the config validator
                mock_settings.github_allowed_users = {"alice"}
                client = TestClient(app)
                response = client.get("/me", headers={"Authorization": "Bearer test_token"})

        assert response.status_code == 200


class TestAllowlistUnsetAllowsEveryone:
    """Empty/unset allowlist allows everyone (existing behavior)."""

    def test_none_allowlist_allows_everyone(self):
        app = _make_app()
        db_user = _mock_db_user(login="anyone")

        with _patch_db_and_github(db_user=db_user):
            with patch("orpheus.api.deps.settings") as mock_settings:
                mock_settings.github_allowed_users = None
                client = TestClient(app)
                response = client.get("/me", headers={"Authorization": "Bearer test_token"})

        assert response.status_code == 200


class TestAllowlistCachedTokenRejection:
    """Cached token user not on allowlist still gets 403."""

    def test_cached_user_not_on_allowlist_gets_403(self):
        """A user whose token is already in the DB but whose login is not on the allowlist gets 403."""
        app = _make_app()
        # User has a cached token in DB with login stored
        db_user = _mock_db_user(login="eve")

        with _patch_db_and_github(db_user=db_user):
            with patch("orpheus.api.deps.settings") as mock_settings:
                mock_settings.github_allowed_users = {"alice"}
                client = TestClient(app)
                response = client.get("/me", headers={"Authorization": "Bearer test_token"})

        assert response.status_code == 403

    def test_cached_user_without_login_refetches_from_github(self):
        """A cached user with no stored github_login triggers a GitHub API call to resolve the login."""
        app = _make_app()
        # User in DB but without github_login (pre-migration user)
        db_user = _mock_db_user(login=None)
        github_user = _mock_github_user(login="alice")

        with _patch_db_and_github(db_user=db_user, github_user=github_user):
            with patch("orpheus.api.deps.settings") as mock_settings:
                mock_settings.github_allowed_users = {"alice"}
                client = TestClient(app)
                response = client.get("/me", headers={"Authorization": "Bearer test_token"})

        assert response.status_code == 200

    def test_cached_user_without_login_not_on_allowlist_gets_403(self):
        """A cached user with no stored login, whose GitHub login is not on the allowlist, gets 403."""
        app = _make_app()
        db_user = _mock_db_user(login=None)
        github_user = _mock_github_user(login="eve")

        with _patch_db_and_github(db_user=db_user, github_user=github_user):
            with patch("orpheus.api.deps.settings") as mock_settings:
                mock_settings.github_allowed_users = {"alice"}
                client = TestClient(app)
                response = client.get("/me", headers={"Authorization": "Bearer test_token"})

        assert response.status_code == 403


class TestAllowlistNewUser:
    """New user (not in DB) checked against allowlist."""

    def test_new_user_on_allowlist(self):
        app = _make_app()
        github_user = _mock_github_user(login="bob")

        with _patch_db_and_github(db_user=None, github_user=github_user):
            with patch("orpheus.api.deps.settings") as mock_settings:
                mock_settings.github_allowed_users = {"bob"}
                client = TestClient(app)
                response = client.get("/me", headers={"Authorization": "Bearer new_token"})

        assert response.status_code == 200

    def test_new_user_not_on_allowlist(self):
        app = _make_app()
        github_user = _mock_github_user(login="eve")

        with _patch_db_and_github(db_user=None, github_user=github_user):
            with patch("orpheus.api.deps.settings") as mock_settings:
                mock_settings.github_allowed_users = {"alice"}
                client = TestClient(app)
                response = client.get("/me", headers={"Authorization": "Bearer new_token"})

        assert response.status_code == 403
