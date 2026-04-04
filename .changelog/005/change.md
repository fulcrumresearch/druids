# v005: database-layer

## What Changed

Implemented the database layer for the Rust translation with SQLx and PostgreSQL:

- **Database operations** (`druids-db/src/lib.rs`):
  - `create_execution()` - insert new execution records
  - `get_execution()` - retrieve execution by slug and user
  - `update_execution()` - update execution state and results
  - `list_executions()` - query executions with filters
  - `create_devbox()` - provision devbox sandboxes
  - `get_devbox()` - retrieve devbox by name
  - `update_devbox()` - update devbox state
  - Encryption/decryption for sensitive fields using AES-256-GCM

- **Schema** (`druids-db/migrations/`):
  - `executions` table with proper indexing
  - `devboxes` table for sandbox management
  - UUID primary keys and foreign key constraints
  - Timestamps with timezone support

- **Encryption**:
  - AES-256-GCM encryption for sensitive data
  - Documented as NOT compatible with Python Fernet (which uses AES-128-CBC+HMAC-SHA256)
  - Separate encryption format for the Rust implementation

- **Integration tests**:
  - Tests against real PostgreSQL database
  - Marked with `#[ignore]` attribute (run with `cargo test -- --ignored`)
  - Coverage for all CRUD operations
  - Test data cleanup after each test

- **Fixed issues**:
  1. Removed fake defaults for `Devbox` fields (using proper `Option` types)
  2. Fixed `update_execution()` parameter count to match function signature
  3. Fixed test compilation error comparing `Option<String>` with `String`

## Why

The database layer is critical infrastructure for persisting execution state, agent topology, and devbox lifecycle. This implementation:

1. Uses SQLx for compile-time SQL verification
2. Supports async/await for non-blocking database operations
3. Implements proper encryption for sensitive data
4. Follows CLAUDE.md conventions (no fake defaults, proper Option types)

The encryption format is intentionally different from Python to avoid confusion — the Rust and Python systems use different cryptographic algorithms and are not interoperable.

## New Goals

Added Database Layer section to GOALS.md:
- druids-db integration tests compile and pass against PostgreSQL
- druids-db encryption documented as AES-256-GCM (not Fernet-compatible)
