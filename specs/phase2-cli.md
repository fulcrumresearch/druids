# Phase 2.2: CLI Binary

**Target**: `druids-client` crate (binary portion)
**Dependencies**: Phase 2.1 (HTTP client), Phase 1 (config)

## Task: Implement Druids CLI

Create the `druids` command-line interface using clap.

### Reference Files
- `client/druids/main.py` - Python CLI
- `client/druids/commands/*.py` - CLI commands

### Deliverables

**1. Main CLI** (`crates/druids-client/src/main.rs`):
```rust
#[derive(Parser)]
#[command(name = "druids", version, about)]
struct Cli {
    #[command(subcommand)]
    command: Commands,
}

#[derive(Subcommand)]
enum Commands {
    Exec(ExecArgs),
    Execution(ExecutionArgs),
    Devbox(DevboxArgs),
    Config(ConfigArgs),
}
```

**2. Exec Command** (`crates/druids-client/src/cli/exec.rs`):
- `druids exec <program> [ARGS...]`
- Load program file
- Parse kwargs
- Call API to create execution
- Stream events and display
- Handle Ctrl+C gracefully

**3. Execution Commands** (`crates/druids-client/src/cli/execution.rs`):
- `druids execution ls [--all]` - list executions
- `druids execution status <slug>` - show status
- `druids execution activity <slug>` - show recent activity
- `druids execution stop <slug>` - stop execution
- `druids execution send <slug> <message> [-a agent]` - send message
- `druids execution ssh <slug> [-a agent]` - SSH into VM
- `druids execution connect <slug> [-a agent]` - resume session

**4. Devbox Commands** (`crates/druids-client/src/cli/devbox.rs`):
- `druids devbox create --repo <owner/repo>`
- `druids devbox snapshot --name <name>`
- `druids devbox ls`
- `druids devbox secret set -d <devbox> <key> <value>`
- `druids devbox secret ls -d <devbox>`

**5. Config Commands** (`crates/druids-client/src/cli/config.rs`):
- `druids config set <key> <value>` - set config value
- `druids config get <key>` - get config value
- `druids config list` - show all config

**6. Display Formatting** (`crates/druids-client/src/cli/display.rs`):
- Table formatting for list commands
- Progress indicators for long operations
- Colored output for status
- Use `comfy-table` or `tabled` for tables
- Use `indicatif` for progress bars

**7. Error Handling**:
- User-friendly error messages
- Exit codes (0 for success, 1 for errors)
- Suggestions for common mistakes

### Success Criteria
- All commands from Python CLI implemented
- Help text clear and useful
- Output formatting matches Python CLI
- Handles network errors gracefully
- Config persistence works
- Integration tests for all commands

### Notes
- Use `clap` derive API
- Use `tokio` runtime
- Use `anyhow` for error handling in binary
- Colorize output with `colored` or `owo-colors`
- Match Python CLI UX exactly
