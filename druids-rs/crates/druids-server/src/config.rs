//! Server configuration management.
//!
//! Configuration is loaded from environment variables with the `DRUIDS_` prefix.
//! Settings can also be loaded from a `.env` file using dotenvy.

use druids_core::{ConfigError, SandboxType};
use secrecy::{ExposeSecret, SecretString};
use std::env;
use std::path::PathBuf;
use std::str::FromStr;
use url::Url;

/// Server configuration.
#[derive(Debug, Clone)]
pub struct ServerConfig {
    /// Server host to bind to.
    pub host: String,

    /// Server port.
    pub port: u16,

    /// Base URL for the server (used in responses and redirects).
    pub base_url: Url,

    /// Database connection URL.
    pub database_url: String,

    /// Sandbox backend type.
    pub sandbox_type: SandboxType,

    /// Docker image to use for agent sandboxes (when sandbox_type=Docker).
    pub docker_image: String,

    /// Docker container ID to attach to (optional, for single-container mode).
    pub docker_container_id: Option<String>,

    /// Docker host for SSH/HTTP access to containers.
    pub docker_host: String,

    /// Encryption key for secrets stored in the database (Fernet-compatible).
    pub secret_key: SecretString,

    /// Anthropic API key.
    pub anthropic_api_key: SecretString,

    /// Token secret for forwarding tokens.
    pub forwarding_token_secret: SecretString,

    /// OpenAI API key (optional).
    pub openai_api_key: Option<SecretString>,

    /// GitHub PAT for cloning repos and pushing branches (optional).
    pub github_pat: Option<SecretString>,

    /// Maximum execution TTL in seconds (0 = no limit).
    pub max_execution_ttl: u64,
}

impl ServerConfig {
    /// Load server configuration from environment variables.
    ///
    /// Reads from environment variables with the `DRUIDS_` prefix.
    /// Optionally loads from a `.env` file if `env_file` is provided.
    pub fn load(env_file: Option<PathBuf>) -> Result<Self, ConfigError> {
        // Load .env file if provided
        if let Some(path) = env_file {
            dotenvy::from_path(path)?;
        } else {
            // Try loading from default .env location
            let _ = dotenvy::dotenv();
        }

        // Required fields
        let anthropic_api_key = get_secret_env("ANTHROPIC_API_KEY")
            .ok_or_else(|| ConfigError::MissingRequired("ANTHROPIC_API_KEY".to_string()))?;

        // Optional fields with defaults
        let host = get_env("DRUIDS_HOST").unwrap_or_else(|| "0.0.0.0".to_string());
        let port = get_env("DRUIDS_PORT")
            .map(|s| s.parse().map_err(|_| ConfigError::InvalidValue {
                field: "DRUIDS_PORT".to_string(),
                message: "must be a valid port number".to_string(),
            }))
            .transpose()?
            .unwrap_or(8000);

        let base_url = get_env("DRUIDS_BASE_URL")
            .unwrap_or_else(|| "http://localhost:8000".to_string());
        let base_url = Url::parse(&base_url).map_err(|e| ConfigError::InvalidValue {
            field: "DRUIDS_BASE_URL".to_string(),
            message: format!("invalid URL: {}", e),
        })?;

        let database_url = get_env("DRUIDS_DATABASE_URL")
            .unwrap_or_else(|| "sqlite+aiosqlite:///druids.db".to_string());

        let sandbox_type = get_env("DRUIDS_SANDBOX_TYPE")
            .map(|s| SandboxType::from_str(&s))
            .transpose()?
            .unwrap_or(SandboxType::Docker);

        let docker_image = get_env("DRUIDS_DOCKER_IMAGE")
            .unwrap_or_else(|| "ghcr.io/fulcrumresearch/druids-base:latest".to_string());
        let docker_container_id = get_env("DRUIDS_DOCKER_CONTAINER_ID");
        let docker_host = get_env("DRUIDS_DOCKER_HOST")
            .unwrap_or_else(|| "localhost".to_string());

        // Secrets with auto-generation
        let secret_key = get_secret_env("DRUIDS_SECRET_KEY")
            .unwrap_or_else(|| SecretString::new(generate_fernet_key()));

        let forwarding_token_secret = get_secret_env("FORWARDING_TOKEN_SECRET")
            .unwrap_or_else(|| SecretString::new(generate_random_secret()));

        let openai_api_key = get_secret_env("OPENAI_API_KEY");
        let github_pat = get_secret_env("GITHUB_PAT");

        let max_execution_ttl = get_env("DRUIDS_MAX_EXECUTION_TTL")
            .map(|s| s.parse().map_err(|_| ConfigError::InvalidValue {
                field: "DRUIDS_MAX_EXECUTION_TTL".to_string(),
                message: "must be a valid number of seconds".to_string(),
            }))
            .transpose()?
            .unwrap_or(86400); // 24 hours

        Ok(ServerConfig {
            host,
            port,
            base_url,
            database_url,
            sandbox_type,
            docker_image,
            docker_container_id,
            docker_host,
            secret_key,
            anthropic_api_key,
            forwarding_token_secret,
            openai_api_key,
            github_pat,
            max_execution_ttl,
        })
    }

