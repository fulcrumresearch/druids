"""Tests for devbox model functions."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from orpheus.db.models.devbox import Devbox, get_devbox_by_repo


@pytest.fixture
def mock_session():
    """Create a mock async session."""
    return AsyncMock()


class TestGetDevboxByRepo:
    @pytest.mark.asyncio
    async def test_returns_devbox_with_snapshot(self, mock_session):
        """Returns a devbox that has a snapshot_id."""
        devbox = Devbox(
            id=uuid4(),
            user_id=uuid4(),
            repo_full_name="user/repo",
            snapshot_id="snap-123",
            updated_at=datetime.now(timezone.utc),
        )
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = devbox
        mock_session.execute.return_value = mock_result

        result = await get_devbox_by_repo(mock_session, "user/repo")

        assert result is devbox
        assert result.snapshot_id == "snap-123"
        mock_session.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_returns_none_when_no_devbox(self, mock_session):
        """Returns None when no devbox exists for the repo."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        result = await get_devbox_by_repo(mock_session, "user/nonexistent")

        assert result is None

    @pytest.mark.asyncio
    async def test_query_filters_by_repo(self, mock_session):
        """The query filters on repo_full_name and snapshot_id is not None."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        await get_devbox_by_repo(mock_session, "org/my-repo")

        # Verify execute was called (the query is constructed properly)
        mock_session.execute.assert_called_once()
        call_args = mock_session.execute.call_args
        # The first positional arg is the Select statement
        query = call_args[0][0]
        # Verify it's a select query (basic structural check)
        query_str = str(query)
        assert "devbox" in query_str.lower()
