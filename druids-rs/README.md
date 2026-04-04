# Druids Rust Implementation

This is the Rust implementation of Druids, a multi-agent orchestration system.

## Structure

This workspace contains the following crates:

- **druids-core**: Shared types and utilities used across all components
- **druids-db**: Database layer with SQLx integration and migrations
- **druids-server**: HTTP server binary providing the Druids API
- **druids-client**: CLI binary and client library for interacting with the server
- **druids-runtime**: Runtime SDK for Druids programs
- **druids-bridge**: Bridge binary connecting agent sandboxes to the server

## Requirements

- Rust 1.75 or later
- PostgreSQL (for database features)

## Building

Build the entire workspace:

```bash
cargo build --workspace
```

Build in release mode:

```bash
cargo build --workspace --release
```

Build a specific crate:

```bash
cargo build -p druids-server
```

## Testing

Run all tests:

```bash
cargo test --workspace
```

Run tests for a specific crate:

```bash
cargo test -p druids-core
```

## Code Quality

Format code:

```bash
cargo fmt --all
```

Check formatting without modifying files:

```bash
cargo fmt --all --check
```

Run clippy lints:

```bash
cargo clippy --workspace --all-targets --all-features
```

Fix clippy warnings automatically where possible:

```bash
cargo clippy --workspace --all-targets --all-features --fix
```

## Running

Start the server:

```bash
cargo run -p druids-server
```

Run the CLI:

```bash
cargo run -p druids-client -- --help
```

Start the bridge:

```bash
cargo run -p druids-bridge
```

## Development

The workspace uses shared dependencies defined in the root `Cargo.toml`. When adding new dependencies:

1. Add them to `[workspace.dependencies]` in the root `Cargo.toml`
2. Reference them in individual crate `Cargo.toml` files using `{ workspace = true }`

Example:

```toml
# In root Cargo.toml
[workspace.dependencies]
tokio = { version = "1.41", features = ["full"] }

# In crate Cargo.toml
[dependencies]
tokio = { workspace = true }
```

## CI/CD

The project uses GitHub Actions for continuous integration:

- **CI workflow**: Runs on every push and pull request
  - Tests all crates
  - Runs clippy with strict lints
  - Checks code formatting
  - Builds in debug and release modes

- **Release workflow**: Runs on version tags
  - Builds release binaries for Linux and macOS
  - Creates GitHub releases with compiled artifacts

## License

MIT
