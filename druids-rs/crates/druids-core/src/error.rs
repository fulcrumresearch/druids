//! Core error types.

use thiserror::Error;

/// Core error type for Druids operations.
#[derive(Debug, Error)]
pub enum CoreError {
    #[error("serialization error: {0}")]
    Serialization(#[from] serde_json::Error),

    #[error("invalid UUID: {0}")]
    InvalidUuid(#[from] uuid::Error),

    #[error("{0}")]
    Other(String),
}
