//! Runtime SDK for Druids programs.
//!
//! This crate provides the runtime API that Druids programs use to interact
//! with the execution environment.

use thiserror::Error;

/// Runtime error types.
#[derive(Debug, Error)]
pub enum RuntimeError {
    /// Message send failed.
    #[error("failed to send message: {0}")]
    SendFailed(String),

    /// Connection error.
    #[error("connection error: {0}")]
    Connection(String),
}

/// Runtime context for a Druids program.
pub struct Runtime {
    // Placeholder for runtime state
}

impl Runtime {
    /// Creates a new runtime instance.
    pub fn new() -> Self {
        Self {}
    }

    /// Sends a message to another agent.
    pub async fn send_message(&self, _recipient: &str, _message: &str) -> Result<(), RuntimeError> {
        // Placeholder implementation
        Ok(())
    }
}

impl Default for Runtime {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn test_runtime_creation() {
        let runtime = Runtime::new();
        assert!(runtime.send_message("test", "hello").await.is_ok());
    }
}
