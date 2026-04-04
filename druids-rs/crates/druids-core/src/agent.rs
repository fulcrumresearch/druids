//! Agent types and state.

use serde::{Deserialize, Serialize};
use std::collections::HashMap;

/// Agent type.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum AgentType {
    /// Claude agent (Anthropic).
    Claude,
    /// Codex agent (OpenAI).
    Codex,
}

impl std::fmt::Display for AgentType {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        let s = match self {
            AgentType::Claude => "claude",
            AgentType::Codex => "codex",
        };
        write!(f, "{}", s)
    }
}

impl std::str::FromStr for AgentType {
    type Err = String;

    fn from_str(s: &str) -> Result<Self, Self::Err> {
        match s {
            "claude" => Ok(AgentType::Claude),
            "codex" => Ok(AgentType::Codex),
            _ => Err(format!("unknown agent type: {}", s)),
        }
    }
}

/// Agent state.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum AgentState {
    /// Agent is initializing.
    Initializing,
    /// Agent is connected and ready.
    Connected,
    /// Agent is actively working.
    Active,
    /// Agent is idle (connected but not actively working).
    Idle,
    /// Agent has disconnected.
    Disconnected,
    /// Agent encountered an error.
    Error,
}

impl std::fmt::Display for AgentState {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        let s = match self {
            AgentState::Initializing => "initializing",
            AgentState::Connected => "connected",
            AgentState::Active => "active",
            AgentState::Idle => "idle",
            AgentState::Disconnected => "disconnected",
            AgentState::Error => "error",
        };
        write!(f, "{}", s)
    }
}

impl std::str::FromStr for AgentState {
    type Err = String;

    fn from_str(s: &str) -> Result<Self, Self::Err> {
        match s {
            "initializing" => Ok(AgentState::Initializing),
            "connected" => Ok(AgentState::Connected),
            "active" => Ok(AgentState::Active),
            "idle" => Ok(AgentState::Idle),
            "disconnected" => Ok(AgentState::Disconnected),
            "error" => Ok(AgentState::Error),
            _ => Err(format!("unknown agent state: {}", s)),
        }
    }
}

/// Agent metadata and information.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AgentInfo {
    /// Agent name.
    pub name: String,
    /// Agent type.
    pub agent_type: AgentType,
    /// Current state.
    pub state: AgentState,
    /// Instance ID (sandbox identifier).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub instance_id: Option<String>,
    /// Session ID (connection identifier).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub session_id: Option<String>,
    /// Custom metadata.
    #[serde(default, skip_serializing_if = "HashMap::is_empty")]
    pub metadata: HashMap<String, serde_json::Value>,
}

impl AgentInfo {
    /// Create a new agent info.
    pub fn new(name: impl Into<String>, agent_type: AgentType) -> Self {
        Self {
            name: name.into(),
            agent_type,
            state: AgentState::Initializing,
            instance_id: None,
            session_id: None,
            metadata: HashMap::new(),
        }
    }

    /// Check if the agent is connected.
    pub fn is_connected(&self) -> bool {
        matches!(
            self.state,
            AgentState::Connected | AgentState::Active | AgentState::Idle
        )
    }

    /// Check if the agent is active.
    pub fn is_active(&self) -> bool {
        self.state == AgentState::Active
    }
}

/// Agent connection details.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AgentConnection {
    /// Agent name.
    pub name: String,
    /// Session ID.
    pub session_id: String,
    /// WebSocket URL for the agent bridge.
    pub ws_url: String,
    /// Instance ID (sandbox).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub instance_id: Option<String>,
}

impl AgentConnection {
    /// Create a new agent connection.
    pub fn new(name: impl Into<String>, session_id: impl Into<String>, ws_url: impl Into<String>) -> Self {
        Self {
            name: name.into(),
            session_id: session_id.into(),
            ws_url: ws_url.into(),
            instance_id: None,
        }
    }

    /// Set the instance ID.
    pub fn with_instance_id(mut self, instance_id: impl Into<String>) -> Self {
        self.instance_id = Some(instance_id.into());
        self
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_agent_type() {
        use std::str::FromStr;
        assert_eq!(AgentType::from_str("claude").unwrap(), AgentType::Claude);
        assert_eq!(AgentType::from_str("codex").unwrap(), AgentType::Codex);
        assert!(AgentType::from_str("invalid").is_err());
    }

    #[test]
    fn test_agent_state() {
        use std::str::FromStr;
        assert_eq!(
            AgentState::from_str("initializing").unwrap(),
            AgentState::Initializing
        );
        assert_eq!(
            AgentState::from_str("connected").unwrap(),
            AgentState::Connected
        );
        assert!(AgentState::from_str("invalid").is_err());
    }

    #[test]
    fn test_agent_info() {
        let info = AgentInfo::new("builder", AgentType::Claude);
        assert_eq!(info.name, "builder");
        assert_eq!(info.agent_type, AgentType::Claude);
        assert_eq!(info.state, AgentState::Initializing);
        assert!(!info.is_connected());

        let mut info = info;
        info.state = AgentState::Connected;
        assert!(info.is_connected());
        assert!(!info.is_active());

        info.state = AgentState::Active;
        assert!(info.is_active());
    }

    #[test]
    fn test_agent_connection() {
        let conn = AgentConnection::new("builder", "session-123", "ws://localhost:8080")
            .with_instance_id("inst-456");

        assert_eq!(conn.name, "builder");
        assert_eq!(conn.session_id, "session-123");
        assert_eq!(conn.ws_url, "ws://localhost:8080");
        assert_eq!(conn.instance_id, Some("inst-456".to_string()));
    }

    #[test]
    fn test_agent_info_serialization() {
        let info = AgentInfo::new("builder", AgentType::Claude);
        let json = serde_json::to_string(&info).unwrap();
        let deserialized: AgentInfo = serde_json::from_str(&json).unwrap();

        assert_eq!(info.name, deserialized.name);
        assert_eq!(info.agent_type, deserialized.agent_type);
        assert_eq!(info.state, deserialized.state);
    }
}
