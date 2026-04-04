//! Druids Runtime SDK
//!
//! This crate provides the runtime SDK for writing Druids programs.
//! Programs use this SDK to spawn agents, register event handlers,
//! and coordinate multi-agent workflows.

pub mod agent;
pub mod context;
pub mod error;
pub mod events;
pub mod server;

pub use agent::AgentHandle;
pub use context::ProgramContext;
pub use error::{Result, RuntimeError};
pub use events::{EventData, EventHandler};

use serde::Deserialize;
use std::path::Path;
use std::sync::Arc;
use tokio::sync::{RwLock, oneshot};

/// Runtime configuration loaded from file
#[derive(Debug, Deserialize)]
pub struct RuntimeConfig {
    pub slug: String,
    pub base_url: String,
    pub token: String,
    pub program_source: String,
    pub repo_full_name: Option<String>,
    pub spec: Option<String>,
    #[serde(default)]
    pub args: serde_json::Value,
}

/// Load runtime configuration from a JSON file
pub async fn load_config(path: impl AsRef<Path>) -> Result<RuntimeConfig> {
    let contents = tokio::fs::read_to_string(path).await?;
    let config: RuntimeConfig = serde_json::from_str(&contents)?;
    Ok(config)
}

/// Program trait that user programs implement
#[async_trait::async_trait]
pub trait Program: Send + Sync {
    async fn run(&mut self, ctx: Arc<RwLock<ProgramContext>>) -> Result<()>;
}

/// Run a program with the runtime server
pub async fn run_program<P: Program>(mut program: P, config: RuntimeConfig) -> Result<()> {
    // Create program context
    let mut ctx = ProgramContext::new(&config.slug, &config.base_url, &config.token);

    if let Some(repo) = config.repo_full_name {
        ctx = ctx.with_repo(repo);
    }
    if let Some(spec) = config.spec {
        ctx = ctx.with_spec(spec);
    }

    let ctx_arc = Arc::new(RwLock::new(ctx));

    // Create channel for server readiness signaling
    let (ready_tx, ready_rx) = oneshot::channel();

    // Start runtime server in background
    let server_ctx = ctx_arc.clone();
    let server_task = tokio::spawn(async move {
        if let Err(e) = server::start_server(server_ctx, ready_tx).await {
            tracing::error!("runtime server error: {}", e);
        }
    });

    // Wait for server to be ready
    ready_rx
        .await
        .map_err(|_| RuntimeError::other("server ready channel closed"))?;

    tracing::info!("runtime server is ready");

    // Run the program
    let result = program.run(ctx_arc.clone()).await;

    // TODO: Send ready signal and topology to server

    // Wait for server task
    let _ = server_task.await;

    result
}
