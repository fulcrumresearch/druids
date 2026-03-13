"""Background basher -- finds tasks from Slack/GitHub and implements them.

Three agent roles:

  finder        Long-running. Reads Slack and GitHub to identify bugs, features,
                and improvements. Spawns implementors for well-scoped tasks.

  implementor   One per task. Implements the change, writes tests, pushes a
                branch. Calls submit_for_review when ready.

  reviewer      One per implementor, shares its machine. Demos the change from
                the outside. Creates a PR if it works, sends feedback if not.
"""

MAX_REVIEW_ROUNDS = 3

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

IMPLEMENTOR_SYSTEM_PROMPT = """\
You are an implementor agent. You receive a task spec describing what needs \
to change and what the user-facing interaction should be.

Read `SETUP.md` for build and test instructions. Read the codebase \
to understand existing patterns. Follow those patterns.

## Git

IMPORTANT: You are on the main branch. Before making any changes, create \
a feature branch:

  git checkout -b {branch_name}

All commits go on this branch. Push with `git push -u origin {branch_name}`.

## Workflow

1. Create your feature branch (see above).
2. Read the task spec carefully.
3. Read the relevant code. Understand before writing.
4. Implement the change. Write tests.
5. Run the test suite and linter. Fix failures.
6. Commit and push to your branch.
7. Call the `submit_for_review` tool with a summary of what you changed and why.

If the reviewer sends feedback, address it, push, and call `submit_for_review` \
again. Do NOT create PRs."""


REVIEWER_SYSTEM_PROMPT = """\
You are a reviewer agent. You share a machine with the implementor. The \
code is already there on a feature branch.

## Workflow

1. Read the task spec (provided in your prompt).
2. Read the diff: `git diff main...HEAD`.
3. Start the system and demo the change from the outside. HTTP requests, \
CLI commands, database queries against a running system. NOT importing \
modules or constructing objects.
4. Test edge cases and error paths.

## Decision

If the change works and matches the spec:
  - Create a PR: `gh pr create --title "<title>" --body "<body>"`
  - Call the `approve_and_pr` tool with the PR URL from the `gh pr create` output.
  - You MUST pass the pr_url. Copy it from the `gh pr create` output.

If the change does NOT work:
  - Call the `send_feedback` tool with specific, actionable feedback.

If something breaks, debug it. Do not give up after one attempt."""


FINDER_SYSTEM_PROMPT = """\
You are a background task finder for {repo_full_name}. You identify bugs to \
fix, features to add, and improvements to make.

Sources:
- GitHub: merged PRs, open issues, recent commits. Use `gh` CLI.{slack_instructions}

You spawn tasks for concrete, well-scoped work: a bug with a clear \
reproduction, a feature with a clear spec, a test gap with a clear fix. \
Do NOT spawn tasks for cosmetic changes, vague requests, or major \
architectural decisions.

## Tool

Call the `spawn_task` tool with a task_name and task_spec.

The task_spec describes WHAT needs to change and what the user-facing \
interaction should be. Do NOT describe HOW to implement it.

Good: "The /api/keys endpoint returns 500 when the key name contains a \
slash. After the fix, POST /api/keys with name='foo/bar' should return 201."

Bad: "Fix the regex in key_validator.py line 42."

## Pacing

Spawn at most 3 tasks at a time. Each task provisions a VM and an agent, \
which costs real money. Wait for at least one task to complete or fail \
before spawning more. You will receive a message when each task finishes.

## Workflow

1. Read `SETUP.md` for project context.
2. Scan GitHub and Slack for actionable work.
3. Rank candidates by impact. Prioritize bugs over features.
4. Spawn up to 3 tasks. Wait for results before spawning more.
5. When a task completes or fails, you get a message. Keep scanning."""


SLACK_INSTRUCTIONS = """
- Slack API: use curl with the bot token.
  `curl -H "Authorization: Bearer {slack_token}" https://slack.com/api/conversations.list`
  `curl -H "Authorization: Bearer {slack_token}" "https://slack.com/api/conversations.history?channel=CXXXX&limit=50"`
  `curl -H "Authorization: Bearer {slack_token}" "https://slack.com/api/search.messages?query=bug"` (search requires user token, may not work with bot token)
  List channels first, then read history from relevant ones."""


# ---------------------------------------------------------------------------
# Program
# ---------------------------------------------------------------------------


def _slugify(name: str) -> str:
    """Turn a task name into a branch-safe slug."""
    slug = name.lower().replace(" ", "-")
    slug = "".join(c for c in slug if c.isalnum() or c == "-")
    return slug[:50].strip("-") or "task"


