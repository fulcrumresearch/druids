//! Configuration types shared across Druids components.

use serde::{Deserialize, Serialize};
use std::fmt;

/// Sandbox backend type.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum SandboxType {
    /// Docker container backend.
    Docker,
    /// MorphCloud VM backend.
    #[serde(rename = "morphcloud")]
    MorphCloud,
}

impl fmt::Display for SandboxType {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            SandboxType::Docker => write!(f, "docker"),
            SandboxType::MorphCloud => write!(f, "morphcloud"),
        }
    }
}

impl std::str::FromStr for SandboxType {
    type Err = ConfigError;

    fn from_str(s: &str) -> Result<Self, Self::Err> {
        match s.to_lowercase().as_str() {
            "docker" => Ok(SandboxType::Docker),
            "morphcloud" => Ok(SandboxType::MorphCloud),
            _ => Err(ConfigError::InvalidSandboxType(s.to_string())),
        }
    }
}

/// Configuration error types.
#[derive(Debug, thiserror::Error)]
pub enum ConfigError {
    #[error("invalid sandbox type: {0} (expected 'docker' or 'morphcloud')")]
    InvalidSandboxType(String),

    #[error("missing required configuration: {0}")]
    MissingRequired(String),

    #[error("invalid configuration value for {field}: {message}")]
    InvalidValue { field: String, message: String },

    #[error("failed to read config file: {0}")]
    IoError(#[from] std::io::Error),

    #[error("failed to parse config: {0}")]
    ParseError(#[from] serde_json::Error),
}
