//! Druids core types and utilities.
//!
//! This crate contains shared types used across all Druids components.

use serde::{Deserialize, Serialize};
use std::fmt;
use uuid::Uuid;

pub mod config;
pub mod error;

pub use config::SandboxType;
pub use error::{ConfigError, Error, Result};

/// Execution slug identifier.
#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct ExecutionSlug(String);

impl ExecutionSlug {
    /// Creates a new execution slug.
    pub fn new(slug: impl Into<String>) -> Self {
        Self(slug.into())
    }

    /// Returns the slug as a string slice.
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl fmt::Display for ExecutionSlug {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}", self.0)
    }
}

/// User identifier.
#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct UserId(Uuid);

impl UserId {
    /// Creates a new user ID.
    pub fn new() -> Self {
        Self(Uuid::new_v4())
    }

    /// Creates a user ID from a UUID.
    pub fn from_uuid(id: Uuid) -> Self {
        Self(id)
    }
}

impl Default for UserId {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_execution_slug() {
        let slug = ExecutionSlug::new("test-execution");
        assert_eq!(slug.as_str(), "test-execution");
        assert_eq!(slug.to_string(), "test-execution");
    }

    #[test]
    fn test_user_id() {
        let user_id = UserId::new();
        let json = serde_json::to_string(&user_id).unwrap();
        let deserialized: UserId = serde_json::from_str(&json).unwrap();
        assert_eq!(user_id, deserialized);
    }
}
