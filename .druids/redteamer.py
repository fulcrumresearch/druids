"""Red team program -- parallel attack/defend pattern for finding bugs.

Spawns N attacker agents (half Claude, half Codex) against a single
defender. Attackers try to find security issues, edge cases, or incorrect
behavior. The defender reviews each report and decides whether it is a real
issue. Reports real issues as GitHub issues, marks false positives as closed.

Usage:
  druids exec .druids/redteamer.py --devbox owner/repo \\
    target_description="FastAPI server with OAuth and file upload endpoints" \\
    n_attackers=4
"""

from __future__ import annotations

import asyncio


MAX_ATTACKERS = 6
DEFAULT_ATTACKERS = 4

ATTACKER_SYSTEM = """\
You are a security and correctness auditor. You will be given a description of
a codebase and asked to find bugs, security vulnerabilities, edge cases, and
incorrect behavior.

Read the source code carefully. Focus on:
- Authentication and authorization bypass
- Input validation and injection
- Error handling and edge cases
- Race conditions and concurrency bugs
- Resource leaks

Be specific. Your report must include:
1. File and line number
2. Exact description of the issue
3. A reproduction scenario (curl command, code snippet, or step-by-step)
4. Severity: critical/high/medium/low

If you do not find a real issue, say so honestly. Do not make things up.
When done, call report_finding with your finding (or "no issues found").
"""

DEFENDER_SYSTEM = """\
You are a senior engineer reviewing security reports from automated auditors.

For each report:
1. Read the referenced code yourself to confirm the issue is real
2. Judge whether the reproduction scenario would actually work
3. Assess the true severity

Call accept_finding if the issue is real and worth filing.
Call reject_finding if it is a false positive, already known, or not exploitable.

Be rigorous. Do not accept vague or speculative reports.
"""


async def program(ctx, target_description="", n_attackers=DEFAULT_ATTACKERS, **kwargs):
    """Spawn N attackers and one defender to find bugs in the target repo."""
    n = min(int(n_attackers), MAX_ATTACKERS)
    if not target_description:
        target_description = "This is a Python web application. Read the source to understand it."

    findings = []
    rejected = []
    pending = 0
    reported = 0

    @ctx.on_client_event("get_state")
    def get_state():
        """Return current red team state."""
        agents = {}
        for name in ctx.agents:
            agents[name] = {"connected": name in ctx.connections}
        return {
            "total_attackers": n,
            "pending": pending,
            "reported": reported,
            "findings_accepted": len(findings),
            "findings_rejected": len(rejected),
            "findings": findings,
            "agents": agents,
        }

    # Spawn defender once
    defender = await ctx.agent(
        "defender",
        system_prompt=DEFENDER_SYSTEM,
        prompt=(
            "You will receive reports from automated auditors. "
            "Review each report carefully against the actual code. "
            "Call accept_finding or reject_finding for each one."
        ),
        git="read",
    )

    @defender.on("accept_finding")
    async def on_accept(summary="", severity="medium", file_path="", line=""):
        """Accept a finding as a real issue worth filing."""
        finding = {"summary": summary, "severity": severity, "file": file_path, "line": line}
        findings.append(finding)
        await ctx.emit("finding_accepted", finding)
        return f"Accepted. Total findings so far: {len(findings)}."

    @defender.on("reject_finding")
    async def on_reject(summary="", reason=""):
        """Reject a finding as a false positive."""
        rejected.append({"summary": summary, "reason": reason})
        await ctx.emit("finding_rejected", {"summary": summary, "reason": reason})
        return f"Rejected: {reason}"

    # Spawn attackers in parallel (half Claude, half Codex)
    attacker_prompt = (
        f"## Target\n\n{target_description}\n\n"
        "Read the source code and find security issues, bugs, or edge cases. "
        "When done, call report_finding with your result."
    )

    async def spawn_attacker(i: int):
        nonlocal pending, reported
        pending += 1
        model = "claude" if i % 2 == 0 else "codex"
        agent = await ctx.agent(
            f"attacker-{i}",
            model=model,
            system_prompt=ATTACKER_SYSTEM,
            prompt=attacker_prompt,
            git="read",
        )

        @agent.on("report_finding")
        async def on_report(finding=""):
            nonlocal pending, reported
            pending -= 1
            reported += 1
            has_finding = finding and "no issues" not in finding.lower()
            await ctx.emit(
                "attacker_reported",
                {
                    "attacker": f"attacker-{i}",
                    "model": model,
                    "has_finding": has_finding,
                },
            )
            if has_finding:
                await defender.send(f"[Attacker {i} ({model})] Finding:\n\n{finding}")
            if pending == 0:
                await ctx.done(
                    {
                        "total_attackers": n,
                        "findings_accepted": len(findings),
                        "findings": findings,
                    }
                )
            return "Report received."

    await asyncio.gather(*[spawn_attacker(i) for i in range(n)])

    @ctx.on_client_event("input")
    async def handle_input(text=""):
        """Route driver input to the defender."""
        await defender.send(f"[Driver]: {text}")
        return {"ack": True}