    /// Validate the configuration.
    pub fn validate(&self) -> Result<(), ConfigError> {
        // Validate Anthropic API key format
        let api_key = self.anthropic_api_key.expose_secret();
        if !api_key.starts_with("sk-ant-") {
            return Err(ConfigError::InvalidValue {
                field: "ANTHROPIC_API_KEY".to_string(),
                message: "must start with 'sk-ant-'".to_string(),
            });
        }

        // Validate Fernet key format (must be 44 characters base64)
        let secret_key = self.secret_key.expose_secret();
        if secret_key.len() != 44 {
            return Err(ConfigError::InvalidValue {
                field: "DRUIDS_SECRET_KEY".to_string(),
                message: "must be a valid Fernet key (44 base64 characters)".to_string(),
            });
        }

        Ok(())
    }
}

impl std::fmt::Display for ServerConfig {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        writeln!(f, "Server Configuration:")?;
        writeln!(f, "  Host: {}", self.host)?;
        writeln!(f, "  Port: {}", self.port)?;
        writeln!(f, "  Base URL: {}", self.base_url)?;
        writeln!(f, "  Database: {}", mask_database_url(&self.database_url))?;
        writeln!(f, "  Sandbox Type: {}", self.sandbox_type)?;
        writeln!(f, "  Docker Image: {}", self.docker_image)?;
        if let Some(ref id) = self.docker_container_id {
            writeln!(f, "  Docker Container ID: {}", id)?;
        }
        writeln!(f, "  Docker Host: {}", self.docker_host)?;
        writeln!(f, "  Secret Key: [REDACTED]")?;
        writeln!(f, "  Anthropic API Key: [REDACTED]")?;
        writeln!(f, "  Forwarding Token Secret: [REDACTED]")?;
        if self.openai_api_key.is_some() {
            writeln!(f, "  OpenAI API Key: [REDACTED]")?;
        }
        if self.github_pat.is_some() {
            writeln!(f, "  GitHub PAT: [REDACTED]")?;
        }
        writeln!(f, "  Max Execution TTL: {} seconds", self.max_execution_ttl)?;
        Ok(())
    }
}

/// Get an environment variable value.
fn get_env(key: &str) -> Option<String> {
    env::var(key).ok().filter(|s| !s.is_empty())
}

/// Get a secret environment variable as SecretString.
fn get_secret_env(key: &str) -> Option<SecretString> {
    get_env(key).map(SecretString::new)
}

/// Generate a random 32-byte hex secret.
fn generate_random_secret() -> String {
    use std::fmt::Write;
    let bytes: [u8; 32] = rand::random();
    let mut s = String::with_capacity(64);
    for byte in &bytes {
        write!(&mut s, "{:02x}", byte).unwrap();
    }
    s
}

/// Generate a Fernet-compatible encryption key.
fn generate_fernet_key() -> String {
    use base64::engine::general_purpose::STANDARD;
    use base64::Engine;
    let bytes: [u8; 32] = rand::random();
    STANDARD.encode(bytes)
}

/// Mask password in database URL for safe display.
fn mask_database_url(url: &str) -> String {
    if let Some(idx) = url.find("://") {
        let after_scheme = &url[idx + 3..];
        if let Some(at_idx) = after_scheme.find('@') {
            let before_at = &after_scheme[..at_idx];
            if let Some(colon_idx) = before_at.find(':') {
                let username = &before_at[..colon_idx];
                let after_at = &after_scheme[at_idx..];
                return format!("{}://{}:****{}", &url[..idx], username, after_at);
            }
        }
    }
    url.to_string()
}

#[cfg(test)]
#[path = "config/tests.rs"]
mod tests;
