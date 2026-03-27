"""Test RuntimeState functionality."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from druids_runtime import RuntimeContext, RuntimeState


@pytest.mark.asyncio
async def test_state_get_set():
    """RuntimeState should support get and set operations."""
    ctx = RuntimeContext(
        slug="test-slug",
        repo_full_name="test/repo",
        _base_url="http://localhost:8000",
        _token="test-token",
    )

    # Set values
    await ctx.state.set("key1", "value1")
    await ctx.state.set("key2", 42)
    await ctx.state.set("key3", {"nested": "data"})

    # Get values
    assert ctx.state.get("key1") == "value1"
    assert ctx.state.get("key2") == 42
    assert ctx.state.get("key3") == {"nested": "data"}


@pytest.mark.asyncio
async def test_state_get_default():
    """RuntimeState.get should return default for missing keys."""
    ctx = RuntimeContext(
        slug="test-slug",
        repo_full_name="test/repo",
        _base_url="http://localhost:8000",
        _token="test-token",
    )

    # Get with default
    assert ctx.state.get("missing") is None
    assert ctx.state.get("missing", "default") == "default"
    assert ctx.state.get("missing", 0) == 0


@pytest.mark.asyncio
async def test_state_all():
    """RuntimeState.all should return all values."""
    ctx = RuntimeContext(
        slug="test-slug",
        repo_full_name="test/repo",
        _base_url="http://localhost:8000",
        _token="test-token",
    )

    # Initially empty
    assert ctx.state.all() == {}

    # Add some values
    await ctx.state.set("a", 1)
    await ctx.state.set("b", 2)
    await ctx.state.set("c", 3)

    # Should return all values
    assert ctx.state.all() == {"a": 1, "b": 2, "c": 3}


@pytest.mark.asyncio
async def test_state_update():
    """RuntimeState should allow updating existing values."""
    ctx = RuntimeContext(
        slug="test-slug",
        repo_full_name="test/repo",
        _base_url="http://localhost:8000",
        _token="test-token",
    )

    # Set initial value
    await ctx.state.set("counter", 1)
    assert ctx.state.get("counter") == 1

    # Update value
    await ctx.state.set("counter", 2)
    assert ctx.state.get("counter") == 2

    # Update to different type
    await ctx.state.set("counter", "three")
    assert ctx.state.get("counter") == "three"


@pytest.mark.asyncio
async def test_state_emits_event():
    """RuntimeState.set should emit program_state events."""
    ctx = RuntimeContext(
        slug="test-slug",
        repo_full_name="test/repo",
        _base_url="http://localhost:8000",
        _token="test-token",
    )

    # Mock the _post method to track calls
    ctx._post = AsyncMock()

    # Set a value
    await ctx.state.set("test_key", "test_value")

    # Verify emit was called
    ctx._post.assert_called_once()
    call_args = ctx._post.call_args[0]
    assert call_args[0] == "/emit"
    assert call_args[1]["event"] == "program_state"
    assert call_args[1]["data"] == {"name": "test_key", "value": "test_value"}


@pytest.mark.asyncio
async def test_state_all_returns_copy():
    """RuntimeState.all should return a copy, not internal dict."""
    ctx = RuntimeContext(
        slug="test-slug",
        repo_full_name="test/repo",
        _base_url="http://localhost:8000",
        _token="test-token",
    )

    await ctx.state.set("key", "value")
    all_state = ctx.state.all()

    # Modifying returned dict should not affect internal state
    all_state["new_key"] = "new_value"
    assert "new_key" not in ctx.state.all()
    assert ctx.state.get("new_key") is None
