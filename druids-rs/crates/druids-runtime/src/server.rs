//! Runtime HTTP server for receiving tool calls and events from the main server.

use crate::context::ProgramContext;
use crate::events::EventData;
use axum::{
    extract::{Query, State},
    http::StatusCode,
    response::{IntoResponse, Json, Response},
    routing::{get, post},
    Router,
};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::HashMap;
use std::net::SocketAddr;
use std::sync::Arc;
use tokio::sync::{RwLock, oneshot};

const RUNTIME_PORT: u16 = 9100;

/// Shared state for the runtime server
#[derive(Clone)]
pub struct RuntimeState {
    context: Arc<RwLock<ProgramContext>>,
}

impl RuntimeState {
    pub fn new(context: Arc<RwLock<ProgramContext>>) -> Self {
        Self { context }
    }
}

/// Request to call a tool
#[derive(Debug, Deserialize)]
struct CallToolRequest {
    agent_name: String,
    tool_name: String,
    #[serde(default)]
    args: HashMap<String, Value>,
}

/// Request to handle a client event
#[derive(Debug, Deserialize)]
struct HandleEventRequest {
    event: String,
    #[serde(default)]
    data: HashMap<String, Value>,
}

/// Query parameters for tool listing
#[derive(Debug, Deserialize)]
struct ListToolsQuery {
    agent: Option<String>,
}

/// Response for tool listing
#[derive(Debug, Serialize)]
struct ListToolsResponse {
    tools: Vec<String>,
}

/// Generic success response
#[derive(Debug, Serialize)]
struct SuccessResponse {
    result: Value,
}

/// Generic error response
#[derive(Debug, Serialize)]
struct ErrorResponse {
    error: String,
}

/// Call a tool handler
async fn call_tool(
    State(state): State<RuntimeState>,
    Json(req): Json<CallToolRequest>,
) -> Response {
    tracing::debug!(
        agent = %req.agent_name,
        tool = %req.tool_name,
        "calling tool"
    );

    // Built-in tools - handle without holding lock across awaits
    match req.tool_name.as_str() {
        "message" => {
            // Extract data and drop lock before HTTP call
            let (receiver, message, result) = {
                let ctx = state.context.read().await;

                let receiver = req
                    .args
                    .get("receiver")
                    .and_then(|v| v.as_str())
                    .unwrap_or("");
                let message = req
                    .args
                    .get("message")
                    .and_then(|v| v.as_str())
                    .unwrap_or("");

                if !ctx.agents().contains_key(receiver) {
                    let available: Vec<_> = ctx.agents().keys().cloned().collect();
                    let result = Value::String(format!(
                        "Agent '{}' not found. Available: {}",
                        receiver,
                        available.join(", ")
                    ));
                    return (StatusCode::OK, Json(SuccessResponse { result })).into_response();
                }

                if !ctx.is_connected(&req.agent_name, receiver) {
                    let reachable: Vec<_> = ctx
                        .agents()
                        .keys()
                        .filter(|&n| n != &req.agent_name && ctx.is_connected(&req.agent_name, n))
                        .cloned()
                        .collect();
                    let result = Value::String(format!(
                        "Agent '{}' not found. Available: {}",
                        receiver,
                        reachable.join(", ")
                    ));
                    return (StatusCode::OK, Json(SuccessResponse { result })).into_response();
                }

                (receiver.to_string(), message.to_string(), ())
            }; // Lock dropped here

            // Now call send_message without holding lock
            let ctx = state.context.read().await;
            match ctx.send_message(&receiver, &message).await {
                Ok(_) => (
                    StatusCode::OK,
                    Json(SuccessResponse {
                        result: Value::String(format!("Message sent to {}", receiver)),
                    }),
                )
                    .into_response(),
                Err(e) => (
                    StatusCode::INTERNAL_SERVER_ERROR,
                    Json(ErrorResponse {
                        error: e.to_string(),
                    }),
                )
                    .into_response(),
            }
        }
        "list_agents" => {
            let ctx = state.context.read().await;
            let reachable: Vec<_> = ctx
                .agents()
                .keys()
                .filter(|&n| n != &req.agent_name && ctx.is_connected(&req.agent_name, n))
                .cloned()
                .collect();
            let result = if reachable.is_empty() {
                "No reachable agents.".to_string()
            } else {
                reachable.join(", ")
            };
            (
                StatusCode::OK,
                Json(SuccessResponse {
                    result: Value::String(result),
                }),
            )
                .into_response()
        }
        _ => {
            // Custom tool - get handler without holding lock across await
            let handler = {
                let ctx = state.context.read().await;
                let agent = match ctx.agents().get(&req.agent_name) {
                    Some(a) => a,
                    None => {
                        return (
                            StatusCode::NOT_FOUND,
                            Json(ErrorResponse {
                                error: format!("agent {} not found", req.agent_name),
                            }),
                        )
                            .into_response();
                    }
                };
                agent.get_handler(&req.tool_name).await
            }; // Lock is dropped here

            match handler {
                Some(handler) => {
                    let event_data = EventData::Object(req.args);
                    handler(event_data).await;
                    (
                        StatusCode::OK,
                        Json(SuccessResponse {
                            result: Value::Null,
                        }),
                    )
                        .into_response()
                }
                None => (
                    StatusCode::NOT_FOUND,
                    Json(ErrorResponse {
                        error: format!("no handler for tool '{}'", req.tool_name),
                    }),
                )
                    .into_response(),
            }
        }
    }
}

