# Rust Translation Guide

This file provides guidance for agents working on translating Druids from Python to Rust.

## Project Overview

Druids is a multi-agent orchestration system with 4 main components (~16,000 lines Python):
- **Server**: FastAPI, execution engine, sandboxing, PostgreSQL
- **Client**: Typer CLI for managing executions
- **Runtime**: In-VM program executor
- **Bridge**: HTTP relay for ACP stdin/stdout

We are translating this entire codebase to Rust while preserving all functionality and architecture.

## Rust Stack Decisions

### Core Dependencies
```toml
# Web framework
axum = "0.7"          # HTTP server (replaces FastAPI/Starlette)
tower = "0.4"         # Middleware
tower-http = "0.5"    # HTTP middleware

# Async runtime
tokio = { version = "1", features = ["full"] }

# Database
sqlx = { version = "0.7", features = ["sqlite", "runtime-tokio", "migrate"] }

# Serialization
serde = { version = "1.0", features = ["derive"] }
serde_json = "1.0"

# HTTP client
reqwest = { version = "0.11", features = ["json", "stream"] }

# Docker
bollard = "0.16"      # Docker API client

# CLI
clap = { version = "4", features = ["derive"] }

# Configuration
config = "0.14"       # Config loading
dotenvy = "0.15"      # .env files

# Error handling
anyhow = "1.0"        # Error context
thiserror = "1.0"     # Custom errors

# Async utilities
async-trait = "0.1"
futures = "0.3"

# Crypto
jsonwebtoken = "9"    # JWT tokens
sha2 = "0.10"         # Hashing
aes-gcm = "0.10"      # Encryption
```

### Architecture Mapping

| Python | Rust |
|--------|------|
| FastAPI | Axum + Tower |
| SQLModel/SQLAlchemy | SQLx |
| Pydantic | Serde + custom validators |
| asyncio | Tokio |
| httpx | Reqwest |
| docker-py | Bollard |
| Typer | Clap |
| Starlette | Axum |

## Code Conventions

### Project Structure
```
druids-rs/
├── server/           # Axum server
│   ├── src/
│   │   ├── main.rs
│   │   ├── api/      # Route handlers
│   │   ├── db/       # Database models + migrations
│   │   ├── lib/      # Core business logic
│   │   └── utils/    # Utilities
│   └── Cargo.toml
├── client/           # CLI
│   ├── src/
│   │   ├── main.rs
│   │   ├── commands/ # CLI commands
│   │   └── client.rs # HTTP client
│   └── Cargo.toml
├── runtime/          # Program executor
│   ├── src/
│   │   ├── main.rs
│   │   └── context.rs
│   └── Cargo.toml
├── bridge/           # ACP relay
│   ├── src/
│   │   └── main.rs
│   └── Cargo.toml
└── Cargo.toml        # Workspace
```

### Naming
- **Modules**: `snake_case`
- **Types**: `PascalCase` (structs, enums, traits)
- **Functions**: `snake_case`
- **Constants**: `SCREAMING_SNAKE_CASE`
- **Trait methods**: verb-based like Python conventions
  - `get_*`, `create_*`, `ensure_*`, `start_*`, `stop_*`

### Error Handling
- Use `Result<T, E>` everywhere
- `anyhow::Result<T>` for application-level errors
- `thiserror` for domain-specific error types
- Never use `unwrap()` or `expect()` in production code paths
- Propagate errors with `?` operator

### Async
- All I/O functions are `async fn`
- Use `tokio::spawn` for background tasks
- Use `tokio::sync::{mpsc, oneshot, RwLock}` for concurrency
- Prefer `Arc<RwLock<T>>` over `Mutex` when reads dominate

### Database
- SQLx compile-time checked queries: `sqlx::query!` macro
- Migrations in `migrations/` directory
- All timestamps are UTC `chrono::DateTime<Utc>`
- Use transactions for multi-step operations

### Testing
- Unit tests in same file: `#[cfg(test)] mod tests`
- Integration tests in `tests/` directory
- Use `#[tokio::test]` for async tests
- Mock external services with traits

### Type Safety
- Use `newtype` pattern for domain IDs (e.g., `ExecutionSlug(String)`)
- Enums for state machines (e.g., `ExecutionStatus`)
- `serde(rename_all = "snake_case")` for JSON serialization
- Explicit conversions with `From`/`TryFrom` traits

### Documentation
- Public items must have doc comments (`///`)
- Module-level docs (`//!`) at top of each file
- Code examples in doc comments where helpful

### Git Workflow
- Branch naming: `rust-{component}-{feature}` (e.g., `rust-server-execution-engine`)
- Commit messages: Conventional Commits with `(rust)` scope
- One component/module per PR

## Translation Strategy

### Phase 1: Foundation
1. Workspace setup + CI/CD
2. Database models + migrations
3. Configuration + crypto utilities
4. HTTP client library (shared)

### Phase 2: Core Components
5. Server: API models + routing skeleton
6. Server: Execution engine (in-memory state)
7. Server: Machine abstraction + sandbox
8. Server: Connection layer + bridge relay
9. Client: CLI skeleton + auth
10. Client: Execution commands
11. Runtime: Context + HTTP server
12. Bridge: HTTP relay server

### Phase 3: Agent System
13. Agent base traits + implementations
14. Tool handler dispatch system
15. ACP integration (subprocess management)

### Phase 4: Integration
16. End-to-end tests
17. Docker image builds
18. Migration tooling (if needed)

## Component-Specific Notes

### Server
- Use Axum state extractor for DB pool + config
- Tower middleware for auth (JWT validation)
- SSE with `axum::response::Sse`
- Background tasks with `tokio::spawn`
- Execution registry: `Arc<RwLock<HashMap<String, Execution>>>`

### Client
- Clap derives for CLI commands
- Reqwest client with middleware for auth
- SSE streaming with `reqwest::get().bytes_stream()`
- Config at `~/.druids/config.json` using `serde_json`

### Runtime
- Starlette → Axum (same framework as server)
- Tool handlers: `HashMap<String, Box<dyn Fn(Value) -> BoxFuture<Value>>>`
- HTTP endpoints on `localhost:9100`

### Bridge
- Minimal dependencies (Axum + Reqwest + Tokio)
- Process management with `tokio::process::Command`
- Batched stdout: `Vec<String>` buffer, flush at 256 lines
- Long-polling: 20s timeout on stdin pull

## Testing Requirements

Each worker must:
1. Write unit tests for pure functions
2. Write integration tests for HTTP endpoints
3. Verify against existing Python behavior (run parallel tests)
4. Update GOALS.md with new capabilities as they're proven

## Build + Deployment

- Single workspace with 4 crates
- Each crate builds to `target/release/{binary}`
- Docker: multi-stage builds (builder + runtime)
- CI: `cargo test`, `cargo clippy`, `cargo fmt --check`

## Performance Targets

Rust implementation should:
- Start server in <1s (vs ~2s Python)
- Handle 100+ concurrent executions
- Trace writes: >10k events/sec
- Memory: <50MB base server footprint

## Migration Path

Build Rust alongside Python:
1. Rust server on different port (e.g., 9000)
2. Shared SQLite database
3. Incremental feature parity testing
4. Eventually: retire Python server

## Questions to Resolve

- ACP SDK: Rust library exists? Or spawn Python bridge?
- Program execution: Embed Python interpreter (PyO3) or keep runtime in Python?
- MCP integration: Rust MCP library available?
- GitHub auth: oauth2 crate or custom implementation?
