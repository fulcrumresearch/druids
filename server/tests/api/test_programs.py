"""Tests for program API endpoints."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4


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


def test_list_programs_empty(authed_client, mock_user):
    """GET /programs returns empty list when no programs exist."""
    with patch("druids_server.api.routes.programs.get_user_programs", new_callable=AsyncMock, return_value=[]):
        resp = authed_client.get("/programs")

    assert resp.status_code == 200
    assert resp.json()["programs"] == []


def test_list_programs(authed_client, mock_user):
    """GET /programs returns saved programs."""
    p = _make_program(mock_user.id)
    with patch("druids_server.api.routes.programs.get_user_programs", new_callable=AsyncMock, return_value=[p]):
        resp = authed_client.get("/programs")

    assert resp.status_code == 200
    programs = resp.json()["programs"]
    assert len(programs) == 1
    assert programs[0]["id"] == str(p.id)
    assert programs[0]["source_hash"] == p.source_hash


def test_get_program(authed_client, mock_user):
    """GET /programs/{id} returns program with source."""
    p = _make_program(mock_user.id)
    with patch("druids_server.api.routes.programs.get_program", new_callable=AsyncMock, return_value=p):
        resp = authed_client.get(f"/programs/{p.id}")

    assert resp.status_code == 200
    data = resp.json()
    assert data["source"] == p.source
    assert data["id"] == str(p.id)


def test_get_program_not_found(authed_client, mock_user):
    """GET /programs/{id} returns 404 when program does not exist."""
    with patch("druids_server.api.routes.programs.get_program", new_callable=AsyncMock, return_value=None):
        resp = authed_client.get(f"/programs/{uuid4()}")

    assert resp.status_code == 404


def test_get_program_wrong_user(authed_client, mock_user):
    """GET /programs/{id} returns 404 when program belongs to another user."""
    other_user_id = uuid4()
    p = _make_program(other_user_id)
    with patch("druids_server.api.routes.programs.get_program", new_callable=AsyncMock, return_value=p):
        resp = authed_client.get(f"/programs/{p.id}")

    assert resp.status_code == 404
