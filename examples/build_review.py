"""Example: builder + auditor with submit/approve/reject flow.

Two agents collaborate:
  - builder implements a feature
  - auditor reviews and approves or rejects

Run with:
    python examples/build_review.py
"""

from __future__ import annotations

import asyncio

from druids import Context, LocalImage


async def main() -> None:
    async with Context(image=LocalImage()) as ctx:
        builder = await ctx.agent("builder")
        auditor = await ctx.agent("auditor")

        ctx.connect(builder, auditor)

        @builder.on("submit")
        async def on_submit(summary: str = "") -> str:
            """Submit your work for audit."""
            await auditor.send(f"Review this:\n{summary}")
            return "Submitted for audit."

        @auditor.on("approve")
        async def on_approve(summary: str = "") -> str:
            """Approve the build."""
            await ctx.done(summary)
            return "Done."

        @auditor.on("reject")
        async def on_reject(feedback: str = "") -> str:
            """Reject with feedback."""
            await builder.send(f"Fix this:\n{feedback}")
            return "Feedback sent."

        await builder.send("Implement the feature described in the spec.")
        await auditor.send("You audit the builder's work.")
        print(await ctx.wait())


if __name__ == "__main__":
    asyncio.run(main())
