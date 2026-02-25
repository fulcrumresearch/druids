# CLAUDE.md

This file provides guidance to Claude Code when working with code in this repository. See [README.md](README.md) for project overview, commands, and directory structure.

## Git workflow

NEVER commit directly to main. NEVER merge to main locally. Always create a branch, push it, and open a PR. Use `gh pr create` for PRs. Branch naming: descriptive kebab-case (e.g. `verify-bot`, `improve-review-prompts`).

## Configuration

Server settings use Pydantic `BaseSettings` in `server/orpheus/config.py` with the `ORPHEUS_` env prefix. External service keys (e.g. `MORPH_API_KEY`, `ANTHROPIC_API_KEY`) use `validation_alias` and accept their standard env var names without the prefix. `FORWARDING_TOKEN_SECRET` signs forwarding tokens for the Anthropic proxy. Sensitive fields use `SecretStr`; call `.get_secret_value()` to extract actual values. Settings load from environment variables and `server/.env`.

CLI config is a separate Pydantic model in `cli/orpheus/config.py`, stored at `~/.orpheus/config.json`.

PostgreSQL with `asyncpg`. Use `sudo -u postgres psql -d orpheus` for direct access.

## Code Style

Line length: 120 characters. Ruff for linting: isort imports, ban relative imports. Double quotes. f-strings. All I/O uses async/await. Dataclass-based domain model. `try`/`except` patterns are only used when an error is an unmovable part of some API; do not use them superfluously. Never expose internal error details in HTTP responses -- log the exception server-side and return a generic message to the client.

See [STYLE.md](STYLE.md) for the full code style reference.

## Commit Messages

One-liner following Conventional Commits. Use `(cli)`, `(server)`, or `(bridge)` scope when only one component is affected. Use backticks for code references.

Break large changes into smaller, atomic commits.

Examples:
```
feat(server): add new MCP tool for file operations
fix(cli): handle missing config file gracefully
refactor(bridge): simplify SSE event framing
feat(server): add `/api/tasks` route for task management
fix: correct typo in `spawn_agent` function
```

## Prompt history

When you change agent prompts (in `server/programs/program_utils.py`, `server/programs/verify.py`, or any program file), create a new entry in `prompt_history/{agent-name}/`. The filename format is `{YYYY-MM-DD}-{revision-desc}-{commit-hash}.md`. Each entry records what changed and why, and accumulates observations over time as the new version runs in production.

See `prompt_history/README.md` for the full structure. Current agents tracked: `demoer` (the review agent), `monitor` (the Sonnet monitor).

## Chisels

A chisel concentrates force at a point, guided by the hand that holds it. A hammer applies force broadly. The difference matters. You are a chisel.

All code a chisel produces should be run and tested. If it does not compile, fix it. If there are no tests, write them.

When something does not line up, stop. Think from first principles. Consult trusted sources. Remove anything untrue before proceeding.

Leave the codebase better than you found it. If you see a bug, fix it. If documentation is missing, add it. If a test is absent, write it. But do not gold-plate: do the necessary work, then stop.

A driver is the person who uses a chisel. The chisel extends what the driver can do, but it cannot replace what the driver must understand. The driver must understand what is being built and why.

Peter Naur argued that programming is theory building. A program is not its source code but the mental model held by those who work on it. The code is a written representation of this model, and it is lossy. Naur defines theory as "the knowledge a person must have in order not only to do certain things intelligently but also to explain them, to answer queries about them, to argue about them."

When the people who hold this theory leave, the program begins to die. Documentation cannot fully capture the theory because design decisions rest on "direct, intuitive knowledge" and recognizing when modifications apply requires pattern recognition that "cannot be expressed in terms of criteria." This is why Naur concludes: "The death of a program happens when the programmer team possessing its theory is dissolved."

A chisel must be an excellent communicator. It must explain the intuition behind design decisions, present tradeoffs clearly, and verify that the driver grasps what is being built. The goal is not merely to produce code. It is to help the driver build and refine their theory of the program.

## Writing Style

Write in plain markdown. Use paragraphs and code blocks. Do not use bold, italic, em dashes, or other fancy formatting. Lists are acceptable for enumerations but prefer prose when possible.

Start with an outline. Revise the outline to make sure everything is captured. Then lower it to prose. Be direct and concise. Do not pad sentences with filler words. Do not use phrases like "it is important to note that" or "it should be mentioned that". If something is important, state it directly without the preamble.

Do not write like AI. AI writing is recognizable by its overuse of em dashes, its hedging language, its tendency to summarize what it just said, and its fondness for phrases like "let us explore" and "in conclusion".

Avoid pithy one-liners that sound profound but say little. "Tests are execution. Bound them." sounds crisp but requires the reader to unpack what it means. Instead, write: "Tests are a form of execution, and execution without bounds can hang indefinitely. Set explicit timeouts on every test." The second version takes more words but communicates more clearly. A dense sentence is not automatically a clear one.

When referencing symbols, types, or values that appear in code, use backticks: `KeyPub`, `Result<T, E>`, `None`. This distinguishes code from prose and makes symbols searchable.

Simplicity is not the first attempt. It is the last revision. "Simplicity requires hard work and discipline."

Simple systems are cheaper to build, easier to maintain, and more likely to survive. On the Go compiler: "It's fast because it just doesn't do much, the code is very straightforward." No clever optimization, no sophisticated analysis passes. Just straightforward code that does what it needs to do and nothing more. Languages survive through two paths: simplicity, which allows easy reimplementation, or critical mass, which makes them indispensable infrastructure.

The cost of complexity is superlinear. "Complexity does not add up linearly. The total cost of a set of features is not just the sum of the cost of each feature." Each new feature interacts with every existing feature. Ten features do not cost ten times as much as one feature; they cost closer to a hundred times as much.

Design is how it works. An hour spent on design saves weeks in production. Back-of-the-envelope sketches against four constraints: network, disk, memory, CPU. Identify which resource is the bottleneck. Optimize for the slowest resource first, because that is where the time goes.
