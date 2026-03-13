---
name: setup
description: >
  Guide for helping users set up their devbox on Druids. Covers the full
  flow from provisioning to snapshot, using remote_exec to assist with
  dependency installation and environment configuration.
user-invocable: true
---

# Devbox Setup

A devbox is a VM snapshot with the user's repo cloned and dependencies installed. Every repo needs one before tasks can run against it. The `setup start` endpoint provisions the VM and clones the repo. The user (or you via `remote_exec`) installs dependencies and configures the environment. Then `setup finish` snapshots the VM.

At each step, explain what you are about to do, what you found, and what you need from the user. By default, figure things out from the codebase. But give the user a chance to intervene -- they may know about services that need to run, dependencies that are tricky to install, environment variables that are not obvious from the code, or testing requirements like headless browsers.

## Flow

### 1. Start the devbox

If it's not clear from the current directory which repo the user wants, ask them. Then call:

```
druids setup start
```

This provisions a VM from the base Druids snapshot, clones the repo, and returns SSH credentials. The user can SSH in, or you can use `remote_exec` to run commands on the devbox.

If the user has already started setup, this returns the existing instance.

Before proceeding, ask the user: "I'm going to explore the repo to figure out dependencies, services, and environment config. Do you have any setup instructions I should follow, or anything I should know before I start? (e.g. specific services that need to run, tricky dependencies, env vars I'll need values for)"

If they have guidance, follow it. If not, proceed by exploring the codebase.

### 2. Install dependencies

Read the project's setup files to figure out what it needs:

```
remote_exec(repo="owner/repo", command="cat /home/agent/repo/package.json")
remote_exec(repo="owner/repo", command="cat /home/agent/repo/pyproject.toml")
remote_exec(repo="owner/repo", command="cat /home/agent/repo/Makefile")
```

Tell the user what you found and what you are about to install. For example: "I found a Python project using uv with these dependencies. I'm going to run `uv sync`. Anything I should know before I install?"

The user may flag things like: pinned system libraries, packages that need build tools (gcc, libffi-dev), or dependencies that require special installation steps.

Then install:

```
remote_exec(repo="owner/repo", command="sudo -u agent bash -c 'cd /home/agent/repo && npm install'")
remote_exec(repo="owner/repo", command="sudo -u agent bash -c 'cd /home/agent/repo && pip install -e .'")
```

Commands run as root, so prefix with `sudo -u agent` when needed. The repo is cloned at `/home/agent/repo`.

### 3. Configure the environment

Figure out what the project needs beyond dependencies:

- Read the code for config: look for `.env` files, `BaseSettings` classes, `os.environ.get` calls, `config.json`, `docker-compose.yml`, etc.
- Identify services: databases, caches, message queues, background workers.
- Identify ports: what the project binds to and whether any need external exposure.

Present your findings to the user. For example: "I found the server reads these env vars from `server/.env`: `DATABASE_URL`, `REDIS_URL`, `API_KEY`, `SECRET_KEY`. The database URL I can set to localhost. For `API_KEY` and `SECRET_KEY`, I'll need values from you. The project also needs PostgreSQL and Redis running. Should I install and start both?"

Be specific about what you need:

- For each env var: what it controls, whether you can set a reasonable default, or whether you need a value from the user.
- For each service: what it is, what the project uses it for, and whether you should install it.
- For ports: what binds where, and whether agents will need to expose any of them externally (which may require updating config like base URLs).

Set up services and write config files via `remote_exec`. Ask the user for secret values (API keys, tokens) -- do not guess or skip these.

### 4. Verify the environment

Run the project's test suite to confirm the basics work:

```
remote_exec(repo="owner/repo", command="sudo -u agent bash -c 'cd /home/agent/repo && npm test'")
```

If something fails, diagnose and fix it.

But tests are just a baseline. Before moving on, ask the user: "Tests pass. Is there anything else I should verify? For example: does the server need to start and respond to requests? Are there integration tests that need a running database? Do tests need a headless browser or other runtime?"

If the project has a server, start it and hit its health endpoint. If it has a CLI, run a command. If it has a database, confirm it connects. The goal is a VM where an agent can build, run, and interact with the system end to end, not just pass unit tests.

### 5. Update SETUP.md

If the repo has a `SETUP.md`, add an "Agent environment" section describing what agents need to know about the snapshot environment. If it does not have one, create it.

Agent VMs are forked from this snapshot, so dependencies are already installed and the environment is already configured. The agent environment section tells agents what is already set up for them and how to use it.

Write the file locally, then upload it to the devbox:

```
druids upload SETUP.md /home/agent/repo/SETUP.md
```

Do not duplicate information already in the repo's README, CLAUDE.md, or other docs. Focus on the runtime environment that was configured during setup:

- What services are running (databases, servers, workers) and on what ports. How to restart each one.
- What environment variables were set, where they live, and what they control (names and purpose, not secret values).
- What ports are in use. If any need to be exposed externally, what env vars or config must change to match the new URL.
- How to verify the system works: not just "run pytest" but the full loop. If the project has a server, how to start it and confirm it responds. If it has an API, how to call it. If it has agents or workers, how to trigger them and observe the result.
- Known issues or quirks specific to the environment (e.g. "Postgres must be running before the server starts").

The purpose is so agents can immediately interact with the system end to end without rediscovering how the project works. An agent reading this file should know how to start the system, exercise it, and confirm their changes work -- not just run a test suite. Keep it concise.

After uploading, ask the user if they want to commit `SETUP.md` to the repo. If yes, commit and push it to the default branch.

### 6. Finish and snapshot

Once the environment is working:

```
druids setup finish
```

This snapshots the VM and stores the snapshot ID in the database. Future executions against this repo will fork from this snapshot. The old snapshot (if any) is deleted.

## Tips

- The VM runs Debian with Python 3.11, Node.js LTS, and GitHub CLI pre-installed.
- The `druids` CLI is available on the VM for file transfers between VMs.
- The agent user has passwordless sudo.
- If the project needs a database (Postgres, Redis, etc.), install and configure it during setup. It will be captured in the snapshot.
- If the project needs environment variables or config files, create them during setup. Use `.env` files or write to `/home/agent/.bashrc`.
- The user can SSH in alongside you. Coordinate if they want to do some steps manually.
- If setup fails partway through, `setup start` will return the same instance so you can retry.
- After finishing, test by creating a quick execution with a simple program that runs the test suite.
