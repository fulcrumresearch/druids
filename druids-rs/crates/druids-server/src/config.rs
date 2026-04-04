//! Server configuration
//!
//! Configuration is loaded from environment variables with the DRUIDS_ prefix.
//! Required settings must be provided, while optional settings have defaults.

use druids_core::config::secrets::SecretString;
use druids_core::config::SandboxType;
use druids_core::{Error, Result};
use std::env;

/// Server configuration
#[derive(Debug, Clone)]
pub struct ServerConfig {
    // Server
    pub host: String,
    pub port: u16,
    pub base_url: String,

    // Database
    pub database_url: String,

    // Sandbox
    pub sandbox_type: SandboxType,

    // Docker (when sandbox_type = Docker)
    pub docker_image: String,
    pub docker_container_id: Option<String>,
    pub docker_host: String,

    // Encryption (for secrets in database)
    pub secret_key: SecretString,

    // API Keys
    pub anthropic_api_key: SecretString,
    pub forwarding_token_secret: SecretString,
    pub openai_api_key: Option<SecretString>,
    pub github_pat: Option<SecretString>,

    // Execution limits
    pub max_execution_ttl: u64,
}

impl ServerConfig {
    /// Load configuration from environment variables
    pub fn from_env() -> Result<Self> {
        // Load .env file if present
        dotenvy::dotenv().ok();

        let config = ServerConfig {
            // Server settings
            host: env::var("DRUIDS_HOST").unwrap_or_else(|_| "0.0.0.0".to_string()),
            port: env::var("DRUIDS_PORT")
                .ok()
                .and_then(|s| s.parse().ok())
                .unwrap_or(8000),
            base_url: env::var("DRUIDS_BASE_URL")
                .unwrap_or_else(|_| "http://localhost:8000".to_string()),

            // Database
            database_url: env::var("DRUIDS_DATABASE_URL")
                .unwrap_or_else(|_| "sqlite://druids.db".to_string()),

            // Sandbox
            sandbox_type: env::var("DRUIDS_SANDBOX_TYPE")
                .ok()
                .map(|s| s.parse())
                .transpose()?
                .unwrap_or_default(),

            // Docker
            docker_image: env::var("DRUIDS_DOCKER_IMAGE").unwrap_or_else(|_| {
                "ghcr.io/fulcrumresearch/druids-base:latest".to_string()
            }),
            docker_container_id: env::var("DRUIDS_DOCKER_CONTAINER_ID").ok(),
            docker_host: env::var("DRUIDS_DOCKER_HOST")
                .unwrap_or_else(|_| "localhost".to_string()),

            // Secrets
            secret_key: Self::load_or_generate_secret("DRUIDS_SECRET_KEY")?,
            forwarding_token_secret: Self::load_or_generate_secret(
                "DRUIDS_FORWARDING_TOKEN_SECRET",
            )?,

            // API Keys
            anthropic_api_key: env::var("ANTHROPIC_API_KEY")
                .map(SecretString::new)
                .map_err(|_| Error::config("ANTHROPIC_API_KEY is required"))?,
            openai_api_key: env::var("OPENAI_API_KEY").ok().map(SecretString::new),
            github_pat: env::var("GITHUB_PAT").ok().map(SecretString::new),

            // Execution limits
            max_execution_ttl: env::var("DRUIDS_MAX_EXECUTION_TTL")
                .ok()
                .and_then(|s| s.parse().ok())
                .unwrap_or(86400), // 24 hours
        };

        config.validate()?;
        Ok(config)
    }

    /// Load a secret from env or generate a random one
    fn load_or_generate_secret(env_var: &str) -> Result<SecretString> {
        Ok(match env::var(env_var) {
            Ok(val) => SecretString::new(val),
            Err(_) => {
                let secret = druids_core::config::loader::generate_random_secret();
                tracing::warn!(
                    "{} not set, generated random secret (will be different on restart)",
                    env_var
                );
                SecretString::new(secret)
            }
        })
    }

    /// Validate the configuration
    fn validate(&self) -> Result<()> {
        // Validate port
        if self.port == 0 {
            return Err(Error::validation("port must be non-zero"));
        }

        // Validate database URL
        if self.database_url.is_empty() {
            return Err(Error::validation("database_url cannot be empty"));
        }

        // Validate base URL
        if self.base_url.is_empty() {
            return Err(Error::validation("base_url cannot be empty"));
        }

        Ok(())
    }
}

impl Default for ServerConfig {
    fn default() -> Self {
        ServerConfig {
            host: "0.0.0.0".to_string(),
            port: 8000,
            base_url: "http://localhost:8000".to_string(),
            database_url: "sqlite://druids.db".to_string(),
            sandbox_type: SandboxType::default(),
            docker_image: "ghcr.io/fulcrumresearch/druids-base:latest".to_string(),
            docker_container_id: None,
            docker_host: "localhost".to_string(),
            secret_key: SecretString::new(
                druids_core::config::loader::generate_random_secret(),
            ),
            anthropic_api_key: SecretString::new("".to_string()),
            forwarding_token_secret: SecretString::new(
                druids_core::config::loader::generate_random_secret(),
            ),
            openai_api_key: None,
            github_pat: None,
            max_execution_ttl: 86400,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::env;

    #[test]
    fn test_default_config() {
        let config = ServerConfig::default();
        assert_eq!(config.host, "0.0.0.0");
        assert_eq!(config.port, 8000);
        assert_eq!(config.sandbox_type, SandboxType::Docker);
    }

    #[test]
    fn test_load_from_env() {
        env::set_var("DRUIDS_PORT", "9000");
        env::set_var("ANTHROPIC_API_KEY", "test-key");

        let config = ServerConfig::from_env().unwrap();
        assert_eq!(config.port, 9000);
        assert_eq!(config.anthropic_api_key.expose(), "test-key");

        env::remove_var("DRUIDS_PORT");
        env::remove_var("ANTHROPIC_API_KEY");
    }

    #[test]
    fn test_validation_zero_port() {
        let mut config = ServerConfig::default();
        config.port = 0;
        assert!(config.validate().is_err());
    }

    #[test]
    fn test_validation_empty_database_url() {
        let mut config = ServerConfig::default();
        config.database_url = String::new();
        assert!(config.validate().is_err());
    }
}
