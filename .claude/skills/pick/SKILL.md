---
name: pick
description: >
  Compare agent solutions for a task and pick a winner. Shows diffs,
  artifacts, live URLs, and technical differences. Closes losing PRs
  when the user chooses.
user-invocable: true
---

# Pick a Winner

The user wants to compare the solutions from a completed (or nearly completed) task and choose the best one.

## Process

### 1. Identify the task

The user may provide an execution slug directly (`/pick burning-prelude`) or you may need to find it. Use `get_execution` or `list_executions` to find executions.

### 2. Gather data for each execution

For every execution with status `running` or `completed` (skip `failed`), collect:

**a. Diff stats** -- call `get_execution_diff` for each. Summarize: files changed, lines added/removed. Note the branch name.

**b. PR URL** -- from the execution data. If the agent opened a PR, include the link.

**c. Exposed services** -- from the execution data. These are live URLs the user can visit (e.g. a leaderboard site, a dashboard). Include every URL.

**d. Recent activity** -- call `get_execution_activity` with `n=5` to see what the agent last did. This tells you if it finished, is still working, or got stuck.

**e. Key technical choices** -- read the diff content (not just stats) and identify:
  - What approach did the agent take?
  - What libraries or patterns did it use?
  - How did it structure the code? (one big file vs. modular)
  - Did it write tests?
  - Did it complete the demo from the spec?

### 3. Present the comparison

Format the comparison as a table followed by prose analysis:

```
## Task: {task_slug}

| | agent-1 | agent-2 | agent-3 |
|---|---------|---------|---------|
| Status | completed | running | completed |
| Files changed | 3 | 5 | 4 |
| Lines (+/-) | +180/-0 | +340/-12 | +220/-5 |
| PR | [#171](url) | -- | [#172](url) |
| Live URL | [link](url) | -- | [link](url) |

### agent-1 (claude)
[2-3 sentences on approach, code quality, completeness]

### agent-2 (orchestrator)
[2-3 sentences on approach, code quality, completeness]

### agent-3 (sonnet-fast)
[2-3 sentences on approach, code quality, completeness]

### Recommendation
[Which one would you pick and why? Be direct.]
```

Keep the analysis concrete. "claude wrote 3 clean files totaling 180 lines with no dependencies; orchestrator split it into 5 files with a build step the spec didn't ask for" is useful. "Both approaches have merit" is not.

### 4. Wait for the user to choose

Do not close PRs or merge anything until the user explicitly says which one they want. They may want to visit the live URLs first, read the diffs, or ask follow-up questions.

### 5. Close losing PRs

When the user picks a winner:

1. Close all other PRs for the task using `gh pr close {pr_number}` with a comment explaining the decision.
2. Optionally stop the losing executions if they're still running using `update_execution` (status="stopped") or `stop_agent`.
3. Report the winner's PR URL so the user can merge it.

Use a comment like:
```
Closing in favor of #{winner_pr_number} ({winner_program} solution).
```

Do NOT merge the winning PR automatically. The user will review and merge it themselves.

## Notes

- If agents are still running, say so. The user may want to wait or may want to pick from what's available.
- If no agents have PRs yet, check if they have diffs (committed locally but couldn't push). Report this -- the user may need to fix permissions or push manually.
- Exposed services (live URLs) are the highest-signal comparison for frontend tasks. Always highlight them.
- For backend-only tasks, the diff content and test results matter more.
- If an agent's diff is empty, it either failed or hasn't started. Skip it in the comparison.
