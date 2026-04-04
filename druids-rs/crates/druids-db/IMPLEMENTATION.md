# Database Layer Implementation

This document summarizes the implementation of the `druids-db` crate.

## Completed Work

### 1. Workspace Structure
- Created Cargo workspace at `druids-rs/` with 6 crates
- Set up workspace-level dependency management
- Created placeholder crates for future implementation

### 2. Core Types (`druids-core`)
- Shared error types
- Foundation for cross-crate types

### 3. Database Layer (`druids-db`)

#### Models
All models replicate the Python SQLModel classes with full feature parity:

- **User** (`user.rs`): GitHub-authenticated users
  - `get_or_create_user()` - upserts user by github_id
  - `get_user()` - retrieves by UUID

- **ExecutionRecord** (`execution.rs`): Program execution tracking
  - `create_execution()` - creates with auto-generated slug
  - `get_execution()` / `get_execution_by_slug()` - retrieval
  - `get_user_executions()` - list with filtering
  - `update_execution()` - updates mutable fields
  - `increment_usage()` - atomic token counter updates

- **Devbox** (`devbox.rs`): Environment snapshots
  - `get_devbox()` / `get_devbox_by_name()` / `get_devbox_by_repo()` - various lookups
  - `get_user_devboxes()` - list all for user
  - `resolve_devbox()` - smart resolution by name or repo
  - `get_or_create_devbox()` - ensures existence

- **Secret** (`secret.rs`): Encrypted environment variables
  - `get_secrets()` / `get_secret_by_name()` - retrieval
  - `set_secret()` - create or update with encryption
  - `delete_secret()` - removal
  - `get_decrypted_secrets()` - bulk decryption for provisioning

- **Program** (`program.rs`): Deduplicated program source
  - `hash_source()` - SHA-256 content hashing
  - `get_or_create_program()` - deduplication by hash
  - `get_program()` / `get_user_programs()` - retrieval

#### Infrastructure

- **Connection Pool** (`pool.rs`): PgPool management with tuned timeouts
- **Encryption** (`crypto.rs`): AES-256-GCM for secrets
  - Fernet-compatible encryption
  - Random nonce generation
  - Base64 encoding
- **Migrations** (`migrations/`): 3 SQL migrations ported from Alembic
- **Error Handling** (`error.rs`): Typed DatabaseError enum
- **Tests** (`tests/integration_test.rs`): Comprehensive lifecycle tests

## Key Design Decisions

### 1. Raw SQL Queries
Used `sqlx::query_as!()` throughout instead of an ORM. This provides:
- Compile-time query verification
- No runtime overhead
- Direct control over SQL
- Clear migration from Python SQLAlchemy

### 2. Encryption
Implemented AES-256-GCM encryption for secrets, matching Python's Fernet:
- 256-bit keys (32 bytes)
- Random 96-bit nonces (12 bytes)
- Base64 encoding for storage
- Authenticated encryption

### 3. Slug Generation
Ported Python's adjective-noun-number pattern:
- Retries 10 times for uniqueness
- Adds random hex suffix on collision
- Generates slugs like "happy-panda-42"

### 4. Query Patterns
All queries follow Rust conventions:
- `get_*` returns `Option<T>` (None if not found)
- `create_*` performs I/O and returns created record
- `ensure_*` / `get_or_create_*` upsert semantics
- Transactions via `pool.begin().await?`

## Testing

Integration tests cover all models and operations:
- User creation and retrieval
- Execution lifecycle (create, update, token tracking)
- Devbox management
- Secret encryption/decryption
- Program deduplication

Run with:
```bash
export DATABASE_URL="postgresql://localhost/druids_test"
cargo test --package druids-db -- --ignored
```

## Migration from Python

The Rust implementation maintains feature parity with Python:
- All database models implemented
- All query functions ported
- Same schema (via ported migrations)
- Compatible encryption (AES-256-GCM)
- Identical slug generation

## Next Steps

This completes Phase 1.3 (Database Layer) from STATE.md. Next phases:
- Phase 2.1: HTTP Client Library (druids-client)
- Phase 2.2: CLI Binary (druids-client)
- Phase 4.1: API Layer (druids-server)

## Dependencies

```toml
sqlx = { version = "0.8", features = ["runtime-tokio", "postgres", "uuid", "chrono", "json"] }
tokio = { version = "1.41", features = ["full"] }
serde = { version = "1.0", features = ["derive"] }
aes-gcm = { version = "0.10", features = ["std"] }
sha2 = "0.10"
rand = "0.8"
```

## File Structure

```
druids-db/
├── Cargo.toml
├── README.md
├── IMPLEMENTATION.md (this file)
├── .env.example
├── migrations/
│   ├── 20260309000000_initial_schema.sql
│   ├── 20260316000000_add_program_table.sql
│   └── 20260325000000_add_devbox_resources.sql
├── src/
│   ├── lib.rs
│   ├── error.rs
│   ├── pool.rs
│   ├── crypto.rs
│   └── models/
│       ├── mod.rs
│       ├── user.rs
│       ├── execution.rs
│       ├── devbox.rs
│       ├── secret.rs
│       └── program.rs
└── tests/
    └── integration_test.rs
```

## Verification

All models and query functions have been implemented with:
- Correct types matching Python models
- Proper async/await patterns
- Error handling via Result<T, DatabaseError>
- Documentation comments
- Integration tests

The implementation is ready for use by druids-server and other components.
