//! Database connection pool management.

use sqlx::postgres::{PgPool, PgPoolOptions};
use std::time::Duration;

use crate::Result;

/// Type alias for the database connection pool.
pub type Pool = PgPool;

/// Creates a new database connection pool.
///
/// # Arguments
///
/// * `database_url` - PostgreSQL connection URL
///
/// # Examples
///
/// ```no_run
/// # use druids_db::create_pool;
/// # async fn example() -> Result<(), Box<dyn std::error::Error>> {
/// let pool = create_pool("postgresql://localhost/druids").await?;
/// # Ok(())
/// # }
/// ```
pub async fn create_pool(database_url: &str) -> Result<Pool> {
    let pool = PgPoolOptions::new()
        .max_connections(20)
        .acquire_timeout(Duration::from_secs(30))
        .idle_timeout(Duration::from_secs(600))
        .max_lifetime(Duration::from_secs(1800))
        .connect(database_url)
        .await?;

    Ok(pool)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    #[ignore] // Requires database to be running
    async fn test_create_pool() {
        let url = std::env::var("DATABASE_URL").expect("DATABASE_URL must be set for tests");
        let pool = create_pool(&url).await.expect("Failed to create pool");

        // Test that we can acquire a connection
        let conn = pool.acquire().await.expect("Failed to acquire connection");
        drop(conn);
    }
}
