# Getting started

This walkthrough gets you from zero to a running agent execution. It assumes you have Claude Code (or another coding agent) and want it to drive the setup.

## Install the CLI

```bash
uv tool install druids
```

## Authenticate

Sign in at druids.dev and go to Settings to create an API key. Then:

```bash
druids auth set-key druid_your_key_here
```

Verify it worked:

```bash
druids auth status
```

## How it works

Once set up, the workflow looks like this:

1. You create a devbox: a snapshotted VM with your repo cloned and dependencies installed.
2. You write an agent coordination program (or use a starter program) that defines how agents work on your codebase.
3. You run the program from the CLI or through the MCP server, which launches an execution: one or more agents working on VMs forked from your devbox.
4. You check in on progress through druids.dev, the CLI, or the MCP tools.
5. Agents finish (perhaps opening a PR on your repository).

The rest of this guide walks through each piece.

## Initialize your repo

From the root of your project:

```bash
druids init
```

This fetches starter programs into `.druids/` and prints a snippet to add to your agent instructions (e.g. `CLAUDE.md`). The programs define how agents run on your codebase: `build.py` for feature work, `review.py` for code review, and `main.py` as a general-purpose entry point.

## Add the MCP server

`druids init` (above) automatically adds the Druids MCP server to `.mcp.json` using your authenticated credentials. Restart Claude Code after running init.

Once connected, you can tell Claude Code what you want to build and it will use the Druids tools directly.

## (Optional) Install the GitHub App

Druids needs access to your repositories to clone code and open pull requests. Go to Settings on druids.dev and click "Configure GitHub App" to install it on the repos you want to work with.

## Set up a devbox

A devbox is a snapshotted VM with your repo cloned and dependencies installed. Every execution forks from it, so agents start with a working environment.

Tell Claude Code:

```
Set up a devbox for this repo.
```

It will provision a VM, explore your project to figure out dependencies and services, install everything, verify the environment works, and snapshot the result. It will ask you for anything it cannot figure out on its own, like API keys or non-obvious configuration.

If you prefer to do this manually:

```bash
druids devbox create --repo owner/repo
# SSH in and install dependencies
druids devbox snapshot --name owner/repo
```

## Run your first execution

Tell Claude Code what you want built:

```
Use druids to write a /health endpoint that returns 200 OK.
```

It will write a spec, pick a program, and launch an execution. Agents implement the feature on a branch and open a PR when done. You can also launch directly from the CLI:

```bash
druids exec build \
  task_name="health endpoint" \
  spec="Add a /health endpoint that returns 200 OK"
```

## Monitor progress

Every execution has a page on [druids.dev](https://druids.dev) where you can watch agent activity in real time. The CLI prints the URL when you start an execution.

From the CLI:

```bash
druids execution ls                          # list executions
druids execution status bright-fox           # check one execution
druids execution activity bright-fox         # show recent agent activity
druids execution ssh bright-fox              # open a shell on the VM
druids execution connect bright-fox          # resume agent coding session
```

Or just ask Claude Code: "How is my execution going?"
