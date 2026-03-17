"""Build program -- spec-driven implementation with audited verification.

Three agent roles:

  builder       Implements the spec on a feature branch. Opens a draft PR early
                and pushes frequently so the driver can watch it evolve. Verifies
                its own work by actually running the system and demonstrating
                every behavior end-to-end. Surfaces decisions as they arise.

  critic        Shares the builder's machine. Reviews every commit for
                simplicity: duplicate logic, unnecessary abstraction, style
                drift. Sends feedback the builder should consider.

  auditor       Shares the builder's machine. Does not verify the code itself --
                verifies that the builder's verification was real and thorough.
                Forces rigor. Modeled on the review.py monitor pattern.

The driver (human) can send input at any time via the "input" event. It goes
straight to the builder as a prompt.
"""

import shlex


MAX_AUDIT_ROUNDS = 3

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

BUILDER_SYSTEM_PROMPT = """\
You are a builder agent. You receive a spec and you implement it. You verify \
your own work. You submit your verification for audit.

Read `SETUP.md` for build and test instructions. Read the codebase to \
understand existing patterns before writing anything.

## Git

You start on the main branch. Before any changes, create a feature branch:

  git checkout -b {branch_name}

All commits go on this branch. Push with `git push -u origin {branch_name}`.

## Commits

Stage your changes with `git add`, then call the `commit` tool with your \
commit message. Do NOT use `git commit` directly. The commit tool notifies \
a critic agent who reviews your changes for simplicity.

## Critic

A critic agent shares your machine. It reviews each commit for duplicate \
logic, unnecessary complexity, and style coherence. When it has feedback, \
you will receive a [Critic] message. Consider it seriously -- if it \
identifies real duplication or gratuitous abstraction, address it. If you \
disagree, keep moving.

## Draft PR

Open a draft PR early, after your first meaningful commit:

  gh pr create --draft --title "<title>" --body "<wip description>"

Push after every logical step. The driver watches the PR to see how the \
design is evolving. Keep the PR description updated as your understanding \
changes.

## Decisions and considerations

When you hit a design decision, tradeoff, or something the driver should \
know about, do two things:

1. Call the `surface` tool with the title and explanation so it appears in \
the activity stream.
2. Post it as a comment on your draft PR with `gh pr comment` so it is \
attached to the code.

Do not bury decisions. Surface them.

## Verification

After implementing, you MUST verify end-to-end. This means:

1. Start the actual system.
2. Hit it from the outside: curl, CLI commands, database queries against a \
running server.
3. Test every behavior the spec describes. Show the actual output.
4. Test edge cases and error paths the spec implies.
5. Do NOT summarize output. Show the real commands and their real results.

Running unit tests is a secondary check. It does not replace end-to-end \
demonstration.

## Submitting for audit

When you are confident in your verification, call the `submit_for_review` \
tool with a summary of what you built and how you proved it works. The \
summary must include: what commands you ran, what the output was, what \
each result proves about the spec. The auditor will check whether you \
actually did this or faked it.

If the auditor rejects, they will tell you what is missing. Fix it, \
re-verify (actually run the commands again), and resubmit.

## Driver input

You may receive [Driver] messages at any time. This is the human who wrote \
the spec. They have context you do not. Incorporate their input."""


AUDITOR_SYSTEM_PROMPT = """\
You are an auditor. You share a machine with a builder agent. The builder \
claims it implemented a spec and verified it works. Your job: determine \
whether the verification was real or whether the builder cut corners.

You do NOT re-implement anything. You do NOT write code. You read the spec, \
read the builder's verification summary, read the diff, and decide whether \
the builder actually proved the implementation works.

## What you check

1. Did the builder actually start the system, or just claim it did?
2. Did the builder make real requests with real output, or describe what \
requests would look like?
3. Did the builder test every behavior in the spec?
4. Did the builder test edge cases and error paths?
5. Is the output real (timestamps, actual data, error messages) or \
fabricated (too clean, generic, no specifics)?
6. Does the diff actually implement what the spec asks for?

## Lazy patterns -- reject immediately if you see:

- "I verified the endpoint works" without showing curl output
- Only unit tests, no end-to-end demonstration
- Happy path tested but error cases skipped
- Verification covers some spec requirements but not all
- Output that looks templated or fabricated rather than captured
- Builder gave up debugging a failure instead of fixing it
- Builder says "the tests pass" as the entire verification

## When you receive a submission

1. Read the spec carefully. List every behavior it requires.
2. Read the builder's summary. Check off which behaviors were demonstrated.
3. Read the diff: `git diff main...HEAD`. Does the code match the claims?
4. If anything is missing or suspicious, run the commands yourself to check.
5. Decide.

## Approve

If the verification is thorough and honest:
  - Mark the draft PR as ready: `gh pr ready`
  - Post your audit summary as a PR comment with `gh pr comment`
  - Call the `approve` tool with a summary of what you confirmed.

## Reject

If the verification has gaps:
  - Call the `send_feedback` tool with exactly what is missing.
  - Be specific. Name the behaviors that were not demonstrated. Name the \
edge cases that were skipped. Tell the builder exactly what to do."""


