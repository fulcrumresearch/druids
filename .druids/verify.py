"""Verify program -- find bugs by running the system end-to-end.

Proposers read the codebase and propose tests that exercise real user flows:
CLI commands, full execution lifecycle, the client library, the setup flow.
Not input validation. Not happy-path unit tests. Real integration tests that
start the server, hit endpoints, and check that the system actually works.

Verifiers run each test on a real VM with the server running. Failures become
GitHub issues via the filer agent.
"""

from __future__ import annotations


PROPOSER_SYSTEM = """\
You are a bug hunter. You read codebases and find things that are broken, \
half-implemented, or will fail when a real user tries them.

Read the code deeply. Focus on INTEGRATION POINTS and END-TO-END FLOWS, \
not input validation or trivial checks. The goal is to find bugs that \
would embarrass us in an open source release.

## What to test

Priority 1 -- full user flows:
- Does the CLI actually work? Can you run `uv run druids executions` \
and get output? Can you create an execution via the CLI?
- Does the Python client library work? Can you import it, create a \
client, and call methods?
- Does the server start and serve the OpenAPI docs?
- Can you create an execution via the API, then GET it back?
- Does the setup flow work? Can you call setup endpoints?

Priority 2 -- integration between components:
- Does the bridge relay actually accept connections and relay messages?
- Does the runtime start and serve its tool listing endpoint?
- Do secrets get stored and retrieved correctly?
- Does the execution lifecycle work? Create -> running -> stopped?
- Do agent connections get tracked properly?

Priority 3 -- edge cases that will bite users:
- What happens when you create an execution with a program that imports \
a module that does not exist?
- What happens when the database has no tables yet (first run)?
- What happens when you stop an execution that is already stopped?
- What happens when you try to GET an execution that does not exist?
- Are there any endpoints that return 500 instead of a proper error?

## What NOT to test

- Simple input validation (these always pass and find nothing)
- Anything requiring MorphCloud or external services
- Performance or timing-dependent behavior
- Code style or naming

## How to propose

Call `propose_test` with:
- title: short description
- claim: what the code claims to do, with file references
- steps: exact runnable commands. The server starts with: \
cd /home/agent/repo/server && DRUIDS_LOCAL_MODE=1 \
ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY uv run druids-server & \
The CLI is at: cd /home/agent/repo/client && uv run druids --help
- expected: what should happen if it works

Write steps that a human could copy-paste and run. Be specific about \
what the output should look like.

Propose 8-12 tests. Call `done_proposing` when finished."""


VERIFIER_SYSTEM = """\
You are a bug hunter's verifier. You receive test proposals and execute \
them on a real system. Your job is to find out what is ACTUALLY broken.

## Environment

You have the repo at /home/agent/repo with all dependencies installed.

Start the server:
  cd /home/agent/repo/server
  DRUIDS_LOCAL_MODE=1 ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY \
  uv run druids-server > /tmp/server.log 2>&1 &

Wait for ready:
  for i in $(seq 1 30); do curl -sf http://localhost:8000/health && break; sleep 1; done

If health check fails after 30s, check /tmp/server.log for errors.

The CLI client is at:
  cd /home/agent/repo/client
  uv run druids --help

## Rules

1. Run the EXACT steps from the proposal first.
2. If they fail, try reasonable variations.
3. If the server will not start after 2 tries, call verdict with skip.
4. Do NOT spend more than 5 minutes on one test.
5. Show exact commands and exact output. No summaries.
6. Call `verdict` when done. Do not forget to call it.

verdict values: pass, fail, skip."""


FILER_SYSTEM = """\
You file GitHub issues for bugs found during verification.

Title: [Verify] <short description>

Body must include:
- What was expected
- What actually happened
- Exact reproduction steps (commands that can be copy-pasted)
- Server log snippets if relevant

Use: gh issue create --repo fulcrumresearch/druids --label bug
If the bug label does not exist, skip the label flag.

After filing, call `filed` with the issue URL."""


