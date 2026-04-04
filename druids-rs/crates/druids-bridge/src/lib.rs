//! Druids bridge library.

use thiserror::Error;

/// Bridge error types.
#[derive(Debug, Error)]
pub enum BridgeError {
    /// Connection error.
    #[error("connection error: {0}")]
    Connection(String),

    /// Protocol error.
    #[error("protocol error: {0}")]
    Protocol(String),
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_error_types() {
        let err = BridgeError::Connection("test".to_string());
        assert_eq!(err.to_string(), "connection error: test");
    }
}
