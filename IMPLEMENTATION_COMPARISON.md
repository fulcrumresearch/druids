# Implementation comparison: this repo vs `../2-druids`

Date: 2026-04-11

Compared versions:
- This repo: `edb36d9` (`Fix pi extension integration and launch path`)
- `../2-druids`: `d8f8090` (`Final polish: fail test, connect logging, cleanup`)

This document compares the two independent implementations of the `spec.md` design and records what currently looks best.

## Short recommendation

If forced to choose a single base today, I would pick **`../2-druids` as the foundation**, then port several correctness fixes from this repo into it.

Why:
- It has the stronger overall code shape.
- It has the stronger automated test surface.
- It is closer to the intended async-internals / sync-public-API architecture.
- It already includes real `pi` / `tmux` end-to-end tests in the suite.

That said, this repo is better in a few important areas:
- stricter protocol validation
- duplicate-agent rejection
- better HTTP error semantics
- better pi extension API usage
- better message delivery semantics
- useful `launch_mode` control
- closer alignment with the spec on using env vars for extension config

So the real recommendation is:

> **Use `../2-druids` as the base, then merge in the best protocol/runtime correctness choices from this repo.**

---

## Evidence used

### This repo
- Read source in:
  - `druids/context.py`
  - `druids/server.py`
  - `druids/machines.py`
  - `druids/extension.ts`
- Ran tests:
  - `PYTHONPATH=. ../druids/runtime/.venv/bin/pytest -q`
  - result: `10 passed`
- Ran manual live end-to-end checks with real `pi` + `tmux`:
  - single-agent tool-call completion
  - two-agent messaging
  - file transfer
  - dynamic tool registration

### `../2-druids`
- Read source in:
  - `../2-druids/druids/_context.py`
  - `../2-druids/druids/_agent.py`
  - `../2-druids/druids/_server.py`
  - `../2-druids/druids/_machine.py`
  - `../2-druids/druids/_extension.py`
  - `../2-druids/druids/_tools.py`
- Ran tests:
  - `cd ../2-druids && .venv/bin/pytest -q`
  - result: `43 passed`
- Ran targeted sanity probes for edge cases:
  - duplicate agent names
  - register endpoint execution-id validation
  - shared-machine server URL resolution
  - `caller` schema behavior

---

## High-level comparison

| Area | This repo | `../2-druids` | Preferred |
|---|---|---|---|
| Overall code structure | Works, but more compact / ad hoc | Cleaner decomposition | `../2-druids` |
| Internal model | Mostly synchronous internals with threads | Async internals, sync API bridge | `../2-druids` |
| Protocol strictness | Stronger | Weaker in a few places | This repo |
| Extension implementation | Better aligned with current pi API | Simpler, but rougher | This repo |
| Automated test coverage | Smaller | Much larger | `../2-druids` |
| Real live e2e in committed suite | No | Yes | `../2-druids` |
| Spec alignment on env-var config | Yes | No, uses config file path | This repo |
| Error semantics | Proper HTTP errors | Many errors flattened to strings | This repo |
| Launch ergonomics | `launch_mode` is useful | No equivalent | This repo |
| Readiness semantics on spawn | Weaker | Better | `../2-druids` |

---

## What `../2-druids` does better

### 1. Better architecture for the core runtime
`../2-druids` has the better core split:
- `_context.py`
- `_agent.py`
- `_server.py`
- `_machine.py`
- `_extension.py`
- `_tools.py`

It is easier to reason about and easier to extend.

### 2. Better sync API / async internals story
Its public API is sync, but the internals are truly async. Handlers bridge back into the loop with `asyncio.run_coroutine_threadsafe(...)`. That is closer to the intended model from the spec than this repo’s mostly thread-first design.

### 3. Better spawn readiness semantics
In `../2-druids/druids/_context.py`, `_spawn_agent()` waits for the agent to register before considering the agent ready. That is important for spec alignment, especially for dynamic agent creation inside handlers.

This repo currently does **not** wait for registration after launching `pi`; it relies on SSE backlog buffering. That works for many paths, but it is not as strong a guarantee.

### 4. Better automated test surface
`../2-druids` has substantially more tests and, importantly, includes committed end-to-end tests that actually launch `pi` and `tmux`.

That matters a lot. The biggest implementation risk in this project is “the protocol looks right on paper but the real `pi` process behaves differently.” `../2-druids` is more battle-tested against that risk.

### 5. Better examples and developer ergonomics
The examples in `../2-druids/examples/` are a nicer reference set and make the intended usage clearer.

---

## What this repo does better

### 1. Stronger protocol validation
In `druids/context.py`, `_register_agent()` validates `execution_id` and returns a proper error if it does not match.

`../2-druids` does not validate `execution_id` on register.

### 2. Duplicate agent names are rejected
In `druids/context.py`, `Context.agent()` rejects duplicate names.

`../2-druids` silently overwrites `_agents[name]` while still appending multiple deferred spawn entries. That is a real bug.

### 3. Better HTTP error semantics
This repo uses structured HTTP errors via `ToolCallError` and returns meaningful status codes:
- 400 for bad execution id
- 403 for forbidden topology
- 404 for unknown agents/tools

`../2-druids` often returns HTTP 200 with result strings like `"Error: ..."`, which weakens correctness and makes extension behavior less precise.

### 4. Better pi extension integration
This repo’s `druids/extension.ts` was revised against the actual pi extension docs and examples:
- registers real pi tools dynamically
- converts schemas into TypeBox values
- uses `ctx.isIdle()` + `sendUserMessage(..., { deliverAs: "steer" })` when needed
- throws on HTTP failures rather than flattening them into normal tool text

