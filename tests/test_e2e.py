"""End-to-end tests that launch real pi processes.

These test the full path: spawn → extension → register → tool call → done.
"""

import os
import subprocess
import sys
import tempfile

import pytest


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
import logging, sys
logging.basicConfig(level=logging.INFO, stream=sys.stderr)

from druids import Context, LocalImage

ctx = Context(image=LocalImage())
worker = ctx.agent(
    "worker",
    prompt="Call the finish tool with result='hello-druids'. Do not say anything else, just call the tool immediately.",
)

@worker.on("finish")
def on_finish(result=""):
    """Signal completion. Call this with your result."""
    ctx.done(result)
    return "Done."

result = ctx.run(timeout=60)
print(f"RESULT:{result}")
'''


def test_single_agent_tool_call():
    """Single agent receives prompt, calls tool, ctx.done() ends run."""
    proc = _run_program(SINGLE_AGENT, timeout=90)
    assert proc.returncode == 0, f"Failed:\nSTDOUT: {proc.stdout}\nSTDERR: {proc.stderr}"
    assert "RESULT:hello-druids" in proc.stdout


TWO_AGENTS = '''
import logging, sys
logging.basicConfig(level=logging.INFO, stream=sys.stderr)

from druids import Context, LocalImage

ctx = Context(image=LocalImage())

sender = ctx.agent(
    "sender",
    prompt="Call the notify tool with text='ping-from-sender'. Do nothing else, just call the tool.",
)
receiver = ctx.agent(
    "receiver",
    prompt="Wait for a message. When you receive one, call the finish tool with the message text you received.",
)

ctx.connect(sender, receiver)

@sender.on("notify")
def on_notify(text=""):
    """Send a notification to the receiver."""
    receiver.send(f"Notification: {text}")
    return "Notification sent."

@receiver.on("finish")
def on_finish(summary=""):
    """Finish the execution with a summary."""
    ctx.done(summary)
    return "Done."

result = ctx.run(timeout=90)
print(f"RESULT:{result}")
'''


def test_two_agents_message_passing():
    """Two agents: sender notifies receiver, receiver calls done."""
    proc = _run_program(TWO_AGENTS, timeout=120)
    assert proc.returncode == 0, f"Failed:\nSTDOUT: {proc.stdout}\nSTDERR: {proc.stderr}"
    assert "RESULT:" in proc.stdout
    result_line = [l for l in proc.stdout.splitlines() if l.startswith("RESULT:")][0]
    assert len(result_line) > len("RESULT:")


FAIL_TEST = '''
import logging, sys
logging.basicConfig(level=logging.INFO, stream=sys.stderr)

from druids import Context, LocalImage, ExecutionFailed

ctx = Context(image=LocalImage())
worker = ctx.agent(
    "worker",
    prompt="Call the abort tool immediately. Do nothing else.",
)

@worker.on("abort")
def on_abort(reason=""):
    """Abort the execution."""
    ctx.fail(reason or "aborted")
    return "Aborting."

try:
    ctx.run(timeout=60)
    print("ERROR:no-exception")
except ExecutionFailed as e:
    print(f"CAUGHT:{e.reason}")
'''


def test_fail_raises_execution_failed():
    """ctx.fail() causes ctx.run() to raise ExecutionFailed."""
    proc = _run_program(FAIL_TEST, timeout=90)
    assert "CAUGHT:" in proc.stdout, f"Expected ExecutionFailed.\nSTDOUT: {proc.stdout}\nSTDERR: {proc.stderr}"
