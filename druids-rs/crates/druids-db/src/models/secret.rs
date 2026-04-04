//! Secret model and queries.
//!
//! Secrets are encrypted environment variables associated with devboxes.

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use sqlx::PgPool;
use uuid::Uuid;

use crate::{crypto, Result};

/// An encrypted environment variable associated with a devbox.
#[derive(Debug, Clone, Serialize, Deserialize, sqlx::FromRow)]
pub struct Secret {
    pub id: Uuid,
    pub devbox_id: Uuid,
    pub name: String,
    pub encrypted_value: String,
    pub created_at: DateTime<Utc>,
    pub updated_at: DateTime<Utc>,
}

impl Secret {
    /// Sets the value of the secret, encrypting it.
    pub fn set_value(&mut self, plaintext: &str, secret_key: &str) -> Result<()> {
        self.encrypted_value = crypto::encrypt(plaintext, secret_key)?;
        self.updated_at = Utc::now();
        Ok(())
    }

    /// Gets the decrypted value of the secret.
    pub fn get_value(&self, secret_key: &str) -> Result<String> {
        crypto::decrypt(&self.encrypted_value, secret_key)
    }
}

/// Gets all secrets for a devbox.
pub async fn get_secrets(pool: &PgPool, devbox_id: Uuid) -> Result<Vec<Secret>> {
    let secrets = sqlx::query_as::<_, Secret>(
        r#"
        SELECT id, devbox_id, name, encrypted_value, created_at, updated_at
        FROM secret
        WHERE devbox_id = $1
        ORDER BY name
        "#
    )
    .bind(devbox_id)
    .fetch_all(pool)
    .await?;

    Ok(secrets)
}

/// Gets a secret by devbox and name.
pub async fn get_secret_by_name(
    pool: &PgPool,
    devbox_id: Uuid,
    name: &str,
) -> Result<Option<Secret>> {
    let secret = sqlx::query_as::<_, Secret>(
        r#"
        SELECT id, devbox_id, name, encrypted_value, created_at, updated_at
        FROM secret
        WHERE devbox_id = $1 AND name = $2
        "#
    )
    .bind(devbox_id)
    .bind(name)
    .fetch_optional(pool)
    .await?;

    Ok(secret)
}

/// Creates or updates a secret. Returns the secret.
pub async fn set_secret(
    pool: &PgPool,
    devbox_id: Uuid,
    name: &str,
    value: &str,
    secret_key: &str,
) -> Result<Secret> {
    let encrypted = crypto::encrypt(value, secret_key)?;
    let now = Utc::now();

    if let Some(mut secret) = get_secret_by_name(pool, devbox_id, name).await? {
        // Update existing
        secret.encrypted_value = encrypted;
        secret.updated_at = now;

        sqlx::query("UPDATE secret SET encrypted_value = $1, updated_at = $2 WHERE id = $3")
            .bind(&secret.encrypted_value)
            .bind(now)
            .bind(secret.id)
            .execute(pool)
            .await?;

        Ok(secret)
    } else {
        // Create new
        let id = Uuid::new_v4();

        sqlx::query(
            r#"
            INSERT INTO secret (id, devbox_id, name, encrypted_value, created_at, updated_at)
            VALUES ($1, $2, $3, $4, $5, $6)
            "#
        )
        .bind(id)
        .bind(devbox_id)
        .bind(name)
        .bind(&encrypted)
        .bind(now)
        .bind(now)
        .execute(pool)
        .await?;

        Ok(Secret {
            id,
            devbox_id,
            name: name.to_string(),
            encrypted_value: encrypted,
            created_at: now,
            updated_at: now,
        })
    }
}

/// Deletes a secret by name. Returns true if it existed.
pub async fn delete_secret(pool: &PgPool, devbox_id: Uuid, name: &str) -> Result<bool> {
    let result = sqlx::query("DELETE FROM secret WHERE devbox_id = $1 AND name = $2")
        .bind(devbox_id)
        .bind(name)
        .execute(pool)
        .await?;

    Ok(result.rows_affected() > 0)
}

/// Gets all secrets for a devbox as a plaintext dict. Used during provisioning.
pub async fn get_decrypted_secrets(
    pool: &PgPool,
    devbox_id: Uuid,
    secret_key: &str,
) -> Result<std::collections::HashMap<String, String>> {
    let secrets = get_secrets(pool, devbox_id).await?;
    let mut map = std::collections::HashMap::new();

    for secret in secrets {
        let value = secret.get_value(secret_key)?;
        map.insert(secret.name.clone(), value);
    }

    Ok(map)
}
