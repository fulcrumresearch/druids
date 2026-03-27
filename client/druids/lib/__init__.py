"""Type definitions for Druids programs.

Programs can import these types for editor autocomplete and type checking::

    from druids.lib import ProgramContext, Agent

    async def program(ctx: ProgramContext, spec: str = "", **kwargs):
        agent: Agent = await ctx.agent("worker", prompt=spec, git="write")

        @agent.on("submit")
        async def on_submit(summary: str = ""):
            ctx.done(summary)

At runtime on the server, the real Execution and Agent classes are used.
These types describe the same public API so programs type-check correctly
in both environments.
"""

from druids.lib.context import Agent, GitPermission, ProgramContext


__all__ = ["Agent", "GitPermission", "ProgramContext"]
