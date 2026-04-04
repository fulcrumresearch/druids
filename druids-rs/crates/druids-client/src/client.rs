//! HTTP client for the Druids API.

use crate::config::ClientConfig;
use crate::error::{ClientError, Result};
use crate::retry::RetryPolicy;
use crate::streaming::{stream_execution, ActivityEvent};
use crate::types::{
    CallToolRequest, CallToolResponse, ChatMessageRequest, ChatMessageResponse,
    CreateExecutionRequest, CreateExecutionResponse, DeleteSecretRequest, DevboxSummary,
    Execution, ExecutionActivityResponse, ExecutionDiffResponse, ExecutionSummary,
    ListDevboxesResponse, ListExecutionsResponse, ListSecretsResponse, ListToolsResponse,
    SecretInfo, SetSecretsRequest, SetSecretsResponse, SetupFinishRequest, SetupStartRequest,
    SshCredentialsResponse, UpdateExecutionRequest,
};
use futures::stream::Stream;
use reqwest::{Client, Method, RequestBuilder, Response, StatusCode};
use serde::de::DeserializeOwned;
use std::collections::HashMap;
use std::pin::Pin;
use std::time::Duration;
use url::Url;

/// Druids API client.
#[derive(Debug, Clone)]
pub struct DruidsClient {
    /// Base URL for the Druids server.
    base_url: Url,

    /// User access token for authentication.
    user_access_token: Option<String>,

    /// HTTP client.
    client: Client,

    /// Retry policy.
    retry_policy: RetryPolicy,
}

impl DruidsClient {
    /// Create a new Druids client with the given configuration.
    pub fn new(config: ClientConfig) -> Result<Self> {
        let client = Client::builder()
            .timeout(Duration::from_secs(300))
            .build()?;

        Ok(Self {
            base_url: config.base_url,
            user_access_token: config.user_access_token,
            client,
            retry_policy: RetryPolicy::default(),
        })
    }

    /// Create a new Druids client with a specific base URL and token.
    pub fn with_token(base_url: Url, token: String) -> Result<Self> {
        let client = Client::builder()
            .timeout(Duration::from_secs(300))
            .build()?;

        Ok(Self {
            base_url,
            user_access_token: Some(token),
            client,
            retry_policy: RetryPolicy::default(),
        })
    }

    /// Create a new Druids client from the default configuration.
    pub fn from_default_config() -> Result<Self> {
        let config = ClientConfig::load()?;
        Self::new(config)
    }

    /// Get the base URL.
    pub fn base_url(&self) -> &Url {
        &self.base_url
    }

    /// Get the user access token.
    pub fn user_access_token(&self) -> Option<&str> {
        self.user_access_token.as_deref()
    }

    // ========================================================================
    // Internal helpers
    // ========================================================================

    /// Build a request with authorization header.
    fn request(&self, method: Method, path: &str) -> Result<RequestBuilder> {
        let url = self.base_url.join(path).map_err(|e| ClientError::UrlParse(e))?;
        let mut req = self.client.request(method, url);

        if let Some(ref token) = self.user_access_token {
            req = req.header("Authorization", format!("Bearer {}", token));
        }

        Ok(req)
    }

    /// Execute a request and handle errors.
    async fn execute(&self, request: RequestBuilder) -> Result<Response> {
        let response = self.retry_policy.execute(|| async {
            let req = request.try_clone().expect("request is not cloneable");
            req.send().await
        }).await?;

        self.handle_response(response).await
    }

    /// Handle HTTP response and convert errors.
    async fn handle_response(&self, response: Response) -> Result<Response> {
        let status = response.status();

        match status {
            StatusCode::OK | StatusCode::CREATED => Ok(response),
            StatusCode::UNAUTHORIZED => Err(ClientError::Unauthorized),
            StatusCode::NOT_FOUND => {
                let text = response.text().await.unwrap_or_default();
                Err(ClientError::Api {
                    status: status.as_u16(),
                    message: text,
                })
            }
            _ => {
                let text = response.text().await.unwrap_or_default();
                Err(ClientError::Api {
                    status: status.as_u16(),
                    message: text,
                })
            }
        }
    }

