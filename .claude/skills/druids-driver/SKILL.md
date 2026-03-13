---
name: druids-driver
description: >
  Reference for driving Druids: launching agent executions, monitoring
  progress, writing specs, and reviewing results. Loaded automatically
  so Claude Code always knows how to use Druids.
user-invocable: false
---

# Druids

Druids runs coding agents on remote VMs. You send a program and a spec, agents implement on isolated sandboxes, and the results come back as pull requests. Your role as the driver is to translate user intent into specs, launch executions, monitor progress, and review output.

Use Druids when the user asks to build a feature, fix a bug, or do work that benefits from delegation to background agents. Do not use it for quick edits, questions about the codebase, or tasks that require real-time back-and-forth with the user.

## Concepts

A **devbox** is a VM snapshot with the user's repo cloned and dependencies installed. Executions fork from it so each agent starts with a working environment. Created via `druids setup start` and `druids setup finish`.

A **program** is a Python file that defines `async def program(ctx, ...)`. It creates agents, registers tool handlers, and manages lifecycle. Programs live in `.druids/` in the repo. The driver reads the file and sends its source to `create_execution`.

An **execution** is a running instance of a program. Gets a slug like `gentle-nocturne`. Contains one or more agents working on VMs. When agents finish, they push a branch and open a PR.

An **agent** runs on a VM inside an execution. Created by programs via `ctx.agent(name, ...)`. Each has a bridge process connecting it back to the server.

## Workflow

1. User asks to build something.
2. Explore the codebase. Understand conventions, test patterns, relevant files.
3. Write a spec describing what to change. The spec is the primary input to the agent. Include file paths, function signatures, and concrete demo commands. If the `write-spec` skill is available, use it.
4. Choose a program. `basher.py` in direct mode handles most implementation tasks (implementor + reviewer). `main.py` runs Claude and Codex in parallel for comparison. `review.py` demo-reviews an existing PR.
5. Read the program source and call `create_execution` with `program_source` set to the file contents. Pass the spec and other parameters in `args`.
6. Monitor with `get_execution`. Check status, agents, connections, PR URL.
7. If an agent is stuck, check `get_execution_activity` and send guidance via `send_message` with `sender="driver"`.
8. When agents finish, review the diff with `get_execution_diff` and report to the user with a link to the PR.

## Programs in `.druids/`

- `basher.py`: Implementation with review. Direct mode: pass `task_name` and `task_spec` to spawn an implementor+reviewer pair. The implementor builds on a feature branch, the reviewer demos the change and creates a PR if it works (up to 3 review rounds). Full mode: a finder agent scans for tasks and spawns pairs automatically.
- `main.py`: Parallel comparison. Spawns a Claude agent and a Codex agent on the same spec. Both implement independently, both submit when done.
- `build.py`: Spec-driven build with auditing. A builder implements, a critic reviews each commit for simplicity, and an auditor verifies the demo evidence is real.
- `review.py`: Demo-review of a PR. A demo agent checks out the PR, runs the system, and tests every changed behavior from the outside. A monitor watches for lazy behavior. Takes `pr_number`, `pr_title`, `pr_body`, `repo_full_name`.

## MCP tools

These tools are exposed by the Druids server. They are your interface for creating and managing executions.

### Creating work

`create_execution`: start an execution. Required: `program_source` (Python source string). Optional: `devbox_name`, `repo_full_name` (finds devbox by repo when name not set), `git_branch`, `args` (dict of string key-value pairs). Returns `execution_slug` and `execution_id`.

### Monitoring

`list_executions`: list executions. Pass `active_only=false` to include stopped ones.

`get_execution`: get execution by slug. Returns status, agents, connections, branch name, PR URL, exposed services, client events.

`get_execution_activity`: recent trace events for an execution. Optional: `n` (default 50), `compact` (default true). Shows agent names, event count, recent activity.

`get_execution_diff`: git diff from an execution's VM. Optional: `agent` (default picks the first agent with a machine).

`get_agent_events`: event stream for a specific agent. Required: `slug`, `agent_name`. Optional: `after_sequence` (resume from sequence number), `limit` (default 100).

### Interacting with agents

`send_message`: message a running agent. Required: `execution_slug`, `receiver` (agent name), `message`. Set `sender` to `"driver"`.

`remote_exec`: run a shell command on a VM. Target by `repo` (devbox) or `execution_slug` + `agent_name` (agent VM). Required: `command`. Returns `stdout`, `stderr`, `exit_code`.

`stop_agent`: stop an agent. Required: `agent_name`, `execution_slug`.

`get_agent_ssh`: SSH credentials for an agent's VM. Required: `agent_name`, `execution_slug`. Returns host, port, username, private_key, password.

### Stopping work

`update_execution`: change execution status. Set `status` to `"stopped"`, `"completed"`, or `"failed"`.

## CLI commands

The `druids` CLI runs on the driver's local machine:

- `druids exec program.py [--devbox NAME] [--branch BRANCH] [key=value ...]`: run a program.
- `druids ls [--all]`: list executions.
- `druids status SLUG`: check execution status.
- `druids stop SLUG`: stop an execution.
- `druids connect SLUG [--agent NAME]`: SSH into a VM.
- `druids setup start [--name NAME] [--repo OWNER/REPO]`: provision a devbox.
- `druids setup finish [--name NAME] [--repo OWNER/REPO]`: snapshot and save.
- `druids mcp-config`: print MCP server config for Claude Code.
- `druids auth set-key KEY`: set auth key.
- `druids secret ...`: manage devbox secrets.

## Reference

- Execution status values: `created`, `running`, `completed`, `stopped`, `failed`.
- `remote_exec` can target a devbox directly (pass `repo`) without a running execution. Use for setup, debugging, or ad-hoc commands.
- Agents call tools on the VM via `druids tool <tool_name> key=value`. Tools are defined by `@agent.on("tool_name")` in the program.
- Built-in agent tools: `expose` (expose a port as public HTTPS URL), `message` (send message to another agent), `list_agents` (list agents in the execution).
- Programs can use `share_machine_with=other_agent` to run two agents on the same VM.
