# v002: config-system

## What Changed

Implemented the configuration system for both server and client components:

- **Secret management**:
  - `Secret<T>` wrapper type that redacts sensitive values in Debug/Display output
  - Proper serialization support for config files
  - No fake defaults - uses `Option<SecretString>` for optional secrets

- **ServerConfig** (`druids-server/src/config.rs`):
  - Loads from environment variables with `DRUIDS_` prefix
  - Auto-generates `secret_key` and `forwarding_token_secret` when not provided
  - Validates API key format (`sk-ant-` prefix) and Fernet key length (44 chars)
  - Supports Docker and MorphCloud sandbox types
  - Database URL password masking in display output
  - `anthropic_api_key` properly typed as `Option<SecretString>` (not empty string)

- **ClientConfig** (`druids-client/src/config.rs`):
  - Multi-source configuration priority: env vars > `~/.druids/config.json` > defaults
  - Config file operations with proper file permissions (600)
  - `is_local_server()` helper for detecting localhost connections

- **Core utilities** (`druids-core`):
  - `SandboxType` enum with serialization ("docker" / "morphcloud")
  - `ConfigError` type with proper error conversions
  - Config loader utilities shared across components
  - `generate_random_secret()` using multiple entropy sources (documented as non-cryptographic fallback)

- **Documentation**:
  - `example.env` template with all configuration options
  - Inline documentation for all config fields
  - Security warnings for secret generation

## Why

The configuration system is foundational infrastructure needed before implementing the server, client, or runtime. This establishes the patterns for:
- Environment-based configuration (12-factor app)
- Secure secret handling
- Multi-environment support (dev/prod)

Fixed two critical issues from initial review:
1. **No fake defaults**: Changed `anthropic_api_key` to `Option<SecretString>` instead of using empty string as surrogate
2. **Better entropy**: Replaced weak LCG with `RandomState` using multiple sources (time, PID, thread ID, stack address)

## New Goals

Added Configuration System capabilities to GOALS.md:
- Rust workspace compiles cleanly with 6 crates
- Secret<T> wrapper redacts sensitive values correctly
- ServerConfig.anthropic_api_key uses proper Option type with validation
- ServerConfig loads from env vars and auto-generates secrets
- ClientConfig priority order and file permissions
- SandboxType enum serialization
- generate_random_secret entropy sources documented
