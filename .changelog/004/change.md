# v004: core-types

## What Changed

Implemented comprehensive core type system for the Druids Rust translation:

- **Execution types** (`druids-core/src/execution.rs`):
  - `ExecutionRecord` with builder pattern
  - `ExecutionState` enum (Pending, Running, Succeeded, Failed, Stopped)
  - `ExecutionMetadata` for task descriptions and results
  - `ExecutionEdge` for topology graph representation
  - Builder validates required fields (`spec`) instead of using fake defaults

- **Agent types** (`druids-core/src/agent.rs`):
  - `AgentInfo` with connection details
  - `AgentState` enum (Connecting, Connected, Disconnected, Failed)
  - `AgentConnection` for WebSocket/MCP connections
  - `AgentType` enum (Builder, Monitor, Worker, Custom)

- **Common types** (`druids-core/src/common.rs`):
  - `Slug` newtype wrapper with validation
  - `UserId` UUID-based identifier
  - `ExecutionId` for execution tracking
  - `timestamp()` utility for consistent time handling

- **Event types** (`druids-core/src/events.rs`):
  - `TraceEvent` for execution trace logging
  - Comprehensive event types matching Python implementation
  - Serialization support for JSONL trace files

- **Error types** (`druids-core/src/error.rs`):
  - `ExecutionError` for execution operations
  - `AgentError` for agent lifecycle and communication
  - `ConfigError` for configuration issues (merged with existing config module)
  - Proper error messages and context preservation

- **Integration**:
  - Preserved existing `config` module from v002/v003
  - Re-exported commonly used types at crate root
  - Comprehensive documentation with examples

## Why

This establishes the foundational type system that all other components (server, client, runtime, bridge) will build upon. The types mirror the Python implementation's data model while leveraging Rust's type system for:
- Compile-time validation (no invalid state transitions)
- Zero-cost abstractions (newtype wrappers)
- Proper error handling (no silent failures)

**Critical fix**: Changed `ExecutionRecordBuilder` to fail at build time if `spec` is not provided, instead of silently defaulting to empty string. Per CLAUDE.md: "Never use fake defaults" — a missing spec is an error, not an empty string.

## New Goals

Added Core Types section to GOALS.md:
- ExecutionRecordBuilder validates required fields without fake defaults
