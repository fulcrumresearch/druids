# Translation State

## Overview
Translating Druids (Python multi-agent orchestration system) to Rust end-to-end.

**Scope**: ~21,600 lines of Python across 141 files
- `server/` - FastAPI server, execution engine, sandbox management
- `client/` - CLI and Python client library
- `runtime/` - program runtime SDK
- `bridge/` - agent bridge component
- `frontend/` - Vue 3 dashboard (not translating)
- `.druids/` - example programs

## Active Work

**Phase**: Phase 1 - Foundation
**Active Workers**: 3

- **worker-2 (core-types)**: Implementing shared types in `druids-core` (execution, agent, event models)
- **worker-3 (database-layer)**: Building SQLx database layer in `druids-db` with migrations
- **worker-4 (config-system)**: Configuration management for server and client

Waiting for remaining workers to complete Phase 1 tasks before moving to Phase 2.

## Completed Work

### Phase 1.1 - Project Scaffold ✅
- **worker-1**: Created Cargo workspace with 6 crates (druids-server, druids-client, druids-runtime, druids-bridge, druids-core, druids-db)
- Set up CI/CD workflows (GitHub Actions for tests, clippy, fmt)
- Configured strict clippy linting
- All build quality gates passing
- Merged via PR from branch `factory/scaffold-1`

## Architecture Analysis

### Components
1. **Server** (~60% of codebase)
   - FastAPI REST API
   - Execution engine and orchestration
   - Database (SQLModel/SQLAlchemy)
   - MorphCloud/Docker sandbox management
   - WebSocket/SSE event streaming

2. **Client** (~15% of codebase)
   - CLI built with Typer
   - HTTP client for server API
   - Configuration management

3. **Runtime** (~10% of codebase)
   - Program context and SDK
   - Agent spawning and communication
   - Event handling

4. **Bridge** (~5% of codebase)
   - Agent-to-program communication bridge
   - SSE event streaming

5. **Programs** (~10% of codebase)
   - Example orchestration programs
   - Agent coordination patterns

### Key Dependencies to Replace
- **FastAPI** → Axum or Actix-Web
- **SQLModel/SQLAlchemy** → SQLx or Diesel
- **Pydantic** → serde
- **Typer** → clap
- **asyncio** → tokio
- **httpx** → reqwest
- **SSE** → tokio-sse or axum-sse

## Work Breakdown

### Phase 1: Foundation (Priority 1)

**1.1 Project Scaffold**
- Create Cargo workspace structure
- Set up crate dependencies
- Configure CI/CD (GitHub Actions)
- Set up linting (clippy) and formatting (rustfmt)

**1.2 Core Types (`druids-core`)**
- Execution models (ExecutionState, ExecutionRecord, etc.)
- Agent models (AgentInfo, AgentState, etc.)
- Event types (all trace event types)
- Configuration types
- Error types (anyhow/thiserror setup)
- Serialization traits

**1.3 Database Layer (`druids-db`)**
- SQLx setup with Postgres
- Migration system (port Alembic migrations)
- Database models (User, Execution, Devbox, Secret, Program)
- Connection pool management
- Query builders for common operations

**1.4 Configuration System**
- Environment variable parsing
- Config file loading (~/.druids/config.json)
- Settings validation
- Secrets management

### Phase 2: Client & CLI (Priority 1)

**2.1 HTTP Client Library (`druids-client`)**
- API client (reqwest-based)
- Authentication (JWT token handling)
- Request/response types
- Error handling
- Retry logic

**2.2 CLI Binary (`druids-client`)**
- clap setup with subcommands
- `druids exec` - execute programs
- `druids execution ls/status/activity/stop/send/ssh/connect`
- `druids devbox create/snapshot/ls/secret`
- Display formatting (terminal output)
- Config management commands

**2.3 Client Utilities**
- Git operations
- File I/O utilities
- Display/formatting helpers

### Phase 3: Runtime SDK (Priority 2)

**3.1 Program Context (`druids-runtime`)**
- Context API (`ctx.agent()`, `ctx.done()`, etc.)
- Agent handle API
- Event registration (`agent.on()`)
- Message passing (`agent.send()`)

