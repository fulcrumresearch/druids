//! Devbox model and queries.

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use sqlx::PgPool;
use uuid::Uuid;

use crate::Result;

/// A named environment snapshot for a user.
///
/// Devboxes are decoupled from git. A devbox may optionally be associated with
/// a repo (for convenience during setup), but the association is not required.
/// Git credentials are provisioned per-agent at execution time, not stored here.
#[derive(Debug, Clone, Serialize, Deserialize, sqlx::FromRow)]
pub struct Devbox {
    pub id: Uuid,
    pub user_id: Uuid,
    pub name: String,
    pub repo_full_name: String,
    pub instance_id: Option<String>,
    pub snapshot_id: Option<String>,
    pub setup_slug: Option<String>,
    pub setup_completed_at: Option<DateTime<Utc>>,
    pub vcpus: Option<i32>,
    pub memory_mb: Option<i32>,
    pub disk_mb: Option<i32>,
    pub created_at: DateTime<Utc>,
    pub updated_at: Option<DateTime<Utc>>,
}

/// Gets a devbox by user_id and repo_full_name.
pub async fn get_devbox(
    pool: &PgPool,
    user_id: Uuid,
    repo_full_name: &str,
) -> Result<Option<Devbox>> {
    let devbox = sqlx::query_as::<_, Devbox>(
        r#"
        SELECT id, user_id, name, repo_full_name, instance_id, snapshot_id,
               setup_slug, setup_completed_at, vcpus, memory_mb, disk_mb,
               created_at, updated_at
        FROM devbox
        WHERE user_id = $1 AND repo_full_name = $2
        ORDER BY COALESCE(updated_at, created_at) DESC
        LIMIT 1
        "#
    )
    .bind(user_id)
    .bind(repo_full_name)
    .fetch_optional(pool)
    .await?;

    Ok(devbox)
}

/// Gets a devbox by user_id and name.
pub async fn get_devbox_by_name(
    pool: &PgPool,
    user_id: Uuid,
    name: &str,
) -> Result<Option<Devbox>> {
    let devbox = sqlx::query_as::<_, Devbox>(
        r#"
        SELECT id, user_id, name, repo_full_name, instance_id, snapshot_id,
               setup_slug, setup_completed_at, vcpus, memory_mb, disk_mb,
               created_at, updated_at
        FROM devbox
        WHERE user_id = $1 AND name = $2
        "#
    )
    .bind(user_id)
    .bind(name)
    .fetch_optional(pool)
    .await?;

    Ok(devbox)
}

/// Gets the best devbox for a repo, across all users.
///
/// Only returns devboxes with a completed snapshot (snapshot_id is not null).
/// When multiple users have set up the same repo, picks the most recently updated one.
pub async fn get_devbox_by_repo(pool: &PgPool, repo_full_name: &str) -> Result<Option<Devbox>> {
    let devbox = sqlx::query_as::<_, Devbox>(
        r#"
        SELECT id, user_id, name, repo_full_name, instance_id, snapshot_id,
               setup_slug, setup_completed_at, vcpus, memory_mb, disk_mb,
               created_at, updated_at
        FROM devbox
        WHERE repo_full_name = $1 AND snapshot_id IS NOT NULL
        ORDER BY COALESCE(updated_at, created_at) DESC, created_at DESC
        LIMIT 1
        "#
    )
    .bind(repo_full_name)
    .fetch_optional(pool)
    .await?;

    Ok(devbox)
}

/// Gets all devboxes for a user.
pub async fn get_user_devboxes(pool: &PgPool, user_id: Uuid) -> Result<Vec<Devbox>> {
    let devboxes = sqlx::query_as::<_, Devbox>(
        r#"
        SELECT id, user_id, name, repo_full_name, instance_id, snapshot_id,
               setup_slug, setup_completed_at, vcpus, memory_mb, disk_mb,
               created_at, updated_at
        FROM devbox
        WHERE user_id = $1
        ORDER BY COALESCE(updated_at, created_at) DESC
        "#
    )
    .bind(user_id)
    .fetch_all(pool)
    .await?;

    Ok(devboxes)
}

/// Resolves a devbox by name or repo. Name takes priority.
///
/// When resolving by repo, tries the user's own devbox first, then falls back
/// to any devbox with a completed snapshot for the same repo.
pub async fn resolve_devbox(
    pool: &PgPool,
    user_id: Uuid,
    name: Option<&str>,
    repo_full_name: Option<&str>,
) -> Result<Option<Devbox>> {
    if let Some(n) = name {
        return get_devbox_by_name(pool, user_id, n).await;
    }
    if let Some(repo) = repo_full_name {
        if let Some(own) = get_devbox(pool, user_id, repo).await? {
            return Ok(Some(own));
        }
        return get_devbox_by_repo(pool, repo).await;
    }
    Ok(None)
}

/// Gets an existing devbox or creates a new one.
pub async fn get_or_create_devbox(
    pool: &PgPool,
    user_id: Uuid,
    repo_full_name: &str,
) -> Result<Devbox> {
    if let Some(devbox) = get_devbox(pool, user_id, repo_full_name).await? {
        return Ok(devbox);
    }

    let id = Uuid::new_v4();
    let now = Utc::now();

    sqlx::query(
        r#"
        INSERT INTO devbox (
            id, user_id, name, repo_full_name, instance_id, snapshot_id,
            setup_slug, setup_completed_at, vcpus, memory_mb, disk_mb,
            created_at, updated_at
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
        "#
    )
    .bind(id)
    .bind(user_id)
    .bind("") // empty name initially
    .bind(repo_full_name)
    .bind(None::<String>)
    .bind(None::<String>)
    .bind(None::<String>)
    .bind(None::<DateTime<Utc>>)
    .bind(None::<i32>)
    .bind(None::<i32>)
    .bind(None::<i32>)
    .bind(now)
    .bind(None::<DateTime<Utc>>)
    .execute(pool)
    .await?;

    Ok(Devbox {
        id,
        user_id,
        name: String::new(),
        repo_full_name: repo_full_name.to_string(),
        instance_id: None,
        snapshot_id: None,
        setup_slug: None,
        setup_completed_at: None,
        vcpus: None,
        memory_mb: None,
        disk_mb: None,
        created_at: now,
        updated_at: None,
    })
}
