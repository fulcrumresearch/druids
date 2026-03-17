# How to write a Druids program

## Minimal program

```python
async def program(ctx, spec=""):
    agent = await ctx.agent("worker", prompt=spec, git="write")

    @agent.on("done")
    async def on_done(summary=""):
        ctx.done(summary)
        return "Finished."
```

```bash
druids exec my_program.py spec="Add a health check endpoint to the API"
```

A program is an async function named `program`. It receives `ctx` (the execution context) and keyword arguments from the CLI. This program creates one agent, gives it a prompt, and ends when the agent calls its `done` tool.

## Adding a reviewer

```python
async def program(ctx, spec=""):
    builder = await ctx.agent("builder", prompt=spec, git="write")
    reviewer = await ctx.agent(
        "reviewer",
        system_prompt="You review code. Read the diff, demo it, approve or reject.",
        git="post",
        share_machine_with=builder,
    )

    @builder.on("submit")
    async def on_submit(summary=""):
        await reviewer.send(f"Review this:\n\n{summary}\n\nDiff: `git diff main...HEAD`")
        return "Submitted for review."

    @reviewer.on("approve")
    async def on_approve(pr_url=""):
        ctx.done(f"PR: {pr_url}")
        return "Done."

    @reviewer.on("reject")
    async def on_reject(feedback=""):
        await builder.send(f"Fix this:\n{feedback}")
        return "Feedback sent."
```

`share_machine_with=builder` puts the reviewer on the same VM. They share a filesystem, so the reviewer reads the builder's files without a push. `git="post"` lets the reviewer create PRs but not push branches.

Tool handlers define the protocol. The builder calls `submit`, which messages the reviewer. The reviewer calls `approve` (ending the execution) or `reject` (looping back to the builder).

## Running commands on a VM

```python
@builder.on("deploy")
async def on_deploy(service=""):
    result = await builder.exec(f"cd /home/agent/repo && make deploy-{service}")
    if not result.ok:
        return f"Deploy failed:\n{result.stderr}"
    return f"Deployed.\n{result.stdout}"
```

`agent.exec()` runs a shell command on the agent's VM and returns an object with `exit_code`, `stdout`, `stderr`, and `ok` (a bool). The handler runs on the server, not inside the VM.

## Using system prompts

```python
BUILDER_PROMPT = """\
You are a builder. Read SETUP.md first. Create a feature branch:

  git checkout -b {branch}

Implement the spec. Run tests. Push. Call submit when ready."""

async def program(ctx, spec="", task_name=""):
    branch = f"druids/{task_name.lower().replace(' ', '-')}"
    builder = await ctx.agent(
        "builder",
        system_prompt=BUILDER_PROMPT.format(branch=branch),
        prompt=f"## Spec\n\n{spec}",
        git="write",
    )
```

The system prompt sets the agent's role and workflow. The user prompt provides the specific task. Keep system prompts reusable across tasks. Put task-specific details in the prompt.

For more patterns (parallel agents, client events, retry loops, dynamic agent creation), see the [tutorials](../tutorials/build-flow.md). For every method on `ctx` and `agent`, see the [program API reference](../reference/program-api.md).
