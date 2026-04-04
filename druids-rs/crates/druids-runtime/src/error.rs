//! Runtime error types.

use std::fmt;

/// Runtime error type
#[derive(Debug, thiserror::Error)]
pub enum RuntimeError {
    #[error("HTTP error: {0}")]
    Http(#[from] reqwest::Error),

    #[error("serialization error: {0}")]
    Serialization(#[from] serde_json::Error),

    #[error("I/O error: {0}")]
    Io(#[from] std::io::Error),

    #[error("agent not found: {0}")]
    AgentNotFound(String),

    #[error("handler not found for tool: {0}")]
    HandlerNotFound(String),

    #[error("server error: {0}")]
    Server(String),

    #[error("not connected: agent {0} cannot reach agent {1}")]
    NotConnected(String, String),

    #[error("{0}")]
    Other(String),
}

impl RuntimeError {
    pub fn other(msg: impl fmt::Display) -> Self {
        Self::Other(msg.to_string())
    }

    pub fn server(msg: impl fmt::Display) -> Self {
        Self::Server(msg.to_string())
    }
}

pub type Result<T> = std::result::Result<T, RuntimeError>;
