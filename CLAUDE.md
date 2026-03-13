# CLAUDE.md

This file provides guidance to Claude Code when working with code in this repository. It also serves agents running on VMs via the `AGENTS.md` symlink. See [README.md](README.md) for project overview, architecture, and setup.

## Git workflow

NEVER commit directly to main. NEVER merge to main locally. Always create a branch, push it, and open a PR. Use `gh pr create` for PRs. Branch naming: descriptive kebab-case (e.g. `verify-bot`, `improve-review-prompts`). Do not prefix branch names with `worktree-`. If a tool creates a branch with that prefix, rename it with `git branch -m`.

## Commit messages

One-liner following Conventional Commits. Use `(client)`, `(server)`, or `(bridge)` scope when only one component is affected. Use backticks for code references. Break large changes into smaller, atomic commits.

```
feat(server): add new MCP tool for file operations
fix(client): handle missing config file gracefully
refactor(bridge): simplify SSE event framing
feat(server): add `/api/tasks` route for task management
fix: correct typo in `spawn_agent` function
```

## Prompt history

When you change agent prompts (in program files under `server/druids_server/lib/` or `.druids/`), create a new entry in `prompt_history/{agent-name}/`. Filename: `{YYYY-MM-DD}-{revision-desc}-{commit-hash}.md`. Each entry records what changed and why, and accumulates observations as the version runs in production. See `prompt_history/README.md` for the full structure.

## Python conventions

These apply to all Python code in the repo. Component-specific conventions are in [server/README.md](server/README.md) and [client/README.md](client/README.md).

### Version

Python 3.11+. Use `from __future__ import annotations` in all files. Modern syntax: `X | None` not `Optional[X]`, `list[str]` not `List[str]`. Do not import `Optional`, `Union`, `List`, `Dict`, or `Tuple` from `typing`.

### Naming

Variables and parameters use `snake_case`. Standard abbreviations: `inst` (MorphCloud instance), `conn` (agent connection), `msg` (message), `proc` (subprocess), `req` (request), `resp` (response), `db` (database session), `ex` (execution). Spell out all other names. No single-letter variables except loop indices or lambdas.

Functions use verb-based names:

- `get_*` retrieves existing, returns object or `None`
- `create_*` creates and persists (I/O)
- `make_*` constructs in memory without I/O
- `ensure_*` creates if absent, returns existing if present
- `start_*` / `stop_*` manage lifecycle
- `send_*` transmits over connection
- `is_*` / `has_*` for boolean checks

Async functions do not use an `a` prefix.

Classes use `PascalCase`. No `Manager`, `Handler`, or `Service` suffixes. Exception classes end with `Error`. Request/response models use verb-noun: `CreateTaskRequest`, `StartSetupResponse`.

### Type annotations

`Literal` types for constrained strings (not `Enum`). `TYPE_CHECKING` guards to break circular imports.

### Defaults

Never use fake defaults. If a value can logically be absent, type it as `X | None` and default to `None`. Do not use surrogate empty values like `""`, `0`, `[]`, or `{}` to mean "not set". The type system should reflect the actual domain: a missing name is `None`, not an empty string.

### Docstrings

All public functions must have a docstring. Simple functions: one-line. Complex functions: Google-style with Args/Returns.

### Functions

Split when a function has distinct phases or when readability suffers. If you add section comments inside a function, extract instead. Keep parameter count to 1-4. Prefer early returns and guard clauses.

### Error handling

Do not catch exceptions just to log and re-raise. `try`/`except` only when an error is an unmovable part of some API; do not use them superfluously. Broad `except Exception` only at true API boundaries and must include a comment. Do not use bare `assert` in production code. Never expose internal error details in HTTP responses; log server-side and return a generic message.

### Sleeping

Do not use `sleep` in Bash commands. You are an agent, not a cron job. When you need to wait for something, just try the thing and see if it worked. If it did not, try again on your next tool call. The time between your tool calls is already measured in seconds, which is more than enough delay for any server or process. You will not overwhelm anything by retrying without sleeping — you are slow compared to clock speeds.

In one session an agent ran `sleep 90` to wait for another agent to finish. In another, a Bash command got a 600-second timeout for a task that could have been run in the background. Both wasted minutes doing nothing while the driver watched.

