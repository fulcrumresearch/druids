---
name: create-pr
description: >
  Create a branch, commit, push, and open a PR with a well-written description.
  Run this after your work is done and tests pass.
allowed-tools:
  - Bash
  - Read
  - Glob
  - Grep
---

Create a pull request.

## Verify before anything else

Do not create a PR until you have verified your work. This is the most
important part of the process.

1. Run the project's test suite. Find it: look for pytest, cargo test,
   npm test, go test, or check the README / Makefile / CI config.
2. Run the linter/formatter if one exists (ruff, eslint, clippy, etc.).
3. Go beyond the test suite. Exercise the thing you built. If you added
   an endpoint, call it. If you added a CLI command, run it. If you
   changed a config path, verify the config loads. Use the full environment
   you have -- you are on a real machine with network access, a database,
   and real tools. Use them.
4. Read your own diff (`git diff --stat` and `git diff`). Does it match
   what was asked? Did you leave any debug prints, TODOs, or commented-out
   code?

If anything fails, fix it first. Do not proceed to PR creation with
failing tests or untested functionality.

## Branch

If you have a branch name from your execution context or from a reviewer
message, use it. Otherwise create one in kebab-case: `add-system-prompt-support`.

```
git checkout -b <branch>
```

## Commit

Stage files explicitly. Do not use `git add .` or `git add -A`.


## Push and create PR

```
git push -u origin <branch>
```

Then create the PR. The description matters -- it is the primary artifact
a reviewer reads. Write it well.

```
gh pr create --title "<title>" --body "$(cat <<'EOF'
<description>
EOF
)"
```

## PR format

The title is a short phrase that says what the PR does. Under 72 characters.
Do not repeat the branch name or ticket number. Examples:

- Add system prompt and agent subclasses
- Fix reviewer stall after approval
- Add `event_type_counts` to activity endpoint

The body has three parts: what changed, why, and what you did to verify it.

Do not use bullet point lists of every file touched. Do not use emoji
checkmarks. Write in prose where possible.

The verification section at the bottom is mandatory. It is not a checkbox
that says "tests pass." It is a narrative of what you actually did to
convince yourself the change works. This is the most valuable part of
the PR for a reviewer -- it shows the work was exercised, not just written.

Include raw command outputs in collapsible `<details>` blocks so reviewers
can expand them if needed. The prose narrative describes what you did and
what you observed. The `<details>` blocks prove it.

Here is an example for a large change:

```
Adds system prompt support and typed agent subclasses.

Previously all agents were configured through a single `Agent` class with
manual `ACPConfig` construction in every program file. The init prompt was
the only way to give an agent instructions, and it was a user message --
no system prompt.

This PR introduces `ClaudeAgent` and `CodexAgent` subclasses that handle
backend-specific configuration (API keys, permission bypass, model
selection) in `__post_init__`. Programs now create agents with just a name,
working directory, and prompts.

System prompts are delivered via the ACP protocol: `_meta.systemPrompt.append`
for Claude, `developer_instructions` in config.toml for Codex. A shared
`AGENT_SYSTEM_PROMPT` provides identity and autonomy rules. Root agents also
get `ROOT_AGENT_SYSTEM_PROMPT` with the git/PR workflow.

Template variables (`$execution_slug`, `$agent_name`, `$working_directory`,
`$branch_name`) replace the runtime `[Execution Context]` block that was
previously prepended to every init prompt.

## Verification

<details>
<summary>uv run pytest (103 passed)</summary>

```
============================= test session starts ==============================
...
============================= 103 passed in 4.12s ==============================
```

</details>

<details>
<summary>ruff check && ruff format --check</summary>

```
All checks passed!
3 files already formatted
```

</details>

Ran a full end-to-end test with real API keys, no mocking. Started the
server, created a task via `POST /tasks` with the `claude` program, and
watched the execution through to agent connection.

Confirmed the system prompt was delivered correctly: SSHed into the agent's
VM and inspected the ACP session logs. The `new_session` request included
`_meta.systemPrompt.append` with the full `AGENT_SYSTEM_PROMPT` text.
The agent's first response referenced its own name and execution slug,
confirming template variables were substituted.

<details>
<summary>Task creation and agent connection</summary>

```
$ curl -X POST http://localhost:8000/api/tasks \
    -H "Content-Type: application/json" \
    -d '{"spec": "Say hello and report your name.", "programs": ["claude"]}'
{"task_id": "abc-123", "executions": [{"slug": "warm-trio-claude", ...}]}

