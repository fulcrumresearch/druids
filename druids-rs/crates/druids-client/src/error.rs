//! Error types for the Druids client.

use std::fmt;

/// Error types for the Druids client.
#[derive(Debug, thiserror::Error)]
pub enum ClientError {
    /// HTTP request failed.
    #[error("HTTP error: {0}")]
    Http(#[from] reqwest::Error),

    /// API returned an error response.
    #[error("API error {status}: {message}")]
    Api {
        /// HTTP status code.
        status: u16,
        /// Error message from the server.
        message: String,
    },

    /// Resource not found (404).
    #[error("{resource_type} '{identifier}' not found")]
    NotFound {
        /// Type of resource (e.g., "Execution", "Agent").
        resource_type: String,
        /// Resource identifier.
        identifier: String,
    },

    /// Authentication required (401).
    #[error("authentication required")]
    Unauthorized,

    /// JSON serialization/deserialization error.
    #[error("JSON error: {0}")]
    Json(#[from] serde_json::Error),

    /// Configuration error.
    #[error("configuration error: {0}")]
    Config(#[from] druids_core::ConfigError),

    /// I/O error.
    #[error("I/O error: {0}")]
    Io(#[from] std::io::Error),

    /// URL parsing error.
    #[error("invalid URL: {0}")]
    UrlParse(#[from] url::ParseError),

    /// SSE streaming error.
    #[error("SSE stream error: {0}")]
    Stream(String),

    /// Retry exhausted.
    #[error("retry exhausted after {attempts} attempts: {message}")]
    RetryExhausted {
        /// Number of retry attempts made.
        attempts: u32,
        /// Last error message.
        message: String,
    },
}

/// Result type for client operations.
pub type Result<T> = std::result::Result<T, ClientError>;
