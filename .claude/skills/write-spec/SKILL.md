---
name: write-spec
description: >
  Reference for writing specs that get sent to Orpheus agents. Loaded
  automatically so spec quality is consistent. Covers demo-first design,
  codebase exploration, and persistence through failure.
user-invocable: false
---

# Writing Specs

When you write a spec for Orpheus execution, follow these guidelines. A spec is the primary input to an agent running on a remote VM. The agent has no other context. Everything it needs to know must be in the spec.

## What the agent has

The agent runs on a VM that the user set up via `orpheus setup`. The repo is already cloned, dependencies are already installed, and the environment is ready to go. The agent does not need to clone, install, or configure anything. It can start coding immediately.

The environment includes real credentials, real network access, real databases—whatever the project needs. This means a live demo is always feasible. The agent can start servers, run CLI commands, hit endpoints with curl, inspect databases, check process state, trigger real errors against real APIs, and do anything else that works on a Linux VM with the project fully set up. There is no excuse for skipping the demo. The environment is ready.

## Design the demo first

This is the most important instruction in this document.

Before writing requirements, before exploring the codebase, before anything else: figure out how the agent will demo the finished work. What commands will it run? What output will prove the change works? Write those commands down. Then work backward to the requirements.

Why this order? Because the demo shapes everything. If you write requirements first and design the demo last, you will end up with a demo that is an afterthought—"run pytest, check the health endpoint, submit." That proves nothing about the new behavior. But if you start with "how would I show a teammate this works?", the demo becomes specific, and the requirements naturally follow from what the demo needs.

### The demo-first process

1. **Ask: "what would I show a teammate?"** Not "what tests would I write." Imagine you are sitting next to someone and they say "show me it works." What would you do? What would you type into a terminal? That is your demo.

2. **Always start with the happy path.** The first thing the demo must show is the new feature actually working: the endpoint returning the right data, the CLI producing the right output, the UI rendering correctly. This is the most important single step in the entire spec. If the agent does nothing else, it must at least demonstrate that the thing it built works for the normal case. Error handling, edge cases, and failure modes come after—they matter, but the happy path comes first, always.

3. **Then add error cases and edge cases.** Bad input, missing resources, auth failures, malformed data. These are important, but they are the second layer of the demo, not the first.

4. **Write the demo commands.** Concrete shell commands, expected output, what to look for. These become the demo section of the spec.

5. **Work backward to requirements.** The demo implies what needs to be built. If the demo says "curl this endpoint and get a 201," then the requirement is "implement this endpoint." If the demo says "set a bad API key and confirm you get a useful error," then the requirement is "propagate errors with context instead of swallowing them."

6. **Only then think about automated tests.** If there are behaviors the demo exercised that should be protected from regression, those become test requirements. But the tests are downstream of the demo, not the other way around. And the tests go in the requirements section, not the demo section.

### Why this matters: a cautionary example

Consider a task like "fix error handling in a module that wraps an external API." The module currently swallows all exceptions and returns None. The fix involves distinguishing 404s (return None) from auth failures, network errors, and server errors (raise with context).

A spec written requirements-first will naturally include a requirement like: "Write tests that confirm ApiError is raised for non-404 errors." The agent will satisfy this with `unittest.mock.AsyncMock`, patching the API client to raise fake errors, and asserting the right exception type comes out. The mocked tests pass. The agent runs `pytest`, sees green, submits.

But the VM has a real API key. The agent could have:
- Started the server, called the endpoint with a valid resource ID, and confirmed the happy path still works correctly
- Started the server with `API_KEY=garbage`, hit an endpoint, and confirmed it returns a 502 with a useful error message instead of a misleading 404
- Called the endpoint with a nonexistent resource ID and confirmed it returns a clean 404
- Checked the logs and confirmed warnings appear for real errors

No mocks needed. The real error paths exercised by real API calls that fail in real ways. The demo would have caught bugs that the mocked tests cannot—misconfigured error handlers, logging that doesn't actually fire, status codes that don't map correctly through the HTTP layer.

A spec written demo-first would have started with "how do I show that error handling works?" and arrived at "trigger real errors against a running server." The mock-based test requirement would never have been written, because the demo *is* the test.

### What a demo is and is not

A demo is NOT:
- A pytest file with mocks, monkeypatches, or fixtures
- An in-process test harness (TestClient, ASGITransport, supertest)
- Running the existing test suite and seeing green
- A test that asserts a function was called with certain arguments
- Starting the server, hitting the health endpoint, and calling it a day

