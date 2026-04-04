//! User model and queries.

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use sqlx::PgPool;
use uuid::Uuid;

use crate::Result;

/// A GitHub-authenticated user.
#[derive(Debug, Clone, Serialize, Deserialize, sqlx::FromRow)]
pub struct User {
    pub id: Uuid,
    pub github_id: i32,
    pub github_login: Option<String>,
    pub created_at: DateTime<Utc>,
}

/// Gets an existing user by github_id or creates a new one.
///
/// Updates github_login if it has changed.
pub async fn get_or_create_user(
    pool: &PgPool,
    github_id: i32,
    github_login: Option<&str>,
) -> Result<User> {
    // Try to find existing user
    let existing = sqlx::query_as::<_, User>(
        "SELECT id, github_id, github_login, created_at FROM \"user\" WHERE github_id = $1"
    )
    .bind(github_id)
    .fetch_optional(pool)
    .await?;

    match existing {
        Some(mut user) => {
            // Update github_login if it changed
            if github_login.is_some() && user.github_login.as_deref() != github_login {
                user.github_login = github_login.map(|s| s.to_string());
                sqlx::query("UPDATE \"user\" SET github_login = $1 WHERE id = $2")
                    .bind(&user.github_login)
                    .bind(user.id)
                    .execute(pool)
                    .await?;
            }
            Ok(user)
        }
        None => {
            // Create new user
            let id = Uuid::new_v4();
            let now = Utc::now();
            let github_login = github_login.map(|s| s.to_string());

            sqlx::query(
                "INSERT INTO \"user\" (id, github_id, github_login, created_at) VALUES ($1, $2, $3, $4)"
            )
            .bind(id)
            .bind(github_id)
            .bind(&github_login)
            .bind(now)
            .execute(pool)
            .await?;

            Ok(User {
                id,
                github_id,
                github_login,
                created_at: now,
            })
        }
    }
}

/// Gets a user by ID.
pub async fn get_user(pool: &PgPool, user_id: Uuid) -> Result<Option<User>> {
    let user = sqlx::query_as::<_, User>(
        "SELECT id, github_id, github_login, created_at FROM \"user\" WHERE id = $1"
    )
    .bind(user_id)
    .fetch_optional(pool)
    .await?;

    Ok(user)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    #[ignore] // Requires database
    async fn test_get_or_create_user() {
        let url = std::env::var("DATABASE_URL").expect("DATABASE_URL must be set");
        let pool = crate::create_pool(&url).await.expect("Failed to create pool");

        let github_id = 12345;
        let github_login = "testuser";

        let user1 = get_or_create_user(&pool, github_id, Some(github_login))
            .await
            .expect("Failed to create user");

        assert_eq!(user1.github_id, github_id);
        assert_eq!(user1.github_login.as_deref(), Some(github_login));

        // Getting again should return the same user
        let user2 = get_or_create_user(&pool, github_id, Some(github_login))
            .await
            .expect("Failed to get user");

        assert_eq!(user1.id, user2.id);
    }
}
