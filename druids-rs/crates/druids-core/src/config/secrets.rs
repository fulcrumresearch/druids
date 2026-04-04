//! Secret handling utilities
//!
//! This module provides types for handling sensitive data (API keys, tokens, etc.)
//! that should never be logged or accidentally exposed.

use serde::{Deserialize, Deserializer, Serialize, Serializer};
use std::fmt;

/// A secret value that is never logged or displayed
#[derive(Clone)]
pub struct Secret<T>(T);

impl<T> Secret<T> {
    /// Create a new secret
    pub fn new(value: T) -> Self {
        Secret(value)
    }

    /// Get the underlying value
    ///
    /// # Security
    /// Use with caution. The exposed value should not be logged or displayed.
    pub fn expose(&self) -> &T {
        &self.0
    }

    /// Consume and return the underlying value
    pub fn into_inner(self) -> T {
        self.0
    }
}

impl<T: Default> Default for Secret<T> {
    fn default() -> Self {
        Secret(T::default())
    }
}

// Never display the actual secret value
impl<T> fmt::Debug for Secret<T> {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("[REDACTED]")
    }
}

impl<T> fmt::Display for Secret<T> {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("[REDACTED]")
    }
}

// Serialize the actual value (for config files)
impl<T: Serialize> Serialize for Secret<T> {
    fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        self.0.serialize(serializer)
    }
}

// Deserialize the actual value
impl<'de, T: Deserialize<'de>> Deserialize<'de> for Secret<T> {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        T::deserialize(deserializer).map(Secret)
    }
}

impl<T> From<T> for Secret<T> {
    fn from(value: T) -> Self {
        Secret(value)
    }
}

/// Type alias for secret strings (API keys, tokens, etc.)
pub type SecretString = Secret<String>;

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_secret_debug() {
        let secret = SecretString::new("my-secret-key".to_string());
        let debug_output = format!("{:?}", secret);
        assert_eq!(debug_output, "[REDACTED]");
        assert!(!debug_output.contains("my-secret-key"));
    }

    #[test]
    fn test_secret_display() {
        let secret = SecretString::new("my-secret-key".to_string());
        let display_output = format!("{}", secret);
        assert_eq!(display_output, "[REDACTED]");
        assert!(!display_output.contains("my-secret-key"));
    }

    #[test]
    fn test_secret_expose() {
        let secret = SecretString::new("my-secret-key".to_string());
        assert_eq!(secret.expose(), "my-secret-key");
    }

    #[test]
    fn test_secret_serialization() {
        let secret = SecretString::new("my-secret-key".to_string());
        let json = serde_json::to_string(&secret).unwrap();
        assert_eq!(json, r#""my-secret-key""#);
    }

    #[test]
    fn test_secret_deserialization() {
        let secret: SecretString = serde_json::from_str(r#""my-secret-key""#).unwrap();
        assert_eq!(secret.expose(), "my-secret-key");
    }
}
