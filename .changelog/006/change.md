# v006: http-client

## What Changed

Implemented the HTTP client for interacting with the Druids server API:

- **Client implementation** (`druids-client/src/client.rs`):
  - `DruidsClient` for making authenticated API requests
  - Full API coverage: executions, devboxes, messages, SSH, setup, secrets, tools
  - Methods: `create_execution()`, `get_execution()`, `list_executions()`, `stop_execution()`, `send_message()`, etc.
  - Automatic retry logic with exponential backoff
  - SSE streaming support for real-time events
  - Proper URL construction without panics (returns `Result`)

- **Type definitions** (`druids-client/src/types.rs`):
  - Request types: `CreateExecutionRequest`, `UpdateExecutionRequest`, `ChatMessageRequest`, etc.
  - Response types: `Execution`, `ExecutionSummary`, `CreateExecutionResponse`, etc.
  - 22 types covering all API operations
  - Serde serialization/deserialization

- **Retry logic** (`druids-client/src/retry.rs`):
  - Configurable retry policy with exponential backoff
  - Jitter to avoid thundering herd
  - Custom retry predicates for different error types
  - `RetryExhausted` error properly wired into error system

- **SSE streaming** (`druids-client/src/streaming.rs`):
  - Stream execution events in real-time
  - Fixed moved-value issues with proper borrowing
  - Event parsing from SSE format

- **Error handling** (`druids-client/src/error.rs`):
  - `ClientError` enum with variants for all failure modes
  - HTTP errors, serialization errors, retry exhaustion
  - Proper error context and messages

- **Tests** (`druids-client/src/tests.rs`):
  - Unit tests for client methods
  - Mock server responses
  - Test coverage for success and error cases

- **Code quality fixes**:
  - Removed all wildcard imports (`use crate::types::*`)
  - Explicit imports in client.rs (22 types), tests.rs, and lib.rs
  - Complies with `clippy.toml`'s `warn-on-all-wildcard-imports = true`
  - No panics in URL joining (returns `Result`)

## Why

The HTTP client is essential infrastructure for the CLI and other components to interact with the Druids server. This implementation:

1. Provides a type-safe Rust API matching the Python client
2. Handles retries automatically for transient failures
3. Supports SSE streaming for real-time event consumption
4. Follows CLAUDE.md conventions (explicit imports, no panics)

**Critical fix from review**: Removed wildcard imports in client.rs and tests.rs. Every type is now explicitly imported, making dependencies clear and avoiding namespace pollution.

## New Goals

Added HTTP Client section to GOALS.md:
- druids-client has no wildcard imports (explicit named imports in all modules)
