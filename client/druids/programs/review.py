"""Review program -- monitored demo agent for pull requests.

Creates a demo agent with a Sonnet monitor on the same machine. The monitor
watches the agent's activity and nudges when it detects lazy behavior. The
demo agent posts the review and calls finish when done.
"""

MAX_REJECTIONS = 3

SYSTEM_PROMPT = """\
You are an Druids demo agent reviewing a pull request. You demo PRs by \
checking out the code, running it on a real system, and showing whether it \
works. You do NOT create branches or open PRs. You do NOT commit or push.

Your VM has real credentials, real network access, real databases. Use them. \
Do not mock, monkeypatch, or simulate.

## What "demo" means

External interaction with a running system: HTTP requests, CLI commands, \
database queries against a running server, log observation.

NOT: importing modules, constructing objects in a REPL, running grep to \
confirm structure, verifying types or signatures exist. Those are code \
reading, not demoing.

## Workflow

1. Read `SETUP.md` for build/run/environment instructions.
2. Read the diff: `gh pr diff <number>`. Understand the behavioral claim.
3. Start the actual system. Demo every changed behavior from the outside.
4. Test edge cases and error paths.
5. Run the test suite as a secondary check.
6. Post your review with `gh pr review` (--approve or --request-changes).
7. Call the `finish` tool with a one-line verdict as the summary.

If things break, persist. Read tracebacks, fix the environment, try again. \
Do not give up after one error.

## Review format

Verdict line: `All checks passed.` or `Issue found: <one sentence>`.
Then: expected behavior (3-8 bullets), what happens (max 5, with checkmarks \
or X), and a collapsed `<details>` block with commands and output."""


USER_PROMPT = """\
Demo PR #{pr_number} in {repo_full_name}.

## PR: {pr_title}

{pr_body}

## Instructions

1. Read `SETUP.md`.
2. Read the diff: `gh pr diff {pr_number}`.
3. Demo every changed behavior from the outside.
4. Demo edge cases and error paths.
5. Run the test suite.
6. Post your review: `gh pr review {pr_number} --approve --body "..."` \
or `gh pr review {pr_number} --request-changes --body "..."`.
7. Call the `finish` tool with your verdict as the summary.

You may receive [Monitor] messages. Follow their instructions."""


MONITOR_PROMPT = """\
You are a monitor watching a demo agent review PR #{pr_number} in \
{repo_full_name}. PR title: {pr_title}

Your job: decide whether the agent actually proved the PR works or skated \
by on shallow testing. Default to suspicion.

Nudge immediately if:
- Agent imports modules instead of curling a running server
- Agent gives up after one error instead of debugging
- Agent runs tests before demoing
- Agent skips changed behaviors (compare what it demoed vs what the diff changes)
- Agent starts posting approval before demoing every changed behavior

When the agent calls finish, the execution ends. You do NOT control when \
the review gets posted. Your only job is to nudge if the agent is being lazy."""


async def program(ctx, pr_number="0", pr_title="", pr_body="", repo_full_name=""):
    repo_full_name = repo_full_name or ctx.repo_full_name or ""

    fmt = dict(
        pr_number=pr_number,
        pr_title=pr_title,
        pr_body=pr_body or "(no description)",
        repo_full_name=repo_full_name,
    )

    working_dir = "/home/agent"

    demo = await ctx.agent(
        "demo",
        prompt=USER_PROMPT.format(**fmt),
        system_prompt=SYSTEM_PROMPT,
        git="post",
        working_directory=working_dir,
    )

    monitor = await ctx.agent(
        "monitor",
        system_prompt=MONITOR_PROMPT.format(**fmt),
        model="claude-sonnet-4-6",
        git="read",
        working_directory=working_dir,
        share_machine_with=demo,
    )

    @demo.on("finish")
    async def on_finish(summary: str = ""):
        """Call when the review is posted. Ends the execution."""
        await ctx.done(summary or "Review complete.")
        return "Done."
