# druids-db

Database layer for Druids using SQLx with compile-time verified queries.

## Features

- **Compile-time verified queries** using SQLx macros
- **Async/await** support with tokio
- **PostgreSQL** backend
- **Migrations** via SQLx CLI
- **Encrypted secrets** using AES-256-GCM

## Database Setup

### Prerequisites

- PostgreSQL 14+
- SQLx CLI: `cargo install sqlx-cli --no-default-features --features postgres`

### Running Migrations

```bash
# Set database URL
export DATABASE_URL="postgresql://localhost/druids"

# Create database
sqlx database create

# Run migrations
sqlx migrate run --source crates/druids-db/migrations
```

### Preparing for Offline Compilation

SQLx can verify queries at compile time, but this requires access to a database during compilation. For CI/CD environments without a database, you can prepare metadata:

```bash
# Generate sqlx-data.json for offline compilation
cargo sqlx prepare --workspace
```

This creates `.sqlx/query-*.json` files that contain the metadata SQLx needs to verify queries without connecting to a database.

## Usage

### Creating a Connection Pool

```rust
use druids_db::{create_pool, Pool};

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let pool = create_pool("postgresql://localhost/druids").await?;

    // Use the pool
    // ...

    Ok(())
}
```

### User Operations

```rust
use druids_db::models::{get_or_create_user, get_user};

// Create or get a user
let user = get_or_create_user(&pool, 12345, Some("github_username")).await?;

// Get user by ID
let user = get_user(&pool, user_id).await?;
```

### Execution Operations

```rust
use druids_db::models::{create_execution, get_execution_by_slug, update_execution};

// Create an execution
let execution = create_execution(
    &pool,
    user_id,
    "Build feature X",
    Some("owner/repo"),
    None,
    None,
).await?;

// Get execution by slug
let execution = get_execution_by_slug(&pool, user_id, "happy-panda-42").await?;

// Update execution status
let execution = update_execution(
    &pool,
    execution_id,
    Some("completed"),
    None,
    None,
    None,
    None,
    None,
).await?;
```

### Secret Operations

```rust
use druids_db::models::{set_secret, get_decrypted_secrets};

// Store an encrypted secret
let secret_key = "your-base64-encoded-32-byte-key";
set_secret(&pool, devbox_id, "API_KEY", "secret-value", secret_key).await?;

// Retrieve all secrets for a devbox
let secrets = get_decrypted_secrets(&pool, devbox_id, secret_key).await?;
```

## Models

### User
GitHub-authenticated users with their GitHub ID and login.

### ExecutionRecord
Tracks program executions, including status, PR information, token usage, and agent topology.

### Devbox
Named environment snapshots for repositories, including instance and snapshot IDs.

### Secret
Encrypted environment variables associated with devboxes.

### Program
Deduplicated program source code, identified by SHA-256 hash.

## Environment Variables

- `DATABASE_URL` - PostgreSQL connection string (required)

Example:
```bash
DATABASE_URL="postgresql://user:password@localhost:5432/druids"
```

## Testing

Tests require a running PostgreSQL database:

```bash
# Set test database URL
export DATABASE_URL="postgresql://localhost/druids_test"

# Create and migrate test database
sqlx database create
sqlx migrate run --source crates/druids-db/migrations

# Run tests (most are ignored by default)
cargo test --package druids-db -- --ignored
```

## Security

Secrets are encrypted using AES-256-GCM with a 32-byte key. The encryption key must be:
- 32 bytes (256 bits)
- Base64-encoded
- Stored securely (environment variable or secrets manager)

Example key generation:
```bash
# Generate a random 32-byte key and encode as base64
openssl rand -base64 32
```
