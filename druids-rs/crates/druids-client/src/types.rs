//! Request and response types for the Druids API.

use serde::{Deserialize, Serialize};
use std::collections::HashMap;

// ============================================================================
// Execution Types
// ============================================================================

/// Request to create a new execution.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CreateExecutionRequest {
    /// Program source code.
    pub program_source: String,

    /// Devbox name to use for execution.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub devbox_name: Option<String>,

    /// Repository full name (used to find devbox if devbox_name not set).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub repo_full_name: Option<String>,

    /// Git branch to checkout.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub git_branch: Option<String>,

    /// Program arguments.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub args: Option<HashMap<String, String>>,

    /// Time-to-live in seconds (0 = use server default).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub ttl: Option<i32>,

    /// Files to write to sandbox before program runs (path -> content).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub files: Option<HashMap<String, String>>,
}

/// Response from creating an execution.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CreateExecutionResponse {
    /// Execution slug.
    pub execution_slug: String,

    /// Execution ID (UUID).
    pub execution_id: String,
}

/// Request to update an execution.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct UpdateExecutionRequest {
    /// New status (completed, failed, stopped).
    pub status: String,

    /// Result value (for completed executions).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub result: Option<serde_json::Value>,

    /// Reason (for failed executions).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub reason: Option<String>,
}

/// Execution details.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Execution {
    /// Execution ID (UUID).
    pub execution_id: String,

    /// Execution slug.
    pub execution_slug: String,

    /// Task specification.
    pub spec: String,

    /// Repository full name.
    pub repo_full_name: String,

    /// Execution status.
    pub status: String,

    /// Error message if failed.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,

    /// Metadata.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub metadata: Option<HashMap<String, serde_json::Value>>,

    /// Git branch name.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub branch_name: Option<String>,

    /// Pull request URL.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub pr_url: Option<String>,

    /// Program ID.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub program_id: Option<String>,

    /// Started at timestamp.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub started_at: Option<String>,

    /// Stopped at timestamp.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub stopped_at: Option<String>,

    /// Agent names.
    pub agents: Vec<String>,

    /// Exposed services.
    #[serde(default)]
    pub exposed_services: Vec<ExposedService>,

    /// Client events.
    #[serde(default)]
    pub client_events: Vec<serde_json::Value>,

    /// Execution edges.
    #[serde(default)]
    pub edges: Vec<ExecutionEdge>,
}

/// Exposed service information.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ExposedService {
    /// Service name.
    pub name: String,

    /// Local port.
    pub port: u16,

    /// Public URL.
    pub url: String,
}

/// Execution edge (agent connection).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ExecutionEdge {
    /// Source agent.
    pub from: String,

    /// Destination agent.
    pub to: String,
}

/// List of executions.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ListExecutionsResponse {
    /// Executions.
    pub executions: Vec<ExecutionSummary>,
}

/// Execution summary.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ExecutionSummary {
    /// Execution ID.
    pub id: String,

    /// Execution slug.
    pub slug: String,

    /// Task specification (truncated).
    pub spec: String,

    /// Repository full name.
    pub repo_full_name: String,

    /// Status.
    pub status: String,

    /// Error message if failed.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,

    /// Metadata.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub metadata: Option<HashMap<String, serde_json::Value>>,

    /// Git branch name.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub branch_name: Option<String>,

    /// Pull request URL.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub pr_url: Option<String>,

    /// Program ID.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub program_id: Option<String>,

    /// Started at timestamp.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub started_at: Option<String>,
}

// ============================================================================
// Devbox Types
// ============================================================================

/// Request to start devbox setup.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SetupStartRequest {
    /// Devbox name.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub name: Option<String>,

    /// Repository full name.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub repo_full_name: Option<String>,

    /// Make devbox public.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub public: Option<bool>,

    /// Number of vCPUs.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub vcpus: Option<i32>,

    /// Memory in MB.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub memory_mb: Option<i32>,

    /// Disk size in MB.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub disk_mb: Option<i32>,
}

/// Request to finish devbox setup.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SetupFinishRequest {
    /// Devbox name.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub name: Option<String>,

    /// Repository full name.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub repo_full_name: Option<String>,
}

/// List of devboxes.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ListDevboxesResponse {
    /// Devboxes.
    pub devboxes: Vec<DevboxSummary>,
}

/// Devbox summary.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DevboxSummary {
    /// Repository full name.
    pub repo_full_name: String,

    /// Devbox name.
    pub name: String,

    /// Created at timestamp.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub created_at: Option<String>,
}

// ============================================================================
// Secret Types
// ============================================================================

/// Request to set secrets.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SetSecretsRequest {
    /// Devbox name.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub devbox_name: Option<String>,

    /// Repository full name.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub repo_full_name: Option<String>,

    /// Secrets to set (key -> value).
    pub secrets: HashMap<String, String>,
}

/// Response from setting secrets.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SetSecretsResponse {
    /// Status.
    pub status: String,

    /// Number of secrets set.
    pub count: usize,
}

/// List of secrets.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ListSecretsResponse {
    /// Secret names and metadata.
    pub secrets: Vec<SecretInfo>,
}

/// Secret information.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SecretInfo {
    /// Secret name.
    pub name: String,

    /// Last updated timestamp.
    pub updated_at: String,
}

/// Request to delete a secret.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DeleteSecretRequest {
    /// Devbox name.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub devbox_name: Option<String>,

    /// Repository full name.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub repo_full_name: Option<String>,

    /// Secret name to delete.
    pub name: String,
}

// ============================================================================
// Chat/Message Types
// ============================================================================

/// Request to send a chat message to an agent.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ChatMessageRequest {
    /// Message text.
    pub text: String,
}

/// Response from sending a chat message.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ChatMessageResponse {
    /// Status.
    pub status: String,
}

// ============================================================================
// Activity Types
// ============================================================================

/// Execution activity response.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ExecutionActivityResponse {
    /// Execution slug.
    pub execution_slug: String,

    /// Agent names.
    pub agents: Vec<String>,

    /// Total event count.
    pub event_count: usize,

    /// Recent activity events.
    pub recent_activity: Vec<serde_json::Value>,
}

// ============================================================================
// Diff Types
// ============================================================================

/// Execution diff response.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ExecutionDiffResponse {
    /// Git diff output.
    pub diff: String,

    /// Execution ID.
    pub execution_id: String,

    /// Execution slug.
    pub execution_slug: String,
}

// ============================================================================
// SSH Types
// ============================================================================

/// SSH credentials response.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SshCredentialsResponse {
    /// SSH host.
    pub host: String,

    /// SSH port.
    pub port: u16,

    /// SSH username.
    pub username: String,

    /// SSH private key.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub private_key: Option<String>,

    /// SSH password.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub password: Option<String>,

    /// Execution slug.
    pub execution_slug: String,

    /// Agent name.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub agent: Option<String>,

    /// Backend type.
    pub backend: String,

    /// Session ID.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub session_id: Option<String>,
}

// ============================================================================
// Tool Types
// ============================================================================

/// List of tools response.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ListToolsResponse {
    /// Tool names.
    pub tools: Vec<String>,
}

/// Request to call a tool.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CallToolRequest {
    /// Tool arguments.
    pub args: HashMap<String, serde_json::Value>,
}

/// Response from calling a tool.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CallToolResponse {
    /// Tool result.
    pub result: serde_json::Value,
}
