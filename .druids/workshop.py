"""Workshop program -- interactive design with driver-spawned executors.

The workshop runs as a long-lived program. Drivers (local Claude Code sessions
connected via MCP) interact through two client events:

  get_state   Returns connected agents, active/completed features, and
              available actions. Drivers call this to see what is happening.

  propose     Spawns an executor agent for a named feature. The driver
              provides a feature name and a plan (prompt). The program
              creates a new agent that implements the plan on a feature branch.

The driver interaction loop:

  1. send_client_event(slug, "get_state", {})         -- observe
  2. send_client_event(slug, "propose", {feature, plan})  -- act
  3. get_agent_trace(slug, "feature-exec")              -- watch executor
  4. get_execution_diff(slug)                          -- review code
  5. send_message to executor                          -- steer
"""


async def program(ctx):
    executors = {}  # feature_name -> agent_name
    results = {}  # feature_name -> result

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
        if not feature or not plan:
            return {"error": "Both 'feature' and 'plan' required"}
        if feature in executors:
            return {"error": f"'{feature}' already has an executor"}

        agent_name = f"{feature}-exec"
        await ctx.agent(agent_name, prompt=plan, git="write")
        executors[feature] = agent_name
        return {"status": "executor_spawned", "feature": feature, "agent": agent_name}

    await ctx.emit("ready", {"message": "Workshop running."})
    await ctx.wait()
