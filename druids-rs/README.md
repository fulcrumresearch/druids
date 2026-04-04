# Druids (Rust)

Rust implementation of the Druids multi-agent orchestration system.

## Project Structure

This is a Cargo workspace with the following crates:

- `druids-core` - Shared types and utilities
- `druids-server` - HTTP server (Axum-based)
- `druids-client` - CLI and client library
- `druids-runtime` - Program runtime SDK
- `druids-bridge` - Agent bridge component
- `druids-db` - Database layer (SQLx-based)

## Building

```bash
cargo build
```

## Testing

```bash
cargo test
```

## Configuration

### Server Configuration

The server reads configuration from environment variables with the `DRUIDS_` prefix. Copy `example.env` to `.env` and update with your values:

```bash
cp example.env .env
# Edit .env with your settings
```

Required environment variables:
- `ANTHROPIC_API_KEY` - Anthropic API key for Claude

Optional environment variables (with defaults):
- `DRUIDS_HOST` (default: `0.0.0.0`)
- `DRUIDS_PORT` (default: `8000`)
- `DRUIDS_BASE_URL` (default: `http://localhost:8000`)
- `DRUIDS_DATABASE_URL` (default: `sqlite://druids.db`)
- `DRUIDS_SANDBOX_TYPE` (default: `docker`)
- `DRUIDS_DOCKER_IMAGE` (default: `ghcr.io/fulcrumresearch/druids-base:latest`)
- `DRUIDS_DOCKER_HOST` (default: `localhost`)
- `DRUIDS_MAX_EXECUTION_TTL` (default: `86400` - 24 hours)

Auto-generated if not provided:
- `DRUIDS_SECRET_KEY` - Encryption key for secrets
- `DRUIDS_FORWARDING_TOKEN_SECRET` - Token secret for forwarding

### Client Configuration

The client reads configuration from:
1. Environment variables (`DRUIDS_BASE_URL`, `DRUIDS_ACCESS_TOKEN`)
2. `~/.druids/config.json`
3. Built-in defaults

Example `~/.druids/config.json`:
```json
{
  "base_url": "http://localhost:8000",
  "user_access_token": "your-token-here"
}
```

## Running

### Server

```bash
cargo run --bin druids-server
```

### Client

```bash
cargo run --bin druids config  # Show current configuration
```

## Development Status

This is an in-progress translation of the Python Druids implementation to Rust. Current status:

- [x] Project scaffold
- [x] Configuration system
- [ ] Core types
- [ ] Database layer
- [ ] Server API
- [ ] Client CLI
- [ ] Runtime SDK
- [ ] Bridge component

See `../GOALS.md` for the full feature parity checklist.

## License

MIT
