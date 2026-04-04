//! Axum app and request handlers.

use crate::process::{AcpConfig, ProcessHandle};
use axum::{
    extract::State,
    http::StatusCode,
    response::Json,
    routing::{get, post},
    Router,
};
use serde::{Deserialize, Serialize};
use std::{collections::VecDeque, sync::Arc};
use tokio::sync::{mpsc, RwLock};

/// Shared state across all handlers.
#[derive(Clone)]
pub struct BridgeState {
    pub process: Arc<RwLock<Option<ProcessHandle>>>,
    pub stdout_buffer: Arc<RwLock<VecDeque<String>>>,
    pub stdin_sender: Arc<RwLock<Option<mpsc::UnboundedSender<String>>>>,
}

impl Default for BridgeState {
    fn default() -> Self {
        Self {
            process: Arc::new(RwLock::new(None)),
            stdout_buffer: Arc::new(RwLock::new(VecDeque::new())),
            stdin_sender: Arc::new(RwLock::new(None)),
        }
    }
}

/// Request to start ACP process.
#[derive(Debug, Deserialize)]
pub struct StartRequest {
    pub command: String,
    #[serde(default)]
    pub args: Vec<String>,
}

/// Response from start endpoint.
#[derive(Debug, Serialize)]
pub struct StartResponse {
    pub status: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub pid: Option<u32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
}

/// Response from stop endpoint.
#[derive(Debug, Serialize)]
pub struct StopResponse {
    pub status: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
}

/// Response from status endpoint.
#[derive(Debug, Serialize)]
pub struct StatusResponse {
    pub status: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub pid: Option<u32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub uptime_seconds: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub buffer_size: Option<usize>,
}

/// Request to write to stdin.
#[derive(Debug, Deserialize)]
pub struct StdinRequest {
    pub data: String,
}

/// Response from stdin endpoint.
#[derive(Debug, Serialize)]
pub struct StdinResponse {
    pub status: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
}

/// Create the Axum router with all endpoints.
pub fn create_app() -> Router {
    let state = BridgeState::default();

    Router::new()
        .route("/start", post(start_process))
        .route("/stop", post(stop_process))
        .route("/status", get(process_status))
        .route("/stdin", post(write_stdin))
        .with_state(state)
}

/// POST /start - Start ACP subprocess.
async fn start_process(
    State(state): State<BridgeState>,
    Json(req): Json<StartRequest>,
) -> Result<Json<StartResponse>, StatusCode> {
    // Check if a process is already running
    let proc_guard = state.process.read().await;
    if proc_guard.is_some() {
        drop(proc_guard);
        return Ok(Json(StartResponse {
            status: "error".to_string(),
            pid: None,
            error: Some("Agent already running".to_string()),
        }));
    }
    drop(proc_guard);

    // Clear buffers
    state.stdout_buffer.write().await.clear();

    // Build config
    let config = AcpConfig {
        command: req.command,
        args: req.args,
    };

    // Spawn the process
    match crate::process::spawn_acp_process(config, state.clone()).await {
        Ok((handle, stdin_sender)) => {
            let pid = handle.pid;
            *state.stdin_sender.write().await = Some(stdin_sender);
            *state.process.write().await = Some(handle);

            Ok(Json(StartResponse {
                status: "started".to_string(),
                pid: Some(pid),
                error: None,
            }))
        }
        Err(e) => {
            tracing::error!("Failed to start process: {}", e);
            Ok(Json(StartResponse {
                status: "error".to_string(),
                pid: None,
                error: Some(format!("Failed to start: {}", e)),
            }))
        }
    }
}

/// POST /stop - Terminate process.
async fn stop_process(
    State(state): State<BridgeState>,
) -> Result<Json<StopResponse>, StatusCode> {
    let mut proc_guard = state.process.write().await;

    if let Some(mut handle) = proc_guard.take() {
        // Drop stdin sender to close the channel
        *state.stdin_sender.write().await = None;

        // Terminate the process
        if let Err(e) = handle.child.kill().await {
            tracing::warn!("Failed to kill process: {}", e);
        }

        // Cancel background tasks
        if let Some(task) = handle.stdout_task {
            task.abort();
        }
        if let Some(task) = handle.stdin_task {
            task.abort();
        }
        if let Some(task) = handle.stderr_task {
            task.abort();
        }

        Ok(Json(StopResponse {
            status: "stopped".to_string(),
            error: None,
        }))
    } else {
        Ok(Json(StopResponse {
            status: "error".to_string(),
            error: Some("No agent running".to_string()),
        }))
    }
}

/// GET /status - Process health check.
async fn process_status(
    State(state): State<BridgeState>,
) -> Result<Json<StatusResponse>, StatusCode> {
    let proc_guard = state.process.read().await;

    if let Some(handle) = proc_guard.as_ref() {
        let uptime = handle.started_at.elapsed().as_secs();
        let buffer_size = state.stdout_buffer.read().await.len();

        Ok(Json(StatusResponse {
            status: "running".to_string(),
            pid: Some(handle.pid),
            uptime_seconds: Some(uptime),
            buffer_size: Some(buffer_size),
        }))
    } else {
        Ok(Json(StatusResponse {
            status: "not_running".to_string(),
            pid: None,
            uptime_seconds: None,
            buffer_size: None,
        }))
    }
}

/// POST /stdin - Write data to process stdin.
async fn write_stdin(
    State(state): State<BridgeState>,
    Json(req): Json<StdinRequest>,
) -> Result<Json<StdinResponse>, StatusCode> {
    let sender_guard = state.stdin_sender.read().await;

    if let Some(sender) = sender_guard.as_ref() {
        match sender.send(req.data) {
            Ok(_) => Ok(Json(StdinResponse {
                status: "sent".to_string(),
                error: None,
            })),
            Err(e) => Ok(Json(StdinResponse {
                status: "error".to_string(),
                error: Some(format!("Failed to send: {}", e)),
            })),
        }
    } else {
        Ok(Json(StdinResponse {
            status: "error".to_string(),
            error: Some("No process running".to_string()),
        }))
    }
}
