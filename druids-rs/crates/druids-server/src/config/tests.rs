//! Tests for server configuration.

use super::*;
use std::env;

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
fn test_config_load_with_env_vars() {
    // Set required env vars
    env::set_var("ANTHROPIC_API_KEY", "sk-ant-test-key-12345678901234567890123456789012345678901234567890");
    env::set_var("DRUIDS_HOST", "127.0.0.1");
    env::set_var("DRUIDS_PORT", "9000");
    env::set_var("DRUIDS_SANDBOX_TYPE", "docker");

    let config = ServerConfig::load(None).unwrap();

    assert_eq!(config.host, "127.0.0.1");
    assert_eq!(config.port, 9000);
    assert_eq!(config.sandbox_type, SandboxType::Docker);

    // Clean up
    env::remove_var("ANTHROPIC_API_KEY");
    env::remove_var("DRUIDS_HOST");
    env::remove_var("DRUIDS_PORT");
    env::remove_var("DRUIDS_SANDBOX_TYPE");
}

#[test]
fn test_config_validation_invalid_api_key() {
    env::set_var("ANTHROPIC_API_KEY", "invalid-key");

    let config = ServerConfig::load(None).unwrap();
    let result = config.validate();

    assert!(result.is_err());
    assert!(matches!(result.unwrap_err(), ConfigError::InvalidValue { .. }));

    env::remove_var("ANTHROPIC_API_KEY");
}

#[test]
fn test_config_missing_required() {
    env::remove_var("ANTHROPIC_API_KEY");

    let result = ServerConfig::load(None);
    assert!(result.is_err());
    assert!(matches!(result.unwrap_err(), ConfigError::MissingRequired(_)));
}

#[test]
fn test_mask_database_url() {
    let tests = vec![
        (
            "postgresql://user:password@localhost/db",
            "postgresql://user:****@localhost/db",
        ),
        (
            "postgresql://user:secret123@db.example.com:5432/mydb",
            "postgresql://user:****@db.example.com:5432/mydb",
        ),
        ("sqlite:///druids.db", "sqlite:///druids.db"),
    ];

    for (input, expected) in tests {
        assert_eq!(mask_database_url(input), expected);
    }
}
