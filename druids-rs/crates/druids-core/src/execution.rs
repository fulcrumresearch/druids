//! Execution record types.

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use uuid::Uuid;

use crate::common::{ExecutionId, Slug, UserId};

/// Execution state.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ExecutionState {
    /// Execution is starting up.
    Starting,
    /// Execution is running.
    Running,
    /// Execution has stopped.
    Stopped,
    /// Execution completed successfully.
    Completed,
    /// Execution failed with an error.
    Failed,
}

impl ExecutionState {
    /// Check if this is a terminal state.
    pub fn is_terminal(&self) -> bool {
        matches!(
            self,
            ExecutionState::Stopped | ExecutionState::Completed | ExecutionState::Failed
        )
    }

    /// Check if this is an active state.
    pub fn is_active(&self) -> bool {
        matches!(self, ExecutionState::Starting | ExecutionState::Running)
    }
}

impl std::fmt::Display for ExecutionState {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        let s = match self {
            ExecutionState::Starting => "starting",
            ExecutionState::Running => "running",
            ExecutionState::Stopped => "stopped",
            ExecutionState::Completed => "completed",
            ExecutionState::Failed => "failed",
        };
        write!(f, "{}", s)
    }
}

impl std::str::FromStr for ExecutionState {
    type Err = String;

    fn from_str(s: &str) -> Result<Self, Self::Err> {
        match s {
            "starting" => Ok(ExecutionState::Starting),
            "running" => Ok(ExecutionState::Running),
            "stopped" => Ok(ExecutionState::Stopped),
            "completed" => Ok(ExecutionState::Completed),
            "failed" => Ok(ExecutionState::Failed),
            _ => Err(format!("unknown execution state: {}", s)),
        }
    }
}

/// Execution metadata (JSON blob).
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
#[serde(transparent)]
pub struct ExecutionMetadata(pub HashMap<String, serde_json::Value>);

impl ExecutionMetadata {
    /// Create new empty metadata.
    pub fn new() -> Self {
        Self(HashMap::new())
    }

    /// Get a value by key.
    pub fn get(&self, key: &str) -> Option<&serde_json::Value> {
        self.0.get(key)
    }

    /// Set a value by key.
    pub fn set(&mut self, key: impl Into<String>, value: serde_json::Value) {
        self.0.insert(key.into(), value);
    }

    /// Remove a value by key.
    pub fn remove(&mut self, key: &str) -> Option<serde_json::Value> {
        self.0.remove(key)
    }

    /// Check if metadata is empty.
    pub fn is_empty(&self) -> bool {
        self.0.is_empty()
    }
}

/// Execution record (matches Python ExecutionRecord).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ExecutionRecord {
    /// Unique ID.
    pub id: ExecutionId,
    /// Human-readable slug.
    pub slug: Slug,
    /// User who created this execution.
    pub user_id: UserId,
    /// Execution spec (task description).
    pub spec: String,
    /// Repository full name (e.g., "owner/repo").
    #[serde(skip_serializing_if = "Option::is_none")]
    pub repo_full_name: Option<String>,
    /// Arbitrary metadata.
    #[serde(default)]
    pub metadata: ExecutionMetadata,
    /// Current status.
    pub status: ExecutionState,
    /// When the execution started.
    pub started_at: DateTime<Utc>,
    /// When the execution stopped (if stopped).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub stopped_at: Option<DateTime<Utc>>,
    /// When the execution completed (if completed).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub completed_at: Option<DateTime<Utc>>,
    /// Git branch name.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub branch_name: Option<String>,
    /// Pull request number.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub pr_number: Option<i32>,
    /// Pull request URL.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub pr_url: Option<String>,
    /// Error message (if failed).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
    /// Agent topology (list of agent names).
    #[serde(default)]
    pub agents: Vec<String>,
    /// Edge topology (list of edges).
    #[serde(default)]
    pub edges: Vec<ExecutionEdge>,
    /// Program ID that was executed.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub program_id: Option<Uuid>,
    /// Cumulative input tokens.
    #[serde(default)]
    pub input_tokens: i64,
    /// Cumulative output tokens.
    #[serde(default)]
    pub output_tokens: i64,
    /// Cumulative cache read input tokens.
    #[serde(default)]
    pub cache_read_input_tokens: i64,
    /// Cumulative cache creation input tokens.
    #[serde(default)]
    pub cache_creation_input_tokens: i64,
}

/// Edge in the agent topology graph.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ExecutionEdge {
    /// Source agent name.
    pub from: String,
    /// Destination agent name.
    pub to: String,
}

impl ExecutionRecord {
    /// Create a new execution record builder.
    pub fn builder() -> ExecutionRecordBuilder {
        ExecutionRecordBuilder::default()
    }
}

/// Builder for ExecutionRecord.
#[derive(Debug, Default)]
pub struct ExecutionRecordBuilder {
    id: Option<ExecutionId>,
    slug: Option<Slug>,
    user_id: Option<UserId>,
    spec: Option<String>,
    repo_full_name: Option<String>,
    metadata: ExecutionMetadata,
    status: Option<ExecutionState>,
    started_at: Option<DateTime<Utc>>,
    branch_name: Option<String>,
    program_id: Option<Uuid>,
}

