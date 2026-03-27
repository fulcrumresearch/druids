# Druids

Druids is a batteries-included library to coordinate and deploy coding agents across machines. Druids makes it easy to do this by abstracting away all the VM infrastructure, agent provisioning, and communication.

For example, here's a Druids program to deploy N task agents on N copies of a software environment and have a judge pick the best output:

```python
async def program(ctx, spec="", n=3):
    submissions = {}

    # each agent gets its own sandboxed VM with your repo
    judge = await ctx.agent("judge")

    # events define how agents report back to the program
    @judge.on("pick")
    async def on_pick(winner=""):
        await ctx.done({"winner": winner, "submissions": submissions})

    # spawn n workers in parallel — each implements the spec independently
    for i in range(n):
        worker = await ctx.agent(f"worker-{i}", prompt=spec, git="write")
        worker_name = worker.name

        @worker.on("submit")
        async def on_submit(pr_url=""):
            submissions[worker_name] = pr_url
            if len(submissions) == n:
                # all done — send the PRs to the judge
                await judge.send(f"Review these PRs and pick the best:\n\n{submissions}")
```

```bash
druids exec best_of_n.py spec="Refactor the auth module" n=5
```

Druids is useful for things like:

- running many agents to do performance optimization
- building custom automated software pipelines for eg code review, pentesting, large-scale migrations, long-running autonomous features
- building data pipelines with agents

You can use it locally or deployed on [druids.dev](https://druids.dev).

[Website](https://druids.dev) · [Docs](https://druids.dev/docs) · [Discord](https://discord.gg/QmMybVuwWp)

**[Demo video](https://www.youtube.com/watch?v=EVJqW-tvSy4)**

[![Watch the demo](https://img.youtube.com/vi/EVJqW-tvSy4/maxresdefault.jpg)](https://www.youtube.com/watch?v=EVJqW-tvSy4)

## Agent programs

A druids program is an async function that defines how your agents should run on your task. You create agents, define events they can trigger, and control what happens when they do. The agent decides *when* to trigger an event — the program decides *what happens*.

Events allow you to inject deterministic structure and control flow to structure how your agents work towards the task of the program. They are useful for defining controlled steps and flows, like:

- forcing a model to iterate against hard tests and harness signals
- building a verification hierarchy, where agents spawn outputs that are verified and redteamed by other agents until they match a set of properties
- controlling distributed task state, like having a lock around the ways agents write to shared resources or user-facing systems

Each agent gets a sandboxed VM with your repo and dependencies. Agents can share machines (`share_machine_with`), transfer files (`ctx.connect`), and work on git branches. On the hosted version, `agent.fork()` creates instant copy-on-write clones. You can message any agent while it runs, inspect program state, and redirect work without stopping the execution.

## Quickstart

You need Docker, [uv](https://docs.astral.sh/uv/), and an Anthropic API key.

```bash
bash scripts/setup.sh
```

This builds the Docker image, starts the server, and configures the CLI. Then run a program:

```bash
druids exec .druids/build.py spec="Add a /health endpoint that returns 200 OK"
```

See the [getting started guide](https://druids.dev/docs/get-started) for a full walkthrough, or [QUICKSTART.md](QUICKSTART.md) for manual setup and troubleshooting.

## Example programs

- [`build.py`](.druids/build.py) — builder + critic + auditor. Three agents iterating until all are satisfied.
- [`basher.py`](.druids/basher.py) — finder scans for tasks, spawns implementor+reviewer pairs.
- [`review.py`](.druids/review.py) — demo agent tests a PR on a real system, monitor watches for shortcuts.
- [`main.py`](.druids/main.py) — Claude and Codex racing on the same spec in parallel.

## Architecture

- [`server/`](server/) — FastAPI server, execution engine, sandbox management
- [`client/`](client/) — CLI and Python client library
- [`runtime/`](runtime/) — program runtime SDK
- [`frontend/`](frontend/) — Vue 3 dashboard
- [`docs/`](docs/) — documentation (also served at druids.dev/docs)
