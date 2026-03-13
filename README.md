# Druids

Druids is a multi-agent orchestration system. It runs AI agents on cloud VMs and coordinates them through programs: Python async functions that create agents, register tool handlers, and manage lifecycle. Agents create git branches and open pull requests.

## How to use Druids

Druids is a way of structuring and launching agent flows as software. You write a program: a Python async function that creates agents, registers tool handlers, and calls `ctx.done()` when the work is complete. The program defines the shape of the flow. The agents do the work inside isolated sandboxes, and their outputs -- pull requests, reports, test results, whatever the program produces -- come back through whatever mechanism the program defines.

Because orchestration is code, you control where model intelligence gets applied. You can build interaction loops between agents, assign different agents to different roles, or run adversarial setups where one agent checks another's output before the program considers the task done. These flows are logic you write, test, and version alongside everything else.

The programs in [`.druids/`](.druids/) show what this looks like in practice:

- [`basher.py`](.druids/basher.py) -- a finder agent scans for tasks, then spawns implementor+reviewer pairs that iterate up to 3 times before acceptance or rejection.
- [`build.py`](.druids/build.py) -- a builder implements a spec, a critic reviews each commit for simplicity, and an auditor verifies the builder's tests were real. Three agents, iterating until all three are satisfied.
- [`review.py`](.druids/review.py) -- a demo agent checks out a PR and tests it on a real system, while a monitor agent watches and nudges if the demo agent cuts corners.
- [`doc-align.py`](.druids/doc-align.py) -- an auditor reads every doc and compares against source code to produce a report, then a fixer+reviewer pair corrects the discrepancies.
- [`main.py`](.druids/main.py) -- spawns a Claude and a Codex agent in parallel on the same spec, both implementing independently.

The right mental model is close to deciding on the shape of a team. You define the structure of the work, the roles, the feedback loops, and hand it off. The agents run in the background. The quality of the environment matters as much as the instructions. An agent given a clean, well-scoped context with clear success criteria will outperform the same agent given a broad, ambiguous one. That preparation is most of what separates a reliable flow from an unreliable one.

## Quickstart

You need Docker, [uv](https://docs.astral.sh/uv/), and an Anthropic API key.

```bash
bash scripts/setup.sh
```

This builds images, starts the stack, and configures the CLI. Then create a devbox and run a program:

```bash
druids setup start --repo owner/repo
# install project dependencies in the SSH session, then:
druids setup finish --name owner/repo
druids exec .druids/basher.py --devbox owner/repo task_name="test" task_spec="Hello world"
```

See [QUICKSTART.md](QUICKSTART.md) for manual setup, running without Docker Compose, and troubleshooting.

## How it works

When you run `druids exec program.py spec="..."`, the server creates a sandbox from a devbox snapshot, deploys a bridge process, and spawns an agent subprocess. The bridge connects back to the server via a reverse relay, so no inbound connections to the sandbox are needed. The server sends the initial prompt, the agent works, and signals completion through program-defined tools.

Programs orchestrate this by creating agents (`ctx.agent()`), registering tool handlers (`@agent.on("tool_name")`), and signaling lifecycle events (`ctx.done()`, `ctx.fail()`). 

## Architecture

- [server/](server/README.md) -- FastAPI server, execution engine, sandbox management
- [client/](client/README.md) -- CLI and agent-side client library
- [bridge/](bridge/README.md) -- ACP bridge deployed into sandboxes
- [specs/programs.md](specs/programs.md) -- program system design