    /// Execute a request and deserialize JSON response.
    async fn execute_json<T: DeserializeOwned>(&self, request: RequestBuilder) -> Result<T> {
        let response = self.execute(request).await?;
        let data = response.json::<T>().await?;
        Ok(data)
    }

    // ========================================================================
    // Execution API
    // ========================================================================

    /// Create a new execution from program source.
    pub async fn create_execution(
        &self,
        program_source: String,
        repo_full_name: Option<String>,
        devbox_name: Option<String>,
        args: Option<HashMap<String, String>>,
        git_branch: Option<String>,
        ttl: Option<i32>,
        files: Option<HashMap<String, String>>,
    ) -> Result<CreateExecutionResponse> {
        let body = CreateExecutionRequest {
            program_source,
            devbox_name,
            repo_full_name,
            git_branch,
            args,
            ttl,
            files,
        };

        let req = self.request(Method::POST, "/api/executions")?.json(&body);
        self.execute_json(req).await
    }

    /// Get execution details by slug.
    pub async fn get_execution(&self, slug: &str) -> Result<Execution> {
        let req = self.request(Method::GET, &format!("/api/executions/{}", slug))?;

        match self.execute_json(req).await {
            Ok(exec) => Ok(exec),
            Err(ClientError::Api { status: 404, .. }) => {
                Err(ClientError::NotFound {
                    resource_type: "Execution".to_string(),
                    identifier: slug.to_string(),
                })
            }
            Err(e) => Err(e),
        }
    }

    /// List executions for the current user.
    pub async fn list_executions(&self, active_only: bool) -> Result<Vec<ExecutionSummary>> {
        let mut req = self.request(Method::GET, "/api/executions")?;

        if !active_only {
            req = req.query(&[("active_only", "false")]);
        }

        let response: ListExecutionsResponse = self.execute_json(req).await?;
        Ok(response.executions)
    }

    /// Stop an execution by slug.
    pub async fn stop_execution(&self, slug: &str) -> Result<Execution> {
        let body = UpdateExecutionRequest {
            status: "stopped".to_string(),
            result: None,
            reason: None,
        };

        let req = self.request(Method::PATCH, &format!("/api/executions/{}", slug))?.json(&body);

        match self.execute_json(req).await {
            Ok(exec) => Ok(exec),
            Err(ClientError::Api { status: 404, .. }) => {
                Err(ClientError::NotFound {
                    resource_type: "Execution".to_string(),
                    identifier: slug.to_string(),
                })
            }
            Err(e) => Err(e),
        }
    }

    /// Send a chat message to an agent in a running execution.
    pub async fn send_agent_message(
        &self,
        execution_slug: &str,
        agent_name: &str,
        text: String,
    ) -> Result<ChatMessageResponse> {
        let body = ChatMessageRequest { text };

        let req = self.request(
            Method::POST,
            &format!("/api/executions/{}/agents/{}/message", execution_slug, agent_name),
        )?.json(&body);

        match self.execute_json(req).await {
            Ok(resp) => Ok(resp),
            Err(ClientError::Api { status: 404, .. }) => {
                Err(ClientError::NotFound {
                    resource_type: "Agent".to_string(),
                    identifier: agent_name.to_string(),
                })
            }
            Err(e) => Err(e),
        }
    }

    /// Get recent activity for an execution.
    pub async fn get_execution_activity(
        &self,
        slug: &str,
        n: Option<usize>,
        compact: Option<bool>,
    ) -> Result<ExecutionActivityResponse> {
        let mut req = self.request(Method::GET, &format!("/api/executions/{}/activity", slug))?;

        if let Some(n) = n {
            req = req.query(&[("n", n.to_string())]);
        }
        if let Some(compact) = compact {
            req = req.query(&[("compact", compact.to_string())]);
        }

        match self.execute_json(req).await {
            Ok(resp) => Ok(resp),
            Err(ClientError::Api { status: 404, .. }) => {
                Err(ClientError::NotFound {
                    resource_type: "Execution".to_string(),
                    identifier: slug.to_string(),
                })
            }
            Err(e) => Err(e),
        }
    }

