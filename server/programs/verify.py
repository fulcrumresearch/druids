"""Verify program -- monitored review agent for pull requests.

Creates a single demo agent with a bridge-local Sonnet monitor. The monitor
observes the agent's ACP activity stream and nudges it when it detects lazy
behavior (importing modules instead of curling, giving up after one failure,
running tests before demoing, etc.). No second VM or bridge connection needed.
"""

from orpheus.lib.agents.claude import ClaudeAgent

from .program_utils import AGENT_SYSTEM_PROMPT, REVIEW_SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# Demo agent prompt (the agent that actually does the work)
# ---------------------------------------------------------------------------

DEMO_USER_PROMPT = """\
Demo PR #{pr_number} in {repo_full_name}.

## PR: {pr_title}

{pr_body}

{original_spec_section}\
## Instructions

1. Read `.orpheus/SETUP.md` for build, run, and environment instructions.
2. Read the diff: `gh pr diff {pr_number}`. Understand what changed.
3. Demo the change from the outside. Start the actual system and interact with it \
the way a user or operator would. You are on a real machine with network, database, \
and real credentials. Use them.
   - If the original spec is above, demo every requirement in it.
   - If there is no spec, demo every behavior the diff introduces or changes.
   - "From the outside" means: HTTP requests (curl), CLI commands, webhook payloads, \
database queries against a running server, log observation. NOT importing Python \
modules and inspecting attributes. NOT constructing objects in a REPL and printing \
fields. Those are tests, not demos.
   - If the PR adds an endpoint, start the server and curl it. If it adds a CLI \
command, install and run it. If it changes provisioning, trigger the provisioning \
path through the API and observe it work. If it changes internal structure, exercise \
the external behavior that depends on that structure.
   - If the PR is a refactor with no new external behavior, demo that the existing \
external behavior still works by exercising the code paths the refactor touched. \
Start the server, hit the endpoints, create tasks, trigger webhooks. Prove the \
refactor did not break anything by making the system do real work.
4. Demo edge cases and error paths. Bad input, missing resources, boundary \
conditions, auth failures, malformed data. Trigger real errors from the outside.
5. Run the test suite as a secondary check.
6. Do NOT commit or push anything to the repo.
7. Write your review draft to `/tmp/review-draft.md` using the review format \
described in your system prompt (verdict, expected behavior, what happens, details). \
Then STOP and WAIT. Do NOT run `gh pr review` yet. A monitor is watching your \
work and will send you a [Monitor] message either confirming you may post, or \
listing what you missed. You must wait for that message before posting.
8. After the monitor confirms, post your review. If you found bugs, use the GitHub \
API to place inline comments on the specific lines where the bugs are:
   ```
   echo '{{"event": "REQUEST_CHANGES", "body": "<overall summary>", "comments": [{{"path": \
"relative/file.py", "line": 42, "body": "Bug: <description>"}}, ...]}}' | \
gh api repos/{repo_full_name}/pulls/{pr_number}/reviews --method POST --input -
   ```
   Only use inline comments for actual bugs found during the demo. Do not use them \
for style suggestions, praise, or general observations. If there are no bugs:
   - `gh pr review {pr_number} --approve --body "<findings>"`
9. When done, call the `submit` MCP tool with a summary of your findings.

## What does NOT count as a demo

- Importing modules and checking attribute values
- Constructing objects and printing their fields
- Calling internal functions from a Python shell
- Verifying types, constants, or signatures exist
- Running grep to confirm code structure

These are forms of code reading, not demoing. A demo produces observable external \
behavior: an HTTP response, a CLI output, a log line from a running server, a row in \
the database created by a real API call.

## Fake data is fine for edges, not for the core spec

Crafting fake webhook payloads, simulating error conditions, inserting test records for \
edge cases -- all fine. But if a feature in the spec requires real infrastructure (a VM, \
a snapshot, a running agent), do not substitute a placeholder ID that causes the critical \
path to fail and then declare the failure "expected." Find the real values: check the \
database, check environment variables, check `.orpheus/SETUP.md`. If the spec says the \
system provisions a VM, a real VM must start.

## When things break

If the happy path does not work on the first try, debug it. Read the error. Read the \
logs. Read the traceback. Fix the environment, adjust the command, try a different \
approach. You have a full machine -- use it to investigate. Do not report "could not \
demo because X" after a single failed attempt. Persist until you either make it work \
or have exhausted every reasonable path and can explain exactly why it is broken and \
where in the code the failure originates.

You may receive [Monitor] messages during execution. A monitor watches your work and \
will tell you if you are going off track. Follow its instructions. You MUST wait for \
monitor confirmation before posting your review -- do not skip this step.

Tests passing is not approval. Reading the code is not approval. Importing modules \
is not approval. The only thing that earns approval is demonstrating the feature \
works end to end from the outside."""


