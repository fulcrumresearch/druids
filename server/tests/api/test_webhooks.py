"""Tests for POST /webhooks/github endpoint."""

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
    """Compute X-Hub-Signature-256 for a payload."""
    return "sha256=" + hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()


def _make_issue_comment_payload(
    pr_number: int = 42,
    repo_full_name: str = "user/repo",
    commenter: str = "reviewer",
    body: str = "Please fix the typo",
    sender_type: str = "User",
    is_pr: bool = True,
) -> dict:
    """Build a GitHub issue_comment.created webhook payload."""
    issue = {"number": pr_number}
    if is_pr:
        issue["pull_request"] = {"url": f"https://api.github.com/repos/{repo_full_name}/pulls/{pr_number}"}
    return {
        "action": "created",
        "issue": issue,
        "comment": {"body": body},
        "sender": {"login": commenter, "type": sender_type},
        "repository": {"full_name": repo_full_name},
    }


def _make_review_payload(
    pr_number: int = 42,
    repo_full_name: str = "user/repo",
    commenter: str = "reviewer",
    body: str = "Needs work",
    state: str = "CHANGES_REQUESTED",
    sender_type: str = "User",
) -> dict:
    """Build a GitHub pull_request_review.submitted webhook payload."""
    return {
        "action": "submitted",
        "review": {
            "user": {"login": commenter, "type": sender_type},
            "body": body,
            "state": state,
        },
        "pull_request": {"number": pr_number},
        "sender": {"login": commenter, "type": sender_type},
        "repository": {"full_name": repo_full_name},
    }


def _post_webhook(client: TestClient, payload: dict, event_type: str, secret: str = WEBHOOK_SECRET) -> object:
    """Send a signed webhook request."""
    body = json.dumps(payload).encode()
    signature = _sign(body, secret)
    return client.post(
        "/webhooks/github",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": event_type,
            "X-Hub-Signature-256": signature,
        },
    )


def _patch_settings_and_db(mock_record, mock_task):
    """Return a combined patch context for settings, get_session, get_execution_by_pr, get_task, and update_execution."""

    @contextlib.contextmanager
    def combined():
        with (
            patch("orpheus.api.routes.webhooks.settings") as mock_settings,
            patch("orpheus.api.routes.webhooks.get_session", new=make_mock_session()),
            patch("orpheus.api.routes.webhooks.get_execution_by_pr", new_callable=AsyncMock, return_value=mock_record),
            patch("orpheus.api.routes.webhooks.get_task", new_callable=AsyncMock, return_value=mock_task),
            patch("orpheus.api.routes.webhooks.update_execution", new_callable=AsyncMock),
            patch("orpheus.api.routes.webhooks.execution_trace"),
        ):
            mock_settings.github_webhook_secret = MagicMock()
            mock_settings.github_webhook_secret.get_secret_value.return_value = WEBHOOK_SECRET
            mock_settings.github_app_slug = "test-app"
            yield mock_settings

    return combined()


@pytest.fixture
def mock_execution():
    """Create a mock Execution with connections and prompt support."""
    ex = MagicMock()
    ex.id = uuid4()
    ex.slug = "gentle-nocturne-claude"
    ex.user_id = "test-user-id"
    ex.root = MagicMock()
    ex.root.name = "claude"
    ex.connections = {"claude": MagicMock()}
    ex.resume = MagicMock()
    ex.prompt = AsyncMock()
    return ex


@pytest.fixture
def mock_task():
    """Create a mock Task."""
    task = MagicMock(spec=Task)
    task.id = uuid4()
    task.user_id = uuid4()
    return task


@pytest.fixture
def mock_record(mock_task):
    """Create a mock ExecutionRecord."""
    record = MagicMock(spec=ExecutionRecord)
    record.id = uuid4()
    record.slug = "gentle-nocturne-claude"
    record.task_id = mock_task.id
    return record


@pytest.fixture
def app_with_webhook(mock_execution, mock_task):
    """Create test app with webhook secret configured and execution in registry."""
    app = FastAPI()
    app.include_router(router)

    user_id_str = str(mock_task.user_id)
    registry = get_executions_registry()
    registry.clear()
    registry[user_id_str] = {mock_execution.slug: mock_execution}

    yield app

    registry.clear()


@pytest.fixture
def client(app_with_webhook):
    return TestClient(app_with_webhook)


