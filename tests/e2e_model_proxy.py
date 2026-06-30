"""End-to-end test for the host-side model proxy (ramure.proxy).

Runs a real pi agent in a Docker container with ``proxy_models`` enabled, for
both an OpenAI and an Anthropic model. The container is handed only a scoped
token (no real provider keys); the agent's pi extension redirects the provider
baseUrls at ramure's host proxy, which swaps the token for the real key and
forwards to the provider. A completed task therefore *proves* the call went
through the proxy — and we also assert the proxy's per-route hit counter moved.

Requires: docker + the ``ramure-docker-pi-e2e:latest`` image, and the real
provider keys in the host env (or a .env). Run directly:

    python tests/e2e_model_proxy.py
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path

# Enable the host-side model proxy for every runtime in this process.
os.environ["RAMURE_PROXY_MODELS"] = "1"

# Load provider keys from a .env if present (host side only — never the container).
try:
    from dotenv import load_dotenv

    for cand in (Path.cwd() / ".env", Path("/home/ubuntu/code/lesswronger/.env")):
        if cand.exists():
            load_dotenv(cand)
            break
except Exception:
    pass

from ramure import DockerImage, agent, agent_process, current_runtime, done, fail, wait

IMAGE = os.environ.get("RAMURE_DOCKER_REAL_E2E_IMAGE", "ramure-docker-pi-e2e:latest")
# (provider, model, proxy route the call must traverse)
CASES = [
    ("openai", "openai/gpt-4o-mini", "openai"),
    ("anthropic", "anthropic/claude-opus-4-1", "anthropic"),
]


@agent_process(timeout=240)
async def run_case(model: str) -> dict:
    workdir = Path(tempfile.mkdtemp(prefix="ramure-proxy-e2e-"))
    image = DockerImage(IMAGE, workdir="/workspace", volumes=[f"{workdir}:/workspace"])
    worker = await agent(
        "writer",
        system_prompt="You are a terse assistant. Follow the instruction exactly.",
        image=image,
        model=model,
    )

    @worker.on("submit")
    async def submit(answer: str) -> str:
        """Submit your final answer."""
        done(answer)
        return "recorded"

    @worker.on("give_up")
    async def give_up(reason: str) -> str:
        """Call if you cannot complete the task."""
        fail(reason)
        return "recorded"

    await worker.send(
        "Call the `submit` tool with exactly the word PONG as the answer. Do nothing else."
    )
    summary = await wait()
    rt = current_runtime()
    hits = dict(rt.proxy.hits) if rt and rt.proxy else {}
    proxied = rt.proxy is not None
    token = rt.proxy_token if rt else None
    return {"summary": summary, "hits": hits, "proxied": proxied, "token_set": bool(token)}


def main() -> int:
    print(f"image: {IMAGE}")
    print(f"host keys: anthropic={bool(os.environ.get('ANTHROPIC_API_KEY'))} "
          f"openai={bool(os.environ.get('OPENAI_API_KEY'))}\n")
    failures = 0
    for provider, model, route in CASES:
        if not os.environ.get(f"{provider.upper()}_API_KEY"):
            print(f"[SKIP] {provider}: no {provider.upper()}_API_KEY on host")
            continue
        print(f"=== {provider} :: {model} ===")
        try:
            res = asyncio.run(run_case(model))
        except Exception as e:
            print(f"  [FAIL] run raised {type(e).__name__}: {e}\n")
            failures += 1
            continue
        summary = (res.get("summary") or "").strip()
        hits = res.get("hits") or {}
        route_hits = hits.get(route, 0)
        completed = "PONG" in summary.upper()
        used_proxy = res.get("proxied") and route_hits > 0
        ok = completed and used_proxy and res.get("token_set")
        print(f"  proxied={res.get('proxied')} token_set={res.get('token_set')} "
              f"hits={hits} summary={summary!r}")
        print(f"  completed_via_model={completed}  traversed_proxy({route})={used_proxy}")
        print(f"  -> {'PASS' if ok else 'FAIL'}\n")
        failures += 0 if ok else 1
    print("ALL PASS" if failures == 0 else f"{failures} FAILED")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
