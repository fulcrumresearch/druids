# druids-client

HTTP client library for the Druids API.

## Overview

This crate provides a reqwest-based client for communicating with the Druids server, including support for:

- **Execution management**: Create, list, get, stop executions, send messages to agents
- **Devbox management**: Create, list, and snapshot devboxes
- **Secret management**: Set, list, and delete secrets on devboxes
- **SSE streaming**: Stream execution events in real-time
- **Retry logic**: Exponential backoff for transient failures
- **Authentication**: JWT token-based authentication

## Features

### Core Client

The `DruidsClient` struct provides all API methods:

```rust
use druids_client::DruidsClient;

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    // Create client from default config (~/.druids/config.json)
    let client = DruidsClient::from_default_config()?;

    // Or create with explicit base URL and token
    let client = DruidsClient::with_token(
        url::Url::parse("http://localhost:8000")?,
        "your-token".to_string(),
    )?;

    Ok(())
}
```

### Execution API

```rust
use std::collections::HashMap;

// Create an execution
let response = client.create_execution(
    "async def program(ctx): pass".to_string(),
    Some("owner/repo".to_string()),  // repo_full_name
    None,                             // devbox_name
    None,                             // args
    None,                             // git_branch
    None,                             // ttl
    None,                             // files
).await?;

println!("Created execution: {}", response.execution_slug);

// Get execution details
let execution = client.get_execution("my-slug").await?;
println!("Status: {}", execution.status);

// List executions
let executions = client.list_executions(true).await?;
for exec in executions {
    println!("{}: {}", exec.slug, exec.status);
}

// Stop an execution
client.stop_execution("my-slug").await?;

// Send a message to an agent
client.send_agent_message(
    "my-slug",
    "builder",
    "Please try a different approach".to_string(),
).await?;

// Get execution activity
let activity = client.get_execution_activity("my-slug", Some(50), Some(true)).await?;
println!("Recent events: {}", activity.recent_activity.len());

// Get git diff
let diff = client.get_execution_diff("my-slug", None).await?;
println!("Diff:\n{}", diff);

// Get SSH credentials
let ssh = client.get_execution_ssh("my-slug", None).await?;
println!("SSH: {}@{}:{}", ssh.username, ssh.host, ssh.port);
```

### SSE Streaming

```rust
use futures::stream::StreamExt;

// Stream execution events in real-time
let mut stream = client.stream_execution("my-slug", false);

while let Some(event) = stream.next().await {
    match event {
        Ok(activity) => {
            println!("Event {}: {:?}", activity.event_id, activity.payload);
        }
        Err(e) => {
            eprintln!("Stream error: {}", e);
            break;
        }
    }
}
```

### Devbox API

```rust
// Start devbox setup
let setup = client.setup_start(
    Some("my-devbox".to_string()),
    Some("owner/repo".to_string()),
    Some(false),  // public
    None,         // vcpus
    None,         // memory_mb
    None,         // disk_mb
).await?;

// Finish devbox setup (snapshot)
client.setup_finish(
    Some("my-devbox".to_string()),
    None,
).await?;

// List devboxes
let devboxes = client.list_devboxes().await?;
for devbox in devboxes {
    println!("{}: {}", devbox.name, devbox.repo_full_name);
}
```

### Secrets API

```rust
use std::collections::HashMap;

// Set secrets
let mut secrets = HashMap::new();
secrets.insert("API_KEY".to_string(), "secret-value".to_string());
secrets.insert("DB_PASSWORD".to_string(), "another-secret".to_string());

client.set_secrets(
    secrets,
    Some("my-devbox".to_string()),
    None,
).await?;

// List secrets
let secrets = client.list_secrets(
    Some("my-devbox".to_string()),
    None,
).await?;

for secret in secrets {
    println!("{}: updated {}", secret.name, secret.updated_at);
}

// Delete a secret
client.delete_secret(
    "API_KEY".to_string(),
    Some("my-devbox".to_string()),
    None,
).await?;
```

### Tool API

```rust
use std::collections::HashMap;

// List tools for an agent
let tools = client.list_tools("my-slug", "builder").await?;
println!("Available tools: {:?}", tools);

// Call a tool
let mut args = HashMap::new();
args.insert("command".to_string(), serde_json::json!("ls -la"));

let result = client.call_tool(
    "my-slug",
    "builder",
    "bash",
    args,
).await?;

println!("Tool result: {:?}", result);
```

## Error Handling

The client uses the `ClientError` type for all errors:

```rust
use druids_client::ClientError;

match client.get_execution("nonexistent").await {
    Ok(execution) => println!("Found: {}", execution.execution_slug),
    Err(ClientError::NotFound { resource_type, identifier }) => {
        println!("{} '{}' not found", resource_type, identifier);
    }
    Err(ClientError::Unauthorized) => {
        println!("Authentication required");
    }
    Err(ClientError::Api { status, message }) => {
        println!("API error {}: {}", status, message);
    }
    Err(e) => println!("Error: {}", e),
}
```

## Retry Logic

The client automatically retries transient failures (network errors, timeouts, 429, 5xx) with exponential backoff:

- Initial interval: 500ms
- Max interval: 30s
- Multiplier: 2.0
- Max retries: 3

## Configuration

The client can load configuration from:

1. Environment variables (`DRUIDS_BASE_URL`, `DRUIDS_ACCESS_TOKEN`)
2. Config file (`~/.druids/config.json`)
3. Built-in defaults

```rust
use druids_client::config::ClientConfig;

// Load from all sources
let config = ClientConfig::load()?;

// Create client with config
let client = DruidsClient::new(config)?;
```

## Testing

The crate includes comprehensive integration tests with mock servers:

```bash
cargo test --package druids-client
```

## Dependencies

- `reqwest` - HTTP client with async support
- `tokio` - Async runtime
- `serde` / `serde_json` - Serialization
- `eventsource-client` - SSE streaming
- `backoff` - Retry logic with exponential backoff
- `thiserror` - Error handling

## See Also

- [druids-core](../druids-core) - Shared types and utilities
- [druids-server](../druids-server) - Server implementation
