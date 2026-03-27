# CLI Reference

The `druids` CLI runs programs on remote sandboxes and manages devbox
environments, secrets, and authentication.

```
druids <command> [options]
druids --version
```

## druids exec

Run a program on a remote sandbox.

```
druids exec <program> [options] [key=value ...]
```

| Flag | Short | Description |
|---|---|---|
| `--devbox NAME` | `-d` | Devbox name. Default: devbox for current repo. |
| `--branch BRANCH` | `-b` | Git branch to checkout. |
| `--ttl SECONDS` | | Time-to-live in seconds (0 = server default). |
| `--no-stream` | | Don't stream events after starting. |
| `--no-setup` | | Run on the default base image without a devbox. |
| `--add-files PATH` | `-f` | Local files to copy into the sandbox. |

Extra arguments are passed as `key=value` pairs to the program function.
Bare program names are resolved against `.druids/` (e.g. `build` becomes `.druids/build.py`).

```
druids exec build spec="build the login page"
druids exec build --devbox mybox --branch feat/new-ui spec="add dark mode"
druids exec .druids/build.py spec="explicit path also works"
```

## druids execution ls

List executions.

```
druids execution ls [options]
```

| Flag | Short | Description |
|---|---|---|
| `--all` | `-a` | Include stopped executions. |

```
druids execution ls
druids execution ls --all
```

## druids execution status

Check status of an execution.

```
druids execution status <slug>
```

```
druids execution status bright-fox
```

## druids execution activity

Show recent activity for an execution.

```
druids execution activity <slug> [options]
```

| Flag | Short | Description |
|---|---|---|
| `--n N` | `-n` | Number of recent events (default: 50). |
| `--compact / --full` | | Compact output (default) or full event details. |

```
druids execution activity bright-fox
druids execution activity bright-fox -n 20 --full
```

## druids execution stop

Stop a running execution.

```
druids execution stop <slug>
```

```
druids execution stop bright-fox
```

## druids execution send

Send a message to an agent in a running execution.

```
druids execution send <slug> <message> [options]
```

| Flag | Short | Description |
|---|---|---|
| `--agent NAME` | `-a` | Agent name. Default: `builder`. |

```
druids execution send bright-fox "try a different approach"
druids execution send bright-fox "check the logs" --agent monitor
```

## druids execution ssh

Open a shell on an execution's VM.

```
druids execution ssh <slug> [options]
```

| Flag | Short | Description |
|---|---|---|
| `--agent NAME` | `-a` | Agent name. Default: root instance. |

```
druids execution ssh bright-fox
druids execution ssh bright-fox --agent worker
```

## druids execution connect

Resume an agent's coding session interactively.

```
druids execution connect <slug> [options]
```

| Flag | Short | Description |
|---|---|---|
| `--agent NAME` | `-a` | Agent name. |

```
druids execution connect bright-fox
druids execution connect bright-fox --agent builder
```

## druids init

Initialize a repo for Druids.

```
druids init [options]
```

| Flag | Description |
|---|---|
| `--no-programs` | Skip copying starter programs. |

Copies starter programs to `.druids/`, adds the Druids MCP server to `.mcp.json`,
copies `llms.txt`, and prints a snippet to add to your agent instructions.

```
druids init
druids init --no-programs
```

## druids devbox create

Provision a new devbox sandbox.

```
druids devbox create [options]
```

| Flag | Short | Description |
|---|---|---|
| `--name NAME` | `-n` | Devbox name. Default: repo name or `"default"`. |
| `--repo OWNER/REPO` | `-r` | GitHub repo to clone into the devbox. |
| `--public` | | Make this devbox usable by other users on the same repo. |

Creates a sandbox, optionally clones a repo, and prints SSH credentials.
The sandbox stays running until `druids devbox snapshot` is called.

```
druids devbox create --repo myorg/myrepo
druids devbox create --name my-devbox
```

## druids devbox snapshot

Snapshot and stop a devbox sandbox.

```
druids devbox snapshot [options]
```

| Flag | Short | Description |
|---|---|---|
| `--name NAME` | `-n` | Devbox name. |
| `--repo OWNER/REPO` | `-r` | GitHub repo. |

```
druids devbox snapshot --name my-devbox
druids devbox snapshot --repo myorg/myrepo
```

## druids devbox ls

List all devboxes.

```
druids devbox ls
```

## druids devbox secret set

Set secrets on a devbox.

```
druids devbox secret set [NAME] [VALUE] [options]
```

| Flag | Short | Description |
|---|---|---|
| `--devbox NAME` | `-d` | Devbox name. Default: devbox for current repo. |
| `--file PATH` | `-f` | Load secrets from a `.env` file. |

Three modes:

```
# Single secret
druids devbox secret set API_KEY sk-123 --devbox mybox

# From .env file
druids devbox secret set --file .env --devbox mybox

# From stdin
echo sk-123 | druids devbox secret set API_KEY --devbox mybox
```

## druids devbox secret ls

List secrets on a devbox (names only, not values).

```
druids devbox secret ls [options]
```

| Flag | Short | Description |
|---|---|---|
| `--devbox NAME` | `-d` | Devbox name. Default: devbox for current repo. |

```
druids devbox secret ls --devbox mybox
```

## druids devbox secret rm

Delete a secret from a devbox.

```
druids devbox secret rm <name> [options]
```

| Flag | Short | Description |
|---|---|---|
| `--devbox NAME` | `-d` | Devbox name. Default: devbox for current repo. |

```
druids devbox secret rm API_KEY --devbox mybox
```

## druids auth set-key

Authenticate with an API key.

```
druids auth set-key <key>
```

The key must start with `druid_`. Get a key from the Druids dashboard
settings page. Saved to `~/.druids/config.json`.

```
druids auth set-key druid_abc123
```

## druids auth logout

Clear stored credentials.

```
druids auth logout
```

## druids auth status

Show current authentication status.

```
druids auth status
```