`../2-druids`’ extension is clever and compact, but it is rougher in places:
- it uses `followUp` for all inbound messages
- it flattens failed tool calls into text results
- it uses a custom config-file indirection instead of the spec’s env-var model

### 5. Better spec alignment for extension config
The spec says the extension should receive config via environment variables:
- `DRUIDS_SERVER_URL`
- `DRUIDS_EXECUTION_ID`
- `DRUIDS_AGENT_ID`

This repo does that.

`../2-druids` instead writes a JSON config file and passes `DRUIDS_CONFIG`.

### 6. `launch_mode` is useful
This repo supports:
- `auto`
- `always`
- `manual`

That is genuinely useful for testing, protocol development, and environments where `pi` / `tmux` are not always available.

---

## Concrete issues in `../2-druids`

### 1. Register endpoint ignores `execution_id`
File: `../2-druids/druids/_server.py`

The register route accepts `execution_id` from the client but never validates it against the current execution. A wrong execution id still returns tools.

**Preferred behavior:** this repo’s validation in `druids/context.py::_register_agent()`.

### 2. Duplicate agent names are allowed and break state
File: `../2-druids/druids/_context.py`

`Context.agent()` does not reject duplicate names.

Observed behavior:
- `_agents[name]` is overwritten
- `_deferred_agents` gets two entries
- later spawn behavior becomes inconsistent

**Preferred behavior:** reject duplicates immediately.

### 3. Shared-machine server URL resolution is wrong
File: `../2-druids/druids/_context.py`

When spawning an agent with an explicit shared machine handle, the server URL is derived from `image or self._default_image`, not from the image associated with the machine handle.

That means an agent on a shared Docker machine can receive a localhost-style server URL from the default image.

**Preferred behavior:** tie server reachability to the concrete machine/image actually backing the agent.

### 4. Error semantics are text-first, not protocol-first
Files:
- `../2-druids/druids/_server.py`
- `../2-druids/druids/_extension.py`
- `../2-druids/druids/_context.py`

Many failure cases are turned into strings like `"Error: ..."` instead of structured non-200 responses or thrown tool failures.

That makes it harder for pi to distinguish:
- a successful tool result
- from a failed tool invocation

### 5. All inbound messages are delivered as `followUp`
File: `../2-druids/druids/_extension.py`

`deliverMessage()` always calls:
- `pi.sendUserMessage(text, { deliverAs: "followUp" })`

That is safe but can delay inter-agent communication more than necessary.

**Preferred behavior:**
- immediate send when idle
- `steer` when busy

### 6. `connect()` does not validate `direction`
File: `../2-druids/druids/_context.py`

Anything other than `"forward"` becomes effectively bidirectional.

**Preferred behavior:** reject invalid directions loudly.

### 7. `caller` parameter handling is inconsistent
Files:
- `../2-druids/druids/_tools.py`
- `../2-druids/druids/_context.py`

`extract_tool_schema()` includes `caller` if it exists in the signature, but `_handle_program_tool()` does not inject it.

That means a handler like:

```python
@agent.on("x")
def on_x(caller, y=""):
    ...
```

produces a schema requiring `caller`, but the runtime does not supply it.

This is a real mismatch.

---

## Concrete issues in this repo

### 1. Spawn readiness is weaker than the spec wants
File: `druids/context.py`

`_spawn_agent()` launches the process but does not wait for registration before treating the agent as spawned.

That is weaker than the spec’s “spawn immediately and block until ready” story, especially for dynamic agents created inside handlers.

`../2-druids` is better here.

### 2. Automated test coverage is much smaller
This repo has a smaller suite and the committed tests mostly use a fake HTTP agent client.

The live `pi` / `tmux` checks were run manually, but they are not currently part of the committed automated test suite.

`../2-druids` is better here.

### 3. The code is less settled as a reusable base
This repo works, but it still feels like a concentrated implementation sprint rather than the cleaner baseline that `../2-druids` has become.

### 4. The internal model is less elegant
This repo uses a thread-based HTTP server plus synchronous machine APIs. That is workable, but less graceful than the async-internals design in `../2-druids`.

### 5. Dynamic-agent semantics are only partially enforced
This repo does create dynamic agents during a running execution, but because registration waiting is not explicit, the semantics are softer than the spec suggests.

---

## Spec-alignment notes

### Areas where `../2-druids` is closer
- sync public API / async internals
- explicit readiness wait on spawn
- stronger end-to-end exercised orchestration loop in committed tests

### Areas where this repo is closer
- extension config via env vars
- better protocol strictness
- clearer error semantics
- more faithful use of pi’s current extension API

---

## If choosing one base today

### Pick: `../2-druids`

Reasons:
1. better tested
2. cleaner architecture
3. better spawn/readiness behavior
4. easier to grow into a long-lived codebase

### But immediately port these ideas from this repo

1. **register-time execution-id validation**
2. **duplicate-agent-name rejection**
3. **structured HTTP errors / status codes**
4. **better message delivery semantics (`idle` vs `steer`)**
5. **current pi extension API integration patterns**
6. **env-var-based extension config**
7. **optional `launch_mode`**

---

## Best-of-both recommendation

If the goal is the strongest implementation rather than defending one codebase, the best combination looks like this:

### Keep from `../2-druids`
- file/module structure
- async runtime model
- wait-for-registration on spawn
- broader automated suite
- subprocess / real-pi end-to-end tests

### Keep from this repo
- strict protocol validation
- better error handling semantics
- duplicate-name guards
- better extension behavior
- env-var extension config
- `launch_mode`

---

## Final verdict

- **Best current base:** `../2-druids`
- **Best protocol/runtime correctness choices:** a mix, with several important wins from this repo
- **Best next step:** merge the two approaches rather than treating either as final
