## Druids Workshop

You are connected to a Druids workshop at execution slug: `<slug>`.

### See what is happening

- `send_client_event` with `event="get_state"` returns agents, active/completed features, and available actions.
- `get_agent_activity` with `agent_name` returns raw ACP events (tool calls, messages, thoughts) for a specific agent.
- `get_execution_diff` returns an agent's current code changes as a git diff.

### Submit a design

- `send_client_event` with `event="propose"`, `data={"feature": "name", "plan": "..."}` spawns an executor agent that implements the plan on a feature branch.

### Direct agent communication

These platform-level tools bypass the program and talk to agents directly:

- `send_message` to talk to an agent.
- `get_agent_activity` to see what an agent is doing.
- `get_execution_diff` to see code changes.

### Typical workflow

```
1. get_state          -- see what is running
2. propose            -- spawn an executor for a feature
3. get_agent_activity -- watch the executor work
4. get_execution_diff -- review the code
5. send_message       -- steer the executor if needed
6. get_state          -- check completion
```
