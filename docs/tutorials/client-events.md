# Tutorial: Client events and interactive programs

Programs do not have to be fire-and-forget. The `workshop.py` program stays alive and accepts commands from the outside while agents are running. This tutorial explains how that works and how to build similar interactive programs.

## What are client events?

Client events are named endpoints on a running execution that external callers can trigger. You register them in your program with `@ctx.on_client_event("name")`. The handler runs when someone calls `send_client_event(slug, name, args)` via the API or CLI.

This is how you build human-in-the-loop workflows, live dashboards, or programs that accept new work while running.

## The workshop pattern

`workshop.py` is a long-lived program. It starts, registers two client events, and then waits:

```python
async def program(ctx):
    executors = {}
    results = {}

    @ctx.on_client_event("get_state")
    def get_state():
        """Return current workshop state."""
        return {
            "agents": {name: {"connected": name in ctx.connections} for name in ctx.agents},
            "features": {
                "active": [f for f in executors if f not in results],
                "completed": list(results.keys()),
            },
            "available_actions": ctx.list_client_events(),
        }

    @ctx.on_client_event("propose")
    async def on_propose(feature="", plan=""):
        """Spawn an executor agent for a feature."""
        agent_name = f"{feature}-exec"
        await ctx.agent(agent_name, prompt=plan, git="write")
        executors[feature] = agent_name
        return {"status": "executor_spawned", "feature": feature}

    ctx.emit("ready", {"message": "Workshop running."})
    await ctx.wait()
```

`ctx.wait()` suspends the program without ending the execution. The program stays alive until `ctx.done()` or `ctx.fail()` is called or the execution is stopped externally.

`ctx.emit("ready", ...)` sends an event to the execution stream. Clients watching the stream (via `druids exec --stream` or the dashboard) see it immediately.

## Calling client events from the CLI

```bash
# Start the workshop
druids exec .druids/workshop.py --devbox owner/repo
>>> workshop-slug

# Observe the state
druids send workshop-slug get_state

# Spawn an executor for a new feature
druids send workshop-slug propose feature="auth" plan="Add JWT authentication to the API"

# Watch the executor's output
druids status workshop-slug
```

## Building a dynamic UI

Client events are available over the REST API, so you can build any kind of interface around them:

```python
import httpx

BASE = "http://localhost:8000"
SLUG = "workshop-slug"
HEADERS = {"Authorization": "Bearer druid_..."}

# Observe state
state = httpx.get(f"{BASE}/api/executions/{SLUG}/state", headers=HEADERS).json()

# Propose a feature
httpx.post(
    f"{BASE}/api/executions/{SLUG}/events/propose",
    json={"feature": "rate-limiting", "plan": "Add per-user rate limiting"},
    headers=HEADERS,
)

# Stream execution events
with httpx.stream("GET", f"{BASE}/api/executions/{SLUG}/stream", headers=HEADERS) as r:
    for line in r.iter_lines():
        if line.startswith("data: "):
            print(line[6:])
```

## When to use this pattern

Use client events when:
- The scope of work is not fully known upfront (new features proposed as the execution runs)
- You want a human to review and approve intermediate outputs before continuing
- You are building a tool that other programs or scripts will drive

Use fire-and-forget programs (like `build.py`) when:
- The task is fully specified upfront
- You want maximum parallelism and speed
- There is no need for human checkpoints

The two patterns compose: a workshop can spawn build-style sub-flows for each proposed feature, while the workshop itself stays interactive.
