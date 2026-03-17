"""Lab Rat -- parallel experiment runner with browser UI.

A long-lived program for running parallel experiments. Drivers interact
through client events from a browser frontend or Claude Code MCP.

Client events:

  get_state     Returns the full experiment state: best result, all runs
                (active and completed), and connected agents.

  propose       Spawns an executor agent for a named experiment. Takes a
                name, a plan (the agent prompt), and optional hyperparams
                dict. The agent gets a submit_result tool to report back.

  note          Attach a freeform note to a run.

The agent interaction:

  Each spawned agent receives a submit_result tool. When the agent calls
  submit_result(loss=..., notes=..., hyperparams=...), the program updates
  the run state and (if applicable) the best-known result.
"""


async def program(ctx, spec="", **kwargs):
    state = {
        "best": None,
        "runs": {},
    }

    @ctx.on_client_event("get_state")
    def get_state():
        """Return full experiment state."""
        agents = {}
        for name, agent in ctx.agents.items():
            agents[name] = {"connected": name in ctx.connections}
        return {
            "best": state["best"],
            "runs": state["runs"],
            "agents": agents,
        }

    @ctx.on_client_event("propose")
    async def on_propose(name="", plan="", hyperparams=None):
        """Spawn an executor agent for an experiment."""
        if not name or not plan:
            return {"error": "Both 'name' and 'plan' required"}
        if name in state["runs"]:
            return {"error": f"Run '{name}' already exists"}

        agent_name = f"{name}-exec"

        preamble = ""
        if state["best"]:
            preamble = (
                f"Current best result: loss={state['best']['loss']} "
                f"from run '{state['best']['run']}' with hyperparams: "
                f"{state['best']['hyperparams']}\n\n"
            )

        full_prompt = (
            f"{preamble}"
            f"Experiment: {name}\n"
            f"Hyperparams: {hyperparams or 'not specified, use your judgment'}\n\n"
            f"{plan}\n\n"
            "When you have a result, call the submit_result tool with:\n"
            "  loss: the final validation loss (float)\n"
            "  notes: what you observed\n"
            "  hyperparams: the actual hyperparams you used (dict)\n"
        )

        agent = await ctx.agent(agent_name, prompt=full_prompt, git="write")

        @agent.on("submit_result")
        def on_submit(loss="", notes="", hyperparams_used=None):
            try:
                loss_val = float(loss)
            except (ValueError, TypeError):
                return {"error": f"loss must be a float, got: {loss}"}

            state["runs"][name]["status"] = "completed"
            state["runs"][name]["result"] = {
                "loss": loss_val,
                "notes": notes,
            }
            if hyperparams_used:
                state["runs"][name]["hyperparams"] = hyperparams_used

            if state["best"] is None or loss_val < state["best"]["loss"]:
                state["best"] = {
                    "run": name,
                    "loss": loss_val,
                    "hyperparams": state["runs"][name]["hyperparams"],
                }
                await ctx.emit("new_best", state["best"])

            await ctx.emit("run_completed", {"name": name, "loss": loss_val})
            return {"status": "recorded", "is_new_best": state["best"]["run"] == name}

        state["runs"][name] = {
            "agent": agent_name,
            "plan": plan,
            "hyperparams": hyperparams or {},
            "status": "running",
            "result": None,
            "notes": [],
        }

        return {"status": "spawned", "agent": agent_name}

    @ctx.on_client_event("note")
    def on_note(run="", text=""):
        """Attach a note to a run."""
        if run not in state["runs"]:
            return {"error": f"No run named '{run}'"}
        state["runs"][run]["notes"].append(text)
        return {"status": "noted"}

    await ctx.emit("ready", {"message": "Lab Rat running. Waiting for experiments."})
    await ctx.wait()