    /// Get diff for an execution.
    pub async fn get_execution_diff(
        &self,
        execution_slug: &str,
        agent: Option<&str>,
    ) -> Result<String> {
        let mut req = self.request(Method::GET, &format!("/api/executions/{}/diff", execution_slug))?;

        if let Some(agent) = agent {
            req = req.query(&[("agent", agent)]);
        }

        let response: ExecutionDiffResponse = match self.execute_json(req).await {
            Ok(resp) => resp,
            Err(ClientError::Api { status: 404, .. }) => {
                return Err(ClientError::NotFound {
                    resource_type: "Execution".to_string(),
                    identifier: execution_slug.to_string(),
                });
            }
            Err(e) => return Err(e),
        };

        Ok(response.diff)
    }

    /// Get SSH credentials for an execution's VM.
    pub async fn get_execution_ssh(
        &self,
        execution_slug: &str,
        agent: Option<&str>,
    ) -> Result<SshCredentialsResponse> {
        let mut req = self.request(Method::GET, &format!("/api/executions/{}/ssh", execution_slug))?;

        if let Some(agent) = agent {
            req = req.query(&[("agent", agent)]);
        }

        match self.execute_json(req).await {
            Ok(creds) => Ok(creds),
            Err(ClientError::Api { status: 404, .. }) => {
                Err(ClientError::NotFound {
                    resource_type: "Execution".to_string(),
                    identifier: execution_slug.to_string(),
                })
            }
            Err(e) => Err(e),
        }
    }

    // ========================================================================
    // Devbox API
    // ========================================================================

    /// Start devbox setup: provision sandbox and return SSH credentials.
    pub async fn setup_start(
        &self,
        name: Option<String>,
        repo_full_name: Option<String>,
        public: Option<bool>,
        vcpus: Option<i32>,
        memory_mb: Option<i32>,
        disk_mb: Option<i32>,
    ) -> Result<serde_json::Value> {
        let body = SetupStartRequest {
            name,
            repo_full_name,
            public,
            vcpus,
            memory_mb,
            disk_mb,
        };

        let req = self.request(Method::POST, "/api/devbox/setup/start")?.json(&body);
        self.execute_json(req).await
    }

    /// Finish devbox setup: snapshot and stop the sandbox.
    pub async fn setup_finish(
        &self,
        name: Option<String>,
        repo_full_name: Option<String>,
    ) -> Result<serde_json::Value> {
        let body = SetupFinishRequest {
            name,
            repo_full_name,
        };

        let req = self.request(Method::POST, "/api/devbox/setup/finish")?.json(&body);
        self.execute_json(req).await
    }

    /// List all devboxes for the current user.
    pub async fn list_devboxes(&self) -> Result<Vec<DevboxSummary>> {
        let req = self.request(Method::GET, "/api/devboxes")?;
        let response: ListDevboxesResponse = self.execute_json(req).await?;
        Ok(response.devboxes)
    }

    // ========================================================================
    // Secrets API
    // ========================================================================

    /// Set one or more secrets on a devbox.
    pub async fn set_secrets(
        &self,
        secrets: HashMap<String, String>,
        devbox_name: Option<String>,
        repo_full_name: Option<String>,
    ) -> Result<SetSecretsResponse> {
        let body = SetSecretsRequest {
            devbox_name,
            repo_full_name,
            secrets,
        };

        let req = self.request(Method::POST, "/api/secrets")?.json(&body);
        self.execute_json(req).await
    }

