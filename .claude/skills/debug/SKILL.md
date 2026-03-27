---
name: debug
description: >
  Diagnose a running or completed Druids execution. Pulls agent traces,
  activity logs, and diffs, then produces a structured diagnostic covering
  communication health, errors, goal progress, agent performance, and
  behavioral bottlenecks.
user-invocable: true
---

# Debug an Execution

The user wants to understand what is happening (or what went wrong) inside a Druids execution. This skill produces a diagnostic report by pulling every available signal and analyzing it systematically.

## 1. Identify the execution

The user may pass a slug directly (`/debug gentle-nocturne`) or say something like "debug the current run". If no slug is given, call `list_executions` with `active_only=true` and pick the most recent one. If there are multiple active executions, ask which one.

## 2. Gather all available data

Make these calls in parallel where possible:

**a. Execution state** -- `get_execution` for the slug. Record: status, agent names, agent types, connections, topology edges, exposed services, PR URL, branch name.

**b. Full activity log** -- `get_execution_activity` with `n=200` and `compact=false`. This is the richest signal. It contains every tool call, message, error, connection event, and response across all agents. Request the full (non-compact) version so you can see tool arguments and outputs.

**c. Per-agent traces** -- For each agent in the execution, call `get_agent_trace`. This gives you the coalesced view: messages, thoughts, tool calls with status, and plans. Pull traces for all agents in parallel.

**d. Diff** -- `get_execution_diff`. If no diff exists yet, note that.

**e. Spec** -- from the execution data. You need this to evaluate whether agents are achieving the goal.

## 3. Analyze

Work through each dimension below. Do not skip dimensions even if they seem fine -- explicitly confirming health is part of the diagnostic.

### 3a. Communication health

Questions to answer:

- Are all agents connected? Check for `connected` and `disconnected` events. An agent that connected and then disconnected has a problem.
- Is the topology correct for the program? Do agents that need to talk to each other have edges between them?
- Are messages actually flowing? Look for `message` tool calls in the activity. Check that messages sent by one agent show up as received by the target.
- How long between a message being sent and the receiver acting on it? Gaps longer than 30 seconds suggest the receiver is stuck or not listening.
- Are any agents talking to themselves or sending messages that go nowhere?

### 3b. Errors

Questions to answer:

- Are there any `error` type events in the activity? What do they say?
- Are there tool calls that returned errors? Look at `tool_result` events with error indicators.
- Did any agent disconnect unexpectedly?
- Are there repeated failures on the same tool call? This usually means the agent is stuck in a retry loop.
- Did any agent hit a timeout?
- Are there permission errors (git push failures, file access denied, port already in use)?

### 3c. Agent performance

For each agent, characterize:

- **Activity level**: How many tool calls has it made? Is it actively working or idle?
- **Focus**: What is it spending its time on? (e.g., 80% file edits, 10% git, 10% messages)
- **Progress**: Based on its trace, what has it accomplished relative to its role?
- **Stuck indicators**: Is it repeating the same action? Has it gone silent? Is it producing long stretches of thinking without action?
- **Tool usage patterns**: Which tools does it use most? Are there tools it should be using but isn't?

Then compare across agents:

- Which agent is furthest along?
- Which agent is the weakest link (blocking others or making no progress)?
- Is any agent doing redundant work that another agent already did?

### 3d. Goal progress

Compare the current state against the spec:

- What did the spec ask for?
- What has actually been built? (Use the diff and exposed services.)
- Has anyone attempted the demo from the spec?
- What percentage of the requirements are met?
- What is left to do?

### 3e. Behavioral bottlenecks

These are the pragmatic, structural problems that slow executions down:

- **File sharing**: Are agents trying to read files another agent is still writing? Look for file-not-found errors or stale reads.
- **Info aggregation**: In programs with sub-agents, is the orchestrator actually collecting and using sub-agent output? Or is information getting lost?
- **Messaging timeliness**: Are there long gaps where an agent should have sent a message but didn't? Calculate the longest gap between activity events for each agent.
- **Hanging**: Is any agent completely silent for more than 2 minutes? This usually means it's stuck waiting for something or has crashed.
- **Serialization**: Are agents doing work sequentially that could be parallel? (e.g., one agent waiting for another to finish before starting its own independent work)
- **Scope creep**: Is any agent doing work outside its assigned role? (e.g., the reviewer starting to implement instead of reviewing)
- **Thrashing**: Is any agent undoing and redoing work? Look for patterns like edit-revert-edit on the same files.

## 4. Present the diagnostic

Structure the output as follows. Be concrete and specific -- cite actual tool names, file paths, message contents, and timestamps from the trace. Do not hedge or generalize.

```
## Diagnostic: {slug}

**Status**: {status} | **Agents**: {count} | **Duration**: {time since start}
**Spec**: {one-line summary of what was asked for}
**Branch**: {branch} | **PR**: {url or "none yet"} | **Diff**: +{added}/-{removed} lines

### Communication
{2-4 sentences on topology health, message flow, latency. Flag any issues.}

### Errors
{List each error with agent name and context. Or "No errors detected."}

### Agent Performance

#### {agent_name} ({agent_type})
- Activity: {active/idle/stuck} -- {N} tool calls, last active {time}
- Focus: {what it's spending time on}
- Progress: {what it's accomplished}
- Issues: {any problems, or "none"}

(repeat for each agent)

### Weakest link
{Which agent is the bottleneck and why. Be direct.}

### Goal Progress
- Spec asks for: {requirements list}
- Completed: {what's done}
- Remaining: {what's left}
- Estimated completion: {close / far / stuck}

### Bottlenecks
{List each bottleneck found, with evidence from the trace. Or "No structural bottlenecks detected."}

### Recommended actions
{Concrete next steps. Examples:
- "Send builder a message: the tests are failing because X, try Y"
- "Stop agent Z, it's been hanging for 5 minutes"
- "The reviewer hasn't received the builder's submission -- check topology"
- "Everything looks healthy, just needs more time"}
```

## 5. Offer to act

After presenting the diagnostic, ask the user if they want to take any of the recommended actions. You can:

- Send a message to an agent via `send_message`
- Stop a stuck agent via `stop_agent`
- Run a command on an agent's VM via `remote_exec` to inspect state
- Check specific files or processes on the VM
- SSH into the VM for the user via `get_agent_ssh`

Do not take action without the user's confirmation.

## Notes

- For running executions, the trace is live. If the user asks to "keep watching", re-pull activity after a minute and report changes.
- If the execution has already completed or failed, the diagnostic is a post-mortem. Shift language accordingly: "what happened" instead of "what's happening".
- The activity log is the primary signal. Agent traces are secondary -- they show the agent's perspective but miss inter-agent dynamics.
- When citing evidence, include the agent name and a brief quote or description of the event. "builder called `Write` on `src/app.py` at 14:32" is better than "an agent edited a file".
- If you see an agent in a retry loop (same tool call 3+ times with errors), that is the highest-priority finding. Flag it first.
- Token usage from the execution record can indicate whether an agent is doing real work (high token usage) or stuck early (low token usage).
