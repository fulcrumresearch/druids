"""Tests for health endpoint."""

from __future__ import annotations


def test_health_check_returns_200(authed_client):
    """GET /health returns 200 OK."""
    resp = authed_client.get("/health")

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_health_check_no_auth(unauthed_client):
    """GET /health works without authentication."""
    resp = unauthed_client.get("/health")

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
