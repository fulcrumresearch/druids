//! Error types for Druids operations.

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

/// Errors related to execution operations.
#[derive(Debug, thiserror::Error)]
pub enum ExecutionError {
    /// Execution not found.
    #[error("execution {0} not found")]
    NotFound(String),

    /// Invalid execution state transition.
    #[error("invalid state transition from {from} to {to}")]
    InvalidStateTransition {
        /// Current state.
        from: String,
        /// Attempted new state.
        to: String,
    },

    /// Execution has already stopped.
    #[error("execution {0} has already stopped")]
    AlreadyStopped(String),

    /// Execution timeout.
    #[error("execution {slug} timed out after {seconds}s")]
    Timeout {
        /// Execution slug.
        slug: String,
        /// Timeout duration in seconds.
        seconds: u64,
    },

    /// Database error.
    #[error("database error: {0}")]
    Database(String),

    /// Generic error.
    #[error("{0}")]
    Other(String),
}

/// Errors related to agent operations.
#[derive(Debug, thiserror::Error)]
pub enum AgentError {
    /// Agent not found.
    #[error("agent {0} not found")]
    NotFound(String),

    /// Agent connection failed.
    #[error("failed to connect to agent {name}: {reason}")]
    ConnectionFailed {
        /// Agent name.
        name: String,
        /// Failure reason.
        reason: String,
    },

    /// Agent disconnected unexpectedly.
    #[error("agent {0} disconnected unexpectedly")]
    Disconnected(String),

    /// Invalid agent configuration.
    #[error("invalid agent configuration: {0}")]
    InvalidConfig(String),

    /// Tool execution error.
    #[error("tool {tool} failed: {reason}")]
    ToolFailed {
        /// Tool name.
        tool: String,
        /// Failure reason.
        reason: String,
    },

    /// Generic error.
    #[error("{0}")]
    Other(String),
}

/// Errors related to configuration.
#[derive(Debug, thiserror::Error)]
pub enum ConfigError {
    /// Missing required configuration.
    #[error("missing required configuration: {0}")]
    MissingRequired(String),

    /// Invalid configuration value.
    #[error("invalid configuration for {key}: {reason}")]
    InvalidValue {
        /// Configuration key.
        key: String,
        /// Reason why the value is invalid.
        reason: String,
    },

    /// Environment variable error.
    #[error("environment variable {0} not set")]
    EnvVarNotSet(String),

    /// File read error.
    #[error("failed to read config file {path}: {reason}")]
    FileRead {
        /// File path.
        path: String,
        /// Failure reason.
        reason: String,
    },

    /// Parse error.
    #[error("failed to parse configuration: {0}")]
    Parse(String),

    /// Generic error.
    #[error("{0}")]
    Other(String),
}
