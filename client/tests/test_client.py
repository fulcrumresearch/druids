"""Tests for the HTTP client."""

from __future__ import annotations

import json

import httpx
import pytest
from druids.client import APIError, DruidsClient, NotFoundError
from druids.config import Config


def make_client(transport: httpx.MockTransport) -> DruidsClient:
    """Build a client with mock transport."""
    client = DruidsClient(config=Config(base_url="https://example.test"))
    client._client = httpx.Client(base_url=str(client.base_url), timeout=300, transport=transport)
    return client


def test_stop_execution_sends_patch_with_stopped_payload():
    """Stop should call PATCH /api/executions/{slug} with status=stopped."""
    seen: dict[str, str | dict[str, str]] = {}

    def handle(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content.decode())
        return httpx.Response(200, json={"status": "stopped", "execution_slug": "test-slug"})

    client = make_client(httpx.MockTransport(handle))
    result = client.stop_execution("test-slug")

    assert seen == {
        "method": "PATCH",
        "path": "/api/executions/test-slug",
        "body": {"status": "stopped"},
    }
    assert result == {"status": "stopped", "execution_slug": "test-slug"}


def test_stop_execution_raises_not_found_for_404():
    """404 should map to NotFoundError."""

    def handle(_: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="missing")

    client = make_client(httpx.MockTransport(handle))
    with pytest.raises(NotFoundError):
        client.stop_execution("missing-slug")


def test_stop_execution_raises_api_error_for_non_200():
    """Non-200 non-404 should map to APIError."""

    def handle(_: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    client = make_client(httpx.MockTransport(handle))
    with pytest.raises(APIError, match="boom"):
        client.stop_execution("bad-slug")


def test_send_agent_message_posts_text():
    """send_agent_message should POST to the agent message endpoint."""
    seen: dict[str, str | dict[str, str]] = {}

    def handle(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content.decode())
        return httpx.Response(200, json={"status": "sent"})

    client = make_client(httpx.MockTransport(handle))
    result = client.send_agent_message("my-exec", "builder", "hello agent")

    assert seen == {
        "method": "POST",
        "path": "/api/executions/my-exec/agents/builder/message",
        "body": {"text": "hello agent"},
    }
    assert result == {"status": "sent"}


def test_send_agent_message_raises_not_found_for_404():
    """404 should map to NotFoundError."""

    def handle(_: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="missing")

    client = make_client(httpx.MockTransport(handle))
    with pytest.raises(NotFoundError):
        client.send_agent_message("slug", "agent", "hi")
