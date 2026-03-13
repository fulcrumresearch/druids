# Program API

A program is an `async def program(ctx)` function in a `.py` file. The runtime
calls it with a `ctx` object that creates agents, registers event handlers,
and controls the execution lifecycle.

## Reference pages

- [ctx](#/docs/ctx) -- the context object passed to every program: `ctx.agent()`, `ctx.done()`, `ctx.fail()`, `ctx.wait()`, `ctx.emit()`, `ctx.on_client_event()`, and ctx properties.
- [Agent](#/docs/agent) -- the agent object returned by `ctx.agent()`: `agent.on()`, `agent.send()`, `agent.exec()`, `agent.expose()`.
- [Git permission levels](#/docs/git-permissions) -- the `git` parameter values and their GitHub API scopes.
- [Built-in agent tools](#/docs/built-in-tools) -- tools available to every agent via the `druids` CLI.
- [Client API](#/docs/client-api) -- SSE streaming, REST client events, and other client-facing endpoints.
