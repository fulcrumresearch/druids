"""Webhook endpoints for PR comment reactions and issue mention triggers."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import re

from fastapi import APIRouter, HTTPException, Request
from programs.verify import create_review_agent

from orpheus.api.deps import get_executions_registry
from orpheus.api.github import close_pull_request, get_pull_request, post_issue_comment
from orpheus.api.launch import launch_execution
from orpheus.config import settings
from orpheus.lib import execution_trace
from orpheus.lib.devbox import machine_from_devbox
from orpheus.lib.elo import record_comparison
from orpheus.lib.machine import Machine
from orpheus.db.models.devbox import get_devbox_by_repo
from orpheus.db.models.execution import (
    get_active_review_execution,
    get_execution_by_pr,
    get_task_executions,
    get_user_execution_count,
    update_execution,
    update_execution_outcome,
)
from orpheus.db.models.task import get_task
from orpheus.db.models.user import get_user
from orpheus.db.session import get_session
from orpheus.programs import discover_programs


router = APIRouter()
logger = logging.getLogger(__name__)


def _verify_github_signature(payload: bytes, signature: str, secret: str) -> bool:
    """Verify GitHub webhook signature (HMAC-SHA256)."""
    expected = "sha256=" + hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


@router.post("/webhooks/github", tags=["webhooks"])
async def github_webhook(request: Request):
    """Handle GitHub webhook events for PR comment reactions.

    Listens for issue_comment, pull_request_review, and
    pull_request_review_comment events. When feedback arrives on a PR
    created by an Orpheus execution, re-prompts the agent.
    """
    if not settings.github_webhook_secret:
        raise HTTPException(501, "Webhook secret not configured")

    signature = request.headers.get("X-Hub-Signature-256", "")
    payload = await request.body()
    if not _verify_github_signature(payload, signature, settings.github_webhook_secret.get_secret_value()):
        raise HTTPException(401, "Invalid webhook signature")

    event_type = request.headers.get("X-GitHub-Event")
    body = await request.json()
    action = body.get("action")

    match (event_type, action):
        case ("issue_comment", "created"):
            return await _handle_issue_comment(body)
        case ("pull_request_review", "submitted"):
            return await _handle_pr_review(body)
        case ("pull_request_review_comment", "created"):
            return await _handle_review_comment(body)
        case ("pull_request", "opened"):
            return await _handle_pull_request_review(body)
        case ("pull_request", "closed"):
            if body.get("pull_request", {}).get("merged"):
                return await _handle_pr_merged(body)
            return {"status": "ignored", "event": event_type, "action": action}
        case _:
            return {"status": "ignored", "event": event_type, "action": action}


def _is_bot_mentioned(text: str) -> bool:
    """Check if the bot is mentioned in the text.

    Matches @{slug} with or without a [bot] suffix, case-insensitive.
    """
    slug = settings.github_app_slug
    pattern = re.compile(rf"@{re.escape(slug)}(\[bot\])?", re.IGNORECASE)
    return bool(pattern.search(text))


async def _handle_issue_comment(body: dict) -> dict:
    """Route issue_comment.created to PR handler or issue mention handler."""
    issue = body.get("issue", {})

    if "pull_request" in issue:
        return await _handle_pr_comment(body)

    return await _handle_issue_mention(body)


async def _handle_pr_comment(body: dict) -> dict:
    """Handle issue_comment.created on a PR.

    If the bot is @mentioned, triggers a new review execution.
    Otherwise, delivers feedback to the existing code execution.
    """
    issue = body.get("issue", {})
    commenter = body.get("sender", {}).get("login", "unknown")
    comment_body = body.get("comment", {}).get("body", "")
    pr_number = issue.get("number")
    repo_full_name = body.get("repository", {}).get("full_name")

    # Only ignore our own bot to avoid loops; other bots can provide feedback
    if commenter == f"{settings.github_app_slug}[bot]":
        return {"status": "ignored", "reason": "own bot comment"}

    # Bot mentioned on a PR -> trigger a review
    if _is_bot_mentioned(comment_body):
        return await _trigger_pr_review(repo_full_name, pr_number)

    return await _deliver_pr_feedback(repo_full_name, pr_number, commenter, comment_body, event_type="comment")


async def _handle_issue_mention(body: dict) -> dict:
    """Handle issue_comment.created on an issue (not a PR) that mentions the bot.

    Creates a task and starts a claude execution when triggered.
    """
    if not settings.enable_task_creation:
        return {"status": "ignored", "reason": "task creation disabled"}

    issue = body.get("issue", {})
    commenter = body.get("sender", {}).get("login", "unknown")
    comment_body = body.get("comment", {}).get("body", "")
    issue_number = issue.get("number")
    repo_full_name = body.get("repository", {}).get("full_name")

    # Ignore the bot's own comments to avoid loops
    if commenter == f"{settings.github_app_slug}[bot]":
        return {"status": "ignored", "reason": "own bot comment"}

    # Only trigger on comments that mention the bot
    if not _is_bot_mentioned(comment_body):
        return {"status": "ignored", "reason": "bot not mentioned"}

    if not repo_full_name or not issue_number:
        return {"status": "ignored", "reason": "missing repo or issue number"}

    async with get_session() as db:
        devbox = await get_devbox_by_repo(db, repo_full_name)
        if not devbox:
            await post_issue_comment(
                repo_full_name,
                issue_number,
                "This repository has not been set up with Orpheus yet. "
                "Run `orpheus setup start` and `orpheus setup save` to configure a devbox first.",
            )
            return {"status": "error", "reason": "no devbox for repo"}

        user = await get_user(db, devbox.user_id)
        if not user:
            logger.error(f"User {devbox.user_id} from devbox not found in DB")
            return {"status": "error", "reason": "devbox owner not found"}

    # Find the claude program
    programs = discover_programs()
    claude_programs = [(name, fn) for name, fn in programs if name == "claude"]
    if not claude_programs:
        logger.error("No 'claude' program found")
        return {"status": "error", "reason": "claude program not found"}

    _, create_fn = claude_programs[0]

    # Build spec from issue
    issue_title = issue.get("title", "")
    issue_body = issue.get("body") or ""
    spec = f"# {issue_title}\n\n{issue_body}\n\n---\n\nTriggered by @{commenter}:\n\n{comment_body}"

    devbox_machine = await machine_from_devbox(devbox)
    repo_name = repo_full_name.split("/")[-1]
    root = create_fn(spec, repo_name)

    ex = await launch_execution(
        root,
        user=user,
        spec=spec,
        devbox_machine=devbox_machine,
        repo_full_name=repo_full_name,
        task_metadata={"repo_full_name": repo_full_name, "issue_number": issue_number},
    )

    await post_issue_comment(repo_full_name, issue_number, f"Working on it. Execution: `{ex.slug}`")

    logger.info(f"Created execution {ex.slug} from issue #{issue_number} mention in {repo_full_name}")
    return {"status": "task_created", "execution_slug": ex.slug}


async def _handle_pr_review(body: dict) -> dict:
    """Handle pull_request_review.submitted."""
    review = body.get("review", {})
    # GitHub sends uppercase state values (APPROVED, CHANGES_REQUESTED, COMMENTED)
    state = review.get("state", "").lower()

    # Skip approvals (no action needed)
    if state == "approved":
        return {"status": "ignored", "reason": "review approved"}

    commenter = review.get("user", {}).get("login", "unknown")
    review_body = review.get("body") or ""
    pr_number = body.get("pull_request", {}).get("number")
    repo_full_name = body.get("repository", {}).get("full_name")

    # Only ignore our own bot to avoid loops
    if commenter == f"{settings.github_app_slug}[bot]":
        return {"status": "ignored", "reason": "own bot review"}

    state_label = "Changes Requested" if state == "changes_requested" else "Comment"
    formatted = f"[Review state: {state_label}]\n{review_body}" if review_body else f"[Review state: {state_label}]"

    return await _deliver_pr_feedback(repo_full_name, pr_number, commenter, formatted, event_type="review")


async def _handle_review_comment(body: dict) -> dict:
    """Handle pull_request_review_comment.created (inline diff comment)."""
    comment = body.get("comment", {})
    commenter = comment.get("user", {}).get("login", "unknown")
    comment_body = comment.get("body", "")
    path = comment.get("path", "")
    line = comment.get("line") or comment.get("original_line")
    pr_number = body.get("pull_request", {}).get("number")
    repo_full_name = body.get("repository", {}).get("full_name")

    # Only ignore our own bot to avoid loops
    if commenter == f"{settings.github_app_slug}[bot]":
        return {"status": "ignored", "reason": "own bot comment"}

    # Include file location context so the agent knows where to look
    location = f"{path}:{line}" if line else path
    formatted = f"[Inline comment on {location}]\n{comment_body}" if path else comment_body

    return await _deliver_pr_feedback(repo_full_name, pr_number, commenter, formatted, event_type="review_comment")


async def _deliver_pr_feedback(
    repo_full_name: str | None,
    pr_number: int | None,
    commenter: str,
    feedback_body: str,
    event_type: str,
) -> dict:
    """Look up execution for a PR and send feedback to the agent."""
    if not repo_full_name or not pr_number:
        return {"status": "ignored", "reason": "missing repo or PR number"}

    async with get_session() as db:
        record = await get_execution_by_pr(db, repo_full_name, pr_number)
        if record:
            task = await get_task(db, record.task_id)
            if not task:
                return {"status": "ignored", "reason": "task not found"}

            user_id_str = str(task.user_id)
            registry = get_executions_registry()
            ex = registry.get(user_id_str, {}).get(record.slug)
            if not ex:
                logger.warning(f"Execution {record.slug} not in registry (server may have restarted)")
                await update_execution(db, record.id, status="failed")
                # Fall through to launch a new execution below
                record = None

    if not record:
        return await _trigger_pr_review(repo_full_name, pr_number)

    root_name = ex.root.name
    if root_name not in ex.connections:
        logger.warning(f"Agent {root_name} not connected for execution {record.slug}")
        return {"status": "error", "reason": "agent not connected"}

    # Resume if submitted, then prompt
    ex.resume()
    execution_trace.pr_comment_received(user_id_str, ex.slug, commenter, feedback_body, event_type)

    prompt_text = (
        f"[PR Feedback from @{commenter}]\n"
        f"{feedback_body}\n\n"
        f"Address this feedback: make the necessary code changes, commit, push to the existing branch, "
        f'and call the `submit` tool with execution_slug="{ex.slug}" when done.'
    )
    asyncio.create_task(ex.prompt(root_name, prompt_text))

    # Update DB status back to running
    async with get_session() as db:
        await update_execution(db, ex.id, status="running")

    logger.info(f"Delivered {event_type} from @{commenter} to execution {record.slug}")
    return {"status": "delivered", "execution_slug": record.slug}


async def _trigger_pr_review(repo_full_name: str | None, pr_number: int | None) -> dict:
    """Trigger a review from a bot @mention on a PR.

    If a review execution already exists and is still in memory, re-prompts it.
    Otherwise fetches PR details from the GitHub API and starts a new one.
    """
    if not repo_full_name or not pr_number:
        return {"status": "ignored", "reason": "missing repo or PR number"}

    # Check for an existing review execution we can re-prompt
    async with get_session() as db:
        active = await get_active_review_execution(db, repo_full_name, pr_number)
        if active:
            task = await get_task(db, active.task_id)

    if active and task:
        user_id_str = str(task.user_id)
        registry = get_executions_registry()
        ex = registry.get(user_id_str, {}).get(active.slug)
        if ex and ex.root.name in ex.connections:
            ex.resume()
            prompt_text = (
                "You have been asked to re-review this PR. "
                "Run `gh pr diff` again, re-run verification, and post an updated review."
            )
            asyncio.create_task(ex.prompt(ex.root.name, prompt_text))

            async with get_session() as db:
                await update_execution(db, ex.id, status="running")

            logger.info(f"Re-prompted review execution {active.slug} for {repo_full_name}#{pr_number}")
            return {"status": "re_prompted", "execution_slug": active.slug}

    # No existing review -- start a new one
    pr = await get_pull_request(repo_full_name, pr_number)

    return await _start_pr_review(
        repo_full_name=repo_full_name,
        pr_number=pr_number,
        head_branch=pr.get("head", {}).get("ref"),
        pr_title=pr.get("title", ""),
        pr_body=pr.get("body") or "",
        pr_url=pr.get("html_url", ""),
    )


async def _handle_pr_merged(body: dict) -> dict:
    """Handle a PR merge event: set outcomes, close siblings, update ELO ratings."""
    pr = body.get("pull_request", {})
    repo_full_name = body.get("repository", {}).get("full_name")
    pr_number = pr.get("number")

    if not repo_full_name or not pr_number:
        return {"status": "ignored", "reason": "missing repo or PR number"}

    async with get_session() as db:
        winner = await get_execution_by_pr(db, repo_full_name, pr_number)
        if not winner:
            return {"status": "ignored", "reason": "not an orpheus PR"}

        task = await get_task(db, winner.task_id)
        if not task:
            return {"status": "ignored", "reason": "task not found"}

        siblings = await get_task_executions(db, task.id)
        # Filter to siblings that have PRs and are not the winner
        losers = [s for s in siblings if s.id != winner.id and s.pr_number is not None]

        if not losers:
            # Single execution or no other siblings with PRs -- no comparison
            await update_execution_outcome(db, winner.id, "merged")
            return {"status": "merged", "execution_slug": winner.slug, "comparisons": 0}

        # Set outcomes
        await update_execution_outcome(db, winner.id, "merged")
        for loser in losers:
            await update_execution_outcome(db, loser.id, "rejected")

        # Update ELO ratings
        await record_comparison(db, winner, losers)

    # Close sibling PRs on GitHub (best-effort, outside the DB transaction)
    for loser in losers:
        if loser.pr_url and loser.pr_number:
            try:
                await close_pull_request(repo_full_name, loser.pr_number)
            except Exception:
                logger.warning("Failed to close sibling PR #%s for %s", loser.pr_number, repo_full_name)

    # Stop running sibling executions
    registry = get_executions_registry()
    user_id_str = str(task.user_id)
    user_execs = registry.get(user_id_str, {})
    for loser in losers:
        ex = user_execs.get(loser.slug)
        if ex:
            await ex.stop()

    logger.info(
        "PR merged: winner=%s losers=%s repo=%s",
        winner.slug,
        [l.slug for l in losers],
        repo_full_name,
    )
    return {"status": "merged", "execution_slug": winner.slug, "comparisons": len(losers)}


async def _handle_pull_request_review(body: dict) -> dict:
    """Handle pull_request.opened or pull_request.synchronize.

    Auto-triggers a review execution that checks out the PR branch, runs the code,
    and posts a GitHub review with findings.
    """
    pr = body.get("pull_request", {})
    sender = body.get("sender", {}).get("login", "unknown")
    repo_full_name = body.get("repository", {}).get("full_name")
    pr_number = pr.get("number")
    is_draft = pr.get("draft", False)

    # Skip drafts and bot's own PRs
    if is_draft:
        return {"status": "ignored", "reason": "draft PR"}
    if sender == f"{settings.github_app_slug}[bot]":
        return {"status": "ignored", "reason": "own bot PR"}
    if not repo_full_name or not pr_number or not pr.get("head", {}).get("ref"):
        return {"status": "ignored", "reason": "missing PR metadata"}

    return await _start_pr_review(
        repo_full_name=repo_full_name,
        pr_number=pr_number,
        head_branch=pr["head"]["ref"],
        pr_title=pr.get("title", ""),
        pr_body=pr.get("body") or "",
        pr_url=pr.get("html_url", ""),
    )


async def _start_pr_review(
    *,
    repo_full_name: str,
    pr_number: int,
    head_branch: str | None,
    pr_title: str,
    pr_body: str,
    pr_url: str,
) -> dict:
    """Shared logic for starting a review execution.

    Called by both the pull_request webhook handler (auto-trigger on open/sync)
    and the @mention handler (manual re-trigger from PR comment).
    """
    # Single DB session for all read queries
    original_spec = ""
    source_machine: Machine | None = None
    async with get_session() as db:
        devbox = await get_devbox_by_repo(db, repo_full_name)
        if not devbox:
            return {"status": "ignored", "reason": "no devbox for repo"}

        active = await get_active_review_execution(db, repo_full_name, pr_number)
        if active:
            logger.info(
                "Review execution %s already running for %s#%s (status=%s, root_instance_id=%s, started_at=%s)",
                active.slug,
                repo_full_name,
                pr_number,
                active.status,
                active.root_instance_id,
                active.started_at,
            )
            return {"status": "ignored", "reason": "review already running"}

        user = await get_user(db, devbox.user_id)
        if not user:
            logger.error(f"User {devbox.user_id} from devbox not found in DB")
            return {"status": "error", "reason": "devbox owner not found"}

        # Enforce billing: free tier allows N reviews, then require subscription
        if settings.stripe_api_key and user.subscription_status != "active":
            count = await get_user_execution_count(db, user.id)
            if count >= settings.free_tier_reviews:
                logger.info(
                    "Skipping PR review for %s: free tier limit reached (%d/%d)",
                    user.github_login,
                    count,
                    settings.free_tier_reviews,
                )
                billing_url = f"{settings.base_url}/#/billing"
                app_slug = settings.github_app_slug
                await post_issue_comment(
                    repo_full_name,
                    pr_number,
                    f"Review limit reached ({count}/{settings.free_tier_reviews} free reviews used).\n\n"
                    f"[Subscribe]({billing_url}) to continue receiving reviews, "
                    f"or [uninstall the app](https://github.com/apps/{app_slug}/installations/new) "
                    "to stop these messages.",
                )
                return {"status": "ignored", "reason": "free tier limit reached"}

        # Find the execution that generated this PR in the live registry.
        # Only coding agents (not review agents) submit with a PR URL.
        registry = get_executions_registry()
        for user_execs in registry.values():
            for ex in user_execs.values():
                if (
                    ex.submit_pr_url
                    and ex.submit_pr_url.endswith(f"/pull/{pr_number}")
                    and ex.repo_full_name == repo_full_name
                ):
                    if ex.root.is_agent and ex.root.machine:
                        source_machine = ex.root.machine
                    original_task = await get_task(db, ex.task_id)
                    if original_task:
                        original_spec = original_task.spec
                    break

    devbox_machine = await machine_from_devbox(devbox)
    repo_name = repo_full_name.split("/")[-1]

    # Create Task and ExecutionRecord
    root = create_review_agent(
        repo_name,
        pr_number=pr_number,
        pr_title=pr_title,
        pr_body=pr_body,
        repo_full_name=repo_full_name,
        original_spec=original_spec,
        fork_source=source_machine,
    )

    ex = await launch_execution(
        root,
        user=user,
        spec=root.user_prompt,
        devbox_machine=devbox_machine,
        repo_full_name=repo_full_name,
        task_metadata={
            "repo_full_name": repo_full_name,
            "pr_number": str(pr_number),
            "pr_url": pr_url,
        },
        git_branch=head_branch,
        pr_number=pr_number,
        pr_url=pr_url,
    )

    await post_issue_comment(repo_full_name, pr_number, "Review started.")

    logger.info(f"Created review execution {ex.slug} for {repo_full_name}#{pr_number}")
    return {"status": "review_started", "execution_slug": ex.slug}
