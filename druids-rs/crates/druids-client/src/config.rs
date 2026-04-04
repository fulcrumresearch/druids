//! Client configuration management.
//!
//! Configuration is resolved in priority order:
//! 1. Environment variables (`DRUIDS_BASE_URL`, `DRUIDS_ACCESS_TOKEN`, etc.)
//! 2. `~/.druids/config.json` (machine-level defaults)
//! 3. Built-in defaults

use druids_core::ConfigError;
use serde::{Deserialize, Serialize};
use std::env;
use std::fs;
use std::path::PathBuf;
use url::Url;

/// Client configuration.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ClientConfig {
    /// Base URL of the Druids server.
    pub base_url: Url,

    /// User access token for API authentication (optional).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub user_access_token: Option<String>,

    /// Execution slug (set by bridge when running in agent context).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub execution_slug: Option<String>,

    /// Agent name (set by bridge when running in agent context).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub agent_name: Option<String>,
}

impl Default for ClientConfig {
    fn default() -> Self {
        Self {
            base_url: Url::parse("https://druids.dev").unwrap(),
            user_access_token: None,
            execution_slug: None,
            agent_name: None,
        }
    }
}

impl ClientConfig {
    /// Load client configuration from all sources.
    ///
    /// Configuration is resolved in priority order:
    /// 1. Environment variables
    /// 2. Config file (`~/.druids/config.json`)
    /// 3. Defaults
    pub fn load() -> Result<Self, ConfigError> {
        let mut config = Self::default();

        // Load from config file if it exists
        if let Some(config_path) = get_config_path() {
            if config_path.exists() {
                let content = fs::read_to_string(&config_path)?;
                let file_config: ClientConfig = serde_json::from_str(&content)?;

                // Merge with defaults
                if file_config.base_url.as_str() != "https://druids.dev" {
                    config.base_url = file_config.base_url;
                }
                if file_config.user_access_token.is_some() {
                    config.user_access_token = file_config.user_access_token;
                }
            }
        }

        // Override with environment variables
        if let Some(base_url) = get_env("DRUIDS_BASE_URL") {
            config.base_url = Url::parse(&base_url).map_err(|e| ConfigError::InvalidValue {
                field: "DRUIDS_BASE_URL".to_string(),
                message: format!("invalid URL: {}", e),
            })?;
        }

        if let Some(token) = get_env("DRUIDS_ACCESS_TOKEN") {
            config.user_access_token = Some(token);
        }

        if let Some(slug) = get_env("DRUIDS_EXECUTION_SLUG") {
            config.execution_slug = Some(slug);
        }

        if let Some(name) = get_env("DRUIDS_AGENT_NAME") {
            config.agent_name = Some(name);
        }

        Ok(config)
    }

    /// Save configuration to `~/.druids/config.json`.
    pub fn save(&self) -> Result<(), ConfigError> {
        let config_path = get_config_path().ok_or_else(|| {
            ConfigError::InvalidValue {
                field: "config_path".to_string(),
                message: "could not determine home directory".to_string(),
            }
        })?;

        // Create parent directory if it doesn't exist
        if let Some(parent) = config_path.parent() {
            fs::create_dir_all(parent)?;
        }

        // Create a minimal config to save (only base_url and user_access_token)
        let save_config = SavedConfig {
            base_url: self.base_url.clone(),
            user_access_token: self.user_access_token.clone(),
        };

        let content = serde_json::to_string_pretty(&save_config)?;
        fs::write(&config_path, content)?;

        // Set file permissions to 600 (owner read/write only)
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let mut perms = fs::metadata(&config_path)?.permissions();
            perms.set_mode(0o600);
            fs::set_permissions(&config_path, perms)?;
        }

        Ok(())
    }

    /// Check if the configured server is a local instance.
    pub fn is_local_server(&self) -> bool {
        let url = self.base_url.as_str();
        url.contains("://localhost") || url.contains("://127.0.0.1")
    }
}