class TestWebhookSignature:
    def test_invalid_signature_returns_401(self, client):
        """Invalid HMAC signature returns 401."""
        payload = _make_issue_comment_payload()
        body = json.dumps(payload).encode()
        with patch("orpheus.api.routes.webhooks.settings") as mock_settings:
            mock_settings.github_webhook_secret = MagicMock()
            mock_settings.github_webhook_secret.get_secret_value.return_value = WEBHOOK_SECRET
            response = client.post(
                "/webhooks/github",
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-GitHub-Event": "issue_comment",
                    "X-Hub-Signature-256": "sha256=invalid",
                },
            )
        assert response.status_code == 401

    def test_webhook_disabled_without_secret(self, client):
        """Returns 501 when github_webhook_secret is None."""
        payload = _make_issue_comment_payload()
        body = json.dumps(payload).encode()
        with patch("orpheus.api.routes.webhooks.settings") as mock_settings:
            mock_settings.github_webhook_secret = None
            response = client.post(
                "/webhooks/github",
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-GitHub-Event": "issue_comment",
                    "X-Hub-Signature-256": "sha256=whatever",
                },
            )
        assert response.status_code == 501


class TestIssueComment:
    def test_delivers_to_agent(self, client, mock_execution, mock_record, mock_task):
        """PR comment is delivered to the agent as a prompt."""
        payload = _make_issue_comment_payload()
        with _patch_settings_and_db(mock_record, mock_task):
            response = _post_webhook(client, payload, "issue_comment")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "delivered"
        assert data["execution_slug"] == "gentle-nocturne-claude"
        mock_execution.resume.assert_called_once()

    def test_non_pr_without_mention_ignored(self, client):
        """Comment on a regular issue without bot mention returns ignored."""
        payload = _make_issue_comment_payload(is_pr=False)
        with patch("orpheus.api.routes.webhooks.settings") as mock_settings:
            mock_settings.github_webhook_secret = MagicMock()
            mock_settings.github_webhook_secret.get_secret_value.return_value = WEBHOOK_SECRET
            mock_settings.github_app_slug = "test-app"
            response = _post_webhook(client, payload, "issue_comment")

        assert response.status_code == 200
        assert response.json()["status"] == "ignored"
        assert response.json()["reason"] == "bot not mentioned"

    def test_skips_own_bot_comment(self, client):
        """Own bot comments are ignored to avoid loops."""
        payload = _make_issue_comment_payload(commenter="test-app[bot]")
        with patch("orpheus.api.routes.webhooks.settings") as mock_settings:
            mock_settings.github_webhook_secret = MagicMock()
            mock_settings.github_webhook_secret.get_secret_value.return_value = WEBHOOK_SECRET
            mock_settings.github_app_slug = "test-app"
            response = _post_webhook(client, payload, "issue_comment")

        assert response.status_code == 200
        assert response.json()["status"] == "ignored"
        assert response.json()["reason"] == "own bot comment"

    def test_unknown_pr_returns_ignored(self, client):
        """PR not in DB returns ignored."""
        payload = _make_issue_comment_payload()
        with (
            patch("orpheus.api.routes.webhooks.settings") as mock_settings,
            patch("orpheus.api.routes.webhooks.get_session", new=make_mock_session()),
            patch("orpheus.api.routes.webhooks.get_execution_by_pr", new_callable=AsyncMock, return_value=None),
            patch("orpheus.api.routes.webhooks.execution_trace"),
        ):
            mock_settings.github_webhook_secret = MagicMock()
            mock_settings.github_webhook_secret.get_secret_value.return_value = WEBHOOK_SECRET
            mock_settings.github_app_slug = "test-app"
            response = _post_webhook(client, payload, "issue_comment")

        assert response.status_code == 200
        assert response.json()["status"] == "ignored"
        assert response.json()["reason"] == "no matching execution"

    def test_execution_not_in_registry(self, client, mock_record, mock_task):
        """DB record exists but runtime execution is gone (server restarted)."""
        # Clear the registry so execution won't be found
        registry = get_executions_registry()
        registry.clear()
        registry[str(mock_task.user_id)] = {}

        payload = _make_issue_comment_payload()
        with (
            patch("orpheus.api.routes.webhooks.settings") as mock_settings,
            patch("orpheus.api.routes.webhooks.get_session", new=make_mock_session()),
            patch("orpheus.api.routes.webhooks.get_execution_by_pr", new_callable=AsyncMock, return_value=mock_record),
            patch("orpheus.api.routes.webhooks.get_task", new_callable=AsyncMock, return_value=mock_task),
            patch("orpheus.api.routes.webhooks.update_execution", new_callable=AsyncMock),
            patch("orpheus.api.routes.webhooks.execution_trace"),
        ):
            mock_settings.github_webhook_secret = MagicMock()
            mock_settings.github_webhook_secret.get_secret_value.return_value = WEBHOOK_SECRET
            mock_settings.github_app_slug = "test-app"
            response = _post_webhook(client, payload, "issue_comment")

        assert response.status_code == 200
        assert response.json()["status"] == "error"
        assert response.json()["reason"] == "execution not in memory"


