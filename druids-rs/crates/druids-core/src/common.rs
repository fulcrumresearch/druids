//! Common types used across Druids components.

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use uuid::Uuid;

/// Execution identifier (UUID).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(transparent)]
pub struct ExecutionId(pub Uuid);

impl ExecutionId {
    /// Create a new execution ID.
    pub fn new() -> Self {
        Self(Uuid::new_v4())
    }

    /// Get the inner UUID.
    pub fn as_uuid(&self) -> &Uuid {
        &self.0
    }
}

impl Default for ExecutionId {
    fn default() -> Self {
        Self::new()
    }
}

impl From<Uuid> for ExecutionId {
    fn from(uuid: Uuid) -> Self {
        Self(uuid)
    }
}

impl std::fmt::Display for ExecutionId {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}", self.0)
    }
}

/// User identifier (UUID).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(transparent)]
pub struct UserId(pub Uuid);

impl UserId {
    /// Create a new user ID.
    pub fn new() -> Self {
        Self(Uuid::new_v4())
    }

    /// Get the inner UUID.
    pub fn as_uuid(&self) -> &Uuid {
        &self.0
    }
}

impl Default for UserId {
    fn default() -> Self {
        Self::new()
    }
}

impl From<Uuid> for UserId {
    fn from(uuid: Uuid) -> Self {
        Self(uuid)
    }
}

impl std::fmt::Display for UserId {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}", self.0)
    }
}

/// Execution slug (human-readable identifier).
#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(transparent)]
pub struct Slug(pub String);

impl Slug {
    /// Create a new slug from a string.
    pub fn new(s: impl Into<String>) -> Self {
        Self(s.into())
    }

    /// Get the inner string.
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl From<String> for Slug {
    fn from(s: String) -> Self {
        Self(s)
    }
}

impl From<&str> for Slug {
    fn from(s: &str) -> Self {
        Self(s.to_string())
    }
}

impl std::fmt::Display for Slug {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}", self.0)
    }
}

/// Timestamp utility functions.
pub mod timestamp {
    use super::*;

    /// Get the current UTC timestamp.
    pub fn now() -> DateTime<Utc> {
        Utc::now()
    }

    /// Parse an ISO 8601 timestamp.
    pub fn parse(s: &str) -> Result<DateTime<Utc>, chrono::ParseError> {
        DateTime::parse_from_rfc3339(s).map(|dt| dt.with_timezone(&Utc))
    }

    /// Format a timestamp as ISO 8601.
    pub fn format(dt: &DateTime<Utc>) -> String {
        dt.to_rfc3339()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_execution_id() {
        let id1 = ExecutionId::new();
        let id2 = ExecutionId::new();
        assert_ne!(id1, id2);

        let uuid = Uuid::new_v4();
        let id3 = ExecutionId::from(uuid);
        assert_eq!(id3.as_uuid(), &uuid);
    }

    #[test]
    fn test_user_id() {
        let id1 = UserId::new();
        let id2 = UserId::new();
        assert_ne!(id1, id2);

        let uuid = Uuid::new_v4();
        let id3 = UserId::from(uuid);
        assert_eq!(id3.as_uuid(), &uuid);
    }

    #[test]
    fn test_slug() {
        let slug1 = Slug::new("test-task");
        assert_eq!(slug1.as_str(), "test-task");

        let slug2 = Slug::from("another-task");
        assert_eq!(slug2.as_str(), "another-task");
    }

    #[test]
    fn test_timestamp() {
        let now = timestamp::now();
        let formatted = timestamp::format(&now);
        let parsed = timestamp::parse(&formatted).unwrap();

        // Allow for small precision differences
        let diff = (now - parsed).num_milliseconds().abs();
        assert!(diff < 1000);
    }
}
