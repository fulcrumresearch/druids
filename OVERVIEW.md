# Druids

Druids is a library for launching software agents on remote sandboxes. You write a Python program that defines which agents to create, what tools they have, and how they coordinate. Druids provisions cloud VMs, deploys agents into them, and streams events back to your terminal. 

## How it works

An execution starts with two inputs: a program and a spec. The program is a Python file that defines the shape of the work. The spec is a plain-text description of what you want built. You run `druids exec` and the system takes it from there.

The server reads your program, provisions a sandbox from a saved snapshot of your repo (called a devbox), and starts the agents your program defines. Each agent runs in an isolated environment with access to the repo, its dependencies, and any tools the program registers. Agents work independently, calling tools, reading and writing code, running tests, and pushing commits.

Programs control coordination. A program can wire agents into feedback loops, where one agent implements and another reviews. It can set up approval gates, enforce iteration limits, or route human input to the right agent. When the work is done, the program signals completion and the execution ends.

## Programs

A program is an async Python function. It receives a context object and the spec as arguments. From there, it creates agents, registers tool handlers, and sends prompts.

```python
async def program(ctx, spec="", **kwargs):
    builder = await ctx.agent("builder", prompt=f"Implement this:\n\n{spec}", git="write")

    @builder.on("submit")
    async def on_submit(summary: str = ""):
        ctx.done(summary)
```

This is a minimal program. It creates one agent with write access to git, gives it the spec as a prompt, and registers a `submit` tool the agent can call when it finishes. When the agent calls `submit`, the handler runs `ctx.done()` and the execution ends.

Tool handlers are the mechanism for everything: ending the execution, sending messages between agents, enforcing constraints, spawning new agents mid-run. A handler registered with `@agent.on("tool_name")` becomes an MCP tool the agent can call. The handler's return value goes back to the agent as the tool result.

Programs can be simple (one agent, one tool) or elaborate (multiple agents with different roles, iterative review loops, dynamic agent creation). The `.druids/` directory in a repo holds programs. `build.py` runs a builder, critic, and auditor in a feedback loop. `ralph.py` runs a single agent in a mechanical retry loop until it produces the right output. The orchestration is just Python, so you can compose programs however you want.

## Getting started

Install the CLI, authenticate, set up a devbox for your repo, and run a program.

```
pip install druids
druids auth set-key <your-api-key>
druids setup start --repo owner/repo
```

The `setup start` command provisions a sandbox and prints SSH credentials. SSH in, install your project's dependencies, configure anything the agents will need, then finalize the snapshot:

```
druids setup finish --name owner/repo
```

Now run a program against it:

```
druids exec .druids/build.py spec="Add a /healthz endpoint that returns 200"
```

The CLI streams events as agents work. When they finish, review the PR they opened.