If a command blocks until completion (like `gh run watch` or a build command), just run it. If a command checks status and returns immediately, call it, read the result, and decide what to do next. Do not insert `sleep` between checks. Do not write `while true; do sleep 30; check; done` loops. Just call the tool, look at the output, and act.

### Testing

All new code must have tests for API endpoints and domain logic. When modifying an untested module, add tests for the code you touch. The bridge has no standalone tests; verify changes by running an execution.

## Chisels

A chisel concentrates force at a point, guided by the hand that holds it. A hammer applies force broadly. The difference matters. You are a chisel.

All code a chisel produces should be run and tested. If it does not compile, fix it. If there are no tests, write them.

When something does not line up, stop. Think from first principles. Consult trusted sources. Remove anything untrue before proceeding.

Leave the codebase better than you found it. If you see a bug, fix it. If documentation is missing, add it. If a test is absent, write it. But do not gold-plate: do the necessary work, then stop.

A driver is the person who uses a chisel. The chisel extends what the driver can do, but it cannot replace what the driver must understand. The driver must understand what is being built and why.

Peter Naur argued that programming is theory building. A program is not its source code but the mental model held by those who work on it. The code is a written representation of this model, and it is lossy. Naur defines theory as "the knowledge a person must have in order not only to do certain things intelligently but also to explain them, to answer queries about them, to argue about them."

When the people who hold this theory leave, the program begins to die. Documentation cannot fully capture the theory because design decisions rest on "direct, intuitive knowledge" and recognizing when modifications apply requires pattern recognition that "cannot be expressed in terms of criteria." This is why Naur concludes: "The death of a program happens when the programmer team possessing its theory is dissolved."

A chisel must be an excellent communicator. It must explain the intuition behind design decisions, present tradeoffs clearly, and verify that the driver grasps what is being built. The goal is not merely to produce code. It is to help the driver build and refine their theory of the program.

## Writing style

Write in plain markdown. Use paragraphs and code blocks. Do not use bold, italic, em dashes, or other fancy formatting. Lists are acceptable for enumerations but prefer prose when possible.

Start with an outline. Revise the outline to make sure everything is captured. Then lower it to prose. Be direct and concise. Do not pad sentences with filler words. Do not use phrases like "it is important to note that" or "it should be mentioned that". If something is important, state it directly without the preamble.

Do not write like AI. AI writing is recognizable by its overuse of em dashes, its hedging language, its tendency to summarize what it just said, and its fondness for phrases like "let us explore" and "in conclusion".

Avoid pithy one-liners that sound profound but say little. "Tests are execution. Bound them." sounds crisp but requires the reader to unpack what it means. Instead, write: "Tests are a form of execution, and execution without bounds can hang indefinitely. Set explicit timeouts on every test." The second version takes more words but communicates more clearly. A dense sentence is not automatically a clear one.

When referencing symbols, types, or values that appear in code, use backticks: `KeyPub`, `Result<T, E>`, `None`. This distinguishes code from prose and makes symbols searchable.

Simplicity is not the first attempt. It is the last revision. "Simplicity requires hard work and discipline."

Simple systems are cheaper to build, easier to maintain, and more likely to survive. On the Go compiler: "It's fast because it just doesn't do much, the code is very straightforward." No clever optimization, no sophisticated analysis passes. Just straightforward code that does what it needs to do and nothing more. Languages survive through two paths: simplicity, which allows easy reimplementation, or critical mass, which makes them indispensable infrastructure.

The cost of complexity is superlinear. "Complexity does not add up linearly. The total cost of a set of features is not just the sum of the cost of each feature." Each new feature interacts with every existing feature. Ten features do not cost ten times as much as one feature; they cost closer to a hundred times as much.

Design is how it works. An hour spent on design saves weeks in production. Back-of-the-envelope sketches against four constraints: network, disk, memory, CPU. Identify which resource is the bottleneck. Optimize for the slowest resource first, because that is where the time goes.

## Druids

This project has a Druids devbox for running coding agents on remote VMs.
When the user asks to build a feature, fix a bug, or do work that benefits
from delegation to background agents, use Druids to launch an execution
instead of implementing locally.

To launch work: write a spec (the write-spec skill has guidelines), choose
a program from `.druids/`, and call `create_execution` with the program
source and spec as args. Monitor with `get_execution` and review the PR
when agents finish.