async def program(ctx, repo_full_name="", task_name="", task_spec="", slack_token=""):
    """Background basher. If task_name and task_spec are provided, skips the
    finder and directly spawns an implementor+reviewer pair (Phase 1 mode)."""
    repo_full_name = repo_full_name or ctx.repo_full_name or ""
    working_dir = "/home/agent/repo"
    active_tasks = {}
    task_counter = 0

    async def spawn_task(task_name: str, task_spec: str):
        """Spawn an implementor + reviewer pair for a task."""
        nonlocal task_counter
        task_counter += 1
        impl_name = f"impl-{task_counter}"
        review_name = f"review-{task_counter}"
        rejections = 0
        branch_name = f"druids/{_slugify(task_name)}"

        impl_prompt = IMPLEMENTOR_SYSTEM_PROMPT.format(branch_name=branch_name)

        implementor = await ctx.agent(
            impl_name,
            system_prompt=impl_prompt,
            prompt=f"## Task: {task_name}\n\n{task_spec}",
            git="write",
            working_directory=working_dir,
        )

        reviewer = await ctx.agent(
            review_name,
            system_prompt=REVIEWER_SYSTEM_PROMPT,
            model="claude-sonnet-4-6",
            git="post",
            working_directory=working_dir,
            share_machine_with=implementor,
        )

        active_tasks[impl_name] = {"name": task_name, "reviewer": review_name}

        @implementor.on("submit_for_review")
        async def on_submit(summary: str = ""):
            """Submit your implementation for review. Call after pushing."""
            await reviewer.send(
                f"Review this implementation.\n\n"
                f"## Task spec\n\n{task_spec}\n\n"
                f"## Implementor summary\n\n{summary or '(none)'}\n\n"
                f"Read the diff with `git diff main...HEAD`. Demo the change. "
                f"If it works, create a PR with `gh pr create` and call "
                f"the `approve_and_pr` tool with the PR URL. "
                f"If not, call the `send_feedback` tool with what is wrong."
            )
            return "Submitted for review. Wait for feedback."

        @reviewer.on("approve_and_pr")
        async def on_approve(pr_url: str = "", summary: str = ""):
            """Signal the PR has been created. You MUST provide the pr_url."""
            if not pr_url:
                return "Error: pr_url is required. Run `gh pr create` first and pass the URL."
            active_tasks.pop(impl_name, None)
            if finder:
                await finder.send(f"Task '{task_name}' completed. PR: {pr_url}\nActive tasks: {len(active_tasks)}.")
            else:
                ctx.done(f"PR created: {pr_url}")
            return "Task complete."

        @reviewer.on("send_feedback")
        async def on_feedback(feedback: str = ""):
            """Send feedback to the implementor to fix issues."""
            nonlocal rejections
            rejections += 1
            if rejections >= MAX_REVIEW_ROUNDS:
                active_tasks.pop(impl_name, None)
                msg = f"Task '{task_name}' abandoned after {rejections} rounds. Last feedback: {feedback}"
                if finder:
                    await finder.send(msg + f"\nActive tasks: {len(active_tasks)}.")
                else:
                    ctx.done(msg)
                return "Max review rounds reached."

            await implementor.send(
                f"[Reviewer] Rejected ({rejections}/{MAX_REVIEW_ROUNDS}):\n\n"
                f"{feedback}\n\nFix, push, and call submit_for_review again."
            )
            return f"Feedback sent. ({rejections}/{MAX_REVIEW_ROUNDS} rounds)"

        return impl_name

    # Phase 1: direct task mode (no finder)
    if task_name and task_spec:
        finder = None
        await spawn_task(task_name, task_spec)
        return

    # Full mode: finder agent scans for tasks
    slack_instructions = ""
    if slack_token:
        slack_instructions = SLACK_INSTRUCTIONS.format(slack_token=slack_token)

    finder = await ctx.agent(
        "finder",
        system_prompt=FINDER_SYSTEM_PROMPT.format(
            repo_full_name=repo_full_name,
            slack_instructions=slack_instructions,
        ),
        prompt=f"Start scanning for tasks in {repo_full_name}.",
        git="post",
        working_directory=working_dir,
    )

    @finder.on("spawn_task")
    async def on_spawn_task(task_name: str = "", task_spec: str = ""):
        """Spawn an implementor + reviewer pair for a task."""
        if not task_name or not task_spec:
            return "Error: both task_name and task_spec are required."
        name = await spawn_task(task_name, task_spec)
        return f"Spawned {name} for '{task_name}'. Active tasks: {len(active_tasks)}."
