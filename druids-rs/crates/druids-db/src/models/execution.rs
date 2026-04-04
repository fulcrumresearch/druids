//! Execution record model and queries.

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use serde_json::Value as JsonValue;
use sqlx::PgPool;
use uuid::Uuid;

use crate::Result;

/// A single execution.
#[derive(Debug, Clone, Serialize, Deserialize, sqlx::FromRow)]
pub struct ExecutionRecord {
    pub id: Uuid,
    pub slug: String,
    pub user_id: Uuid,
    pub spec: String,
    pub repo_full_name: Option<String>,
    #[sqlx(rename = "metadata_")]
    pub metadata: JsonValue,
    pub status: String,
    pub started_at: DateTime<Utc>,
    pub stopped_at: Option<DateTime<Utc>>,
    pub completed_at: Option<DateTime<Utc>>,
    pub branch_name: Option<String>,
    pub pr_number: Option<i32>,
    pub pr_url: Option<String>,
    pub error: Option<String>,
    pub agents: JsonValue,
    pub edges: JsonValue,
    pub program_id: Option<Uuid>,
    pub input_tokens: i32,
    pub output_tokens: i32,
    pub cache_read_input_tokens: i32,
    pub cache_creation_input_tokens: i32,
}

/// Generates a task slug (simplified version - replicates Python logic).
fn generate_task_slug() -> String {
    use rand::Rng;
    const ADJECTIVES: &[&str] = &[
        "happy", "clever", "bright", "swift", "brave", "calm", "eager", "fair",
        "gentle", "jolly", "kind", "lively", "merry", "nice", "proud", "silly",
    ];
    const NOUNS: &[&str] = &[
        "panda", "tiger", "eagle", "dolphin", "falcon", "badger", "otter", "hawk",
        "lynx", "wolf", "bear", "fox", "owl", "deer", "seal", "lion",
    ];

    let mut rng = rand::thread_rng();
    let adj = ADJECTIVES[rng.gen_range(0..ADJECTIVES.len())];
    let noun = NOUNS[rng.gen_range(0..NOUNS.len())];
    let num = rng.gen_range(1..100);

    format!("{}-{}-{}", adj, noun, num)
}

/// Creates a new execution record with auto-generated slug.
pub async fn create_execution(
    pool: &PgPool,
    user_id: Uuid,
    spec: &str,
    repo_full_name: Option<&str>,
    metadata: Option<JsonValue>,
    program_id: Option<Uuid>,
) -> Result<ExecutionRecord> {
    // Generate unique slug
    let mut slug = String::new();
    for attempt in 0..10 {
        slug = generate_task_slug();
        let existing = get_execution_by_slug(pool, user_id, &slug).await?;
        if existing.is_none() {
            break;
        }
        if attempt == 9 {
            // Add random hex suffix on last attempt
            slug = format!("{}-{:04x}", slug, rand::random::<u16>());
        }
    }

    let id = Uuid::new_v4();
    let now = Utc::now();
    let branch_name = format!("druids/{}", slug);
    let metadata = metadata.unwrap_or(JsonValue::Object(serde_json::Map::new()));

    sqlx::query(
        r#"
        INSERT INTO execution (
            id, slug, user_id, spec, repo_full_name, metadata_, status,
            started_at, stopped_at, completed_at, branch_name, pr_number, pr_url,
            error, agents, edges, program_id, input_tokens, output_tokens,
            cache_read_input_tokens, cache_creation_input_tokens
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19, $20, $21)
        "#
    )
    .bind(id)
    .bind(&slug)
    .bind(user_id)
    .bind(spec)
    .bind(repo_full_name)
    .bind(&metadata)
    .bind("starting")
    .bind(now)
    .bind(None::<DateTime<Utc>>)
    .bind(None::<DateTime<Utc>>)
    .bind(&branch_name)
    .bind(None::<i32>)
    .bind(None::<String>)
    .bind(None::<String>)
    .bind(JsonValue::Array(vec![]))
    .bind(JsonValue::Array(vec![]))
    .bind(program_id)
    .bind(0_i32)
    .bind(0_i32)
    .bind(0_i32)
    .bind(0_i32)
    .execute(pool)
    .await?;

    Ok(ExecutionRecord {
        id,
        slug,
        user_id,
        spec: spec.to_string(),
        repo_full_name: repo_full_name.map(|s| s.to_string()),
        metadata,
        status: "starting".to_string(),
        started_at: now,
        stopped_at: None,
        completed_at: None,
        branch_name: Some(branch_name),
        pr_number: None,
        pr_url: None,
        error: None,
        agents: JsonValue::Array(vec![]),
        edges: JsonValue::Array(vec![]),
        program_id,
        input_tokens: 0,
        output_tokens: 0,
        cache_read_input_tokens: 0,
        cache_creation_input_tokens: 0,
    })
}

/// Gets an execution by ID.
pub async fn get_execution(pool: &PgPool, execution_id: Uuid) -> Result<Option<ExecutionRecord>> {
    let record = sqlx::query_as::<_, ExecutionRecord>(
        r#"
        SELECT id, slug, user_id, spec, repo_full_name, metadata_, status,
               started_at, stopped_at, completed_at, branch_name, pr_number, pr_url,
               error, agents, edges, program_id, input_tokens, output_tokens,
               cache_read_input_tokens, cache_creation_input_tokens
        FROM execution WHERE id = $1
        "#
    )
    .bind(execution_id)
    .fetch_optional(pool)
    .await?;

    Ok(record)
}

