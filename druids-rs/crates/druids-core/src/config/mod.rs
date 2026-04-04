//! Configuration types and utilities

use serde::{Deserialize, Serialize};

pub mod loader;
pub mod secrets;

/// Sandbox backend type
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum SandboxType {
    Docker,
    #[serde(rename = "morphcloud")]
    MorphCloud,
}

impl Default for SandboxType {
    fn default() -> Self {
        SandboxType::Docker
    }
}

impl std::fmt::Display for SandboxType {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            SandboxType::Docker => write!(f, "docker"),
            SandboxType::MorphCloud => write!(f, "morphcloud"),
        }
    }
}

impl std::str::FromStr for SandboxType {
    type Err = crate::Error;

    fn from_str(s: &str) -> Result<Self, Self::Err> {
        match s.to_lowercase().as_str() {
            "docker" => Ok(SandboxType::Docker),
            "morphcloud" => Ok(SandboxType::MorphCloud),
            _ => Err(crate::Error::validation(format!(
                "invalid sandbox type: {}, expected 'docker' or 'morphcloud'",
                s
            ))),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_sandbox_type_serialization() {
        let docker = SandboxType::Docker;
        let json = serde_json::to_string(&docker).unwrap();
        assert_eq!(json, r#""docker""#);

        let morphcloud = SandboxType::MorphCloud;
        let json = serde_json::to_string(&morphcloud).unwrap();
        assert_eq!(json, r#""morphcloud""#);
    }

    #[test]
    fn test_sandbox_type_deserialization() {
        let docker: SandboxType = serde_json::from_str(r#""docker""#).unwrap();
        assert_eq!(docker, SandboxType::Docker);

        let morphcloud: SandboxType = serde_json::from_str(r#""morphcloud""#).unwrap();
        assert_eq!(morphcloud, SandboxType::MorphCloud);
    }

    #[test]
    fn test_sandbox_type_from_str() {
        use std::str::FromStr;

        assert_eq!(SandboxType::from_str("docker").unwrap(), SandboxType::Docker);
        assert_eq!(
            SandboxType::from_str("Docker").unwrap(),
            SandboxType::Docker
        );
        assert_eq!(
            SandboxType::from_str("morphcloud").unwrap(),
            SandboxType::MorphCloud
        );
        assert_eq!(
            SandboxType::from_str("MorphCloud").unwrap(),
            SandboxType::MorphCloud
        );
        assert!(SandboxType::from_str("invalid").is_err());
    }
}