A demo IS:
- First and foremost: showing the happy path works. The new endpoint returns the right data. The CLI produces the right output. The feature does the thing it is supposed to do.
- Then: error cases and edge cases. Bad input, missing resources, auth failures.
- Starting the actual server and curling it—the new endpoints, the changed behavior, the error cases
- Running the actual CLI command and showing its output
- Deliberately triggering failure modes (bad credentials, missing resources, malformed input) and showing the system handles them correctly
- Exposing a public URL so the user can try it themselves
- Anything where the agent operates the software from the outside, the way a user or another service would

The distinction matters because agents have an extremely strong bias toward writing test files when asked to "verify" or "prove" something works. The word "test" pulls them into a genre where mocking is virtuous and in-process harnesses are standard. The word "demo" pulls them into a genre where they are an operator showing real behavior. Use "demo."

### Make it user-facing when possible

The agent's VM can expose public URLs. Whenever the change involves something a human could see or interact with—a web UI, an API, a dashboard—the demo should include a live URL the user can visit. This is the strongest possible form of proof: not "here's my terminal output," but "here, try it yourself."

The spec should say:

```
Expose the running server on a public URL and include the URL in your
submission. The user will check it.
```

This also has a useful side effect: it forces the agent to actually start the real server and keep it running, which makes it very hard to fake the demo with mocks or in-process harnesses.

When the change is purely backend or has no meaningful visual/interactive component (a migration, a refactor of internal logic, a CLI tool), terminal output is fine. But default to user-facing when there's any UI or API involved.

## Explore the codebase

After designing the demo, search the repository to understand:

- What files and modules are relevant?
- What conventions does the codebase follow?
- What existing tests must not break?
- What build/lint/test commands are available?

Collect specific file paths, function names, and type signatures. Embed them in the spec. Every minute an agent spends rediscovering the codebase layout is wasted.

## Writing the demo section

### When you know the exact steps

Spell out the commands and expected output:

**Example: a new API endpoint**
```
DEMO

Start the server:
  cd server && uv run my-server &
  curl --retry 5 --retry-delay 2 http://localhost:8000/health

Call the new endpoint:
  curl -X POST http://localhost:8000/api/widgets \
    -H 'Content-Type: application/json' \
    -d '{"name": "test-widget", "color": "blue"}'
Expected: 201 status, body contains {"id": "<uuid>", "name": "test-widget", "color": "blue"}

Include the terminal output in your submission as proof.
```

**Example: a CLI tool**
```
DEMO

Build and run the CLI:
  cargo build
  ./target/debug/mytool convert --input data/sample.csv --output /tmp/out.json

  cat /tmp/out.json
Expected: valid JSON array with one object per CSV row, keys matching
the CSV headers, date fields in ISO 8601 format.

Then run it on malformed input:
  ./target/debug/mytool convert --input data/malformed.csv --output /tmp/bad.json
Expected: exit code 1, stderr contains a human-readable error message
mentioning the line number of the first bad row.

Include the terminal output in your submission as proof.
```

**Example: error handling changes**
```
DEMO

Start the server:
  cd server && uv run my-server &
  curl --retry 5 --retry-delay 2 http://localhost:8000/health

Happy path first—request a resource that exists and confirm it works:
  curl -s http://localhost:8000/api/resources/KNOWN_VALID_ID | jq .
Expected: 200 with the resource data.

Then error cases. Not-found—request a resource that does not exist:
  curl -s -w "\n%{http_code}" http://localhost:8000/api/resources/nonexistent-abc123
Expected: 404 with a clear error message.

Auth failure—restart the server with a bad API key:
  kill %1
  EXTERNAL_API_KEY=garbage uv run my-server &
  curl --retry 5 --retry-delay 2 http://localhost:8000/health
  curl -s -w "\n%{http_code}" http://localhost:8000/api/resources/anything
Expected: 502 with an error message that includes the upstream status
code (e.g. "External API error (401): ..."), NOT a 404.

Check the logs for warnings about the auth failure.

Include the terminal output in your submission as proof.
```

**Example: a database migration**
```
DEMO

Apply the migration and inspect the schema:
  cd backend && uv run alembic upgrade head
  sqlite3 app.db ".schema users"
Expected: the `users` table has a new `email_verified` column (boolean, default false).

Then verify the app still starts and can create a user:
  uv run my-app &
  curl --retry 5 --retry-delay 2 http://localhost:5000/health
  curl -X POST http://localhost:5000/api/users \
    -H 'Content-Type: application/json' \
    -d '{"username": "testuser", "email": "test@example.com"}'
Expected: 201, and `sqlite3 app.db "SELECT email_verified FROM users WHERE username='testuser'"` returns `0`.

Include the terminal output in your submission as proof.
```

### When the spec is loose

When you cannot prescribe exact commands—a refactor, a design improvement, an open-ended feature—the spec must still demand a demo. Tell the agent what "showing it works" means in context:

