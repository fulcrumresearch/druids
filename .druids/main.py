import asyncio


async def program(ctx, spec="", **kwargs):
    """Spawn a Claude and a Codex agent on the same spec in parallel."""

    common_prompt = f"""You are implementing a feature in the Druids codebase.

The repo is a Python project using FastAPI, SQLAlchemy, and asyncpg. The server is in server/, the client in client/, and the bridge in bridge/.

Read the spec carefully. Read the relevant source files before making changes. Run the server tests with `cd /home/agent/repo/server && uv run pytest` to verify your changes compile and pass.

Follow the conventions in CLAUDE.md. Create a feature branch, commit your changes, push, and open a PR using `gh pr create`.

When you are done, call the submit tool with a summary of what you did.

## Spec

{spec}
"""

    results = {}

    @ctx.on_client_event("get_state")
    def get_state():
        """Return current race state."""
        agents = {}
        for name in ctx.agents:
            agents[name] = {"connected": name in ctx.connections}
        return {
            "results": results,
            "agents": agents,
        }

    claude, codex = await asyncio.gather(
        ctx.agent("claude", model="claude", prompt=common_prompt, git="write"),
        ctx.agent("codex", model="codex", prompt=common_prompt, git="write"),
    )

    @claude.on("submit")
    async def on_claude_submit(summary=""):
        results["claude"] = summary
        await ctx.emit("agent_submitted", {"agent": "claude", "summary": summary})
        if len(results) == 2:
            await ctx.done(results)

    @codex.on("submit")
    async def on_codex_submit(summary=""):
        results["codex"] = summary
        await ctx.emit("agent_submitted", {"agent": "codex", "summary": summary})
        if len(results) == 2:
            await ctx.done(results)

    @ctx.on_client_event("input")
    async def handle_input(text="", agent=""):
        """Send input to a specific agent (or both)."""
        targets = [claude, codex] if not agent else [claude if agent == "claude" else codex]
        for target in targets:
            await target.send(f"[Driver]: {text}")
        return {"ack": True}
