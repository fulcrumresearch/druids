# Druids Rust Translation Progress

**Status**: Foundation Phase In Progress
**Last Updated**: 2026-04-04

## Overview

Translating the Druids multi-agent orchestration system from Python to Rust for improved performance, type safety, and reliability.

## Current Status

### Phase 1: Foundation (In Progress)

| Task | Worker | Status | Branch | Notes |
|------|--------|--------|--------|-------|
| Project Scaffold | worker-1 | ✅ Complete | factory/scaffold-1 | Cargo workspace, CI/CD, 6 crates created |
| Core Types | worker-2 | ⏳ In Progress | - | Execution, Agent, Event types |
| Database Layer | worker-3 | ⏳ In Progress | - | SQLx, migrations, models |
| Config System | worker-4 | ✅ Complete | factory/config-system-4 | ServerConfig, ClientConfig, Secret<T> wrapper |

### Completed Deliverables

**worker-1 (scaffold)**:
- ✅ Cargo workspace structure in `druids-rs/`
- ✅ 6 crates: druids-{server,client,runtime,bridge,core,db}
- ✅ GitHub Actions CI/CD (test, clippy, fmt)
- ✅ Strict clippy configuration
- ✅ All quality gates passing

**worker-4 (config-system)**:
- ✅ `Secret<T>` wrapper with redacted Debug output
- ✅ `ServerConfig` with DRUIDS_ env prefix loading
- ✅ `ClientConfig` with ~/.druids/config.json support
- ✅ Config priority resolution (env > file > defaults)
- ✅ `SandboxType` enum for Docker/MorphCloud
- ✅ No secrets in logs, secure memory handling
- ✅ Improved entropy sources for secret generation

### Pending Work

**worker-2 (core-types)** - Expected deliverables:
- Execution types (ExecutionRecord, ExecutionState, etc.)
- Agent types (AgentInfo, AgentState, AgentConnection)
- Event types (TraceEvent enum with all variants)
- Error types (thiserror-based)
- Serialization support (serde)

**worker-3 (database-layer)** - Expected deliverables:
- SQLx-based database layer
- Ported Alembic migrations
- Database models for User, Execution, Devbox, Secret, Program
- Connection pool management
- Query builders

## Next Phase Preparation

### Phase 2: Client & CLI (Specs Ready)

Three workers ready to spawn once Phase 1 completes:

1. **HTTP Client Library** (`specs/phase2-http-client.md`)
   - reqwest-based API client
   - Authentication (JWT)
   - Retry logic
   - SSE streaming

2. **CLI Binary** (`specs/phase2-cli.md`)
   - clap-based CLI
   - All druids commands (exec, execution, devbox, config)
   - Display formatting
   - User-friendly errors

### Phase 3: Runtime SDK (Spec Ready)

1. **Runtime SDK** (`specs/phase3-runtime.md`)
   - Program context API
   - Agent handle API
   - Event system
   - Runtime server

## Architecture Decisions

### Technology Stack

| Component | Python | Rust |
|-----------|--------|------|
| Web Framework | FastAPI | Axum |
| Database | SQLModel/SQLAlchemy | SQLx |
| Async Runtime | asyncio | tokio |
| CLI | Typer | clap |
| HTTP Client | httpx | reqwest |
| Serialization | Pydantic | serde |
| Error Handling | exceptions | thiserror/anyhow |

### Key Principles

1. **Functional Equivalence**: Match Python behavior exactly
2. **Type Safety**: Leverage Rust's type system
3. **Performance**: Use zero-copy where possible
4. **Async-First**: tokio throughout
5. **Compile-Time Checks**: SQLx compile-time query verification

## Project Structure

```
druids-rs/
├── Cargo.toml              # Workspace root
├── .github/workflows/      # CI/CD
│   ├── ci.yml
│   └── release.yml
└── crates/
    ├── druids-core/        # Shared types
    ├── druids-db/          # Database layer
    ├── druids-server/      # Server binary
    ├── druids-client/      # CLI + client library
    ├── druids-runtime/     # Runtime SDK
    └── druids-bridge/      # Bridge binary
```

## Coordination Files

- **STATE.md**: Detailed progress tracking
- **GOALS.md**: Behavioral capabilities checklist (feature parity)
- **CLAUDE.md**: Rust coding conventions
- **specs/**: Detailed task specifications for each phase

## Success Metrics

- [ ] All GOALS.md capabilities checked off
- [ ] Performance >= Python version
- [ ] CI passing on all PRs
- [ ] Integration tests passing
- [ ] Example programs running
- [ ] Documentation complete

## Timeline

- **Phase 1 (Foundation)**: In progress (3-4 workers active)
- **Phase 2-3 (Client/Runtime)**: Ready to spawn (specs prepared)
- **Phase 4-5 (Server/Sandbox)**: Planned
- **Phase 6-8 (Bridge/Testing/Programs)**: Planned

## Notes

- Frontend (Vue 3) remains in JavaScript - no translation needed
- Programs can initially stay in Python, focus on infrastructure first
- Using workspace for multi-crate project structure
- Tokio as the standard async runtime choice
