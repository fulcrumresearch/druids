//! Database error types.

use thiserror::Error;

/// Database error type.
#[derive(Debug, Error)]
pub enum DatabaseError {
    #[error("database error: {0}")]
    Sqlx(#[from] sqlx::Error),

    #[error("not found: {0}")]
    NotFound(String),

    #[error("encryption error: {0}")]
    Encryption(String),

    #[error("decryption error: {0}")]
    Decryption(String),

    #[error("invalid data: {0}")]
    InvalidData(String),

    #[error("{0}")]
    Other(String),
}

/// Type alias for database Results.
pub type Result<T> = std::result::Result<T, DatabaseError>;