class TestPullRequestReview:
    def test_changes_requested_delivers(self, client, mock_execution, mock_record, mock_task):
        """Review with changes_requested state delivers to agent."""
        payload = _make_review_payload(state="CHANGES_REQUESTED")
        with _patch_settings_and_db(mock_record, mock_task):
            response = _post_webhook(client, payload, "pull_request_review")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "delivered"
        mock_execution.resume.assert_called_once()

    def test_approved_ignored(self, client):
        """Review with approved state is ignored."""
        payload = _make_review_payload(state="APPROVED")
        with patch("orpheus.api.routes.webhooks.settings") as mock_settings:
            mock_settings.github_webhook_secret = MagicMock()
            mock_settings.github_webhook_secret.get_secret_value.return_value = WEBHOOK_SECRET
            response = _post_webhook(client, payload, "pull_request_review")

        assert response.status_code == 200
        assert response.json()["status"] == "ignored"
        assert response.json()["reason"] == "review approved"

    def test_own_bot_review_ignored(self, client):
        """Own bot reviews are ignored."""
        payload = _make_review_payload(commenter="test-app[bot]")
        with patch("orpheus.api.routes.webhooks.settings") as mock_settings:
            mock_settings.github_webhook_secret = MagicMock()
            mock_settings.github_webhook_secret.get_secret_value.return_value = WEBHOOK_SECRET
            mock_settings.github_app_slug = "test-app"
            response = _post_webhook(client, payload, "pull_request_review")

        assert response.status_code == 200
        assert response.json()["status"] == "ignored"
        assert response.json()["reason"] == "own bot review"


def _make_review_comment_payload(
    pr_number: int = 42,
    repo_full_name: str = "user/repo",
    commenter: str = "reviewer",
    body: str = "This variable name is confusing",
    path: str = "src/main.py",
    line: int = 15,
    sender_type: str = "User",
) -> dict:
    """Build a GitHub pull_request_review_comment.created webhook payload."""
    return {
        "action": "created",
        "comment": {
            "body": body,
            "path": path,
            "line": line,
            "user": {"login": commenter, "type": sender_type},
        },
        "pull_request": {"number": pr_number},
        "sender": {"login": commenter, "type": sender_type},
        "repository": {"full_name": repo_full_name},
    }


class TestPullRequestReviewComment:
    def test_inline_comment_delivers(self, client, mock_execution, mock_record, mock_task):
        """Inline diff comment is delivered to the agent with file location."""
        payload = _make_review_comment_payload()
        with _patch_settings_and_db(mock_record, mock_task):
            response = _post_webhook(client, payload, "pull_request_review_comment")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "delivered"
        mock_execution.resume.assert_called_once()

    def test_own_bot_inline_comment_ignored(self, client):
        """Own bot inline comments are ignored."""
        payload = _make_review_comment_payload(commenter="test-app[bot]")
        with patch("orpheus.api.routes.webhooks.settings") as mock_settings:
            mock_settings.github_webhook_secret = MagicMock()
            mock_settings.github_webhook_secret.get_secret_value.return_value = WEBHOOK_SECRET
            mock_settings.github_app_slug = "test-app"
            response = _post_webhook(client, payload, "pull_request_review_comment")

        assert response.status_code == 200
        assert response.json()["status"] == "ignored"
        assert response.json()["reason"] == "own bot comment"


def _make_issue_mention_payload(
    issue_number: int = 10,
    repo_full_name: str = "user/repo",
    commenter: str = "requester",
    body: str = "@test-app please fix this bug",
    issue_title: str = "Bug: something is broken",
    issue_body: str = "Steps to reproduce...",
) -> dict:
    """Build a GitHub issue_comment.created webhook payload for an issue (not PR)."""
    return {
        "action": "created",
        "issue": {
            "number": issue_number,
            "title": issue_title,
            "body": issue_body,
        },
        "comment": {"body": body},
        "sender": {"login": commenter, "type": "User"},
        "repository": {"full_name": repo_full_name},
    }


