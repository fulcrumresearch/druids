"""Tests for program API endpoints."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from druids_server.api.deps import Caller, get_caller
from druids_server.api.routes import router
from druids_server.db.models.user import User
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def mock_user():
    return User(id=uuid4(), github_id=12345)


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


def _make_program(user_id, source="async def program(ctx): pass"):
    """Build a mock Program."""
    from druids_server.db.models.program import hash_source

    p = MagicMock()
    p.id = uuid4()
    p.user_id = user_id
    p.source = source
    p.source_hash = hash_source(source)
    p.created_at = datetime.now(timezone.utc)
    return p


def test_list_programs_empty(client, mock_user):
    """GET /programs returns empty list when no programs exist."""
    with patch("druids_server.api.routes.programs.get_user_programs", new_callable=AsyncMock, return_value=[]):
        resp = client.get("/programs")

    assert resp.status_code == 200
    assert resp.json()["programs"] == []


def test_list_programs(client, mock_user):
    """GET /programs returns saved programs."""
    p = _make_program(mock_user.id)
    with patch("druids_server.api.routes.programs.get_user_programs", new_callable=AsyncMock, return_value=[p]):
        resp = client.get("/programs")

    assert resp.status_code == 200
    programs = resp.json()["programs"]
    assert len(programs) == 1
    assert programs[0]["id"] == str(p.id)
    assert programs[0]["source_hash"] == p.source_hash


def test_get_program(client, mock_user):
    """GET /programs/{id} returns program with source."""
    p = _make_program(mock_user.id)
    with patch("druids_server.api.routes.programs.get_program", new_callable=AsyncMock, return_value=p):
        resp = client.get(f"/programs/{p.id}")

    assert resp.status_code == 200
    data = resp.json()
    assert data["source"] == p.source
    assert data["id"] == str(p.id)


def test_get_program_not_found(client, mock_user):
    """GET /programs/{id} returns 404 when program does not exist."""
    with patch("druids_server.api.routes.programs.get_program", new_callable=AsyncMock, return_value=None):
        resp = client.get(f"/programs/{uuid4()}")

    assert resp.status_code == 404


def test_get_program_wrong_user(client, mock_user):
    """GET /programs/{id} returns 404 when program belongs to another user."""
    other_user_id = uuid4()
    p = _make_program(other_user_id)
    with patch("druids_server.api.routes.programs.get_program", new_callable=AsyncMock, return_value=p):
        resp = client.get(f"/programs/{p.id}")

    assert resp.status_code == 404
