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

**Phase**: Initial setup and decomposition
- Created STATE.md, setting up GOALS.md
- Analyzing codebase architecture
- Next: Create detailed work breakdown and spawn workers

## Completed Work

None yet.

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

## Trajectory

1. **Setup & Planning** (current)
   - Establish project structure
   - Define Rust architecture
   - Create work breakdown

2. **Foundation Layer**
   - Shared types and models
   - Configuration system
   - Database layer
   - HTTP client utilities

3. **Server Core**
   - API routes (Axum)
   - Execution engine
   - Sandbox management
   - Event streaming

4. **Client & Runtime**
   - CLI (clap)
   - Client library
   - Runtime SDK

5. **Integration & Testing**
   - End-to-end testing
   - Migration validation
   - Performance benchmarking

## Blockers

None currently identified.

## Notes

- Frontend (Vue 3) stays in JavaScript/TypeScript - no translation needed
- Focus on functional equivalence, not 1:1 translation
- Need to decide on async runtime (tokio is standard choice)
- Consider using workspace for multi-crate project structure