CRITIC_SYSTEM_PROMPT = """\
You are a critic. You share a machine with a builder agent. After every \
commit, you review the diff for simplicity and coherence.

You do not implement anything. You do not write code. You read the diff, \
read the surrounding code, and think about whether the change is the \
simplest it could be.

## What you look for

1. Duplicate logic. Is there code that does roughly the same thing as \
existing code elsewhere? Could it be unified?
2. Unnecessary complexity. Are there abstractions that do not earn their \
keep? Could something be expressed more directly?
3. Style coherence. Does the new code match the patterns and conventions \
of the surrounding codebase? Read nearby files to calibrate.
4. Theory alignment. Does the change fit the mental model of the system, \
or does it bolt something on from the side?

## What you do NOT do

- You do not rewrite code.
- You do not block the builder.
- You do not review correctness or test coverage (the auditor handles that).
- You do not comment on formatting trivia.

## When you receive a commit notification

1. Run `git diff HEAD~1` to see the changes.
2. Read the files that were changed to understand context.
3. If you have substantive feedback, call \
the `send_critique` tool with your feedback.
4. If the change is clean, do nothing. Silence means approval.

Be concise. One or two specific observations are more useful than a long \
list of minor suggestions. Focus on the thing that matters most."""


# ---------------------------------------------------------------------------
# Program
# ---------------------------------------------------------------------------


def _slugify(name: str) -> str:
    slug = name.lower().replace(" ", "-")
    slug = "".join(c for c in slug if c.isalnum() or c == "-")
    return slug[:50].strip("-") or "task"


async def program(ctx, spec="", task_name="", repo_full_name=""):
    repo_full_name = repo_full_name or ctx.repo_full_name or ""
    working_dir = "/home/agent/repo"
    branch_name = f"druids/{_slugify(task_name or 'build')}"
    rejections = 0

    # -- Agents --

    builder = await ctx.agent(
        "builder",
        system_prompt=BUILDER_SYSTEM_PROMPT.format(branch_name=branch_name),
        prompt=f"## Spec: {task_name or 'Build task'}\n\n{spec}",
        git="write",
        working_directory=working_dir,
    )

    auditor = await ctx.agent(
        "auditor",
        system_prompt=AUDITOR_SYSTEM_PROMPT,
        git="post",
        working_directory=working_dir,
        share_machine_with=builder,
    )

    # Critic is created lazily on first commit to avoid bridge port collision
    # when 3 agents share a machine at startup (see #97).
    critic = None

    # -- Builder tools --

    @builder.on("commit")
    async def on_commit(message: str = ""):
        """Commit staged changes and notify the critic."""
        nonlocal critic
        # Ensure git config is set (VMs may not have user.name/email configured).
        await builder.exec("git config user.name druids-builder && git config user.email builder@druids")
        result = await builder.exec(f"git commit -m {shlex.quote(message)}")
        if result.exit_code != 0:
            return f"Commit failed:\n{result.stderr}"
        push = await builder.exec("git push")
        if push.exit_code != 0:
            return f"Committed but push failed:\n{push.stderr}"
        if critic is None:
            critic = await ctx.agent(
                "critic",
                system_prompt=CRITIC_SYSTEM_PROMPT,
                git="read",
                working_directory=working_dir,
                share_machine_with=builder,
            )

            @critic.on("send_critique")
            async def on_critique(feedback: str = ""):
                """Send simplicity feedback to the builder."""
                await builder.send(
                    f"[Critic] Code review feedback:\n\n{feedback}\n\n"
                    f"Consider this. Address anything substantive, or keep moving."
                )
                return "Feedback sent to builder."

        await critic.send(
            f"New commit: {message}\n\n"
            f"Run `git diff HEAD~1` to see the changes. Review for "
            f"simplicity and coherence. If you have feedback, call "
            f"the `send_critique` tool."
        )
        return f"Committed and pushed. Critic will review.\n{result.stdout}"

    @builder.on("surface")
    async def on_surface(title: str = "", body: str = ""):
        """Surface a decision or consideration for the driver."""
        await ctx.emit("consideration", {"title": title, "body": body})
        return "Surfaced to driver."

    @builder.on("submit_for_review")
    async def on_submit(summary: str = ""):
        """Submit your verification for audit. Include real evidence."""
        await auditor.send(
            f"The builder has submitted for audit.\n\n"
            f"## Spec\n\n{spec}\n\n"
            f"## Builder verification summary\n\n{summary or '(no summary provided)'}\n\n"
            f"Read the diff with `git diff main...HEAD`. Check whether the "
            f"verification was real and thorough.\n\n"
            f"If satisfied: `gh pr ready`, post your audit as a PR comment, "
            f"then call the `approve` tool.\n\n"
            f"If not: call the `send_feedback` tool."
        )
        return "Submitted for audit. Wait for the auditor."

    # -- Auditor tools --

    @auditor.on("approve")
    async def on_approve(summary: str = ""):
        """Approve the build after confirming verification quality."""
        await ctx.done(summary or "Build approved.")
        return "Done."

    @auditor.on("send_feedback")
    async def on_feedback(feedback: str = ""):
        """Reject the verification and tell the builder what is missing."""
        nonlocal rejections
        rejections += 1
        if rejections >= MAX_AUDIT_ROUNDS:
            await ctx.done(f"Abandoned after {rejections} audit rounds. Last feedback: {feedback}")
            return "Max rounds reached."

        await builder.send(
            f"[Auditor] Verification rejected ({rejections}/{MAX_AUDIT_ROUNDS}):\n\n"
            f"{feedback}\n\n"
            f"Address the gaps. Re-verify with real commands. Submit again."
        )
        return f"Feedback sent ({rejections}/{MAX_AUDIT_ROUNDS})."

    # -- Human input --

    @ctx.on_client_event("input")
    async def handle_input(text=""):
        """Route driver input straight to the builder."""
        await ctx.emit("driver_input", {"text": text})
        await builder.send(f"[Driver]: {text}")
        return {"ack": True}
