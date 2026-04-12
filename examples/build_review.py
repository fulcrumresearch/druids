"""Example: builder + auditor with submit/approve/reject flow.

Two agents collaborate:
  - builder implements a feature
  - auditor reviews and approves or rejects

Run with:
    python examples/build_review.py
"""

from druids import Context, LocalImage

ctx = Context(image=LocalImage())

builder = ctx.agent("builder", prompt="Implement the feature described in the spec.")
auditor = ctx.agent("auditor", prompt="You audit the builder's work.")

ctx.connect(builder, auditor)


@builder.on("submit")
def on_submit(summary=""):
    """Submit your work for audit."""
    auditor.send(f"Review this:\n{summary}")
    return "Submitted for audit."


@auditor.on("approve")
def on_approve(summary=""):
    """Approve the build."""
    ctx.done(summary)
    return "Done."


@auditor.on("reject")
def on_reject(feedback=""):
    """Reject with feedback."""
    builder.send(f"Fix this:\n{feedback}")
    return "Feedback sent."


ctx.run()
