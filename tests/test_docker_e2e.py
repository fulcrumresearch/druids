from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from ramure import DockerImage, agent, agent_process, done, wait

_DOCKER_AGENT_IMAGE = os.environ.get(
    "RAMURE_DOCKER_E2E_IMAGE",
    "ghcr.io/fulcrumresearch/druids-base:latest",
)
_REAL_DOCKER_AGENT_IMAGE = os.environ.get(
    "RAMURE_DOCKER_REAL_E2E_IMAGE",
    "ramure-docker-pi-e2e:latest",
)


def _docker_image_available(image: str) -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        result = subprocess.run(
            ["docker", "image", "inspect", image],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
    except Exception:
        return False
    return result.returncode == 0


def _real_e2e_model() -> str | None:
    if os.environ.get("OPENAI_API_KEY"):
        return "openai/gpt-4o-mini"
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic/claude-3-5-haiku-latest"
    return None


def _write_fake_agent_bin(bin_dir: Path) -> None:
    """Write tiny fake ``pi`` + ``tmux`` binaries for an offline Docker e2e.

    The fake pi speaks ramure's WebSocket protocol directly: register, wait for
    a message, call the ``finish`` tool, then close. This exercises the Docker
    backend and ramure agent lifecycle without needing provider credentials.
    """
    bin_dir.chmod(0o755)
    tmux = bin_dir / "tmux"
    tmux.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
case "${1:-}" in
  has-session)
    exit 1
    ;;
  kill-session)
    exit 0
    ;;
  new-session)
    shift
    session="fake"
    while [[ $# -gt 0 ]]; do
      case "$1" in
        -d)
          shift
          ;;
        -s)
          session="$2"
          shift 2
          ;;
        *)
          break
          ;;
      esac
    done
    nohup "$@" >"/tmp/${session}.log" 2>&1 </dev/null &
    exit 0
    ;;
  *)
    echo "fake tmux: unsupported args: $*" >&2
    exit 2
    ;;
esac
"""
    )

    pi = bin_dir / "pi"
    pi.write_text(
        """#!/usr/bin/env node
const serverUrl = process.env.RAMURE_SERVER_URL;
const agentId = process.env.RAMURE_AGENT_ID;
const executionId = process.env.RAMURE_EXECUTION_ID;
const ws = new WebSocket(`${serverUrl}/agents/${agentId}/ws`);
let called = false;
const timeout = setTimeout(() => {
  console.error('fake pi timed out');
  process.exit(2);
}, 20000);
ws.addEventListener('open', () => {
  ws.send(JSON.stringify({type: 'event', event_type: 'register', data: {execution_id: executionId}}));
});
ws.addEventListener('message', (ev) => {
  const entry = JSON.parse(ev.data);
  if (entry.type === 'message' && !called) {
    called = true;
    ws.send(JSON.stringify({
      type: 'event',
      event_type: 'tool_call',
      data: {call_id: 'finish-1', tool: 'finish', params: {result: 'docker-agent-ok'}}
    }));
  }
  if (entry.type === 'tool_result' && entry.data && entry.data.call_id === 'finish-1') {
    clearTimeout(timeout);
    ws.close(1000, 'done');
    setTimeout(() => process.exit(0), 200);
  }
});
ws.addEventListener('error', (err) => {
  console.error('fake pi websocket error', err.message || err);
  process.exit(3);
});
"""
    )

    tmux.chmod(0o755)
    pi.chmod(0o755)


@pytest.mark.skipif(
    not _docker_image_available(_DOCKER_AGENT_IMAGE),
    reason=(
        "Docker daemon or local Docker e2e image not available "
        f"({_DOCKER_AGENT_IMAGE!r}); set RAMURE_DOCKER_E2E_IMAGE to override"
    ),
)
def test_docker_backend_runs_agent_lifecycle(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_agent_bin(fake_bin)

    image = DockerImage(
        _DOCKER_AGENT_IMAGE,
        env={
            "PATH": "/ramure-bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
        },
        volumes=[f"{fake_bin}:/ramure-bin:ro"],
    )

    @agent_process(image=image, timeout=30)
    async def run() -> str:
        worker = await agent("worker")

        @worker.on("finish")
        async def on_finish(result: str = "") -> str:
            done(result)
            return "recorded"

        await worker.send("Call finish with result='docker-agent-ok'.")
        return await wait()

    assert asyncio.run(run()) == "docker-agent-ok"


@pytest.mark.skipif(
    os.environ.get("RAMURE_DOCKER_REAL_E2E") != "1"
    or _real_e2e_model() is None
    or not _docker_image_available(_REAL_DOCKER_AGENT_IMAGE),
    reason=(
        "real Docker+pi+LLM e2e requires RAMURE_DOCKER_REAL_E2E=1, "
        "OPENAI_API_KEY or ANTHROPIC_API_KEY, and a pi-ready Docker image "
        f"({_REAL_DOCKER_AGENT_IMAGE!r})"
    ),
)
def test_docker_backend_runs_real_pi_agent(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    model = _real_e2e_model()
    assert model is not None

    @agent_process(
        image=DockerImage(_REAL_DOCKER_AGENT_IMAGE, workdir="/home/agent"),
        timeout=180,
        log_dir=log_dir,
    )
    async def run() -> str:
        worker = await agent("worker", model=model)

        @worker.on("finish")
        async def on_finish(result: str = "") -> str:
            """Call this with the final result."""
            done(result)
            return "recorded"

        await worker.send(
            "Call finish with result exactly real-docker-pi-ok. "
            "Do not send any other text."
        )
        return await wait()

    assert asyncio.run(run()) == "real-docker-pi-ok"

    usage_entries = [
        line
        for path in log_dir.rglob("*.jsonl")
        for line in path.read_text().splitlines()
        if '"type": "usage"' in line
    ]
    assert usage_entries
