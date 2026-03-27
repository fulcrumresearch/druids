---
name: regimen-demonstrate
description: >
  Demonstrate that a feature works by interacting with the running system,
  then write a regimen file capturing what you did.
user-invocable: true
---

# /regimen-demonstrate

First prove a feature works by actually using it. Then write down what you did as a regimen file so a judge agent can repeat it later.

## Phase 1: Demonstrate

Interact with the running system to verify the latest changes work.

### What "demonstrate" means

External interaction with a running system: HTTP requests, CLI commands, database queries against a running server, log observation, file output inspection.

NOT: importing modules, constructing objects in a REPL, running grep to confirm structure, verifying types or signatures exist, reading source code to check implementation. Those are code reading, not demonstrating.

### How to demonstrate

1. Start the actual system. Build it, run it, wait for it to be ready.
2. Exercise every behavior you want to verify from the outside.
3. Test edge cases and error paths, not just the happy path.
4. If things break, persist. Read tracebacks, fix the environment, try again. Do not give up after one error.

As you go, pay attention to:
- What you had to set up first (start a server, seed data, install something)
- What commands you ran and in what order
- What output you saw and why it proves the feature works
- What edge cases you tested

## Phase 2: Write it down

Once you have demonstrated the feature, write a `.regimen/<name>.md` file that captures what you did as a reproducible verification plan.

Start the file with a short paragraph describing what this check covers: what part of the system, what functionality, what areas of the codebase it relates to. This is for humans and for deciding which checks to rerun when something changes.

### Format

```markdown
# User authentication

Covers the registration and login endpoints in the API server. Relates to the auth middleware, user model, and session handling. Tests happy path, input validation, and error responses.

## Setup

Start the server.

```bash
cd /path/to/project && npm start &
```

Wait for the server to be ready.

```bash
curl -s --retry 5 --retry-connrefused http://localhost:3000/health
```

Expected: HTTP 200, body contains `{"status": "ok"}`.

## Register a new user

POST to the register endpoint. Should return HTTP 201 with a JSON body containing an `id` field.

```bash
curl -s -X POST http://localhost:3000/register \
  -H "Content-Type: application/json" \
  -d '{"email": "test@example.com", "password": "secret"}'
```

## Bad input returns 400

Sending an empty body should return HTTP 400 with an error message.

```bash
curl -s -w "\n%{http_code}" -X POST http://localhost:3000/register \
  -H "Content-Type: application/json" -d '{}'
```

## Cleanup

```bash
kill %1
```
```

### Steps

Each `##` heading is a step. Steps run in order.

- Prose describes the goal and what output proves success. Be specific enough that someone unfamiliar could judge pass/fail. "Returns a list" is too vague. "Returns JSON array with at least one element, each with `id` and `name` fields" is checkable.
- Bash blocks are commands the judge should run. They are instructions, not rigid scripts.
- One verification per step. Setup and cleanup get their own steps.
- Every step interacts with a running system. If you catch yourself writing a step that greps source code or imports a module, stop. That is not a demonstration.

### Timeouts

When you demonstrate in phase 1, note how long each command takes. In phase 2, write explicit timeouts on the fence line based on what you observed. Give 2-3x headroom over the actual time.

````
```bash timeout=30
npm run build
```
````

If a command took 8 seconds when you ran it, write `timeout=20`. If it was instant, no timeout needed. If it blocks forever (server startup), it should be backgrounded with `&` and needs no timeout.

Avoid commands that block forever. Background servers (`&`). Use bounded retries (`curl --retry 5 --retry-connrefused`) not open-ended loops.