class TestIssueMention:
    """Tests for issue mention webhook handler."""

    def _patch_for_issue_mention(self, devbox=None, user=None, task=None, record=None, programs=None):
        """Return a combined patch context for issue mention tests."""
        import contextlib

        from orpheus.db.models.devbox import Devbox
        from orpheus.db.models.user import User

        @contextlib.contextmanager
        def combined():
            with (
                patch("orpheus.api.routes.webhooks.settings") as mock_settings,
                patch("orpheus.api.routes.webhooks.get_session", new=make_mock_session()),
                patch(
                    "orpheus.api.routes.webhooks.get_devbox_by_repo", new_callable=AsyncMock, return_value=devbox
                ) as mock_get_devbox,
                patch(
                    "orpheus.api.routes.webhooks.get_user", new_callable=AsyncMock, return_value=user
                ) as mock_get_user,
                patch("orpheus.api.routes.webhooks.discover_programs", return_value=programs or []) as mock_programs,
                patch("orpheus.api.routes.webhooks.post_issue_comment", new_callable=AsyncMock) as mock_post_comment,
                patch("orpheus.api.routes.webhooks.launch_execution", new_callable=AsyncMock) as mock_launch,
            ):
                mock_settings.github_webhook_secret = MagicMock()
                mock_settings.github_webhook_secret.get_secret_value.return_value = WEBHOOK_SECRET
                mock_settings.github_app_slug = "test-app"
                mock_settings.base_url = "https://api.orpheus.dev"
                mock_settings.enable_task_creation = True

                mock_ex = MagicMock()
                mock_ex.id = record.id if record else uuid4()
                mock_ex.slug = record.slug if record else "test-slug"
                mock_launch.return_value = mock_ex

                yield {
                    "settings": mock_settings,
                    "get_devbox": mock_get_devbox,
                    "get_user": mock_get_user,
                    "programs": mock_programs,
                    "post_comment": mock_post_comment,
                    "launch": mock_launch,
                    "execution": mock_ex,
                }

        return combined()

    def _make_devbox(self, user_id=None, snapshot_id="snap-123"):
        from orpheus.db.models.devbox import Devbox

        return MagicMock(spec=Devbox, user_id=user_id or uuid4(), snapshot_id=snapshot_id)

    def _make_user(self, user_id=None):
        from orpheus.db.models.user import User

        user = MagicMock(spec=User)
        user.id = user_id or uuid4()
        user.access_token = "user_token"
        return user

    def _make_task(self, user_id=None):
        task = MagicMock(spec=Task)
        task.id = uuid4()
        task.slug = "gentle-nocturne"
        task.user_id = user_id or uuid4()
        return task

    def _make_record(self, task_id=None):
        record = MagicMock(spec=ExecutionRecord)
        record.id = uuid4()
        record.slug = "gentle-nocturne-claude"
        record.task_id = task_id or uuid4()
        return record

    def test_mention_disabled(self, client):
        """Issue mention returns ignored when task creation is disabled."""
        payload = _make_issue_mention_payload()
        with patch("orpheus.api.routes.webhooks.settings") as mock_settings:
            mock_settings.github_webhook_secret = MagicMock()
            mock_settings.github_webhook_secret.get_secret_value.return_value = WEBHOOK_SECRET
            mock_settings.github_app_slug = "test-app"
            mock_settings.enable_task_creation = False
            response = _post_webhook(client, payload, "issue_comment")

        assert response.status_code == 200
        assert response.json()["status"] == "ignored"
        assert response.json()["reason"] == "task creation disabled"

    def test_mention_creates_task(self, client):
        """Bot mentioned on an issue creates a task and posts confirmation."""
        user_id = uuid4()
        devbox = self._make_devbox(user_id=user_id)
        user = self._make_user(user_id=user_id)
        task = self._make_task(user_id=user_id)
        record = self._make_record(task_id=task.id)

        mock_root = MagicMock()
        mock_root.name = "claude"
        mock_root.is_agent = True
        mock_create_fn = MagicMock(return_value=mock_root)
        programs = [("claude", mock_create_fn)]

        payload = _make_issue_mention_payload()
        with self._patch_for_issue_mention(
            devbox=devbox, user=user, task=task, record=record, programs=programs
        ) as mocks:
            response = _post_webhook(client, payload, "issue_comment")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "task_created"
        assert "execution_slug" in data
        mocks["post_comment"].assert_called_once()

    def test_no_mention_ignored(self, client):
        """Comment without bot mention is ignored."""
        payload = _make_issue_mention_payload(body="This is a regular comment")
        with patch("orpheus.api.routes.webhooks.settings") as mock_settings:
            mock_settings.github_webhook_secret = MagicMock()
            mock_settings.github_webhook_secret.get_secret_value.return_value = WEBHOOK_SECRET
            mock_settings.github_app_slug = "test-app"
            response = _post_webhook(client, payload, "issue_comment")

        assert response.status_code == 200
        assert response.json()["status"] == "ignored"
        assert response.json()["reason"] == "bot not mentioned"

    def test_pr_comment_routed_to_pr_handler(self, client, mock_execution, mock_record, mock_task):
        """Comment on a PR is routed to the existing PR handler, not issue mention."""
        payload = _make_issue_comment_payload(is_pr=True)
        with (
            patch("orpheus.api.routes.webhooks.settings") as mock_settings,
            patch("orpheus.api.routes.webhooks.get_session", new=make_mock_session()),
            patch("orpheus.api.routes.webhooks.get_execution_by_pr", new_callable=AsyncMock, return_value=mock_record),
            patch("orpheus.api.routes.webhooks.get_task", new_callable=AsyncMock, return_value=mock_task),
            patch("orpheus.api.routes.webhooks.update_execution", new_callable=AsyncMock),
            patch("orpheus.api.routes.webhooks.execution_trace"),
        ):
            mock_settings.github_webhook_secret = MagicMock()
            mock_settings.github_webhook_secret.get_secret_value.return_value = WEBHOOK_SECRET
            mock_settings.github_app_slug = "test-app"
            response = _post_webhook(client, payload, "issue_comment")

        assert response.status_code == 200
        assert response.json()["status"] == "delivered"

    def test_no_devbox_posts_setup_comment(self, client):
        """When no devbox exists for the repo, posts a setup-needed comment."""
        payload = _make_issue_mention_payload()
        with (
            patch("orpheus.api.routes.webhooks.settings") as mock_settings,
            patch("orpheus.api.routes.webhooks.get_session", new=make_mock_session()),
            patch("orpheus.api.routes.webhooks.get_devbox_by_repo", new_callable=AsyncMock, return_value=None),
            patch("orpheus.api.routes.webhooks.post_issue_comment", new_callable=AsyncMock) as mock_post,
        ):
            mock_settings.github_webhook_secret = MagicMock()
            mock_settings.github_webhook_secret.get_secret_value.return_value = WEBHOOK_SECRET
            mock_settings.github_app_slug = "test-app"
            mock_settings.enable_task_creation = True
            response = _post_webhook(client, payload, "issue_comment")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "error"
        assert data["reason"] == "no devbox for repo"
        mock_post.assert_called_once()
        call_args = mock_post.call_args
        assert "setup" in call_args[0][2].lower()

    def test_bot_own_comment_ignored(self, client):
        """Bot's own comments on issues are ignored."""
        payload = _make_issue_mention_payload(commenter="test-app[bot]", body="@test-app hello")
        with patch("orpheus.api.routes.webhooks.settings") as mock_settings:
            mock_settings.github_webhook_secret = MagicMock()
            mock_settings.github_webhook_secret.get_secret_value.return_value = WEBHOOK_SECRET
            mock_settings.github_app_slug = "test-app"
            response = _post_webhook(client, payload, "issue_comment")

        assert response.status_code == 200
        assert response.json()["status"] == "ignored"
        assert response.json()["reason"] == "own bot comment"

    def test_mention_with_bot_suffix(self, client):
        """Bot mention with [bot] suffix is also detected."""
        user_id = uuid4()
        devbox = self._make_devbox(user_id=user_id)
        user = self._make_user(user_id=user_id)
        task = self._make_task(user_id=user_id)
        record = self._make_record(task_id=task.id)

        mock_root = MagicMock()
        mock_root.name = "claude"
        mock_root.is_agent = True
        mock_create_fn = MagicMock(return_value=mock_root)
        programs = [("claude", mock_create_fn)]

        payload = _make_issue_mention_payload(body="@test-app[bot] please fix this")
        with self._patch_for_issue_mention(
            devbox=devbox, user=user, task=task, record=record, programs=programs
        ) as mocks:
            response = _post_webhook(client, payload, "issue_comment")

        assert response.status_code == 200
        assert response.json()["status"] == "task_created"

    def test_mention_case_insensitive(self, client):
        """Bot mention is case-insensitive."""
        user_id = uuid4()
        devbox = self._make_devbox(user_id=user_id)
        user = self._make_user(user_id=user_id)
        task = self._make_task(user_id=user_id)
        record = self._make_record(task_id=task.id)

        mock_root = MagicMock()
        mock_root.name = "claude"
        mock_root.is_agent = True
        mock_create_fn = MagicMock(return_value=mock_root)
        programs = [("claude", mock_create_fn)]

        payload = _make_issue_mention_payload(body="@TEST-APP please fix this")
        with self._patch_for_issue_mention(
            devbox=devbox, user=user, task=task, record=record, programs=programs
        ) as mocks:
            response = _post_webhook(client, payload, "issue_comment")

        assert response.status_code == 200
        assert response.json()["status"] == "task_created"


