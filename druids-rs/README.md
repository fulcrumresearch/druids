# Druids (Rust Implementation)

This is the Rust implementation of the Druids multi-agent orchestration system, translated from the Python codebase for improved performance and type safety.

## Project Structure

This is a Cargo workspace with the following crates:

- **`druids-core`** - Shared types and utilities used across all components
- **`druids-server`** - Server binary and API implementation
- **`druids-client`** - CLI binary and client library
- **`druids-runtime`** - Program runtime SDK (planned)
- **`druids-bridge`** - Agent bridge component (planned)
- **`druids-db`** - Database layer (planned)

## Configuration

### Server Configuration

The server is configured via environment variables with the `DRUIDS_` prefix. You can also use a `.env` file.

1. Copy the example environment file:
   ```bash
   cp .env.example .env
   ```

2. Edit `.env` and set your configuration:
   - `ANTHROPIC_API_KEY` - **Required**: Your Anthropic API key
   - `DRUIDS_DATABASE_URL` - Database connection URL (default: SQLite)
   - `DRUIDS_SANDBOX_TYPE` - Sandbox backend: `docker` or `morphcloud`
   - See `.env.example` for all available options

3. Run the server:
   ```bash
   cargo run --bin druids-server
   ```

#### Configuration Priority

Environment variables take precedence over `.env` file values.

### Client Configuration

The CLI is configured via:

1. Environment variables (`DRUIDS_BASE_URL`, `DRUIDS_ACCESS_TOKEN`)
2. Config file at `~/.druids/config.json`
3. Built-in defaults

Example `~/.druids/config.json`:
```json
{
  "base_url": "http://localhost:8000",
  "user_access_token": "your-token-here"
}
```

## Building

Build all crates:
```bash
cargo build
```

Build in release mode:
```bash
cargo build --release
```

## Testing

Run tests for all crates:
```bash
cargo test
```

Run tests for a specific crate:
```bash
cargo test -p druids-server
```

## Development

### Code Style

This project uses:
- `rustfmt` for formatting
- `clippy` for linting

Format code:
```bash
cargo fmt
```

Run linter:
```bash
cargo clippy -- -D warnings
```

### Secrets Management

**Important**: Never log or display secrets in debug output. The configuration system uses the `secrecy` crate to protect sensitive values.

All secret fields use `SecretString` and are redacted when displayed.

## License

MIT
