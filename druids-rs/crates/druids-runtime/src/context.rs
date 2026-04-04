//! Program context implementation.

use crate::agent::AgentHandle;
use crate::error::{Result, RuntimeError};
use crate::events::{EventData, EventRegistry};
use druids_core::common::{ExecResult, GitMode};
use futures::future::BoxFuture;
use serde_json::Value;
use std::collections::{HashMap, HashSet};
use tokio::sync::oneshot;

/// Program context passed to user programs
pub struct ProgramContext {
    pub slug: String,
    pub repo_full_name: Option<String>,
    pub spec: Option<String>,
    pub(crate) base_url: String,
    pub(crate) token: String,
    pub(crate) agents: HashMap<String, AgentHandle>,
    pub(crate) client_handlers: EventRegistry,
    pub(crate) topology: HashSet<(String, String)>,
    pub(crate) connections: HashSet<String>,
    pub(crate) state_values: HashMap<String, Value>,
}

impl ProgramContext {
    /// Create a new program context
    pub fn new(
        slug: impl Into<String>,
        base_url: impl Into<String>,
        token: impl Into<String>,
    ) -> Self {
        Self {
            slug: slug.into(),
            repo_full_name: None,
            spec: None,
            base_url: base_url.into(),
            token: token.into(),
            agents: HashMap::new(),
            client_handlers: EventRegistry::new(),
            topology: HashSet::new(),
            connections: HashSet::new(),
            state_values: HashMap::new(),
        }
    }

    /// Set repository full name
    pub fn with_repo(mut self, repo_full_name: impl Into<String>) -> Self {
        self.repo_full_name = Some(repo_full_name.into());
        self
    }

    /// Set spec
    pub fn with_spec(mut self, spec: impl Into<String>) -> Self {
        self.spec = Some(spec.into());
        self
    }

    /// Spawn a new agent
    pub async fn agent(
        &mut self,
        name: impl Into<String>,
        prompt: Option<&str>,
        system_prompt: Option<&str>,
        model: Option<&str>,
        git: Option<GitMode>,
        working_directory: Option<&str>,
        share_machine_with: Option<&AgentHandle>,
    ) -> Result<AgentHandle> {
        let name = name.into();

        // Build payload for agent creation
        let mut payload = serde_json::json!({
            "name": &name,
        });

        if let Some(p) = prompt {
            payload["prompt"] = serde_json::json!(p);
        }
        if let Some(sp) = system_prompt {
            payload["system_prompt"] = serde_json::json!(sp);
        }
        if let Some(m) = model {
            payload["model"] = serde_json::json!(m);
        }
        if let Some(g) = git {
            payload["git"] = serde_json::json!(g);
        }
        if let Some(wd) = working_directory {
            payload["working_directory"] = serde_json::json!(wd);
        }
        if let Some(share) = share_machine_with {
            payload["share_machine_with"] = serde_json::json!(&share.name);
        }

        // Create channel for readiness signaling
        let (ready_tx, ready_rx) = oneshot::channel();

        // Create agent handle before spawning task
        let agent = AgentHandle::new(name.clone(), ready_rx);
        self.agents.insert(name.clone(), agent.clone());

        // Spawn task to create agent on server
        let base_url = self.base_url.clone();
        let token = self.token.clone();
        let slug = self.slug.clone();
        let share_clone = share_machine_with.cloned();

        tokio::spawn(async move {
            let result = async {
                // Wait for share_machine_with agent to be ready if specified
                if let Some(share) = share_clone {
                    share.await_ready().await?;
                }

                // Create agent on server
                let client = reqwest::Client::new();
                let url = format!("{}/api/executions/{}/agents", base_url, slug);

                let resp = client
                    .post(&url)
                    .header("Authorization", format!("Bearer {}", token))
                    .json(&payload)
                    .send()
                    .await?;

                if !resp.status().is_success() {
                    let status = resp.status();
                    let body = resp.text().await.unwrap_or_default();
                    return Err(RuntimeError::server(format!(
                        "agent creation failed with status {}: {}",
                        status, body
                    )));
                }

                resp.json::<Value>().await?;
                Ok(())
            }
            .await;

            // Signal readiness
            let _ = ready_tx.send(result);
        });

        Ok(agent)
    }

