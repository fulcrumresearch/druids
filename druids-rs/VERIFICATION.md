# Configuration System Verification

This document describes how to verify that the configuration system works correctly.

## Prerequisites

To build and test the Rust code, you need:
- Rust 1.75 or later
- Cargo (comes with Rust)

Install Rust: https://rustup.rs/

## Manual Testing

### 1. Verify Project Structure

Check that all files are in place:

```bash
cd druids-rs
tree -L 3
```

Expected structure:
```
druids-rs/
├── Cargo.toml              # workspace manifest
├── .env.example            # server config example
├── config.json.example     # client config example
├── README.md               # documentation
├── VERIFICATION.md         # this file
└── crates/
    ├── druids-core/        # shared types
    │   ├── Cargo.toml
    │   └── src/
    ├── druids-server/      # server binary
    │   ├── Cargo.toml
    │   └── src/
    └── druids-client/      # CLI binary
        ├── Cargo.toml
        └── src/
```

### 2. Build All Crates

```bash
cargo build
```

Expected: Clean build with no errors. Warnings are okay at this stage.

### 3. Run Tests

```bash
cargo test
```

Expected: All tests pass.

Specific test suites:

**Core types:**
```bash
cargo test -p druids-core
```

**Server config:**
```bash
cargo test -p druids-server
```

**Client config:**
```bash
cargo test -p druids-client
```

### 4. Test Server Configuration Loading

Create a test `.env` file:

```bash
cat > .env << 'EOF'
DRUIDS_HOST=127.0.0.1
DRUIDS_PORT=9000
DRUIDS_BASE_URL=http://localhost:9000
ANTHROPIC_API_KEY=sk-ant-api03-test123456789012345678901234567890123456789012
DRUIDS_SANDBOX_TYPE=docker
EOF
```

Run the server binary (it won't fully start without database, but should load config):

```bash
cargo run --bin druids-server
```

Expected: No panic, config validation errors are okay (we're just testing config loading).

### 5. Test Client Configuration Loading

Create a test config file:

```bash
mkdir -p ~/.druids
cat > ~/.druids/config.json << 'EOF'
{
  "base_url": "http://test.example.com:8000",
  "user_access_token": "test-token-123"
}
EOF
```

Run the client binary:

```bash
cargo run --bin druids
```

Expected: No panic, should print "Druids CLI (Rust implementation)".

Test environment variable override:

```bash
DRUIDS_BASE_URL=http://localhost:9000 cargo run --bin druids
```

Expected: Environment variable should override config file.

Clean up:

```bash
rm ~/.druids/config.json
```

### 6. Test Configuration Validation

Test invalid Anthropic API key:

```bash
ANTHROPIC_API_KEY=invalid-key cargo run --bin druids-server
```

Expected: Should show validation error about API key format.

### 7. Verify Secrets Redaction

Check that secrets are never logged:

```bash
ANTHROPIC_API_KEY=sk-ant-api03-test123456789012345678901234567890123456789012 \
cargo run --bin druids-server 2>&1 | grep -i "sk-ant"
```

Expected: No output (secrets should be redacted in display).

## Automated Testing

All configuration functionality is covered by unit tests:

**Core types:**
- `druids-core/src/config.rs` - SandboxType enum, ConfigError

**Server config:**
- `druids-server/src/config/tests.rs` - Loading, validation, masking

**Client config:**
- `druids-client/src/config.rs` (inline tests) - Loading, saving, priority

## Success Criteria

The configuration system is working if:

✅ All crates build without errors
✅ All tests pass
✅ Server config loads from environment variables
✅ Client config loads from file and environment
✅ Environment variables override config file values
✅ Invalid configs produce clear error messages
✅ Secrets are never logged or displayed
✅ Config files are created with correct permissions (600 on Unix)

## Known Limitations

At this stage:
- Database migrations are not implemented (server won't fully start)
- Server API is not implemented (server binary is a stub)
- CLI commands are not implemented (CLI binary is a stub)

These are expected - this task only covers configuration management.
