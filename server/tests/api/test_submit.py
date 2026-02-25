"""Tests for POST /executions/submit endpoint."""

from unittest.mock import AsyncMock, patch

from orpheus.api.routes.mcp import _extract_pr_number

from tests.api.conftest import SLUG


class TestSubmitExecution:
    def test_submit_marks_complete(self, client, mock_execution):
        """Submitting an execution calls ex.submit() and persists to DB."""
        with patch("orpheus.api.routes.mcp.update_execution", new_callable=AsyncMock) as mock_mark:
            with patch("orpheus.api.routes.mcp.get_session"):
                response = client.post(
                    "/executions/submit",
                    json={
                        "execution_slug": SLUG,
                        "pr_url": "https://github.com/user/repo/pull/42",
                        "summary": "Added the feature",
                    },
                )

                assert response.status_code == 200
                data = response.json()
                assert data["status"] == "submitted"
                assert data["execution_slug"] == SLUG

                mock_execution.submit.assert_called_once_with(
                    pr_url="https://github.com/user/repo/pull/42",
                    summary="Added the feature",
                )

                mock_mark.assert_called_once_with(
                    mock_mark.call_args[0][0],  # db session
                    mock_execution.id,
                    status="completed",
                    pr_number=42,
                    pr_url="https://github.com/user/repo/pull/42",
                    summary="Added the feature",
                )

    def test_submit_not_found(self, client):
        """Returns 404 for unknown execution slug."""
        response = client.post(
            "/executions/submit",
            json={
                "execution_slug": "nonexistent-slug",
            },
        )

        assert response.status_code == 404
        assert "not found" in response.json()["detail"]

    def test_submit_without_pr_url(self, client, mock_execution):
        """Submitting without PR info still marks as completed."""
        with patch("orpheus.api.routes.mcp.update_execution", new_callable=AsyncMock) as mock_mark:
            with patch("orpheus.api.routes.mcp.get_session"):
                response = client.post(
                    "/executions/submit",
                    json={
                        "execution_slug": SLUG,
                        "summary": "Did the thing",
                    },
                )

                assert response.status_code == 200

                mock_execution.submit.assert_called_once_with(
                    pr_url=None,
                    summary="Did the thing",
                )

                mock_mark.assert_called_once_with(
                    mock_mark.call_args[0][0],
                    mock_execution.id,
                    status="completed",
                    pr_number=None,
                    pr_url=None,
                    summary="Did the thing",
                )

    def test_submit_without_summary(self, client, mock_execution):
        """Submitting without summary is valid."""
        with patch("orpheus.api.routes.mcp.update_execution", new_callable=AsyncMock):
            with patch("orpheus.api.routes.mcp.get_session"):
                response = client.post(
                    "/executions/submit",
                    json={
                        "execution_slug": SLUG,
                        "pr_url": "https://github.com/user/repo/pull/7",
                    },
                )

                assert response.status_code == 200
                mock_execution.submit.assert_called_once_with(
                    pr_url="https://github.com/user/repo/pull/7",
                    summary=None,
                )

    def test_submit_after_resume(self, client, mock_execution):
        """Submitting after a resume (re-submit cycle) works."""
        # First submit
        with patch("orpheus.api.routes.mcp.update_execution", new_callable=AsyncMock):
            with patch("orpheus.api.routes.mcp.get_session"):
                response = client.post(
                    "/executions/submit",
                    json={
                        "execution_slug": SLUG,
                        "pr_url": "https://github.com/user/repo/pull/42",
                        "summary": "First attempt",
                    },
                )
                assert response.status_code == 200

        # Simulate resume (webhook would call this)
        mock_execution.submit.reset_mock()

        # Second submit after addressing feedback
        with patch("orpheus.api.routes.mcp.update_execution", new_callable=AsyncMock) as mock_mark:
            with patch("orpheus.api.routes.mcp.get_session"):
                response = client.post(
                    "/executions/submit",
                    json={
                        "execution_slug": SLUG,
                        "pr_url": "https://github.com/user/repo/pull/42",
                        "summary": "Addressed feedback",
                    },
                )

                assert response.status_code == 200
                assert response.json()["status"] == "submitted"

                mock_execution.submit.assert_called_once_with(
                    pr_url="https://github.com/user/repo/pull/42",
                    summary="Addressed feedback",
                )


class TestExtractPrNumber:
    def test_standard_url(self):
        assert _extract_pr_number("https://github.com/user/repo/pull/42") == 42

    def test_url_with_trailing_slash(self):
        assert _extract_pr_number("https://github.com/user/repo/pull/123/") == 123

    def test_url_with_extra_path(self):
        assert _extract_pr_number("https://github.com/user/repo/pull/7/files") == 7

    def test_no_pull_in_url(self):
        assert _extract_pr_number("https://github.com/user/repo/issues/42") is None

    def test_empty_string(self):
        assert _extract_pr_number("") is None