```
DEMO

You must demo your changes by actually running them. Start the server
(or CLI, or script—whatever the real interface is), use the feature the
way a human would, and show me the terminal output proving it works.

Do not write test files as your demo. Do not use mocks, test clients,
ASGITransport, or in-process harnesses. Actually start the process and
interact with it from the outside.

Do not just run the existing test suite and submit. The existing tests
do not cover what you just built. You need to show that your changes
actually work.

Include the terminal output in your submission as proof.
```

Even a loose spec must make it clear that "write a new test file, see green, submit" is not acceptable.

### Automated tests are separate

Proof comes in two forms [1]:

1. **Demo** (primary): run it, show the output. This is the demo section.
2. **Automated tests** (secondary): write tests that fail when the change is reverted. These go in the requirements section, not the demo section.

Both matter, but they are different things and the spec must keep them separate. The demo proves the feature works right now. The automated tests protect it from future regressions. An agent that writes tests but skips the demo has not finished. An agent that does the demo but skips the tests has at least proven the code works.

Never let the agent substitute one for the other. If the spec says "demo" and the agent writes a pytest file, it has not done the demo.

## Tell the agent to persist

The other way agents fail: they try the demo, it fails, and they give up. They submit with "I was unable to verify this" or they skip to creating the PR.

The spec must say explicitly: if the demo fails, fix the code and try again. You are done when it works, not when you have tried once. Include language like this in the DEMO section:

```
If a step fails, that means your code has a bug. Fix it and run the
step again. Do not submit until every step produces the expected result.
Do not skip steps. Do not submit with "I was unable to complete the demo."
```

## Spec structure

Write the spec as a Markdown file (`spec.md`). Use this as a loose template—adapt it to the task, drop sections that don't apply, add sections that do.

```markdown
# [Title]

## Objective

[What to build or change and why.]

## Demo

You are not done until you have run the thing you built, for real,
and shown the output. Do not write test files as your demo. Do not
use mocks or in-process test harnesses. Actually start the system and
interact with it from the outside, the way a user would.

If the change involves a web UI or API, expose the running server on
a public URL and include the URL in your submission so the user can
try it themselves. This is the strongest form of proof.

If a step fails, that means your code has a bug. Fix it and try again.
Do not submit until it works. Do not submit with "I was unable to
complete the demo."

[Concrete commands with expected outputs, or a description of what
"showing it works" looks like for this task.]

Include the terminal output (and public URL if applicable) in your
submission as proof.

## Codebase context

[Relevant file paths, module structure, conventions. Be specific.]

Key files:
- `path/to/module.py` -- [what it does]
- `path/to/other.py` -- [what it does]

## Requirements

- [Requirement 1]
- [Requirement 2]
- Write automated tests that cover the new behavior (these are
  separate from the demo above)
- All existing tests must continue to pass

## Constraints

- [Any scope limits, things not to change, etc.]
```

Note: the demo section comes before the requirements. This is intentional. The demo is what the agent is working toward. The requirements are how it gets there.

## Review checks

Before finalizing, ask:

1. Could an agent pass every check in this spec by running `pytest` and `ruff check` and nothing else? If yes, the spec is not done.
2. Could an agent satisfy the demo section by writing a new test file? If yes, the demo section is not specific enough.
3. Does the demo exercise the happy path? The agent must show the new feature actually working for the normal case. A demo that only tests error handling or edge cases is incomplete. A demo that only checks the health endpoint is not a demo of your feature.
4. If the task involves error handling, does the demo also trigger real errors? If the VM has credentials and network access, the demo should use them—not mock them.

## Principles

**Demo-first design.** Design the demo before writing requirements. Ask "how would I show a teammate this works?" first, then work backward to what needs to be built. The demo shapes the spec, not the other way around. [1]

**Happy path first, always.** The first thing the demo must prove is that the feature works for the normal case. Error cases and edge cases come second. If the agent demos nothing else, it must demo the happy path.

**Real environment, real errors.** The VM has real credentials, real network access, real databases. Use them. If you're fixing error handling, trigger real errors. If you're adding an API endpoint, call it with curl. Mocks are for regression tests, not for demos.

**Persist through failure.** If the demo fails, fix the code and try again. Agents that give up on first failure are the norm. The spec must make giving up unacceptable.

**Context over exploration.** Front-load file paths, function signatures, conventions. Every minute the agent spends figuring out the codebase is a minute not spent building.

**Demo, not test.** Use the word "demo." Avoid the word "verify." The genre matters. [2]

## References

[1] Simon Willison, "Your Job Is to Deliver Code You Have Proven to Work" (2025). https://simonwillison.net/2025/Dec/18/code-proven-to-work/

[2] The word "test" activates a strong prior toward mocks, fixtures, and in-process harnesses. The word "demo" activates a prior toward running the real system and showing output. When writing specs, choose language that activates the right prior.
