//! Druids client library.

use thiserror::Error;

/// Client error types.
#[derive(Debug, Error)]
pub enum ClientError {
    /// HTTP request failed.
    #[error("request failed: {0}")]
    Request(#[from] reqwest::Error),

    /// Server returned an error.
    #[error("server error: {0}")]
    Server(String),
}

/// Druids API client.
pub struct Client {
    base_url: String,
    #[allow(dead_code)]
    client: reqwest::Client,
}

impl Client {
    /// Creates a new client.
    pub fn new(base_url: impl Into<String>) -> Self {
        Self {
            base_url: base_url.into(),
            client: reqwest::Client::new(),
        }
    }

    /// Returns the base URL.
    pub fn base_url(&self) -> &str {
        &self.base_url
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_client_creation() {
        let client = Client::new("http://localhost:8000");
        assert_eq!(client.base_url(), "http://localhost:8000");
    }
}