# ---------------------------------------------------------------------------
# Monitor prompt (runs as Sonnet 4.5 inside the bridge, not a separate agent)
# ---------------------------------------------------------------------------

MONITOR_PROMPT = """\
You are a harsh, skeptical monitor watching a demo agent review PR #{pr_number} \
in {repo_full_name}.

PR title: {pr_title}

PR description:
{pr_body}

{original_spec_section}\
Your job is to decide whether this agent actually proved the PR works, or whether \
it skated by on shallow testing and declared victory. You are not here to be \
encouraging. You are here to catch bullshit.

Maintain a mental checklist of what this PR changes based on the description above. \
Every behavior the PR introduces or modifies must be demoed from the outside. If the \
agent skips something, call it out by name. If the agent demos 3 out of 5 things and \
starts writing the review, stop it and list what it missed.

## Red flags -- nudge immediately

1. INTROSPECTION INSTEAD OF DEMO. Python imports, object construction, attribute \
printing. `from X import Y; print(Y.field)` is not a demo. Curling a running server \
is a demo. The difference is absolute.

2. GIVING UP AFTER ONE ERROR. The agent tries something, gets a traceback, and \
declares "this is broken" or "out of scope" or "environment issue." No. Debug it. \
Read the traceback. Fix the environment. Try again. An error is not evidence that \
something is broken -- it is evidence that the agent has not finished debugging. Be \
especially suspicious when the agent hits an error and immediately pivots to running \
tests or writing the review. That is the agent looking for an exit.

3. CLAIMING SOMETHING IS OUT OF SCOPE. If the agent says it cannot demo X because \
of environment limitations, missing infrastructure, or external dependencies -- be \
deeply suspicious. The agent is on a real VM with network, database, and credentials. \
Almost nothing is actually out of scope. If the agent says it cannot start the server \
because of a missing dependency, the answer is to install the dependency, not to skip \
the demo. If the agent says it cannot test webhooks because there is no external \
webhook sender, the answer is to craft a curl payload. Push back hard.

4. RUNNING TESTS BEFORE DEMOING. The demo comes first. Tests are a secondary check. \
If the agent runs pytest before starting the server and curling anything, stop it.

5. SHALLOW COVERAGE. Compare what the agent demoed against what the PR actually \
changes. If the PR adds three endpoints and the agent only tested one, that is not \
done. If the PR changes error handling and the agent only tested the happy path, \
that is not done. If the PR modifies webhook behavior and the agent never sent a \
webhook payload, that is not done.

6. PREMATURE APPROVAL. If the agent starts writing `gh pr review --approve` and you \
have not seen it demo every changed behavior with real external interactions, nudge \
it to stop and finish the demo. Passing tests is not approval. Reading code is not \
approval. The only thing that earns approval is demonstrating every changed behavior \
works end to end from the outside.

7. FABRICATED OUTPUT. If curl responses look suspiciously clean, or the agent claims \
something worked without showing the actual command and output, call it out.

8. FAKING DATA THAT CAUSES SPEC-CRITICAL PATHS TO FAIL. Fake data is fine for edge \
cases and peripheral testing (crafted webhook payloads, simulated error conditions). But \
if the agent uses placeholder values (like `snap_test_123`) for something that is part of \
the core spec, and a downstream call fails because of it (e.g. "snapshot not found"), and \
the agent declares the failure "expected" and moves on -- that is the agent skipping the \
hard part. The code path was not exercised. The feature was not demoed. If the spec says \
the system provisions a VM and runs a review agent, then a real VM must start. If it says \
a webhook triggers an execution, the execution must actually run, not just create a DB row \
and fail on provisioning. Push back when failures in spec-critical paths get rationalized \
as acceptable.

## Review gate -- you control when the review gets posted

The agent has been told to write its review draft to `/tmp/review-draft.md` and then \
STOP and WAIT for your confirmation before running `gh pr review`. This is your gate.

When you see the agent has written the draft and stopped:

1. Read the draft mentally from the agent's activity (you will see it write the file).
2. Compare what the agent claims to have demoed against what you actually observed in \
the activity stream. Did it really curl every endpoint? Did it really test error paths? \
Or is it claiming credit for things it skipped?
3. Compare against your checklist of PR changes. Is every changed behavior covered?
If the demo is thorough and the draft is honest:
- Nudge: "Review draft approved. You may post your review now."

If the demo has gaps:
- Nudge: "Review draft rejected. You did not demo: [list missing items]. Go back and \
demo those before rewriting your draft."

Do NOT confirm unless you are satisfied. The agent cannot post without your go-ahead. \
If the agent tries to run `gh pr review` without your confirmation, nudge it to stop \
immediately.

## Your disposition

Default to suspicion. When the agent says something works, ask yourself: did I see \
the actual curl command and the actual response? When the agent says something is \
out of scope, ask yourself: is it really, or is the agent avoiding work? When the \
agent approves, ask yourself: did it demo every behavior, or did it demo the easy \
ones and skip the hard ones?

You are not a cheerleader. Do not say "excellent progress" or "good work." If the \
agent is on track, say nothing. Only use the nudge tool when something is wrong -- \
or when the agent has written its review draft and you need to confirm or reject it. \
Silence means you have no objection. Words mean something is wrong or the agent is \
waiting for your decision."""


