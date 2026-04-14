# druids

A small async in-process multi-agent orchestration runtime.

```python
import asyncio
from druids import Context, LocalImage


async def main():
    async with Context(image=LocalImage()) as ctx:
        builder = await ctx.agent("builder")

        @builder.on("submit")
        async def submit(summary: str = ""):
            await ctx.done(summary)
            return "done"

        await builder.send("Say hello, then call submit with summary='hello'.")
        result = await ctx.wait(timeout=30)
        print(result)


asyncio.run(main())
```

## What is implemented

- Async `Context`, `Agent`, `Image`, and `Machine` APIs
- Runtime startup in `async with Context(...)`
- Immediate agent creation via `await ctx.agent(...)`
- Shared machines via `await ctx.machine(...)`
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

- The runtime starts when the context is entered.
- `await ctx.wait()` only waits for `await ctx.done(...)` or `await ctx.fail(...)`.
- First instructions are sent explicitly with `await agent.send(...)` after your tools and connections are in place.
- Tool handlers must be `async def`.
- The public API always launches real `pi` agents in `tmux`.