    /// Connect two agents for communication
    pub fn connect(&mut self, agent1: &AgentHandle, agent2: &AgentHandle, direction: &str) {
        self.topology
            .insert((agent1.name.clone(), agent2.name.clone()));
        if direction == "both" {
            self.topology
                .insert((agent2.name.clone(), agent1.name.clone()));
        }
    }

    /// Check if sender can reach receiver
    pub fn is_connected(&self, sender: &str, receiver: &str) -> bool {
        self.topology.contains(&(sender.to_string(), receiver.to_string()))
    }

    /// Register a client event handler
    pub fn on_client_event<F>(&mut self, event_name: impl Into<String>, handler: F)
    where
        F: Fn(EventData) -> BoxFuture<'static, ()> + Send + Sync + 'static,
    {
        self.client_handlers.register(event_name, handler);
    }

    /// Emit an event to connected clients
    pub async fn emit(&self, event: &str, data: Option<Value>) -> Result<()> {
        let payload = serde_json::json!({
            "event": event,
            "data": data,
        });
        self.post("/emit", &payload).await?;
        Ok(())
    }

    /// Signal completion
    pub async fn done(&self, result: Option<Value>) -> Result<()> {
        let mut payload = serde_json::json!({
            "status": "completed",
        });
        if let Some(r) = result {
            payload["result"] = r;
        }
        self.patch("", &payload).await?;
        Ok(())
    }

    /// Signal failure
    pub async fn fail(&self, reason: &str) -> Result<()> {
        let payload = serde_json::json!({
            "status": "failed",
            "reason": reason,
        });
        self.patch("", &payload).await?;
        Ok(())
    }

    /// Send a message to an agent
    pub(crate) async fn send_message(&self, agent_name: &str, message: &str) -> Result<()> {
        let payload = serde_json::json!({
            "text": message,
        });
        self.post(&format!("/agents/{}/message", agent_name), &payload)
            .await?;
        Ok(())
    }

    /// Execute a command on an agent's VM
    pub(crate) async fn remote_exec(
        &self,
        agent_name: &str,
        command: &str,
        user: Option<&str>,
        timeout: Option<u32>,
    ) -> Result<ExecResult> {
        let mut payload = serde_json::json!({
            "execution_slug": &self.slug,
            "agent_name": agent_name,
            "command": command,
        });

        if let Some(u) = user {
            payload["user"] = serde_json::json!(u);
        }
        if let Some(t) = timeout {
            payload["timeout"] = serde_json::json!(t);
        }

        let client = reqwest::Client::builder()
            .timeout(std::time::Duration::from_secs(600))
            .build()?;

        let url = format!("{}/api/remote-exec", self.base_url);
        let resp = client
            .post(&url)
            .header("Authorization", format!("Bearer {}", self.token))
            .json(&payload)
            .send()
            .await?;

        if !resp.status().is_success() {
            let status = resp.status();
            let body = resp.text().await.unwrap_or_default();
            return Err(RuntimeError::server(format!(
                "remote exec failed with status {}: {}",
                status, body
            )));
        }

        Ok(resp.json().await?)
    }

    /// Expose a port on an agent's VM
    pub(crate) async fn expose_port(
        &self,
        agent_name: &str,
        service_name: &str,
        port: u16,
    ) -> Result<String> {
        let payload = serde_json::json!({
            "service_name": service_name,
            "port": port,
        });

        let resp = self
            .post(&format!("/agents/{}/expose", agent_name), &payload)
            .await?;

        resp.get("url")
            .and_then(|v| v.as_str())
            .map(|s| s.to_string())
            .ok_or_else(|| RuntimeError::other("missing url in expose response"))
    }

