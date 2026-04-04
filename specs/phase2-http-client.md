# Phase 2.1: HTTP Client Library

**Target**: `druids-client` crate (library portion)
**Dependencies**: Phase 1 (core-types, config-system)

## Task: Implement HTTP Client for Druids API

Create a reqwest-based HTTP client library that other components use to communicate with the Druids server.

### Reference Files
- `client/druids/client.py` - Python API client
- `server/druids_server/api/routes/*.py` - Server API endpoints

### Deliverables

**1. Client Structure** (`crates/druids-client/src/client.rs`):
```rust
pub struct DruidsClient {
    base_url: String,
    token: Option<String>,
    http: reqwest::Client,
}

impl DruidsClient {
    pub fn new(base_url: String) -> Self;
    pub fn with_token(base_url: String, token: String) -> Self;
}
```

**2. API Methods**:
- Executions:
  - `create_execution()` - POST /api/executions
  - `list_executions()` - GET /api/executions
  - `get_execution()` - GET /api/executions/{slug}
  - `stop_execution()` - DELETE /api/executions/{slug}
  - `send_message()` - POST /api/executions/{slug}/messages
  - `stream_events()` - GET /api/executions/{slug}/events (SSE)

- Devboxes:
  - `create_devbox()` - POST /api/devboxes
  - `list_devboxes()` - GET /api/devboxes
  - `snapshot_devbox()` - POST /api/devboxes/{name}/snapshot

- Secrets:
  - `set_secret()` - POST /api/secrets
  - `list_secrets()` - GET /api/secrets

**3. Request/Response Types** (`crates/druids-client/src/types.rs`):
- Use types from `druids-core` where possible
- Define request/response wrappers for API calls
- Serialize/deserialize with serde

**4. Authentication** (`crates/druids-client/src/auth.rs`):
- JWT token handling
- Authorization header injection
- Token refresh (if applicable)

**5. Error Handling** (`crates/druids-client/src/error.rs`):
```rust
#[derive(Debug, thiserror::Error)]
pub enum ClientError {
    #[error("HTTP error: {0}")]
    Http(#[from] reqwest::Error),
    #[error("API error: {status} - {message}")]
    Api { status: u16, message: String },
    #[error("authentication required")]
    Unauthorized,
}
```

**6. Retry Logic** (`crates/druids-client/src/retry.rs`):
- Configurable retry policy
- Exponential backoff
- Retry on specific status codes (429, 500-599)

**7. SSE Streaming** (`crates/druids-client/src/streaming.rs`):
- `eventsource` or `reqwest-eventsource` for SSE
- Stream execution events
- Parse JSONL event format

### Success Criteria
- All API endpoints covered
- Proper error handling and propagation
- Retry logic tested
- SSE streaming works
- Integration tests with mock server
- Documentation on all public APIs

### Notes
- Use `reqwest` with rustls for HTTPS
- Use `tokio` for async
- Match Python client behavior exactly
