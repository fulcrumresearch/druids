# druids

A simpler Druids runtime with no client/server split.

```python
from druids import Context, LocalImage

ctx = Context(image=LocalImage(), launch_mode="manual")

builder = ctx.agent("builder", prompt="Say hello")

@builder.on("submit")
def submit(summary: str = ""):
    ctx.done(summary)
    return "done"

result = ctx.run(timeout=30)
print(result)
```

## What is implemented

- Synchronous `Context` / `Agent` API
- In-process HTTP server with:
  - `POST /agents/register`
  - `POST /agents/{agent}/tool_call`
  - `GET /agents/{agent}/events` (SSE)
- Built-in tools:
  - `message`
  - `list_agents`
  - `send_file`
  - `download_file`
- Lazy machine spawning with shared-machine support
- `LocalImage` and `DockerImage` backends
- JSONL orchestration logs at `./logs/{execution_id}/orchestrator.jsonl`
- A bundled TypeScript extension file for pi deployment

## Launch modes

`Context(..., launch_mode=...)` controls whether real pi agents are started:

- `"auto"` (default): start pi in tmux if both `pi` and `tmux` are available, otherwise keep the runtime available for manually connected test clients.
- `"always"`: require pi/tmux and fail if launching is not possible.
- `"manual"`: never launch pi automatically.

`manual` is useful for tests and local protocol development.
