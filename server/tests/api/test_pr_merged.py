"""Tests for the PR merged webhook handler."""

import contextlib
import hashlib
import hmac
import json
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from orpheus.api.deps import get_executions_registry
from orpheus.api.routes import router
from orpheus.db.models.execution import ExecutionRecord
from orpheus.db.models.task import Task

from tests.api.conftest import make_mock_session


WEBHOOK_SECRET = "test-webhook-secret"


def _sign(payload: bytes, secret: str = WEBHOOK_SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()


def _post_webhook(client: TestClient, payload: dict, event_type: str) -> object:
    body = json.dumps(payload).encode()
    signature = _sign(body)
    return client.post(
        "/webhooks/github",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": event_type,
            "X-Hub-Signature-256": signature,
        },
    )


def _make_pr_merged_payload(
    pr_number: int = 42,
    repo_full_name: str = "user/repo",
    merged: bool = True,
) -> dict:
    return {
        "action": "closed",
        "pull_request": {
            "number": pr_number,
            "merged": merged,
            "html_url": f"https://github.com/{repo_full_name}/pull/{pr_number}",
        },
        "sender": {"login": "developer"},
        "repository": {"full_name": repo_full_name},
    }


def _make_execution_record(task_id, slug, pr_number=None, pr_url=None, program_name="claude", program_spec=None):
    record = MagicMock(spec=ExecutionRecord)
    record.id = uuid4()
    record.slug = slug
    record.task_id = task_id
    record.pr_number = pr_number
    record.pr_url = pr_url
    record.program_name = program_name
    record.program_spec = program_spec
    record.status = "completed"
    return record


@pytest.fixture
def app_fixture():
    app = FastAPI()
    app.include_router(router)
    registry = get_executions_registry()
    registry.clear()
    yield app
    registry.clear()


@pytest.fixture
def client(app_fixture):
    return TestClient(app_fixture)