/// Gets an execution by slug (scoped to user).
pub async fn get_execution_by_slug(
    pool: &PgPool,
    user_id: Uuid,
    slug: &str,
) -> Result<Option<ExecutionRecord>> {
    let record = sqlx::query_as::<_, ExecutionRecord>(
        r#"
        SELECT id, slug, user_id, spec, repo_full_name, metadata_, status,
               started_at, stopped_at, completed_at, branch_name, pr_number, pr_url,
               error, agents, edges, program_id, input_tokens, output_tokens,
               cache_read_input_tokens, cache_creation_input_tokens
        FROM execution WHERE user_id = $1 AND slug = $2
        "#
    )
    .bind(user_id)
    .bind(slug)
    .fetch_optional(pool)
    .await?;

    Ok(record)
}

/// Gets all executions for a user.
pub async fn get_user_executions(
    pool: &PgPool,
    user_id: Uuid,
    active_only: bool,
) -> Result<Vec<ExecutionRecord>> {
    let records = if active_only {
        sqlx::query_as::<_, ExecutionRecord>(
            r#"
            SELECT id, slug, user_id, spec, repo_full_name, metadata_, status,
                   started_at, stopped_at, completed_at, branch_name, pr_number, pr_url,
                   error, agents, edges, program_id, input_tokens, output_tokens,
                   cache_read_input_tokens, cache_creation_input_tokens
            FROM execution
            WHERE user_id = $1 AND status IN ('running', 'starting')
            ORDER BY started_at DESC
            "#
        )
        .bind(user_id)
        .fetch_all(pool)
        .await?
    } else {
        sqlx::query_as::<_, ExecutionRecord>(
            r#"
            SELECT id, slug, user_id, spec, repo_full_name, metadata_, status,
                   started_at, stopped_at, completed_at, branch_name, pr_number, pr_url,
                   error, agents, edges, program_id, input_tokens, output_tokens,
                   cache_read_input_tokens, cache_creation_input_tokens
            FROM execution
            WHERE user_id = $1
            ORDER BY started_at DESC
            "#
        )
        .bind(user_id)
        .fetch_all(pool)
        .await?
    };

    Ok(records)
}

/// Updates mutable fields on an execution record.
#[allow(clippy::too_many_arguments)]
pub async fn update_execution(
    pool: &PgPool,
    execution_id: Uuid,
    status: Option<&str>,
    pr_number: Option<i32>,
    pr_url: Option<&str>,
    error: Option<&str>,
    agents: Option<JsonValue>,
    edges: Option<JsonValue>,
) -> Result<Option<ExecutionRecord>> {
    // Build update query dynamically based on which fields are provided
    let mut updates = Vec::new();
    let mut param_index = 2; // $1 is execution_id

    if status.is_some() {
        updates.push(format!("status = ${}", param_index));
        param_index += 1;
    }
    if pr_number.is_some() {
        updates.push(format!("pr_number = ${}", param_index));
        param_index += 1;
    }
    if pr_url.is_some() {
        updates.push(format!("pr_url = ${}", param_index));
        param_index += 1;
    }
    if error.is_some() {
        updates.push(format!("error = ${}", param_index));
        param_index += 1;
    }
    if agents.is_some() {
        updates.push(format!("agents = ${}", param_index));
        param_index += 1;
    }
    if edges.is_some() {
        updates.push(format!("edges = ${}", param_index));
        param_index += 1;
    }

    // Handle stopped_at and completed_at based on status
    if let Some(s) = status {
        if matches!(s, "stopped" | "completed" | "failed") {
            updates.push(format!("stopped_at = ${}", param_index));
            param_index += 1;
        }
        if s == "completed" {
            updates.push(format!("completed_at = ${}", param_index));
        }
    }

    if updates.is_empty() {
        return get_execution(pool, execution_id).await;
    }

    let query_str = format!(
        "UPDATE execution SET {} WHERE id = $1",
        updates.join(", ")
    );

    let mut query = sqlx::query(&query_str).bind(execution_id);

    if status.is_some() {
        query = query.bind(status);
    }
    if pr_number.is_some() {
        query = query.bind(pr_number);
    }
    if pr_url.is_some() {
        query = query.bind(pr_url);
    }
    if error.is_some() {
        query = query.bind(error);
    }
    if let Some(a) = agents {
        query = query.bind(a);
    }
    if let Some(e) = edges {
        query = query.bind(e);
    }

    let now = Utc::now();
    if let Some(s) = status {
        if matches!(s, "stopped" | "completed" | "failed") {
            query = query.bind(now);
        }
        if s == "completed" {
            query = query.bind(now);
        }
    }

    query.execute(pool).await?;

    get_execution(pool, execution_id).await
}

/// Atomically increments token usage counters for an execution.
pub async fn increment_usage(
    pool: &PgPool,
    execution_id: Uuid,
    input_tokens: i32,
    output_tokens: i32,
    cache_read_input_tokens: i32,
    cache_creation_input_tokens: i32,
) -> Result<()> {
    sqlx::query(
        r#"
        UPDATE execution SET
            input_tokens = input_tokens + $2,
            output_tokens = output_tokens + $3,
            cache_read_input_tokens = cache_read_input_tokens + $4,
            cache_creation_input_tokens = cache_creation_input_tokens + $5
        WHERE id = $1
        "#
    )
    .bind(execution_id)
    .bind(input_tokens)
    .bind(output_tokens)
    .bind(cache_read_input_tokens)
    .bind(cache_creation_input_tokens)
    .execute(pool)
    .await?;

    Ok(())
}