/// Handle a client event
async fn handle_event(
    State(state): State<RuntimeState>,
    Json(req): Json<HandleEventRequest>,
) -> Response {
    tracing::debug!(event = %req.event, "handling client event");

    let event_data = EventData::Object(req.data);

    // Get handlers without holding lock across awaits
    let handlers = {
        let ctx = state.context.read().await;
        ctx.client_handlers().get_handlers(&req.event)
    }; // Lock is dropped here

    // Now dispatch handlers without holding lock
    for handler in handlers {
        handler(event_data.clone()).await;
    }

    (
        StatusCode::OK,
        Json(SuccessResponse {
            result: Value::Null,
        }),
    )
        .into_response()
}

/// List tools for an agent
async fn list_tools(
    State(state): State<RuntimeState>,
    Query(query): Query<ListToolsQuery>,
) -> Response {
    let ctx = state.context.read().await;

    let agent_name = match query.agent {
        Some(name) => name,
        None => {
            return (
                StatusCode::OK,
                Json(ListToolsResponse { tools: vec![] }),
            )
                .into_response()
        }
    };

    let agent = match ctx.agents().get(&agent_name) {
        Some(a) => a,
        None => {
            return (
                StatusCode::OK,
                Json(ListToolsResponse { tools: vec![] }),
            )
                .into_response()
        }
    };

    let tools = agent.list_tools().await;

    (StatusCode::OK, Json(ListToolsResponse { tools })).into_response()
}

/// Health check endpoint
async fn health() -> Response {
    (
        StatusCode::OK,
        Json(serde_json::json!({"status": "ok"})),
    )
        .into_response()
}

/// Build the runtime server router
fn build_router(state: RuntimeState) -> Router {
    Router::new()
        .route("/call", post(call_tool))
        .route("/event", post(handle_event))
        .route("/tools", get(list_tools))
        .route("/health", get(health))
        .with_state(state)
}

/// Start the runtime HTTP server
///
/// Returns a channel that will be signaled when the server is ready to accept connections.
pub async fn start_server(
    context: Arc<RwLock<ProgramContext>>,
    ready_tx: oneshot::Sender<()>,
) -> anyhow::Result<()> {
    let state = RuntimeState::new(context);
    let app = build_router(state);

    let addr = SocketAddr::from(([127, 0, 0, 1], RUNTIME_PORT));

    let listener = tokio::net::TcpListener::bind(addr).await?;
    tracing::info!("runtime HTTP server listening on {}", addr);

    // Signal that server is ready
    let _ = ready_tx.send(());

    axum::serve(listener, app).await?;

    Ok(())
}