**3.2 Runtime Server**
- Starlette-equivalent HTTP server
- Program execution loop
- Event dispatching
- Connection management to main server

**3.3 Runtime Types**
- Program interface traits
- Event handler types
- Runtime state management

### Phase 4: Server Core (Priority 2)

**4.1 API Layer (`druids-server`)**
- Axum setup with routing
- Middleware (auth, logging, CORS)
- API endpoints (port from Python):
  - `/api/executions` (CRUD)
  - `/api/executions/{slug}/events` (SSE)
  - `/api/executions/{slug}/messages` (POST)
  - `/api/bridge/{bridge_id}/push|pull`
  - `/api/devboxes` (CRUD)
  - `/api/secrets` (CRUD)
  - `/api/programs` (CRUD)
  - `/api/me` (user info)
  - MCP endpoints
- Request validation
- Error responses
- Frontend static file serving

**4.2 Execution Engine**
- Execution lifecycle management
- Agent topology tracking
- State machine (pending → running → stopped)
- Cleanup on termination
- Program loading and validation

**4.3 Connection Management**
- Agent connection tracking
- Bridge relay (push/pull endpoints)
- Message routing (server ↔ bridge ↔ agent)
- Connection state synchronization

**4.4 Event System**
- Trace logging (JSONL files)
- SSE streaming to clients
- Event filtering and routing
- Custom event handling

### Phase 5: Sandbox Management (Priority 2)

**5.1 Sandbox Abstraction (`druids-server`)**
- Trait definition for `Sandbox`
- Common operations (create, destroy, exec, transfer_file)
- Error handling

**5.2 Docker Backend**
- Docker API client (bollard)
- Container lifecycle
- Volume management
- Network setup
- File transfer (tar streams)

**5.3 MorphCloud Backend**
- MorphCloud API client
- VM provisioning
- Snapshot management
- SSH bastion access
- File transfer via SCP

**5.4 Setup Sessions**
- Repository cloning
- Dependency installation (uv, npm, etc.)
- Environment configuration
- Client wheel installation
- Bridge deployment

### Phase 6: Bridge (Priority 3)

**6.1 Bridge Binary (`druids-bridge`)**
- Subprocess spawning (ACP agent)
- Stdio capture and relay
- Reverse connection to server
- Heartbeat/keepalive
- Graceful shutdown

**6.2 Bridge Protocol**
- Push/pull loop implementation
- Message framing
- Event serialization
- Error propagation

### Phase 7: Integration & Testing (Priority 3)

**7.1 Unit Tests**
- Test all public APIs
- Mock database for tests
- Mock HTTP clients
- Property-based tests where applicable

**7.2 Integration Tests**
- End-to-end execution tests
- Docker sandbox tests
- CLI integration tests
- Database migration tests

**7.3 Migration Validation**
- Feature parity checklist (use GOALS.md)
- Performance benchmarks
- Load testing
- Security audit

**7.4 Documentation**
- API documentation (cargo doc)
- Usage examples
- Migration guide from Python
- Architecture documentation

### Phase 8: Programs (Priority 4)

**8.1 Port Example Programs**
- Port `.druids/build.py` to Rust
- Port `.druids/review.py` to Rust
- Port `.druids/basher.py` to Rust
- Port `.druids/main.py` to Rust
- Create Rust program examples

**Note**: Programs can stay in Python initially, focus on infrastructure first.

## Trajectory

**Current**: Phase 1 - Foundation setup
**Next**: Spawn workers for Phase 1 tasks
**Then**: Phase 2 (Client/CLI) in parallel with Phase 3 (Runtime)
**Then**: Phase 4 (Server) depends on 1-3
**Then**: Phase 5 (Sandbox) can be done in parallel with Phase 4
**Finally**: Phase 6-8 (Bridge, Testing, Programs)

## Blockers

None currently identified.

## Notes

- Frontend (Vue 3) stays in JavaScript/TypeScript - no translation needed
- Focus on functional equivalence, not 1:1 translation
- Need to decide on async runtime (tokio is standard choice)
- Consider using workspace for multi-crate project structure
