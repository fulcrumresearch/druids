//! Execution trace event types.

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use std::collections::HashMap;

use crate::execution::ExecutionEdge;

/// A trace event (matches Python execution_trace.py).
///
/// Each event is serialized to JSONL format with a timestamp and type field.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum TraceEvent {
    /// Execution started.
    ExecutionStarted {
        /// Timestamp.
        ts: DateTime<Utc>,
        /// Agent name (always None for execution-level events).
        agent: Option<String>,
        /// Task ID.
        #[serde(skip_serializing_if = "Option::is_none")]
        task_id: Option<String>,
        /// Base snapshot ID.
        #[serde(skip_serializing_if = "Option::is_none")]
        base_snapshot: Option<String>,
    },

    /// Execution stopped.
    ExecutionStopped {
        /// Timestamp.
        ts: DateTime<Utc>,
        /// Agent name (always None for execution-level events).
        agent: Option<String>,
        /// Reason for stopping.
        reason: String,
    },

    /// Program added to execution.
    ProgramAdded {
        /// Timestamp.
        ts: DateTime<Utc>,
        /// Agent name (always None for execution-level events).
        agent: Option<String>,
        /// Program name.
        name: String,
        /// Program type.
        program_type: String,
        /// Instance ID.
        #[serde(skip_serializing_if = "Option::is_none")]
        instance_id: Option<String>,
    },

    /// Agent connected.
    Connected {
        /// Timestamp.
        ts: DateTime<Utc>,
        /// Agent name.
        agent: String,
        /// Session ID.
        session_id: String,
    },

    /// Agent disconnected.
    Disconnected {
        /// Timestamp.
        ts: DateTime<Utc>,
        /// Agent name.
        agent: String,
    },

    /// Prompt sent to agent.
    Prompt {
        /// Timestamp.
        ts: DateTime<Utc>,
        /// Agent name.
        agent: String,
        /// Prompt text.
        text: String,
    },

    /// Response chunk from agent.
    ResponseChunk {
        /// Timestamp.
        ts: DateTime<Utc>,
        /// Agent name.
        agent: String,
        /// Response text.
        text: String,
    },

    /// Tool use by agent.
    ToolUse {
        /// Timestamp.
        ts: DateTime<Utc>,
        /// Agent name.
        agent: String,
        /// Tool name.
        tool: String,
        /// Tool parameters.
        #[serde(skip_serializing_if = "Option::is_none")]
        params: Option<HashMap<String, serde_json::Value>>,
    },

    /// Tool result.
    ToolResult {
        /// Timestamp.
        ts: DateTime<Utc>,
        /// Agent name.
        agent: String,
        /// Tool name.
        tool: String,
        /// Result text.
        #[serde(skip_serializing_if = "Option::is_none")]
        result: Option<String>,
    },

    /// Topology update.
    Topology {
        /// Timestamp.
        ts: DateTime<Utc>,
        /// Agent name (always None for execution-level events).
        agent: Option<String>,
        /// List of agent names.
        agents: Vec<String>,
        /// List of edges.
        edges: Vec<ExecutionEdge>,
    },

    /// Client event (emitted by program).
    ClientEvent {
        /// Timestamp.
        ts: DateTime<Utc>,
        /// Agent name (always None for execution-level events).
        agent: Option<String>,
        /// Event name.
        event: String,
        /// Event data.
        #[serde(default)]
        data: HashMap<String, serde_json::Value>,
    },

    /// Error occurred.
    Error {
        /// Timestamp.
        ts: DateTime<Utc>,
        /// Agent name (if agent-specific error).
        agent: Option<String>,
        /// Error message.
        error: String,
    },
}

impl TraceEvent {
    /// Get the timestamp of this event.
    pub fn timestamp(&self) -> &DateTime<Utc> {
        match self {
            TraceEvent::ExecutionStarted { ts, .. }
            | TraceEvent::ExecutionStopped { ts, .. }
            | TraceEvent::ProgramAdded { ts, .. }
            | TraceEvent::Connected { ts, .. }
            | TraceEvent::Disconnected { ts, .. }
            | TraceEvent::Prompt { ts, .. }
            | TraceEvent::ResponseChunk { ts, .. }
            | TraceEvent::ToolUse { ts, .. }
            | TraceEvent::ToolResult { ts, .. }
            | TraceEvent::Topology { ts, .. }
            | TraceEvent::ClientEvent { ts, .. }
            | TraceEvent::Error { ts, .. } => ts,
        }
    }

    /// Get the agent name associated with this event (if any).
    pub fn agent_name(&self) -> Option<&str> {
        match self {
            TraceEvent::ExecutionStarted { agent, .. }
            | TraceEvent::ExecutionStopped { agent, .. }
            | TraceEvent::ProgramAdded { agent, .. }
            | TraceEvent::Topology { agent, .. }
            | TraceEvent::ClientEvent { agent, .. }
            | TraceEvent::Error { agent, .. } => agent.as_deref(),
            TraceEvent::Connected { agent, .. }
            | TraceEvent::Disconnected { agent, .. }
            | TraceEvent::Prompt { agent, .. }
            | TraceEvent::ResponseChunk { agent, .. }
            | TraceEvent::ToolUse { agent, .. }
            | TraceEvent::ToolResult { agent, .. } => Some(agent.as_str()),
        }
    }