def _make_pull_request_payload(
    pr_number: int = 99,
    repo_full_name: str = "user/repo",
    sender: str = "developer",
    title: str = "Add new feature",
    body: str = "This PR adds a new feature.",
    head_branch: str = "feature-branch",
    action: str = "opened",
    draft: bool = False,
) -> dict:
    """Build a GitHub pull_request webhook payload."""
    return {
        "action": action,
        "pull_request": {
            "number": pr_number,
            "title": title,
            "body": body,
            "draft": draft,
            "head": {"ref": head_branch},
            "html_url": f"https://github.com/{repo_full_name}/pull/{pr_number}",
        },
        "sender": {"login": sender, "type": "User"},
        "repository": {"full_name": repo_full_name},
    }


class TestPullRequestAutoReview:
    """Tests for pull_request webhook triggering review execution."""

    def _patch_for_review(
        self,
        devbox=None,
        user=None,
        task=None,
        record=None,
        mock_agent=None,
        active_review=None,
        existing_execution=None,
        existing_task=None,
    ):
        """Return a combined patch context for PR review tests."""
        import contextlib

        @contextlib.contextmanager
        def combined():
            with (
                patch("orpheus.api.routes.webhooks.settings") as mock_settings,
                patch("orpheus.api.routes.webhooks.get_session", new=make_mock_session()),
                patch("orpheus.api.routes.webhooks.get_devbox_by_repo", new_callable=AsyncMock, return_value=devbox),
                patch(
                    "orpheus.api.routes.webhooks.get_active_review_execution",
                    new_callable=AsyncMock,
                    return_value=active_review,
                ),
                patch("orpheus.api.routes.webhooks.get_user", new_callable=AsyncMock, return_value=user),
                patch(
                    "orpheus.api.routes.webhooks.get_execution_by_pr",
                    new_callable=AsyncMock,
                    return_value=existing_execution,
                ),
                patch(
                    "orpheus.api.routes.webhooks.get_task",
                    new_callable=AsyncMock,
                    return_value=existing_task,
                ),
                patch("orpheus.api.routes.webhooks.create_review_agent", return_value=mock_agent) as mock_create_review,
                patch("orpheus.api.routes.webhooks.post_issue_comment", new_callable=AsyncMock) as mock_post,
                patch("orpheus.api.routes.webhooks.launch_execution", new_callable=AsyncMock) as mock_launch,
            ):
                mock_settings.github_webhook_secret = MagicMock()
                mock_settings.github_webhook_secret.get_secret_value.return_value = WEBHOOK_SECRET
                mock_settings.github_app_slug = "test-app"
                mock_settings.base_url = "https://api.orpheus.dev"

                mock_ex = MagicMock()
                mock_ex.id = record.id if record else uuid4()
                mock_ex.slug = record.slug if record else "test-slug"
                mock_launch.return_value = mock_ex

                yield {
                    "settings": mock_settings,
                    "create_review": mock_create_review,
                    "post_comment": mock_post,
                    "launch": mock_launch,
                    "execution": mock_ex,
                }

        return combined()

    def _make_devbox(self, user_id=None, snapshot_id="snap-123"):
        return MagicMock(user_id=user_id or uuid4(), snapshot_id=snapshot_id)

    def _make_user(self, user_id=None):
        user = MagicMock()
        user.id = user_id or uuid4()
        user.access_token = "user_token"
        user.subscription_status = "active"
        return user

    def _make_task(self, user_id=None):
        task = MagicMock(spec=Task)
        task.id = uuid4()
        task.slug = "review-task"
        task.user_id = user_id or uuid4()
        task.spec = "Original task spec text"
        return task

    def _make_record(self, task_id=None):
        record = MagicMock(spec=ExecutionRecord)
        record.id = uuid4()
        record.slug = "review-task-review"
        record.task_id = task_id or uuid4()
        record.pr_number = None
        record.pr_url = None
        return record

    def test_pr_opened_triggers_review(self, client):
        """Opening a PR triggers a review execution."""
        user_id = uuid4()
        devbox = self._make_devbox(user_id=user_id)
        user = self._make_user(user_id=user_id)
        task = self._make_task(user_id=user_id)
        record = self._make_record(task_id=task.id)

        mock_root = MagicMock()
        mock_root.name = "review"
        mock_root.is_agent = True

        payload = _make_pull_request_payload()
        with self._patch_for_review(devbox=devbox, user=user, task=task, record=record, mock_agent=mock_root) as mocks:
            response = _post_webhook(client, payload, "pull_request")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "review_started"
        assert "execution_slug" in data
        mocks["post_comment"].assert_called_once()

    def test_draft_pr_ignored(self, client):
        """Draft PRs are not reviewed."""
        payload = _make_pull_request_payload(draft=True)
        with patch("orpheus.api.routes.webhooks.settings") as mock_settings:
            mock_settings.github_webhook_secret = MagicMock()
            mock_settings.github_webhook_secret.get_secret_value.return_value = WEBHOOK_SECRET
            response = _post_webhook(client, payload, "pull_request")

        assert response.status_code == 200
        assert response.json()["reason"] == "draft PR"

    def test_bot_pr_ignored(self, client):
        """PRs from the bot itself are not reviewed."""
        payload = _make_pull_request_payload(sender="test-app[bot]")
        with patch("orpheus.api.routes.webhooks.settings") as mock_settings:
            mock_settings.github_webhook_secret = MagicMock()
            mock_settings.github_webhook_secret.get_secret_value.return_value = WEBHOOK_SECRET
            mock_settings.github_app_slug = "test-app"
            response = _post_webhook(client, payload, "pull_request")

        assert response.status_code == 200
        assert response.json()["reason"] == "own bot PR"

    def test_no_devbox_ignored(self, client):
        """PRs to repos without a devbox are silently ignored."""
        payload = _make_pull_request_payload()
        with (
            patch("orpheus.api.routes.webhooks.settings") as mock_settings,
            patch("orpheus.api.routes.webhooks.get_session", new=make_mock_session()),
            patch("orpheus.api.routes.webhooks.get_devbox_by_repo", new_callable=AsyncMock, return_value=None),
        ):
            mock_settings.github_webhook_secret = MagicMock()
            mock_settings.github_webhook_secret.get_secret_value.return_value = WEBHOOK_SECRET
            mock_settings.github_app_slug = "test-app"
            response = _post_webhook(client, payload, "pull_request")

        assert response.status_code == 200
        assert response.json()["reason"] == "no devbox for repo"

    def test_synchronize_event_ignored(self, client):
        """Synchronize events are ignored (no review on every push)."""
        user_id = uuid4()
        devbox = self._make_devbox(user_id=user_id)
        active_record = MagicMock(spec=ExecutionRecord)
        active_record.slug = "existing-review"

        payload = _make_pull_request_payload(action="synchronize")
        with self._patch_for_review(devbox=devbox, active_review=active_record) as mocks:
            response = _post_webhook(client, payload, "pull_request")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ignored"
        assert data["event"] == "pull_request"
        assert data["action"] == "synchronize"
        mocks["create_review"].assert_not_called()
        mocks["launch"].assert_not_called()
        mocks["post_comment"].assert_not_called()

    def test_orpheus_pr_includes_original_spec(self, client):
        """When the coding agent that generated the PR is in the registry, its spec is used."""
        user_id = uuid4()
        devbox = self._make_devbox(user_id=user_id)
        user = self._make_user(user_id=user_id)
        task = self._make_task(user_id=user_id)
        record = self._make_record(task_id=task.id)

        # Simulate a live coding execution in the registry that submitted this PR
        coding_task = MagicMock(spec=Task)
        coding_task.spec = "Build a widget that does X."

        coding_ex = MagicMock()
        coding_ex.submit_pr_url = "https://github.com/user/repo/pull/99"
        coding_ex.repo_full_name = "user/repo"
        coding_ex.task_id = uuid4()
        coding_ex.root = MagicMock()
        coding_ex.root.machine = MagicMock()
        coding_ex.root.machine.instance_id = "morphvm_abc123"

        mock_root = MagicMock()
        mock_root.name = "review"
        mock_root.is_agent = True

        payload = _make_pull_request_payload()
        with self._patch_for_review(
            devbox=devbox,
            user=user,
            task=task,
            record=record,
            mock_agent=mock_root,
            existing_task=coding_task,
        ) as mocks:
            # Inject the coding execution into the registry
            with patch(
                "orpheus.api.routes.webhooks.get_executions_registry",
                return_value={"some-user": {"coding-slug": coding_ex}},
            ):
                response = _post_webhook(client, payload, "pull_request")

        assert response.status_code == 200
        assert response.json()["status"] == "review_started"
        assert mocks["create_review"].call_args.kwargs["original_spec"] == "Build a widget that does X."


