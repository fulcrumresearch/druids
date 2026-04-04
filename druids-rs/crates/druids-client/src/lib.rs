//! # druids-client
//!
//! HTTP client library for the Druids API.
//!
//! This crate provides a reqwest-based client for communicating with the Druids server,
//! including support for:
//!
//! - Execution management (create, list, get, stop, send messages)
//! - Devbox management (create, list, snapshot)
//! - Secret management (set, list, delete)
//! - SSE streaming for execution events
//! - Retry logic with exponential backoff
//! - Authentication via JWT tokens
//!
//! ## Example
//!
//! ```no_run
//! use druids_client::{DruidsClient, config::ClientConfig};
//!
//! #[tokio::main]
//! async fn main() -> Result<(), Box<dyn std::error::Error>> {
//!     // Create client from default config
//!     let client = DruidsClient::from_default_config()?;
//!
//!     // List executions
//!     let executions = client.list_executions(true).await?;
//!     println!("Active executions: {}", executions.len());
//!
//!     Ok(())
//! }
//! ```

pub mod client;
pub mod config;
pub mod error;
pub mod retry;
pub mod streaming;
pub mod types;

#[cfg(test)]
mod tests;

// Re-export commonly used types (explicit list, no wildcards)
pub use client::DruidsClient;
pub use config::ClientConfig;
pub use error::{ClientError, Result};
pub use streaming::{stream_execution, ActivityEvent};
pub use types::{
    CallToolRequest, CallToolResponse, ChatMessageRequest, ChatMessageResponse,
    CreateExecutionRequest, CreateExecutionResponse, DevboxSummary, Execution,
    ExecutionActivityResponse, ExecutionDiffResponse, ExecutionSummary, ListDevboxesResponse,
    ListSecretsResponse, ListToolsResponse, SecretInfo, SetSecretsRequest, SetSecretsResponse,
    SetupFinishRequest, SetupStartRequest, SshCredentialsResponse, UpdateExecutionRequest,
};
