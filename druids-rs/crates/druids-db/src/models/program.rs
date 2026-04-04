//! Program model and queries.

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use sha2::{Sha256, Digest};
use sqlx::PgPool;
use uuid::Uuid;

use crate::Result;

/// A saved program source.
///
/// Programs are deduplicated per user by content hash so that re-running the
/// same source code reuses the existing record.
#[derive(Debug, Clone, Serialize, Deserialize, sqlx::FromRow)]
pub struct Program {
    pub id: Uuid,
    pub user_id: Uuid,
    pub source: String,
    pub source_hash: String,
    pub created_at: DateTime<Utc>,
}

/// Computes a SHA-256 hash of program source code.
pub fn hash_source(source: &str) -> String {
    let mut hasher = Sha256::new();
    hasher.update(source.as_bytes());
    format!("{:x}", hasher.finalize())
}

/// Returns an existing program with the same source, or creates a new one.
pub async fn get_or_create_program(
    pool: &PgPool,
    user_id: Uuid,
    source: &str,
) -> Result<Program> {
    let content_hash = hash_source(source);

    // Try to find existing program
    let existing = sqlx::query_as::<_, Program>(
        r#"
        SELECT id, user_id, source, source_hash, created_at
        FROM program
        WHERE user_id = $1 AND source_hash = $2
        "#
    )
    .bind(user_id)
    .bind(&content_hash)
    .fetch_optional(pool)
    .await?;

    if let Some(program) = existing {
        return Ok(program);
    }

    // Create new program
    let id = Uuid::new_v4();
    let now = Utc::now();

    sqlx::query(
        r#"
        INSERT INTO program (id, user_id, source, source_hash, created_at)
        VALUES ($1, $2, $3, $4, $5)
        "#
    )
    .bind(id)
    .bind(user_id)
    .bind(source)
    .bind(&content_hash)
    .bind(now)
    .execute(pool)
    .await?;

    Ok(Program {
        id,
        user_id,
        source: source.to_string(),
        source_hash: content_hash,
        created_at: now,
    })
}

/// Gets a program by ID.
pub async fn get_program(pool: &PgPool, program_id: Uuid) -> Result<Option<Program>> {
    let program = sqlx::query_as::<_, Program>(
        r#"
        SELECT id, user_id, source, source_hash, created_at
        FROM program
        WHERE id = $1
        "#
    )
    .bind(program_id)
    .fetch_optional(pool)
    .await?;

    Ok(program)
}

/// Gets all programs for a user, most recent first.
pub async fn get_user_programs(pool: &PgPool, user_id: Uuid) -> Result<Vec<Program>> {
    let programs = sqlx::query_as::<_, Program>(
        r#"
        SELECT id, user_id, source, source_hash, created_at
        FROM program
        WHERE user_id = $1
        ORDER BY created_at DESC
        "#
    )
    .bind(user_id)
    .fetch_all(pool)
    .await?;

    Ok(programs)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_hash_source() {
        let source1 = "print('hello')";
        let source2 = "print('hello')";
        let source3 = "print('world')";

        let hash1 = hash_source(source1);
        let hash2 = hash_source(source2);
        let hash3 = hash_source(source3);

        // Same source produces same hash
        assert_eq!(hash1, hash2);
        // Different source produces different hash
        assert_ne!(hash1, hash3);
    }
}