    /// Get the event type name as a string.
    pub fn event_type(&self) -> &'static str {
        match self {
            TraceEvent::ExecutionStarted { .. } => "execution_started",
            TraceEvent::ExecutionStopped { .. } => "execution_stopped",
            TraceEvent::ProgramAdded { .. } => "program_added",
            TraceEvent::Connected { .. } => "connected",
            TraceEvent::Disconnected { .. } => "disconnected",
            TraceEvent::Prompt { .. } => "prompt",
            TraceEvent::ResponseChunk { .. } => "response_chunk",
            TraceEvent::ToolUse { .. } => "tool_use",
            TraceEvent::ToolResult { .. } => "tool_result",
            TraceEvent::Topology { .. } => "topology",
            TraceEvent::ClientEvent { .. } => "client_event",
            TraceEvent::Error { .. } => "error",
        }
    }

    /// Create an execution_started event.
    pub fn execution_started(task_id: Option<String>, base_snapshot: Option<String>) -> Self {
        TraceEvent::ExecutionStarted {
            ts: Utc::now(),
            agent: None,
            task_id,
            base_snapshot,
        }
    }

    /// Create an execution_stopped event.
    pub fn execution_stopped(reason: impl Into<String>) -> Self {
        TraceEvent::ExecutionStopped {
            ts: Utc::now(),
            agent: None,
            reason: reason.into(),
        }
    }

    /// Create a program_added event.
    pub fn program_added(
        name: impl Into<String>,
        program_type: impl Into<String>,
        instance_id: Option<String>,
    ) -> Self {
        TraceEvent::ProgramAdded {
            ts: Utc::now(),
            agent: None,
            name: name.into(),
            program_type: program_type.into(),
            instance_id,
        }
    }

    /// Create a connected event.
    pub fn connected(agent: impl Into<String>, session_id: impl Into<String>) -> Self {
        TraceEvent::Connected {
            ts: Utc::now(),
            agent: agent.into(),
            session_id: session_id.into(),
        }
    }

    /// Create a disconnected event.
    pub fn disconnected(agent: impl Into<String>) -> Self {
        TraceEvent::Disconnected {
            ts: Utc::now(),
            agent: agent.into(),
        }
    }

    /// Create a prompt event.
    pub fn prompt(agent: impl Into<String>, text: impl Into<String>) -> Self {
        TraceEvent::Prompt {
            ts: Utc::now(),
            agent: agent.into(),
            text: text.into(),
        }
    }

    /// Create a response_chunk event.
    pub fn response_chunk(agent: impl Into<String>, text: impl Into<String>) -> Self {
        TraceEvent::ResponseChunk {
            ts: Utc::now(),
            agent: agent.into(),
            text: text.into(),
        }
    }

    /// Create a tool_use event.
    pub fn tool_use(
        agent: impl Into<String>,
        tool: impl Into<String>,
        params: Option<HashMap<String, serde_json::Value>>,
    ) -> Self {
        TraceEvent::ToolUse {
            ts: Utc::now(),
            agent: agent.into(),
            tool: tool.into(),
            params,
        }
    }

    /// Create a tool_result event.
    pub fn tool_result(agent: impl Into<String>, tool: impl Into<String>, result: Option<String>) -> Self {
        TraceEvent::ToolResult {
            ts: Utc::now(),
            agent: agent.into(),
            tool: tool.into(),
            result,
        }
    }

    /// Create a topology event.
    pub fn topology(agents: Vec<String>, edges: Vec<ExecutionEdge>) -> Self {
        TraceEvent::Topology {
            ts: Utc::now(),
            agent: None,
            agents,
            edges,
        }
    }

    /// Create a client_event.
    pub fn client_event(event: impl Into<String>, data: HashMap<String, serde_json::Value>) -> Self {
        TraceEvent::ClientEvent {
            ts: Utc::now(),
            agent: None,
            event: event.into(),
            data,
        }
    }

    /// Create an error event.
    pub fn error(agent: Option<String>, error: impl Into<String>) -> Self {
        TraceEvent::Error {
            ts: Utc::now(),
            agent,
            error: error.into(),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_execution_started_event() {
        let event = TraceEvent::execution_started(Some("task-123".to_string()), None);
        assert_eq!(event.event_type(), "execution_started");
        assert!(event.agent_name().is_none());
    }

    #[test]
    fn test_connected_event() {
        let event = TraceEvent::connected("builder", "session-456");
        assert_eq!(event.event_type(), "connected");
        assert_eq!(event.agent_name(), Some("builder"));
    }

    #[test]
    fn test_error_event() {
        let event = TraceEvent::error(Some("builder".to_string()), "something went wrong");
        assert_eq!(event.event_type(), "error");
        assert_eq!(event.agent_name(), Some("builder"));
    }

    #[test]
    fn test_event_serialization() {
        let event = TraceEvent::connected("builder", "session-123");
        let json = serde_json::to_string(&event).unwrap();
        let deserialized: TraceEvent = serde_json::from_str(&json).unwrap();

        assert_eq!(event.event_type(), deserialized.event_type());
        assert_eq!(event.agent_name(), deserialized.agent_name());
    }

    #[test]
    fn test_tool_use_event() {
        let mut params = HashMap::new();
        params.insert("file".to_string(), serde_json::json!("test.txt"));

        let event = TraceEvent::tool_use("builder", "read_file", Some(params));
        assert_eq!(event.event_type(), "tool_use");
        assert_eq!(event.agent_name(), Some("builder"));
    }

    #[test]
    fn test_topology_event() {
        let agents = vec!["builder".to_string(), "reviewer".to_string()];
        let edges = vec![ExecutionEdge {
            from: "builder".to_string(),
            to: "reviewer".to_string(),
        }];

        let event = TraceEvent::topology(agents, edges);
        assert_eq!(event.event_type(), "topology");
        assert!(event.agent_name().is_none());
    }
}
