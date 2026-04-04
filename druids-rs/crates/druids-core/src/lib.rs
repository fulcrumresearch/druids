//! # druids-core
//!
//! Core types and utilities for the Druids multi-agent orchestration system.
//!
//! This crate provides shared type definitions that are used across all Druids components,
//! including the server, client, runtime, and bridge.
//!
//! ## Features
//!
//! - **Execution types**: [`ExecutionRecord`], [`ExecutionState`], [`ExecutionMetadata`]
//! - **Agent types**: [`AgentInfo`], [`AgentState`], [`AgentConnection`], [`AgentType`]
//! - **Event types**: [`TraceEvent`] for execution trace logging
//! - **Common types**: [`ExecutionId`], [`UserId`], [`Slug`]
//! - **Error types**: [`ExecutionError`], [`AgentError`], [`ConfigError`]
//!
//! ## Example
//!
//! ```
//! use druids_core::{ExecutionRecord, ExecutionState, UserId, Slug};
//!
//! let user_id = UserId::new();
//! let record = ExecutionRecord::builder()
//!     .slug("my-task")
//!     .user_id(user_id)
//!     .spec("implement feature X")
//!     .status(ExecutionState::Running)
//!     .build()
//!     .unwrap();
//!
//! assert_eq!(record.slug.as_str(), "my-task");
//! assert_eq!(record.status, ExecutionState::Running);
//! ```

pub mod agent;
pub mod common;
pub mod error;
pub mod events;
pub mod execution;

// Re-export commonly used types at the crate root
pub use agent::{AgentConnection, AgentInfo, AgentState, AgentType};
pub use common::{timestamp, ExecutionId, Slug, UserId};
pub use error::{AgentError, ConfigError, ExecutionError};
pub use events::TraceEvent;
pub use execution::{ExecutionEdge, ExecutionMetadata, ExecutionRecord, ExecutionState};
