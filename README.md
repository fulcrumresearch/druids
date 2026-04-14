# druids

A small async in-process multi-agent orchestration runtime.

```python
import asyncio
from druids import Context, LocalImage


async def setup(ctx: Context) -> None:
    builder = await ctx.agent("builder")

    @builder.on("submit")
    async def submit(summary: str = ""):
        ctx.exit(summary)
        return "done"

    await builder.send("Say hello, then call submit with summary='hello'.")


async def main():
    ctx = Context(image=LocalImage())
    result = await ctx.run(setup, timeout=30)
    print(result)


asyncio.run(main())
```

## What is implemented

- Async `Context`, `Agent`, `Image`, and `Machine` APIs
- Runtime startup via `await ctx.run(...)` or `await ctx.start()`
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

- The primary lifecycle is `await ctx.run(...)`; use `await ctx.start()` / `await ctx.close()` for low-level control.
- `ctx.exit(result)` / `ctx.fail(reason)` signal completion.
- `await ctx.wait()` is the low-level wait primitive used by `ctx.run()`.
- First instructions are sent explicitly with `await agent.send(...)` after your tools and connections are in place.
- Tool handlers must be `async def`.
- The public API always launches real `pi` agents in `tmux`.
