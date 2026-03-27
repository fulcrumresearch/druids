# Tutorial: The build flow

This tutorial walks through `build.py`, the program that implements a spec using three cooperating agents. Reading it shows how the core druids primitives work together in a real flow.

## What it does

`build.py` implements a spec on a feature branch. Three agents collaborate:

- **builder** reads the spec, writes code, commits, and requests audits
- **critic** reviews each commit for simplicity and sends feedback
- **auditor** verifies the builder ran the tests for real before approving

The flow ends when the auditor is satisfied. The result is a branch with working, tested code.

## The program, annotated

```python
async def program(ctx, spec="", task_name="", repo_full_name=""):
```

Every program is an `async def` function. `ctx` is the execution context. Extra keyword arguments are passed from the command line (`druids exec build.py spec="..." task_name="..."`).

```python
    builder = await ctx.agent(
        "builder",
        system_prompt=BUILDER_SYSTEM_PROMPT.format(branch_name=branch_name),
        prompt=f"## Spec: {task_name}\n\n{spec}",
        git="write",
        working_directory=working_dir,
    )
```

`ctx.agent()` provisions a sandbox and starts an agent inside it. The call returns once the agent is running. `git="write"` gives it a GitHub token with push access. The agent immediately starts working on its initial prompt.

```python
    auditor = await ctx.agent(
        "auditor",
        git="post",
        share_machine_with=builder,
    )
```

`share_machine_with=builder` puts the auditor on the same VM as the builder. They share a filesystem, so the auditor can read the builder's uncommitted changes without a push. `git="post"` gives read access plus the ability to create PRs.

```python
    @builder.on("commit")
    async def on_commit(message: str = ""):
        """Commit staged changes and notify the critic."""
        result = await builder.exec(f"git commit -m {shlex.quote(message)}")
        await builder.exec("git push")
        if critic is None:
            critic = await ctx.agent("critic", git="read", share_machine_with=builder)
            ...
        await critic.send(f"New commit: {message}. Run git diff HEAD~1.")
        return f"Committed.\n{result.stdout}"
```

`@builder.on("commit")` registers a tool named `commit` that the builder can call. When the builder calls it, the handler runs on the server, not inside the sandbox. It executes shell commands inside the builder's sandbox (`builder.exec()`), spawns the critic on first commit, and forwards a message to the critic.

The return value becomes the tool result the agent sees. Agents use tool results to understand what happened and decide what to do next.

```python
    @auditor.on("approve")
    async def on_approve(summary: str = ""):
        await ctx.done(summary or "Build approved.")
        return "Done."
```

`await ctx.done()` ends the execution. After this call, the server stops all agents and cleans up the sandboxes. The string passed to `done()` is recorded as the execution result and shown in the dashboard.

## Running it

```bash
druids exec .druids/build.py --devbox owner/repo \
  task_name="add rate limiting" \
  spec="Add per-user rate limiting to the POST /api/keys endpoint. Max 10 keys per user. Return 429 with a clear error message when the limit is hit. Add tests."
```

## Why three agents?

One agent implementing and self-reviewing is less reliable than three agents with distinct roles. The critic sees only the diff, not the full context, which forces it to judge each change on its own merits. The auditor sees only the final output and asks: did the builder actually verify this? Neither can be satisfied by optimistic self-assessment.


## Variants

Change the agent count and roles to suit your task:

- Drop the critic if you trust the builder's judgment and want faster iterations.
- Add a second auditor with a different focus (security vs correctness).
- Replace the auditor's `approve` tool with a tool that runs your CI pipeline and passes only when it goes green.
- Use `model="claude-sonnet-4-6"` for the critic to save cost, since it only needs to read diffs.