impl ExecutionRecordBuilder {
    /// Set the execution ID.
    pub fn id(mut self, id: ExecutionId) -> Self {
        self.id = Some(id);
        self
    }

    /// Set the slug.
    pub fn slug(mut self, slug: impl Into<Slug>) -> Self {
        self.slug = Some(slug.into());
        self
    }

    /// Set the user ID.
    pub fn user_id(mut self, user_id: UserId) -> Self {
        self.user_id = Some(user_id);
        self
    }

    /// Set the spec.
    pub fn spec(mut self, spec: impl Into<String>) -> Self {
        self.spec = Some(spec.into());
        self
    }

    /// Set the repository full name.
    pub fn repo_full_name(mut self, repo: impl Into<String>) -> Self {
        self.repo_full_name = Some(repo.into());
        self
    }

    /// Set the metadata.
    pub fn metadata(mut self, metadata: ExecutionMetadata) -> Self {
        self.metadata = metadata;
        self
    }

    /// Set the status.
    pub fn status(mut self, status: ExecutionState) -> Self {
        self.status = Some(status);
        self
    }

    /// Set the started_at timestamp.
    pub fn started_at(mut self, started_at: DateTime<Utc>) -> Self {
        self.started_at = Some(started_at);
        self
    }

    /// Set the branch name.
    pub fn branch_name(mut self, branch: impl Into<String>) -> Self {
        self.branch_name = Some(branch.into());
        self
    }

    /// Set the program ID.
    pub fn program_id(mut self, program_id: Uuid) -> Self {
        self.program_id = Some(program_id);
        self
    }

    /// Build the execution record.
    pub fn build(self) -> Result<ExecutionRecord, String> {
        let id = self.id.unwrap_or_default();
        let slug = self.slug.ok_or("slug is required")?;
        let user_id = self.user_id.ok_or("user_id is required")?;
        let spec = self.spec.ok_or("spec is required")?;
        let status = self.status.unwrap_or(ExecutionState::Starting);
        let started_at = self.started_at.unwrap_or_else(Utc::now);

        Ok(ExecutionRecord {
            id,
            slug,
            user_id,
            spec,
            repo_full_name: self.repo_full_name,
            metadata: self.metadata,
            status,
            started_at,
            stopped_at: None,
            completed_at: None,
            branch_name: self.branch_name,
            pr_number: None,
            pr_url: None,
            error: None,
            agents: Vec::new(),
            edges: Vec::new(),
            program_id: self.program_id,
            input_tokens: 0,
            output_tokens: 0,
            cache_read_input_tokens: 0,
            cache_creation_input_tokens: 0,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_execution_state() {
        assert!(ExecutionState::Starting.is_active());
        assert!(ExecutionState::Running.is_active());
        assert!(!ExecutionState::Stopped.is_active());

        assert!(ExecutionState::Stopped.is_terminal());
        assert!(ExecutionState::Completed.is_terminal());
        assert!(ExecutionState::Failed.is_terminal());
        assert!(!ExecutionState::Starting.is_terminal());
    }

    #[test]
    fn test_execution_state_from_str() {
        use std::str::FromStr;
        assert_eq!(
            ExecutionState::from_str("starting").unwrap(),
            ExecutionState::Starting
        );
        assert_eq!(
            ExecutionState::from_str("running").unwrap(),
            ExecutionState::Running
        );
        assert!(ExecutionState::from_str("invalid").is_err());
    }

    #[test]
    fn test_execution_metadata() {
        let mut meta = ExecutionMetadata::new();
        assert!(meta.is_empty());

        meta.set("key1", serde_json::json!("value1"));
        assert!(!meta.is_empty());
        assert_eq!(meta.get("key1"), Some(&serde_json::json!("value1")));

        meta.remove("key1");
        assert!(meta.is_empty());
    }

    #[test]
    fn test_execution_record_builder() {
        let user_id = UserId::new();
        let record = ExecutionRecord::builder()
            .slug("test-task")
            .user_id(user_id)
            .spec("test spec")
            .status(ExecutionState::Running)
            .build()
            .unwrap();

        assert_eq!(record.slug.as_str(), "test-task");
        assert_eq!(record.user_id, user_id);
        assert_eq!(record.spec, "test spec");
        assert_eq!(record.status, ExecutionState::Running);
    }

    #[test]
    fn test_execution_record_serialization() {
        let user_id = UserId::new();
        let record = ExecutionRecord::builder()
            .slug("test-task")
            .user_id(user_id)
            .spec("test spec")
            .build()
            .unwrap();

        let json = serde_json::to_string(&record).unwrap();
        let deserialized: ExecutionRecord = serde_json::from_str(&json).unwrap();

        assert_eq!(record.slug, deserialized.slug);
        assert_eq!(record.user_id, deserialized.user_id);
        assert_eq!(record.spec, deserialized.spec);
    }
}
