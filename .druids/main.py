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

    claude, codex = await asyncio.gather(
        ctx.agent("claude", model="claude", prompt=common_prompt, git="write"),
        ctx.agent("codex", model="codex", prompt=common_prompt, git="write"),
    )

    results = {}

    @claude.on("submit")
    async def on_claude_submit(summary=""):
        results["claude"] = summary
        if len(results) == 2:
            ctx.done(results)

    @codex.on("submit")
    async def on_codex_submit(summary=""):
        results["codex"] = summary
        if len(results) == 2:
            ctx.done(results)
