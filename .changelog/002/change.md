# v002: rust-configuration

## What Changed

Fixed all reviewer and checker feedback in the configuration system:

### Reviewer Issues Resolved

1. **Duplicate SecretString types**: Removed the custom `Secret<T>` type from `secrets.rs` and now use the `secrecy` crate consistently throughout. Deleted the entire secrets module as it was dead code.

2. **Duplicate generate_random_secret**: Removed the weaker non-cryptographic version from `druids-core/src/config/loader.rs`. The server now only has the cryptographically secure version using `rand::random()`.

### Checker Issues Resolved

1. **Clippy warning on SandboxType**: Replaced manual `impl Default` with `#[derive(Default)]` and added `#[default]` attribute to the `Docker` variant as clippy recommended.

2. **Test suite failures from env var conflicts**: Added `serial_test` crate dependency and marked all tests that modify environment variables with `#[serial]` attribute to ensure they run sequentially and don't interfere with each other.

### Additional Fixes

- Added `From<serde_json::Error> for ConfigError` conversion to support client config code
- Added `#[allow(dead_code)]` annotations to API methods/fields that aren't used yet but are part of the public interface
- Fixed clippy `field_reassign_with_default` warning in client test by using struct initialization syntax

## Implementation Details

- **ServerConfig** (`druids-server/src/config.rs`):
  - Loads from environment variables with `DRUIDS_` prefix
  - Auto-generates `secret_key` and `forwarding_token_secret` when not provided using `rand::random()`
  - Validates API key format (`sk-ant-` prefix) and Fernet key length (44 chars)
  - Supports Docker and MorphCloud sandbox types via `SandboxType` enum
  - `anthropic_api_key` properly typed as `Option<SecretString>`

- **ClientConfig** (`druids-client/src/config.rs`):
  - Multi-source configuration priority: env vars > `~/.druids/config.json` > defaults
  - Config file operations with proper file permissions (600)
  - `base_url` as `Option<Url>` to honor explicitly configured values

- **Core utilities** (`druids-core`):
  - `SandboxType` enum with `#[derive(Default)]` and serde serialization ("docker" / "morphcloud")
  - `ConfigError` type with proper error conversions including `From<dotenvy::Error>`
  - Uses `secrecy::SecretString` consistently (no custom Secret<T> wrapper)

## Why

The configuration system is foundational infrastructure needed before implementing the server, client, or runtime. Fixed all quality gate issues to ensure:
- Clean clippy builds with `-D warnings`
- No test race conditions from parallel env var modifications
- Cryptographically secure secret generation
- Consistent secret handling via the `secrecy` crate

## Verification

```bash
cargo clippy --workspace --all-targets --all-features -- -D warnings
✓ All clippy checks pass

cargo test --package druids-server
✓ 7 tests pass (with #[serial] ensuring no races)

cargo test --package druids-client
✓ All tests pass
```

## New Goals

- [x] `cargo clippy -p druids-core -p druids-server --all-targets --all-features -- -D warnings` passes with no warnings after SandboxType Default derive fix
- [x] Config tests that mutate environment variables are marked `#[serial]` to prevent parallel-test races
- [x] `generate_random_secret` in druids-server uses `rand::random()` (cryptographically secure), not the RandomState fallback
- [x] `secrets.rs` custom Secret<T> type removed; `secrecy::SecretString` used consistently throughout druids-core and druids-server