    /// Fork an agent
    pub(crate) async fn fork_agent(
        &mut self,
        parent_name: &str,
        new_name: &str,
        prompt: Option<&str>,
        system_prompt: Option<&str>,
        model: Option<&str>,
        git: Option<&str>,
    ) -> Result<AgentHandle> {
        let mut payload = serde_json::json!({
            "name": new_name,
        });

        if let Some(p) = prompt {
            payload["prompt"] = serde_json::json!(p);
        }
        if let Some(sp) = system_prompt {
            payload["system_prompt"] = serde_json::json!(sp);
        }
        if let Some(m) = model {
            payload["model"] = serde_json::json!(m);
        }
        if let Some(g) = git {
            payload["git"] = serde_json::json!(g);
        }

        let resp = self
            .post(&format!("/agents/{}/fork", parent_name), &payload)
            .await?;

        let agent_name = resp
            .get("name")
            .and_then(|v| v.as_str())
            .ok_or_else(|| RuntimeError::other("missing name in fork response"))?
            .to_string();

        // Create agent handle with already-ready channel (fork returns ready agent)
        let (ready_tx, ready_rx) = oneshot::channel();
        let _ = ready_tx.send(Ok(()));

        let agent = AgentHandle::new(agent_name.clone(), ready_rx);
        self.agents.insert(agent_name, agent.clone());

        Ok(agent)
    }

    /// Snapshot an agent's machine
    pub(crate) async fn snapshot_machine(
        &self,
        agent_name: &str,
        devbox_name: Option<&str>,
    ) -> Result<String> {
        let mut payload = serde_json::json!({});
        if let Some(name) = devbox_name {
            payload["devbox_name"] = serde_json::json!(name);
        }

        let resp = self
            .post(&format!("/agents/{}/snapshot", agent_name), &payload)
            .await?;

        resp.get("devbox_name")
            .and_then(|v| v.as_str())
            .map(|s| s.to_string())
            .ok_or_else(|| RuntimeError::other("missing devbox_name in snapshot response"))
    }

    /// Make HTTP POST request
    async fn post(&self, path: &str, data: &Value) -> Result<Value> {
        self.request("POST", path, Some(data)).await
    }

    /// Make HTTP PATCH request
    async fn patch(&self, path: &str, data: &Value) -> Result<Value> {
        self.request("PATCH", path, Some(data)).await
    }

    /// Make HTTP request to execution endpoint
    async fn request(&self, method: &str, path: &str, data: Option<&Value>) -> Result<Value> {
        let url = format!("{}/api/executions/{}{}", self.base_url, self.slug, path);
        let client = reqwest::Client::builder()
            .timeout(std::time::Duration::from_secs(600))
            .build()?;

        let mut req = client
            .request(method.parse().unwrap(), &url)
            .header("Authorization", format!("Bearer {}", self.token));

        if let Some(d) = data {
            req = req.json(d);
        }

        let resp = req.send().await?;

        if !resp.status().is_success() {
            let status = resp.status();
            let body = resp.text().await.unwrap_or_default();
            return Err(RuntimeError::server(format!(
                "request failed with status {}: {}",
                status, body
            )));
        }

        Ok(resp.json().await?)
    }

    /// Get reference to agents map
    pub fn agents(&self) -> &HashMap<String, AgentHandle> {
        &self.agents
    }

    /// Get reference to connections set
    pub fn connections(&self) -> &HashSet<String> {
        &self.connections
    }

    /// Get state value
    pub fn get_state(&self, name: &str) -> Option<&Value> {
        self.state_values.get(name)
    }

    /// Set state value
    pub async fn set_state(&mut self, name: impl Into<String>, value: Value) -> Result<()> {
        let name = name.into();
        self.state_values.insert(name.clone(), value.clone());

        // Emit state update event
        let _ = self
            .emit(
                "program_state",
                Some(serde_json::json!({
                    "name": name,
                    "value": value,
                })),
            )
            .await;

        Ok(())
    }

    /// Get client event handler registry
    pub(crate) fn client_handlers(&self) -> &EventRegistry {
        &self.client_handlers
    }

    /// Get topology edges
    pub(crate) fn topology_edges(&self) -> Vec<(String, String)> {
        self.topology.iter().cloned().collect()
    }
}
