---
name: orpheus-driver
description: >
  Reference for all Orpheus MCP tools, concepts, and workflows.
  Loaded automatically so Claude Code always knows how to drive Orpheus.
user-invocable: false
---

# Orpheus Driver Reference

Orpheus is a multi-agent orchestration system. It runs coding agents on remote VMs provisioned by MorphCloud. You interact with it through MCP tools. Your job is to translate the user's intent into tasks, monitor agents as they work, and review the results.

## Tools

You have access to these MCP tools through the Orpheus server:

### Creating work

- `list_available_programs` -- List available programs (claude, codex, orchestrator, review, orchestrator_with_review).
- `create_task_endpoint` -- Create a task. Required: `spec` (string), `repo_full_name` (like "owner/repo"). Optional: `programs` (list of program names to run). Returns `task_slug` and `execution_slugs`.

### Monitoring

- `list_tasks_endpoint` -- List all tasks. Pass `active_only=false` to include stopped tasks.
- `get_task_endpoint` -- Get task by slug. Returns executions with status, branch name, PR URL.
- `get_execution_activity` -- Get recent trace events for an execution slug. Shows tool calls, prompts, errors.
- `get_execution_diff` -- Get git diff of everything an agent changed.
- `get_programs` -- List programs/agents inside an execution.

### Interacting with agents

- `send_message` -- Send message to a running agent. Set `sender` to `"driver"`, `receiver` to the program name (e.g. `"claude"`), `execution_slug` to the execution.
- `remote_exec` -- Run a shell command on a VM. Target by `repo` (devbox) or `execution_slug` + `agent_name` (agent VM). Returns stdout, stderr, exit_code.
- `stop_agent` -- Stop a specific agent by name.
- `get_agent_ssh` -- Get SSH credentials for an agent's VM.
- `expose_port` -- Expose a port on an agent's VM as a public HTTPS URL.

### Stopping work

- `delete_task_endpoint` -- Stop all executions for a task and deactivate it.

## Concepts

A **task** is a unit of work. Created with a spec and a repo. Gets a slug like `gentle-nocturne`.

An **execution** runs a specific program on a task. Slug: `{task-slug}-{program}`, e.g. `gentle-nocturne-claude`. When done, pushes to branch `orpheus/{execution-slug}` and opens a PR.

**Programs** define what agents run. `claude` and `codex` are single agents. `orchestrator` runs both in parallel and picks the best. `review` pairs an executor with a reviewer that iterates. `orchestrator_with_review` is the maximum-quality option.

A **devbox** is a VM snapshot with the user's repo cloned and deps installed. Created once via `orpheus setup start` / `orpheus setup save`. You cannot create devboxes; tell the user if one is missing.

## Choosing a program

- **claude**: Fast, good default. Single Claude agent.
- **codex**: Single Codex agent. Different model, different approach.
- **orchestrator**: Runs claude and codex in parallel, picks the best result. Use for important tasks where you want multiple approaches.
- **review**: Executor + reviewer pair. Reviewer iterates until satisfied. Use when correctness matters.
- **orchestrator_with_review**: Multiple reviewed executors compared. Maximum quality. Use for complex or high-stakes tasks.

For best-of-N sampling: create N separate tasks with the same spec and compare results. For a council of models: create tasks with different programs and compare approaches.

## Typical workflow

1. User asks you to build something.
2. Explore the codebase. Understand conventions, test structure, relevant files.
3. Write a spec with verifiable requirements (use the `write-spec` skill if you need help structuring this).
4. Call `create_task_endpoint` with the spec, repo, and chosen program(s).
5. Poll `get_task_endpoint` with the task slug. Check execution status.
6. When `pr_url` appears, call `get_execution_diff` to review the agent's work.
7. Report to the user: link to PR, summary of changes, any concerns.

If an agent is stuck (check `get_execution_activity`), send guidance via `send_message` with `sender="driver"`.

## Things to know

- An execution is "completed" when it opens a PR. Until then, status is "running".
- The `execution_slug` is not the task slug. If task is `gentle-nocturne` and program is `claude`, execution is `gentle-nocturne-claude`.
- Codex agents sometimes wait for confirmation before pushing. If no PR after a while, check `get_execution_activity` and send a nudge.
- You can run the same spec through multiple programs simultaneously by passing `programs: ["claude", "codex"]` to `create_task_endpoint`.
