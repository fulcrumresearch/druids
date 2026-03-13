# CLI Reference

The `druids` CLI runs programs on remote sandboxes and manages devbox
environments, secrets, and authentication.

```
druids <command> [options]
```

## druids exec

Run a program on a remote sandbox.

```
druids exec <program_file> [options] [key=value ...]
```

| Flag | Short | Description |
|---|---|---|
| `--devbox NAME` | `-d` | Devbox name. Default: devbox for current repo. |
| `--branch BRANCH` | `-b` | Git branch to checkout. |
| `--no-stream` | | Don't stream events after starting. |

Extra arguments are passed as `key=value` pairs to the program function.

```
druids exec program.py spec="build the login page"
druids exec program.py --devbox mybox --branch feat/new-ui spec="add dark mode"
```

## druids status

Check status of an execution.

```
druids status <slug> [options]
```

| Flag | Short | Description |
|---|---|---|
| `--activity` | `-a` | Show recent activity events. |

```
druids status my-execution-slug
druids status my-execution-slug --activity
```

## druids stop

Stop a running execution.

```
druids stop <slug>
```

```
druids stop my-execution-slug
```

## druids ls

List executions.

```
druids ls [options]
```

| Flag | Short | Description |
|---|---|---|
| `--all` | `-a` | Include stopped executions. |

```
druids ls
druids ls --all
```

## druids connect

SSH into an execution's VM as the agent user.

```
druids connect <execution_slug> [options]
```

| Flag | Short | Description |
|---|---|---|
| `--agent NAME` | `-a` | Agent name. Default: root instance. |

```
druids connect my-execution-slug
druids connect my-execution-slug --agent worker
```

## druids apply

Apply diff from an execution's VM to the local repo.

```
druids apply <execution_slug> [options]
```

| Flag | Short | Description |
|---|---|---|
| `--force` | `-f` | Overwrite existing files (uses `git apply --reject`). |

```
druids apply my-execution-slug
druids apply my-execution-slug --force
```

## druids mcp-config

Print MCP server configuration for Claude Code or Claude Desktop.

```
druids mcp-config
```

Outputs a JSON block suitable for `.mcp.json` or `claude_desktop_config.json`.
Requires authentication.

## druids setup start

Start devbox setup by provisioning a sandbox.

```
druids setup start [options]
```

| Flag | Short | Description |
|---|---|---|
| `--name NAME` | `-n` | Devbox name. Default: repo name or `"default"`. |
| `--repo OWNER/REPO` | `-r` | GitHub repo to clone into the devbox. |

Creates a sandbox, optionally clones a repo, and prints SSH credentials.
The sandbox stays running until `druids setup finish` is called.

```
druids setup start --repo myorg/myrepo
druids setup start --name my-devbox
```

## druids setup finish

Finish devbox setup by snapshotting and stopping the sandbox.

```
druids setup finish [options]
```

| Flag | Short | Description |
|---|---|---|
| `--name NAME` | `-n` | Devbox name. |
| `--repo OWNER/REPO` | `-r` | GitHub repo. |

```
druids setup finish --name my-devbox
druids setup finish --repo myorg/myrepo
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

## druids secret set

Set secrets on a devbox.

```
druids secret set [NAME] [VALUE] [options]
```

| Flag | Short | Description |
|---|---|---|
| `--devbox NAME` | `-d` | Devbox name. Default: devbox for current repo. |
| `--file PATH` | `-f` | Load secrets from a `.env` file. |

Three modes:

```
# Single secret
druids secret set API_KEY sk-123 --devbox mybox

# From .env file
druids secret set --file .env --devbox mybox

# From stdin
echo sk-123 | druids secret set API_KEY --devbox mybox
```

## druids secret ls

List secrets on a devbox (names only, not values).

```
druids secret ls [options]
```

| Flag | Short | Description |
|---|---|---|
| `--devbox NAME` | `-d` | Devbox name. Default: devbox for current repo. |

```
druids secret ls --devbox mybox
```

## druids secret rm

Delete a secret from a devbox.

```
druids secret rm <name> [options]
```

| Flag | Short | Description |
|---|---|---|
| `--devbox NAME` | `-d` | Devbox name. Default: devbox for current repo. |

```
druids secret rm API_KEY --devbox mybox
```

## druids skill install

Install Druids skills into a target codebase for Claude Code.

```
druids skill install [options]
```

| Flag | Short | Description |
|---|---|---|
| `--global` | `-g` | Install globally to `~/.claude` (applies to all repos). |
| `--target-dir PATH` | `-t` | Override install directory. |

Creates `.claude/skills/` entries for `druids-driver` and `write-spec`.

```
druids skill install
druids skill install --global
```

## druids tools

List tools available to this agent. Run from inside an agent VM.

```
druids tools
```

## druids tool

Call a registered tool. Run from inside an agent VM.

```
druids tool <tool_name> [key=value ...]
```

```
druids tool submit diff="..." summary="..."
druids tool message receiver="reviewer" message="please review"
druids tool list_agents
druids tool expose service_name="web" port=3000
```