class TestPrMentionTriggersReview:
    """Tests for @mentioning the bot on a PR to trigger a review."""

    def test_mention_on_pr_triggers_review(self, client):
        """@mentioning the bot on a PR comment triggers a review execution."""
        payload = _make_issue_comment_payload(body="@test-app please review this", is_pr=True)

        devbox = MagicMock(user_id=uuid4(), snapshot_id="snap-123")
        user = MagicMock()
        user.id = devbox.user_id
        user.access_token = "token"
        user.subscription_status = "active"
        mock_root = MagicMock()
        mock_root.name = "review"
        mock_root.user_prompt = "Review PR"
        record_id = uuid4()

        with (
            patch("orpheus.api.routes.webhooks.settings") as mock_settings,
            patch("orpheus.api.routes.webhooks.get_session", new=make_mock_session()),
            patch(
                "orpheus.api.routes.webhooks.get_pull_request",
                new_callable=AsyncMock,
                return_value={
                    "head": {"ref": "feature-branch"},
                    "title": "Add feature",
                    "body": "Adds a feature.",
                    "html_url": "https://github.com/user/repo/pull/42",
                },
            ),
            patch("orpheus.api.routes.webhooks.get_devbox_by_repo", new_callable=AsyncMock, return_value=devbox),
            patch("orpheus.api.routes.webhooks.get_active_review_execution", new_callable=AsyncMock, return_value=None),
            patch("orpheus.api.routes.webhooks.get_user", new_callable=AsyncMock, return_value=user),
            patch("orpheus.api.routes.webhooks.get_execution_by_pr", new_callable=AsyncMock, return_value=None),
            patch("orpheus.api.routes.webhooks.create_review_agent", return_value=mock_root),
            patch("orpheus.api.routes.webhooks.post_issue_comment", new_callable=AsyncMock),
            patch("orpheus.api.routes.webhooks.launch_execution", new_callable=AsyncMock) as mock_launch,
        ):
            mock_settings.github_webhook_secret = MagicMock()
            mock_settings.github_webhook_secret.get_secret_value.return_value = WEBHOOK_SECRET
            mock_settings.github_app_slug = "test-app"
            mock_settings.base_url = "https://api.orpheus.dev"

            mock_ex = MagicMock()
            mock_ex.id = record_id
            mock_ex.slug = "review-slug"
            mock_launch.return_value = mock_ex

            response = _post_webhook(client, payload, "issue_comment")

        assert response.status_code == 200
        assert response.json()["status"] == "review_started"

    def test_mention_re_prompts_existing_review(self, client):
        """@mentioning the bot re-prompts an existing review execution."""
        payload = _make_issue_comment_payload(body="@test-app review again", is_pr=True)

        task_obj = MagicMock(spec=Task)
        task_obj.id = uuid4()
        task_obj.user_id = uuid4()
        user_id_str = str(task_obj.user_id)

        active_record = MagicMock(spec=ExecutionRecord)
        active_record.slug = "existing-review"
        active_record.task_id = task_obj.id

        # Put a runtime execution in the registry
        mock_ex = MagicMock()
        mock_ex.id = uuid4()
        mock_ex.slug = active_record.slug
        mock_ex.root = MagicMock()
        mock_ex.root.name = "review"
        mock_ex.connections = {"review": MagicMock()}
        mock_ex.resume = MagicMock()
        mock_ex.prompt = AsyncMock()

        registry = get_executions_registry()
        registry[user_id_str] = {active_record.slug: mock_ex}

        with (
            patch("orpheus.api.routes.webhooks.settings") as mock_settings,
            patch("orpheus.api.routes.webhooks.get_session", new=make_mock_session()),
            patch(
                "orpheus.api.routes.webhooks.get_active_review_execution",
                new_callable=AsyncMock,
                return_value=active_record,
            ),
            patch("orpheus.api.routes.webhooks.get_task", new_callable=AsyncMock, return_value=task_obj),
            patch("orpheus.api.routes.webhooks.update_execution", new_callable=AsyncMock),
        ):
            mock_settings.github_webhook_secret = MagicMock()
            mock_settings.github_webhook_secret.get_secret_value.return_value = WEBHOOK_SECRET
            mock_settings.github_app_slug = "test-app"

            response = _post_webhook(client, payload, "issue_comment")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "re_prompted"
        assert data["execution_slug"] == "existing-review"
        mock_ex.resume.assert_called_once()

        registry.clear()

    def test_non_mention_pr_comment_delivers_feedback(self, client, mock_execution, mock_record, mock_task):
        """PR comment without bot mention delivers feedback to existing execution."""
        payload = _make_issue_comment_payload(body="Please fix the typo", is_pr=True)
        with _patch_settings_and_db(mock_record, mock_task):
            response = _post_webhook(client, payload, "issue_comment")

        assert response.status_code == 200
        assert response.json()["status"] == "delivered"