def create_review_agent(
    repo_name: str,
    *,
    pr_number: int,
    pr_title: str,
    pr_body: str,
    repo_full_name: str,
    original_spec: str = "",
    fork_source: object | None = None,
) -> ClaudeAgent:
    """Create a monitored review agent.

    Returns a single agent with a bridge-local Sonnet monitor. The monitor
    runs inside the bridge process on the same VM -- no second agent needed.
    """
    original_spec_section = ""
    if original_spec:
        original_spec_section = (
            "## Original task spec\n\n"
            "This PR was generated by an Orpheus agent. "
            f"The original specification:\n\n---\n{original_spec}\n---\n\n"
        )

    demo_prompt = DEMO_USER_PROMPT.format(
        pr_number=pr_number,
        repo_full_name=repo_full_name,
        pr_title=pr_title,
        pr_body=pr_body,
        original_spec_section=original_spec_section,
    )

    monitor_spec_section = ""
    if original_spec:
        monitor_spec_section = (
            "Original task spec (the PR was generated from this):\n\n"
            f"---\n{original_spec}\n---\n\n"
            "The agent must demo every requirement in this spec. "
            "If it skips any, call it out.\n\n"
        )

    monitor_prompt = MONITOR_PROMPT.format(
        pr_number=pr_number,
        repo_full_name=repo_full_name,
        pr_title=pr_title,
        pr_body=pr_body or "(no description)",
        original_spec_section=monitor_spec_section,
    )

    working_dir = f"/home/agent/{repo_name}"
    demo_system_prompt = AGENT_SYSTEM_PROMPT + "\n\n" + REVIEW_SYSTEM_PROMPT

    agent = ClaudeAgent(
        name="demo",
        working_directory=working_dir,
        instance_source="fork" if fork_source else "devbox",
        user_prompt=demo_prompt,
        system_prompt=demo_system_prompt,
        monitor_prompt=monitor_prompt,
    )
    if fork_source:
        agent._fork_source = fork_source
    return agent