$ curl http://localhost:8000/api/executions/warm-trio-claude
{"status": "running", "programs": {"claude-root": {"type": "agent", "connected": true}}}
```

</details>

<details>
<summary>System prompt on the VM (from ACP session log)</summary>

```json
{
  "method": "session/new",
  "params": {
    "cwd": "/home/agent/orpheus",
    "_meta": {
      "systemPrompt": {
        "append": "You are an Orpheus agent.\n\nExecution context:\n- Agent name: claude-root\n- Execution slug: warm-trio-claude\n..."
      }
    }
  }
}
```

</details>

<details>
<summary>Agent response confirming template substitution</summary>

```
I'm claude-root running in execution warm-trio-claude. My working directory
is /home/agent/orpheus. I'll say hello: Hello!
```

</details>

Also verified `CodexAgent` path: created a Codex agent, SSHed into its VM,
and confirmed `~/.codex/config.toml` contains the `developer_instructions`
field with the expected system prompt text.
```

Here is an example for a small change:

```
The activity endpoint used `duration` in full mode but `duration_secs` in
compact mode. Fixes it to use `duration_secs` consistently. Also fixes
the corresponding test assertion.

## Verification

<details>
<summary>uv run pytest (96 passed)</summary>

```
============================= test session starts ==============================
...
============================= 96 passed in 3.41s ==============================
```

</details>

Started the server and created a task to get a real execution with activity
events. Waited for the agent to make a few tool calls, then hit the activity
endpoint in both modes and confirmed `duration_secs` is used consistently.

<details>
<summary>Full mode: GET /executions/warm-trio-claude/activity</summary>

```json
{
  "events": [
    {
      "event_type": "tool_result",
      "agent": "claude-root",
      "tool_name": "Bash",
      "duration_secs": 2.41,
      "status": "completed"
    },
    {
      "event_type": "tool_result",
      "agent": "claude-root",
      "tool_name": "Read",
      "duration_secs": 0.08,
      "status": "completed"
    }
  ]
}
```

</details>

<details>
<summary>Compact mode: GET /executions/warm-trio-claude/activity?compact=true</summary>

```json
{
  "events": [
    {"type": "tool_result", "agent": "claude-root", "tool": "Bash", "duration_secs": 2.41},
    {"type": "tool_result", "agent": "claude-root", "tool": "Read", "duration_secs": 0.08}
  ]
}
```

</details>

Both modes now use `duration_secs`. Previously full mode returned `duration`
(a float) while compact mode returned `duration_secs` (also a float, same
value). Grep confirms no remaining references to the old field name outside
of the migration.
```

Notice what these do:

- First line is a one-sentence summary
- Prose explains the before/after/why
- Verification goes end-to-end: start the server, create real data, hit
  real endpoints, observe real behavior. Do not just check types in a REPL.
- Raw outputs go in collapsible `<details>` blocks -- copy the full output
- You have a full machine with network, database, and real tools. Use them.
  If you added an endpoint, start the server and call it. If you changed
  how agents start, start an agent and watch it run.
- Does not list every file or function name
- Does not use emoji or checkbox lists

## After creating the PR

Report the PR URL. If you are working under a reviewer, send it via
`send_message`. If you are the root agent, call `submit` with the URL.
