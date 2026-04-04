//! Client configuration
//!
//! Settings are resolved in priority order:
//! 1. Environment variables (DRUIDS_BASE_URL, DRUIDS_ACCESS_TOKEN, ...)
//! 2. ~/.druids/config.json (machine-level defaults)
//! 3. Built-in defaults

use druids_core::{Error, Result};
use serde::{Deserialize, Serialize};
use std::env;
use std::fs;
use std::path::PathBuf;

/// Client configuration
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ClientConfig {
    // Machine-level (from config file or env)
    pub base_url: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub user_access_token: Option<String>,

    // Per-process (from env vars, set by bridge)
    #[serde(skip)]
    pub execution_slug: Option<String>,
    #[serde(skip)]
    pub agent_name: Option<String>,
}

impl ClientConfig {
    /// Get the default config file path (~/.druids/config.json)
    pub fn config_path() -> PathBuf {
        let home = env::var("HOME").unwrap_or_else(|_| ".".to_string());
        PathBuf::from(home).join(".druids").join("config.json")
    }

    /// Load configuration from all sources
    pub fn load() -> Result<Self> {
        // Start with defaults
        let mut config = Self::default();

        // Load from config file if it exists
        let config_path = Self::config_path();
        if config_path.exists() {
            let file_config = Self::from_file(&config_path)?;
            config.merge(file_config);
        }

        // Override with environment variables
        config.load_from_env();

        Ok(config)
    }

    /// Load configuration from a file
    pub fn from_file(path: &PathBuf) -> Result<Self> {
        let contents = fs::read_to_string(path).map_err(|e| {
            Error::config(format!("failed to read config file {:?}: {}", path, e))
        })?;

        serde_json::from_str(&contents).map_err(|e| {
            Error::config(format!("failed to parse config file {:?}: {}", path, e))
        })
    }

    /// Save configuration to the default config file
    pub fn save(&self) -> Result<()> {
        let config_path = Self::config_path();

        // Create parent directory if it doesn't exist
        if let Some(parent) = config_path.parent() {
            fs::create_dir_all(parent).map_err(|e| {
                Error::config(format!("failed to create config directory: {}", e))
            })?;
        }

        // Serialize config
        let contents = serde_json::to_string_pretty(self)
            .map_err(|e| Error::config(format!("failed to serialize config: {}", e)))?;

        // Write to file
        fs::write(&config_path, contents)
            .map_err(|e| Error::config(format!("failed to write config file: {}", e)))?;

        // Set permissions to 600 (owner read/write only)
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let mut perms = fs::metadata(&config_path)
                .map_err(|e| Error::config(format!("failed to get file metadata: {}", e)))?
                .permissions();
            perms.set_mode(0o600);
            fs::set_permissions(&config_path, perms)
                .map_err(|e| Error::config(format!("failed to set file permissions: {}", e)))?;
        }

        Ok(())
    }

    /// Load settings from environment variables
    fn load_from_env(&mut self) {
        if let Ok(url) = env::var("DRUIDS_BASE_URL") {
            self.base_url = url;
        }

        if let Ok(token) = env::var("DRUIDS_ACCESS_TOKEN") {
            self.user_access_token = Some(token);
        }

        if let Ok(slug) = env::var("DRUIDS_EXECUTION_SLUG") {
            self.execution_slug = Some(slug);
        }

        if let Ok(name) = env::var("DRUIDS_AGENT_NAME") {
            self.agent_name = Some(name);
        }
    }

    /// Merge another config into this one (other takes precedence)
    fn merge(&mut self, other: Self) {
        if !other.base_url.is_empty() {
            self.base_url = other.base_url;
        }
        if other.user_access_token.is_some() {
            self.user_access_token = other.user_access_token;
        }
    }

    /// Check if the configured server is a local instance
    pub fn is_local_server(&self) -> bool {
        self.base_url.contains("://localhost") || self.base_url.contains("://127.0.0.1")
    }
}

impl Default for ClientConfig {
    fn default() -> Self {
        ClientConfig {
            base_url: "https://druids.dev".to_string(),
            user_access_token: None,
            execution_slug: None,
            agent_name: None,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::env;

    #[test]
    fn test_default_config() {
        let config = ClientConfig::default();
        assert_eq!(config.base_url, "https://druids.dev");
        assert_eq!(config.user_access_token, None);
    }

    #[test]
    fn test_load_from_env() {
        env::set_var("DRUIDS_BASE_URL", "http://localhost:8000");
        env::set_var("DRUIDS_ACCESS_TOKEN", "test-token");

        let mut config = ClientConfig::default();
        config.load_from_env();

        assert_eq!(config.base_url, "http://localhost:8000");
        assert_eq!(config.user_access_token, Some("test-token".to_string()));

        env::remove_var("DRUIDS_BASE_URL");
        env::remove_var("DRUIDS_ACCESS_TOKEN");
    }

    #[test]
    fn test_is_local_server() {
        let mut config = ClientConfig::default();
        assert!(!config.is_local_server());

        config.base_url = "http://localhost:8000".to_string();
        assert!(config.is_local_server());

        config.base_url = "http://127.0.0.1:8000".to_string();
        assert!(config.is_local_server());
    }

    #[test]
    fn test_merge() {
        let mut config1 = ClientConfig::default();
        let mut config2 = ClientConfig::default();
        config2.base_url = "http://example.com".to_string();
        config2.user_access_token = Some("token".to_string());

        config1.merge(config2);
        assert_eq!(config1.base_url, "http://example.com");
        assert_eq!(config1.user_access_token, Some("token".to_string()));
    }

    #[test]
    fn test_serialization() {
        let config = ClientConfig {
            base_url: "http://localhost:8000".to_string(),
            user_access_token: Some("token".to_string()),
            execution_slug: Some("test-slug".to_string()),
            agent_name: Some("test-agent".to_string()),
        };

        let json = serde_json::to_string(&config).unwrap();
        assert!(json.contains("http://localhost:8000"));
        assert!(json.contains("token"));
        // execution_slug and agent_name should be skipped
        assert!(!json.contains("test-slug"));
        assert!(!json.contains("test-agent"));
    }
}