async def program(ctx, focus_areas="", n_proposers="3", n_verifiers="3", repo_full_name=""):
    """Find bugs by running the system end-to-end."""
    repo_full_name = repo_full_name or ctx.repo_full_name or ""
    working_dir = "/home/agent/repo"
    n_prop = int(n_proposers)
    n_ver = int(n_verifiers)

    proposals = {}
    proposal_counter = 0
    proposers_done = 0
    issues_filed = []

    pool = []
    queue = []

    filer = None

    async def dispatch(pid):
        """Assign a proposal to an idle verifier, or queue it."""
        for entry in pool:
            if not entry["busy"]:
                entry["busy"] = True
                entry["pid"] = pid
                prop = proposals[pid]
                prop["status"] = "verifying"
                prop["verifier"] = entry["name"]
                await entry["agent"].send(
                    f"## Test Assignment: {prop['title']}\n\n"
                    f"**Claim:** {prop['claim']}\n\n"
                    f"**Steps:**\n\n{prop['steps']}\n\n"
                    f"**Expected:** {prop['expected']}\n\n"
                    f"Run this test and call `verdict` with the result."
                )
                await ctx.emit("dispatched", {"proposal_id": pid, "verifier": entry["name"]})
                return
        queue.append(pid)
        await ctx.emit("queued", {"proposal_id": pid, "queue_length": len(queue)})

    # -- Create verifier pool --

    for i in range(n_ver):
        agent = await ctx.agent(
            f"verifier-{i}",
            system_prompt=VERIFIER_SYSTEM,
            prompt=(
                "You are verifier {i}. Start the server now:\n\n"
                "  cd /home/agent/repo/server\n"
                "  DRUIDS_LOCAL_MODE=1 ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY "
                "uv run druids-server > /tmp/server.log 2>&1 &\n\n"
                "  for i in $(seq 1 30); do curl -sf http://localhost:8000/health "
                "&& echo 'Server ready' && break; sleep 1; done\n\n"
                "Once the server is ready, wait for test assignments."
            ),
            git="read",
            working_directory=working_dir,
        )
        entry = {"agent": agent, "name": f"verifier-{i}", "busy": False, "pid": None}

        def _reg_verdict(e):
            @e["agent"].on("verdict")
            async def on_verdict(result="", evidence=""):
                """Deliver test verdict: pass, fail, or skip."""
                pid = e["pid"]
                if pid is None:
                    return "No test assigned. Wait for a test assignment."
                prop = proposals[pid]
                prop["status"] = result
                prop["verdict"] = result
                prop["evidence"] = evidence

                await ctx.emit(
                    "verdict",
                    {
                        "proposal_id": pid,
                        "title": prop["title"],
                        "result": result,
                        "evidence": evidence[:500],
                    },
                )

                if result == "fail" and filer is not None:
                    await filer.send(
                        f"[Failed Test] {prop['title']}\n\n"
                        f"## Claim\n\n{prop['claim']}\n\n"
                        f"## Steps\n\n{prop['steps']}\n\n"
                        f"## Expected\n\n{prop['expected']}\n\n"
                        f"## Actual (from verifier)\n\n{evidence}\n\n"
                        f"File a GitHub issue for this failure."
                    )

                e["busy"] = False
                e["pid"] = None
                if queue:
                    next_pid = queue.pop(0)
                    await dispatch(next_pid)

                return f"Verdict recorded: {result}. You may receive another assignment."

        _reg_verdict(entry)
        pool.append(entry)

    # -- Create filer --

    filer = await ctx.agent(
        "filer",
        system_prompt=FILER_SYSTEM,
        prompt="You will receive failed test results. File GitHub issues for each one.",
        git="post",
        working_directory=working_dir,
    )

    @filer.on("filed")
    async def on_filed(issue_url=""):
        """Called after filing a GitHub issue."""
        issues_filed.append(issue_url)
        await ctx.emit("issue_filed", {"url": issue_url, "total": len(issues_filed)})
        return f"Filed. Total issues: {len(issues_filed)}."

    # -- Create proposers --

    areas = [
        "CLI client and Python client library (client/druids/). "
        "Test that the CLI commands actually run, that the client can "
        "connect to the server, and that the documented workflows work.",
        "server API -- full request/response cycles, not just validation. "
        "Create an execution, GET it back, stop it, list executions. "
        "Test the setup endpoints, secrets, devbox management. "
        "Look in server/druids_server/api/routes/.",
        "execution lifecycle and bridge (server/druids_server/lib/). "
        "Test that executions transition through states correctly. "
        "Test the bridge relay, agent connections, the runtime relay. "
        "Look for race conditions, missing error handling, broken flows.",
    ]

    if focus_areas:
        areas = [a.strip() for a in focus_areas.split(",")]

    while len(areas) < n_prop:
        areas.append("any area not yet covered -- look for broken things")
    areas = areas[:n_prop]

    for i in range(n_prop):
        proposer = await ctx.agent(
            f"proposer-{i}",
            system_prompt=PROPOSER_SYSTEM,
            prompt=(
                f"Read the codebase at {working_dir}. Your focus area:\n\n"
                f"{areas[i]}\n\n"
                + (f"Additional context:\n\n{focus_areas}\n\n" if focus_areas else "")
                + "Read README.md and QUICKSTART.md first for project overview. "
                + "Then read source code in your area. "
                + "Propose 8-12 tests that will FIND BUGS. "
                + "Do not propose tests you expect to pass -- propose tests "
                + "that exercise real flows and might break. "
                + "Call `done_proposing` when finished."
            ),
            git="read",
            working_directory=working_dir,
        )

        def _reg_proposer(p, idx):
            @p.on("propose_test")
            async def on_propose(title="", claim="", steps="", expected=""):
                """Propose a behavioral test to verify."""
                nonlocal proposal_counter
                proposal_counter += 1
                pid = f"T{proposal_counter}"
                proposals[pid] = {
                    "title": title,
                    "claim": claim,
                    "steps": steps,
                    "expected": expected,
                    "status": "proposed",
                    "verdict": None,
                    "evidence": None,
                    "verifier": None,
                    "proposer": idx,
                }
                await ctx.emit("proposal", {"id": pid, "title": title, "claim": claim})
                await dispatch(pid)
                return f"Test {pid} proposed and dispatched for verification."

            @p.on("done_proposing")
            async def on_done():
                """Call when you have finished proposing tests."""
                nonlocal proposers_done
                proposers_done += 1
                await ctx.emit("proposer_done", {"proposer": idx, "total_done": proposers_done})
                return f"Done. {proposers_done}/{n_prop} proposers finished."

        _reg_proposer(proposer, i)

    # -- Client events --

    @ctx.on_client_event("get_state")
    def get_state():
        """Return current state."""
        return {
            "proposals": {
                pid: {
                    "title": p["title"],
                    "status": p["status"],
                    "verdict": p["verdict"],
                    "verifier": p["verifier"],
                }
                for pid, p in proposals.items()
            },
            "proposers_done": proposers_done,
            "verifier_count": n_ver,
            "pool_idle": sum(1 for e in pool if not e["busy"]),
            "queue_length": len(queue),
            "issues_filed": issues_filed,
        }

    @ctx.on_client_event("focus")
    async def on_focus(area=""):
        """Tell all proposers to look at a specific area."""
        for i in range(n_prop):
            agent = ctx.agents.get(f"proposer-{i}")
            if agent:
                await agent.send(f"[Driver] Also look at: {area}")
        return {"ack": True}

    @ctx.on_client_event("input")
    async def on_input(agent_name="", text=""):
        """Send a message to any agent."""
        agent = ctx.agents.get(agent_name)
        if not agent:
            return {"error": f"Unknown agent: {agent_name}"}
        await agent.send(f"[Driver]: {text}")
        return {"ack": True}

    await ctx.emit("ready", {"message": "Bug hunt started. Proposers reading. Verifiers warming up."})
    await ctx.wait()
