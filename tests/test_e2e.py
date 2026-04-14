"""End-to-end tests that launch real pi processes."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile


def _run_program(source: str, *, timeout: int = 90) -> subprocess.CompletedProcess:
    """Write source to a temp file and run as a subprocess."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(source)
        f.flush()
        try:
            return subprocess.run(
                [sys.executable, f.name],
                capture_output=True,
                timeout=timeout,
                text=True,
                cwd="/home/ubuntu/code/2--druids-codex",
                env={**os.environ, "PYTHONPATH": "/home/ubuntu/code/2--druids-codex"},
            )
        finally:
            os.unlink(f.name)


SINGLE_AGENT = '''
import asyncio
import logging, sys
logging.basicConfig(level=logging.INFO, stream=sys.stderr)

from druids import Context, LocalImage

async def setup(ctx: Context):
    worker = await ctx.agent("worker")

    @worker.on("finish")
    async def on_finish(result=""):
        """Signal completion. Call this with your result."""
        ctx.exit(result)
        return "Done."

    await worker.send(
        "Call the finish tool with result='hello-druids'. Do not say anything else, just call the tool immediately."
    )

async def main():
    ctx = Context(image=LocalImage())
    result = await ctx.run(setup, timeout=60)
    print(f"RESULT:{result}")

asyncio.run(main())
'''


def test_single_agent_tool_call():
    """Single agent receives a message, calls a tool, and ctx.exit() ends the run."""
    proc = _run_program(SINGLE_AGENT, timeout=90)
    assert proc.returncode == 0, f"Failed:\nSTDOUT: {proc.stdout}\nSTDERR: {proc.stderr}"
    assert "RESULT:hello-druids" in proc.stdout


TWO_AGENTS = '''
import asyncio
import logging, sys
logging.basicConfig(level=logging.INFO, stream=sys.stderr)

from druids import Context, LocalImage

async def setup(ctx: Context):
    sender = await ctx.agent("sender")
    receiver = await ctx.agent("receiver")
    ctx.connect(sender, receiver)

    @sender.on("notify")
    async def on_notify(text=""):
        """Send a notification to the receiver."""
        await receiver.send(f"Notification: {text}")
        return "Notification sent."

    @receiver.on("finish")
    async def on_finish(summary=""):
        """Finish the execution with a summary."""
        ctx.exit(summary)
        return "Done."

    await receiver.send(
        "Wait for a message. When you receive one, call the finish tool with the message text you received."
    )
    await sender.send(
        "Call the notify tool with text='ping-from-sender'. Do nothing else, just call the tool."
    )

async def main():
    ctx = Context(image=LocalImage())
    result = await ctx.run(setup, timeout=90)
    print(f"RESULT:{result}")

asyncio.run(main())
'''


def test_two_agents_message_passing():
    """Two agents: sender notifies receiver, receiver calls exit."""
    proc = _run_program(TWO_AGENTS, timeout=120)
    assert proc.returncode == 0, f"Failed:\nSTDOUT: {proc.stdout}\nSTDERR: {proc.stderr}"
    assert "RESULT:" in proc.stdout
    result_line = [l for l in proc.stdout.splitlines() if l.startswith("RESULT:")][0]
    assert len(result_line) > len("RESULT:")


FAIL_TEST = '''
import asyncio
import logging, sys
logging.basicConfig(level=logging.INFO, stream=sys.stderr)

from druids import Context, LocalImage, ExecutionFailed

async def setup(ctx: Context):
    worker = await ctx.agent("worker")

    @worker.on("abort")
    async def on_abort(reason=""):
        """Abort the execution."""
        ctx.fail(reason or "aborted")
        return "Aborting."

    await worker.send("Call the abort tool immediately. Do nothing else.")

async def main():
    ctx = Context(image=LocalImage())
    try:
        await ctx.run(setup, timeout=60)
        print("ERROR:no-exception")
    except ExecutionFailed as e:
        print(f"CAUGHT:{e.reason}")

asyncio.run(main())
'''


def test_fail_raises_execution_failed():
    """ctx.fail() causes ctx.run() / ctx.wait() to raise ExecutionFailed."""
    proc = _run_program(FAIL_TEST, timeout=90)
    assert proc.returncode == 0, f"Failed:\nSTDOUT: {proc.stdout}\nSTDERR: {proc.stderr}"
    assert "CAUGHT:" in proc.stdout, f"Expected ExecutionFailed.\nSTDOUT: {proc.stdout}\nSTDERR: {proc.stderr}"
