"""Tests for Anthropic proxy endpoint."""

from __future__ import annotations

from unittest.mock import MagicMock, patch
from uuid import uuid4

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from orpheus.api.deps import get_executions_registry
from orpheus.api.routes import router
from orpheus.config import settings
from orpheus.lib.forwarding_tokens import mint_token
from orpheus.db.models.user import User


def _setup_registry():
    """Seed registry with a fake execution, return (user_id, slug)."""
    registry = get_executions_registry()
    registry.clear()
    user = User(id=uuid4(), github_id=12345, access_token="token")
    execution = MagicMock()
    execution.slug = "exec-proxy"
    execution.programs = {"claude": MagicMock()}
    registry[str(user.id)] = {execution.slug: execution}
    return str(user.id), execution.slug


def test_proxy_forwards_request():
    app = FastAPI()
    app.include_router(router)
    user_id, slug = _setup_registry()
    token = mint_token(user_id, slug, "claude")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("x-api-key") == settings.anthropic_api_key.get_secret_value()
        assert "authorization" not in request.headers
        return httpx.Response(200, json={"ok": True}, headers={"content-type": "application/json"})

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with patch("orpheus.api.routes.proxy._client", mock_client):
        resp = TestClient(app).post("/proxy/anthropic/v1/messages", headers={"x-api-key": token}, json={"foo": "bar"})

    get_executions_registry().clear()
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_proxy_rejects_missing_token():
    app = FastAPI()
    app.include_router(router)
    resp = TestClient(app).post("/proxy/anthropic/v1/messages", json={"foo": "bar"})
    assert resp.status_code == 401


def test_proxy_rejects_execution_mismatch():
    app = FastAPI()
    app.include_router(router)
    user_id, _slug = _setup_registry()
    token = mint_token(user_id, "wrong-slug", "claude")
    resp = TestClient(app).post("/proxy/anthropic/v1/messages", headers={"x-api-key": token}, json={})
    get_executions_registry().clear()
    assert resp.status_code == 403