    /// List secret names for a devbox.
    pub async fn list_secrets(
        &self,
        devbox_name: Option<String>,
        repo_full_name: Option<String>,
    ) -> Result<Vec<SecretInfo>> {
        let mut req = self.request(Method::GET, "/api/secrets")?;

        let mut params = Vec::new();
        if let Some(name) = devbox_name {
            params.push(("devbox_name", name));
        }
        if let Some(repo) = repo_full_name {
            params.push(("repo_full_name", repo));
        }

        if !params.is_empty() {
            req = req.query(&params);
        }

        let response: ListSecretsResponse = self.execute_json(req).await?;
        Ok(response.secrets)
    }

    /// Delete a secret from a devbox.
    pub async fn delete_secret(
        &self,
        name: String,
        devbox_name: Option<String>,
        repo_full_name: Option<String>,
    ) -> Result<serde_json::Value> {
        let body = DeleteSecretRequest {
            devbox_name,
            repo_full_name,
            name,
        };

        let req = self.request(Method::DELETE, "/api/secrets")?.json(&body);
        self.execute_json(req).await
    }

    // ========================================================================
    // Tool API
    // ========================================================================

    /// List tools registered for an agent.
    pub async fn list_tools(
        &self,
        execution_slug: &str,
        agent_name: &str,
    ) -> Result<Vec<String>> {
        let req = self.request(
            Method::GET,
            &format!("/api/executions/{}/agents/{}/tools", execution_slug, agent_name),
        )?;

        let response: ListToolsResponse = match self.execute_json(req).await {
            Ok(resp) => resp,
            Err(ClientError::Api { status: 404, .. }) => {
                return Err(ClientError::NotFound {
                    resource_type: "Agent".to_string(),
                    identifier: agent_name.to_string(),
                });
            }
            Err(e) => return Err(e),
        };

        Ok(response.tools)
    }

    /// Call a tool registered for an agent.
    pub async fn call_tool(
        &self,
        execution_slug: &str,
        agent_name: &str,
        tool_name: &str,
        args: HashMap<String, serde_json::Value>,
    ) -> Result<serde_json::Value> {
        let body = CallToolRequest { args };

        let req = self.request(
            Method::POST,
            &format!(
                "/api/executions/{}/agents/{}/tools/{}",
                execution_slug, agent_name, tool_name
            ),
        )?.json(&body);

        let response: CallToolResponse = match self.execute_json(req).await {
            Ok(resp) => resp,
            Err(ClientError::Api { status: 404, .. }) => {
                return Err(ClientError::NotFound {
                    resource_type: "Tool".to_string(),
                    identifier: tool_name.to_string(),
                });
            }
            Err(e) => return Err(e),
        };

        Ok(response.result)
    }

    // ========================================================================
    // Streaming API
    // ========================================================================

    /// Stream execution trace events via SSE.
    ///
    /// Returns a stream that yields activity events as they appear. The stream
    /// ends when the server sends a "done" event or the connection closes.
    ///
    /// # Arguments
    ///
    /// * `execution_slug` - The slug of the execution to stream
    /// * `raw` - Whether to skip response chunk merging
    ///
    /// # Example
    ///
    /// ```no_run
    /// use druids_client::DruidsClient;
    /// use futures::stream::StreamExt;
    ///
    /// #[tokio::main]
    /// async fn main() -> Result<(), Box<dyn std::error::Error>> {
    ///     let client = DruidsClient::from_default_config()?;
    ///     let mut stream = client.stream_execution("test-slug", false);
    ///
    ///     while let Some(event) = stream.next().await {
    ///         match event {
    ///             Ok(activity) => println!("Event: {:?}", activity.payload),
    ///             Err(e) => eprintln!("Stream error: {}", e),
    ///         }
    ///     }
    ///
    ///     Ok(())
    /// }
    /// ```
    pub fn stream_execution(
        &self,
        execution_slug: &str,
        raw: bool,
    ) -> Pin<Box<dyn Stream<Item = Result<ActivityEvent>> + Send>> {
        stream_execution(
            self.base_url.clone(),
            execution_slug.to_string(),
            self.user_access_token.clone(),
            raw,
        )
    }
}
