# druids

A small async in-process multi-agent orchestration runtime.

```python
import asyncio
from druids import LocalImage, agent, agent_runtime, exit, wait


@agent_runtime(image=LocalImage())
async def main():
    builder = await agent("builder")

    @builder.on("submit")
    async def submit(summary: str = ""):
        exit(summary)
        return "done"

    await builder.send("Say hello, then call submit with summary='hello'.")
    print(await wait(timeout=30))


asyncio.run(main())
```

## What is implemented

- Async `Runtime`, `Agent`, `Image`, and `Machine` APIs
- Runtime lifecycle via `@agent_runtime(...)` or `async with Runtime(...)`
- Immediate agent creation via `await agent(...)`
- Shared machines via `await machine(...)`
- In-process async HTTP server with:
  - `POST /agents/register`
  - `POST /agents/{agent}/tool_call`
  - `GET /agents/{agent}/events` (SSE)
- Built-in tools:
  - `message`
  - `send_file`
  - `download_file`
- `LocalImage` / `LocalMachine`
- A bundled TypeScript extension file for pi deployment

## Notes

- The primary lifecycle is `@agent_runtime(...)`; `async with Runtime(...)` is the low-level alternative.
- `exit(result)` / `fail(reason)` signal completion.
- `await wait()` blocks until `exit(...)` or `fail(...)` is called.
- First instructions are sent explicitly with `await agent.send(...)` after your tools and connections are in place.
- Tool handlers must be `async def`.
- The public API always launches real `pi` agents in `tmux`.
