"""Run a real ramure agent on a MorphCloud VM.

Requires:
- ``pip install ramure[morph]``
- ``MORPH_API_KEY`` in the environment
- ``ANTHROPIC_API_KEY`` in the environment (for the agent itself)
- A reverse proxy forwarding a public URL to this host on some port.
  The runtime binds that port on ``0.0.0.0`` so agents on the VM can
  reach it via ``base_url``.

Example, with nginx proxying ``wss://me.example.com`` -> ``127.0.0.1:8002``:

    export RAMURE_BASE_URL=wss://me.example.com
"""

import asyncio
import os

from ramure import agent, agent_process, done, wait
from ramure.machines.morph import MorphImage


@agent_process(
    image=MorphImage(ttl_seconds=1200),
    host="0.0.0.0",
    port=8002,
    base_url=os.environ.get("RAMURE_BASE_URL"),
)
async def greet(name: str) -> str:
    worker = await agent("worker")

    @worker.on("finish")
    async def on_finish(greeting: str) -> str:
        """Call this with the greeting once you are done."""
        done(greeting)
        return "Done."

    await worker.send(f"Write a one-line greeting for {name}, then call finish.")
    return await wait()


async def main() -> None:
    print(await greet("world"))


if __name__ == "__main__":
    asyncio.run(main())
