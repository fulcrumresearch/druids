"""Tests for program model helpers."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from druids_server.db.models.program import get_or_create_program, hash_source


SAMPLE_SOURCE = 'async def program(ctx, spec=""):\n    pass\n'


def test_hash_source_deterministic():
    """Same source always produces the same hash."""
    assert hash_source(SAMPLE_SOURCE) == hash_source(SAMPLE_SOURCE)


def test_hash_source_differs_for_different_source():
    """Different source produces a different hash."""
    assert hash_source("a") != hash_source("b")


def _make_session(existing=None):
    """Build a mock AsyncSession for get_or_create_program."""
    result = MagicMock()
    result.scalar_one_or_none.return_value = existing

    db = MagicMock()
    db.execute = AsyncMock(return_value=result)
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.refresh = AsyncMock()
    return db


@pytest.mark.asyncio
async def test_get_or_create_program_creates_new():
    """When no matching program exists, a new one is created."""
    db = _make_session(existing=None)
    user_id = uuid4()

    program = await get_or_create_program(db, user_id, SAMPLE_SOURCE)

    db.add.assert_called_once()
    db.flush.assert_awaited_once()
    assert program.source == SAMPLE_SOURCE
    assert program.source_hash == hash_source(SAMPLE_SOURCE)
    assert program.user_id == user_id


@pytest.mark.asyncio
async def test_get_or_create_program_returns_existing():
    """When a matching program exists, it is returned without creating."""
    existing = MagicMock()
    existing.id = uuid4()
    db = _make_session(existing=existing)

    result = await get_or_create_program(db, uuid4(), SAMPLE_SOURCE)

    assert result is existing
    db.add.assert_not_called()
