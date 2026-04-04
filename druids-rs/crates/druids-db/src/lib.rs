//! Database layer for Druids.
//!
//! This crate provides database models, queries, and migrations.

use sqlx::PgPool;
use thiserror::Error;

/// Database error types.
#[derive(Debug, Error)]
pub enum DbError {
    /// SQL execution error.
    #[error("database error: {0}")]
    Sqlx(#[from] sqlx::Error),

    /// Record not found.
    #[error("record not found: {0}")]
    NotFound(String),
}

/// Database connection pool.
pub struct Database {
    pool: PgPool,
}

impl Database {
    /// Creates a new database connection pool.
    ///
    /// # Examples
    ///
    /// ```no_run
    /// use druids_db::Database;
    ///
    /// # async fn example() -> Result<(), Box<dyn std::error::Error>> {
    /// let db = Database::connect("postgresql://localhost/druids").await?;
    /// # Ok(())
    /// # }
    /// ```
    pub async fn connect(database_url: &str) -> Result<Self, DbError> {
        let pool = PgPool::connect(database_url).await?;
        Ok(Self { pool })
    }

    /// Returns a reference to the connection pool.
    pub fn pool(&self) -> &PgPool {
        &self.pool
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_error_types() {
        let err = DbError::NotFound("test".to_string());
        assert_eq!(err.to_string(), "record not found: test");
    }
}