impl std::fmt::Display for ClientConfig {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        writeln!(f, "Client Configuration:")?;
        writeln!(f, "  Base URL: {}", self.base_url)?;
        if self.user_access_token.is_some() {
            writeln!(f, "  Access Token: [SET]")?;
        } else {
            writeln!(f, "  Access Token: [NOT SET]")?;
        }
        if let Some(ref slug) = self.execution_slug {
            writeln!(f, "  Execution Slug: {}", slug)?;
        }
        if let Some(ref name) = self.agent_name {
            writeln!(f, "  Agent Name: {}", name)?;
        }
        Ok(())
    }
}

/// Configuration structure for saving to disk (subset of ClientConfig).
#[derive(Serialize, Deserialize)]
struct SavedConfig {
    base_url: Url,
    #[serde(skip_serializing_if = "Option::is_none")]
    user_access_token: Option<String>,
}

/// Get the path to the config file (`~/.druids/config.json`).
fn get_config_path() -> Option<PathBuf> {
    dirs::home_dir().map(|home| home.join(".druids").join("config.json"))
}

/// Get an environment variable value.
fn get_env(key: &str) -> Option<String> {
    env::var(key).ok().filter(|s| !s.is_empty())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::env;

    #[test]
    fn test_default_config() {
        let config = ClientConfig::default();
        assert_eq!(config.base_url.as_str(), "https://druids.dev");
        assert!(config.user_access_token.is_none());
        assert!(config.execution_slug.is_none());
        assert!(config.agent_name.is_none());
    }

    #[test]
    fn test_is_local_server() {
        let mut config = ClientConfig::default();
        assert!(!config.is_local_server());

        config.base_url = Url::parse("http://localhost:8000").unwrap();
        assert!(config.is_local_server());

        config.base_url = Url::parse("http://127.0.0.1:8000").unwrap();
        assert!(config.is_local_server());
    }

    #[test]
    fn test_load_from_env() {
        env::set_var("DRUIDS_BASE_URL", "http://localhost:9000");
        env::set_var("DRUIDS_ACCESS_TOKEN", "test-token");
        env::set_var("DRUIDS_EXECUTION_SLUG", "test-slug");
        env::set_var("DRUIDS_AGENT_NAME", "test-agent");

        let config = ClientConfig::load().unwrap();
        assert_eq!(config.base_url.as_str(), "http://localhost:9000/");
        assert_eq!(config.user_access_token, Some("test-token".to_string()));
        assert_eq!(config.execution_slug, Some("test-slug".to_string()));
        assert_eq!(config.agent_name, Some("test-agent".to_string()));

        // Clean up
        env::remove_var("DRUIDS_BASE_URL");
        env::remove_var("DRUIDS_ACCESS_TOKEN");
        env::remove_var("DRUIDS_EXECUTION_SLUG");
        env::remove_var("DRUIDS_AGENT_NAME");
    }

    #[test]
    fn test_save_and_load_config() {
        use tempfile::TempDir;

        // Create a temporary directory
        let temp_dir = TempDir::new().unwrap();
        let config_path = temp_dir.path().join("config.json");

        // Create a config
        let mut config = ClientConfig::default();
        config.base_url = Url::parse("http://test.example.com").unwrap();
        config.user_access_token = Some("test-token".to_string());

        // Save config
        let content = serde_json::to_string_pretty(&SavedConfig {
            base_url: config.base_url.clone(),
            user_access_token: config.user_access_token.clone(),
        })
        .unwrap();
        fs::write(&config_path, content).unwrap();

        // Load config
        let loaded_content = fs::read_to_string(&config_path).unwrap();
        let loaded: SavedConfig = serde_json::from_str(&loaded_content).unwrap();

        assert_eq!(loaded.base_url, config.base_url);
        assert_eq!(loaded.user_access_token, config.user_access_token);
    }
}
