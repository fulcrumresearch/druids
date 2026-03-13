"""Ralph program -- dumb persistent iteration until done.

Based on the Ralph Wiggum technique. One agent, one prompt, one loop. The
agent works on the spec, then calls `done`. The program checks the agent's
output for a completion promise (exact string match). If the promise is not
found, the program re-sends the prompt. The agent sees its own prior file
changes and git history from previous iterations and self-corrects.

No second agent. No judgment-based evaluation. The promise is a string match,
not an opinion. The loop is mechanical.

  worker        Receives the spec. Works on it. Outputs the promise string
                when finished. If the promise is missing, gets the same
                prompt again. Persists until done.

The driver (human) can send input at any time via the "input" event.
"""

MAX_ITERATIONS = 25
DEFAULT_PROMISE = "RALPH_COMPLETE"


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

WORKER_SYSTEM_PROMPT = """\
You are a worker agent in a Ralph loop. You receive a task. You do the task. \
When it is done, you output the promise string. If you do not output the \
promise string, you get the same prompt again. This continues until you \
succeed or the iteration limit is reached.

Read `SETUP.md` for build and test instructions. Read the codebase before \
writing anything.

## Git

You start on the main branch. Before any changes, create a feature branch:

  git checkout -b {branch_name}

All commits go on this branch. Push with `git push -u origin {branch_name}`.

Open a draft PR early:

  gh pr create --draft --title "<title>" --body "<wip description>"

Push after every logical step.

## Iteration

This is a loop. Each iteration you see the full repo state, including \
everything you changed in prior iterations. Do not start over. Read \
`git log` and your own files. Pick up where you left off.

If you are stuck on the same error for two iterations, try a different \
approach entirely.

## Completion

When the task is fully done -- code works, tests pass, everything the spec \
asks for is verified -- output exactly this on its own line:

  {promise}

Do NOT output the promise until you have verified everything. Run the tests. \
Hit the system from the outside if applicable. Confirm it works.

If you are not done, call the `done` tool with a summary of your progress \
so far and the loop will continue.

If you are done, call the `done` tool with a summary of what you did. \
Include the promise string {promise} in your summary."""


# ---------------------------------------------------------------------------
# Program
# ---------------------------------------------------------------------------


def _slugify(name: str) -> str:
    slug = name.lower().replace(" ", "-")
    slug = "".join(c for c in slug if c.isalnum() or c == "-")
    return slug[:50].strip("-") or "task"


async def program(ctx, spec="", task_name="", repo_full_name="", promise="", max_iterations=""):
    """Ralph loop. One agent, one prompt, iterate until the promise appears."""
    repo_full_name = repo_full_name or ctx.repo_full_name or ""
    working_dir = "/home/agent/repo"
    branch_name = f"druids/{_slugify(task_name or 'ralph')}"
    promise = promise or DEFAULT_PROMISE
    max_iter = int(max_iterations) if max_iterations else MAX_ITERATIONS
    iteration = 0

    task_prompt = (
        f"## Task: {task_name or 'Ralph task'}\n\n{spec}\n\nWhen complete, include `{promise}` in your summary."
    )

    worker = await ctx.agent(
        "worker",
        system_prompt=WORKER_SYSTEM_PROMPT.format(
            branch_name=branch_name,
            promise=promise,
        ),
        prompt=task_prompt,
        git="write",
        working_directory=working_dir,
    )

    @worker.on("done")
    async def on_done(summary: str = ""):
        """Call when you are done with this iteration."""
        nonlocal iteration
        iteration += 1

        # The only check: does the summary contain the promise string?
        if promise in summary:
            # Mark PR as ready if one exists
            await worker.exec("gh pr ready 2>/dev/null || true")
            ctx.done(f"Ralph complete in {iteration} iteration(s). {summary}")
            return "Done."

        if iteration >= max_iter:
            ctx.done(f"Ralph stopped after {iteration} iterations (limit: {max_iter}). Last summary: {summary}")
            return "Max iterations reached."

        # No promise found. Re-feed the prompt.
        await worker.send(
            f"Iteration {iteration}/{max_iter}. The promise `{promise}` was "
            f"not found in your output. The task is not complete.\n\n"
            f"Your prior work is in the repo. Run `git log --oneline` and "
            f"read your files to see what you did. Pick up where you left off.\n\n"
            f"{task_prompt}"
        )
        return f"Continuing. Iteration {iteration}/{max_iter}."

    # -- Human input --

    @ctx.on_client_event("input")
    async def handle_input(text=""):
        """Route driver input to the worker."""
        ctx.emit("driver_input", {"text": text})
        await worker.send(f"[Driver]: {text}")
        return {"ack": True}