class TestPrMerged:
    def test_non_orpheus_pr_ignored(self, client):
        """Merged PR not in our DB returns ignored."""
        payload = _make_pr_merged_payload()
        with (
            patch("orpheus.api.routes.webhooks.settings") as mock_settings,
            patch("orpheus.api.routes.webhooks.get_session", new=make_mock_session()),
            patch("orpheus.api.routes.webhooks.get_execution_by_pr", new_callable=AsyncMock, return_value=None),
        ):
            mock_settings.github_webhook_secret = MagicMock()
            mock_settings.github_webhook_secret.get_secret_value.return_value = WEBHOOK_SECRET
            mock_settings.github_app_slug = "test-app"
            response = _post_webhook(client, payload, "pull_request")

        assert response.status_code == 200
        assert response.json()["status"] == "ignored"
        assert response.json()["reason"] == "not an orpheus PR"

    def test_closed_without_merge_ignored(self, client):
        """Closed PR without merge (merged=false) is ignored."""
        payload = _make_pr_merged_payload(merged=False)
        with patch("orpheus.api.routes.webhooks.settings") as mock_settings:
            mock_settings.github_webhook_secret = MagicMock()
            mock_settings.github_webhook_secret.get_secret_value.return_value = WEBHOOK_SECRET
            mock_settings.github_app_slug = "test-app"
            response = _post_webhook(client, payload, "pull_request")

        assert response.status_code == 200
        assert response.json()["status"] == "ignored"

    def test_single_execution_no_comparison(self, client):
        """Task with only one execution: winner is set but no comparisons made."""
        task_id = uuid4()
        user_id = uuid4()
        winner = _make_execution_record(task_id, "winner-slug", pr_number=42, pr_url="https://github.com/u/r/pull/42")

        task = MagicMock(spec=Task)
        task.id = task_id
        task.user_id = user_id

        payload = _make_pr_merged_payload()
        with (
            patch("orpheus.api.routes.webhooks.settings") as mock_settings,
            patch("orpheus.api.routes.webhooks.get_session", new=make_mock_session()),
            patch("orpheus.api.routes.webhooks.get_execution_by_pr", new_callable=AsyncMock, return_value=winner),
            patch("orpheus.api.routes.webhooks.get_task", new_callable=AsyncMock, return_value=task),
            patch("orpheus.api.routes.webhooks.get_task_executions", new_callable=AsyncMock, return_value=[winner]),
            patch("orpheus.api.routes.webhooks.update_execution_outcome", new_callable=AsyncMock) as mock_outcome,
            patch("orpheus.api.routes.webhooks.record_comparison", new_callable=AsyncMock) as mock_record,
        ):
            mock_settings.github_webhook_secret = MagicMock()
            mock_settings.github_webhook_secret.get_secret_value.return_value = WEBHOOK_SECRET
            mock_settings.github_app_slug = "test-app"
            response = _post_webhook(client, payload, "pull_request")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "merged"
        assert data["comparisons"] == 0
        # Winner outcome is still set
        mock_outcome.assert_called_once()
        # No ELO comparison
        mock_record.assert_not_called()

    def test_multi_execution_sets_outcomes_and_updates_elo(self, client):
        """Task with multiple executions: winner is merged, losers are rejected, ELO is updated."""
        task_id = uuid4()
        user_id = uuid4()
        winner = _make_execution_record(task_id, "winner-slug", pr_number=42, pr_url="https://github.com/u/r/pull/42")
        loser1 = _make_execution_record(task_id, "loser1-slug", pr_number=43, pr_url="https://github.com/u/r/pull/43")
        loser2 = _make_execution_record(task_id, "loser2-slug", pr_number=44, pr_url="https://github.com/u/r/pull/44")

        task = MagicMock(spec=Task)
        task.id = task_id
        task.user_id = user_id

        payload = _make_pr_merged_payload()
        with (
            patch("orpheus.api.routes.webhooks.settings") as mock_settings,
            patch("orpheus.api.routes.webhooks.get_session", new=make_mock_session()),
            patch("orpheus.api.routes.webhooks.get_execution_by_pr", new_callable=AsyncMock, return_value=winner),
            patch("orpheus.api.routes.webhooks.get_task", new_callable=AsyncMock, return_value=task),
            patch(
                "orpheus.api.routes.webhooks.get_task_executions",
                new_callable=AsyncMock,
                return_value=[winner, loser1, loser2],
            ),
            patch("orpheus.api.routes.webhooks.update_execution_outcome", new_callable=AsyncMock) as mock_outcome,
            patch("orpheus.api.routes.webhooks.record_comparison", new_callable=AsyncMock) as mock_record,
            patch("orpheus.api.routes.webhooks.close_pull_request", new_callable=AsyncMock) as mock_close,
        ):
            mock_settings.github_webhook_secret = MagicMock()
            mock_settings.github_webhook_secret.get_secret_value.return_value = WEBHOOK_SECRET
            mock_settings.github_app_slug = "test-app"
            response = _post_webhook(client, payload, "pull_request")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "merged"
        assert data["comparisons"] == 2

        # 3 outcome calls: 1 winner + 2 losers
        assert mock_outcome.call_count == 3

        # ELO comparison called once with winner and list of losers
        mock_record.assert_called_once()
        # record_comparison(db, winner, losers) -- positional args
        args = mock_record.call_args[0]
        assert args[1] is winner
        assert len(args[2]) == 2

        # Sibling PRs closed
        assert mock_close.call_count == 2

    def test_sibling_without_pr_not_closed(self, client):
        """Siblings without a PR number are not closed on GitHub."""
        task_id = uuid4()
        user_id = uuid4()
        winner = _make_execution_record(task_id, "winner-slug", pr_number=42, pr_url="https://github.com/u/r/pull/42")
        # This sibling has no PR
        loser_no_pr = _make_execution_record(task_id, "loser-no-pr", pr_number=None, pr_url=None)

        task = MagicMock(spec=Task)
        task.id = task_id
        task.user_id = user_id

        payload = _make_pr_merged_payload()
        with (
            patch("orpheus.api.routes.webhooks.settings") as mock_settings,
            patch("orpheus.api.routes.webhooks.get_session", new=make_mock_session()),
            patch("orpheus.api.routes.webhooks.get_execution_by_pr", new_callable=AsyncMock, return_value=winner),
            patch("orpheus.api.routes.webhooks.get_task", new_callable=AsyncMock, return_value=task),
            patch(
                "orpheus.api.routes.webhooks.get_task_executions",
                new_callable=AsyncMock,
                return_value=[winner, loser_no_pr],
            ),
            patch("orpheus.api.routes.webhooks.update_execution_outcome", new_callable=AsyncMock),
            patch("orpheus.api.routes.webhooks.record_comparison", new_callable=AsyncMock),
            patch("orpheus.api.routes.webhooks.close_pull_request", new_callable=AsyncMock) as mock_close,
        ):
            mock_settings.github_webhook_secret = MagicMock()
            mock_settings.github_webhook_secret.get_secret_value.return_value = WEBHOOK_SECRET
            mock_settings.github_app_slug = "test-app"
            response = _post_webhook(client, payload, "pull_request")

        assert response.status_code == 200
        # No comparisons since the loser has no PR
        assert response.json()["comparisons"] == 0
        # close_pull_request never called (loser has no pr_url)
        mock_close.assert_not_called()

    def test_close_pr_failure_doesnt_break_handler(self, client):
        """If closing a sibling PR fails, the handler still succeeds."""
        task_id = uuid4()
        user_id = uuid4()
        winner = _make_execution_record(task_id, "winner-slug", pr_number=42, pr_url="https://github.com/u/r/pull/42")
        loser = _make_execution_record(task_id, "loser-slug", pr_number=43, pr_url="https://github.com/u/r/pull/43")

        task = MagicMock(spec=Task)
        task.id = task_id
        task.user_id = user_id

        payload = _make_pr_merged_payload()
        with (
            patch("orpheus.api.routes.webhooks.settings") as mock_settings,
            patch("orpheus.api.routes.webhooks.get_session", new=make_mock_session()),
            patch("orpheus.api.routes.webhooks.get_execution_by_pr", new_callable=AsyncMock, return_value=winner),
            patch("orpheus.api.routes.webhooks.get_task", new_callable=AsyncMock, return_value=task),
            patch(
                "orpheus.api.routes.webhooks.get_task_executions",
                new_callable=AsyncMock,
                return_value=[winner, loser],
            ),
            patch("orpheus.api.routes.webhooks.update_execution_outcome", new_callable=AsyncMock),
            patch("orpheus.api.routes.webhooks.record_comparison", new_callable=AsyncMock),
            patch(
                "orpheus.api.routes.webhooks.close_pull_request",
                new_callable=AsyncMock,
                side_effect=Exception("GitHub API error"),
            ),
        ):
            mock_settings.github_webhook_secret = MagicMock()
            mock_settings.github_webhook_secret.get_secret_value.return_value = WEBHOOK_SECRET
            mock_settings.github_app_slug = "test-app"
            response = _post_webhook(client, payload, "pull_request")

        # Handler still succeeds despite close failure
        assert response.status_code == 200
        assert response.json()["status"] == "merged"

    def test_stops_running_sibling_executions(self, client, app_fixture):
        """Running sibling executions are stopped when a PR is merged."""
        task_id = uuid4()
        user_id = uuid4()
        winner = _make_execution_record(task_id, "winner-slug", pr_number=42, pr_url="https://github.com/u/r/pull/42")
        loser = _make_execution_record(task_id, "loser-slug", pr_number=43, pr_url="https://github.com/u/r/pull/43")

        task = MagicMock(spec=Task)
        task.id = task_id
        task.user_id = user_id

        # Put loser execution in the registry
        mock_loser_ex = MagicMock()
        mock_loser_ex.stop = AsyncMock()
        registry = get_executions_registry()
        registry[str(user_id)] = {loser.slug: mock_loser_ex}

        payload = _make_pr_merged_payload()
        with (
            patch("orpheus.api.routes.webhooks.settings") as mock_settings,
            patch("orpheus.api.routes.webhooks.get_session", new=make_mock_session()),
            patch("orpheus.api.routes.webhooks.get_execution_by_pr", new_callable=AsyncMock, return_value=winner),
            patch("orpheus.api.routes.webhooks.get_task", new_callable=AsyncMock, return_value=task),
            patch(
                "orpheus.api.routes.webhooks.get_task_executions",
                new_callable=AsyncMock,
                return_value=[winner, loser],
            ),
            patch("orpheus.api.routes.webhooks.update_execution_outcome", new_callable=AsyncMock),
            patch("orpheus.api.routes.webhooks.record_comparison", new_callable=AsyncMock),
            patch("orpheus.api.routes.webhooks.close_pull_request", new_callable=AsyncMock),
        ):
            mock_settings.github_webhook_secret = MagicMock()
            mock_settings.github_webhook_secret.get_secret_value.return_value = WEBHOOK_SECRET
            mock_settings.github_app_slug = "test-app"
            response = _post_webhook(client, payload, "pull_request")

        assert response.status_code == 200
        mock_loser_ex.stop.assert_called_once()
