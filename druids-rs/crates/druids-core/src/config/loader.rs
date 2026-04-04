//! Configuration loading utilities
//!
//! This module provides helpers for loading configuration from multiple sources
//! with proper priority ordering:
//! 1. Environment variables
//! 2. Config file
//! 3. Defaults

use crate::{Error, Result};
use std::env;
use std::path::Path;

/// Load an environment variable with a prefix
///
/// # Example
/// ```ignore
/// let port = load_env_var("DRUIDS_PORT", "PORT")?;
/// ```
pub fn load_env_var(prefixed_name: &str, unprefixed_name: &str) -> Option<String> {
    env::var(prefixed_name)
        .ok()
        .or_else(|| env::var(unprefixed_name).ok())
}

/// Load an environment variable and parse it
pub fn load_env_var_parsed<T>(prefixed_name: &str, unprefixed_name: &str) -> Result<Option<T>>
where
    T: std::str::FromStr,
    T::Err: std::fmt::Display,
{
    match load_env_var(prefixed_name, unprefixed_name) {
        Some(val) => val.parse().map(Some).map_err(|e| {
            Error::config(format!(
                "failed to parse {} or {}: {}",
                prefixed_name, unprefixed_name, e
            ))
        }),
        None => Ok(None),
    }
}

/// Load an environment variable or return an error if missing
pub fn require_env_var(name: &str) -> Result<String> {
    env::var(name).map_err(|_| Error::config(format!("missing required environment variable: {}", name)))
}

/// Check if a config file exists and is readable
pub fn config_file_exists(path: &Path) -> bool {
    path.exists() && path.is_file()
}

/// Validate that a required field is present
pub fn validate_required<T>(field: &Option<T>, field_name: &str) -> Result<()> {
    if field.is_none() {
        return Err(Error::validation(format!(
            "missing required configuration field: {}",
            field_name
        )));
    }
    Ok(())
}

/// Generate a random hex secret (32 bytes = 64 hex chars)
pub fn generate_random_secret() -> String {
    use std::fmt::Write;
    let mut rng = rand_simple();
    let mut secret = String::with_capacity(64);
    for _ in 0..32 {
        write!(&mut secret, "{:02x}", rng.next_byte()).unwrap();
    }
    secret
}

// Simple pseudo-random number generator (no dependency on rand crate)
struct SimpleRng {
    state: u64,
}

impl SimpleRng {
    fn next_byte(&mut self) -> u8 {
        // Linear congruential generator
        self.state = self.state.wrapping_mul(6364136223846793005).wrapping_add(1);
        (self.state >> 33) as u8
    }
}

fn rand_simple() -> SimpleRng {
    use std::time::{SystemTime, UNIX_EPOCH};
    let seed = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_nanos() as u64;
    SimpleRng { state: seed }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::env;

    #[test]
    fn test_load_env_var() {
        env::set_var("TEST_VAR", "value1");
        assert_eq!(
            load_env_var("TEST_VAR", "FALLBACK_VAR"),
            Some("value1".to_string())
        );
        env::remove_var("TEST_VAR");

        env::set_var("FALLBACK_VAR", "value2");
        assert_eq!(
            load_env_var("TEST_VAR", "FALLBACK_VAR"),
            Some("value2".to_string())
        );
        env::remove_var("FALLBACK_VAR");

        assert_eq!(load_env_var("TEST_VAR", "FALLBACK_VAR"), None);
    }

    #[test]
    fn test_load_env_var_parsed() {
        env::set_var("TEST_PORT", "8080");
        let port: Option<u16> = load_env_var_parsed("TEST_PORT", "PORT").unwrap();
        assert_eq!(port, Some(8080));
        env::remove_var("TEST_PORT");
    }

    #[test]
    fn test_require_env_var() {
        env::set_var("REQUIRED_VAR", "value");
        assert!(require_env_var("REQUIRED_VAR").is_ok());
        env::remove_var("REQUIRED_VAR");

        assert!(require_env_var("MISSING_VAR").is_err());
    }

    #[test]
    fn test_validate_required() {
        assert!(validate_required(&Some("value"), "field").is_ok());
        assert!(validate_required::<String>(&None, "field").is_err());
    }

    #[test]
    fn test_generate_random_secret() {
        let secret1 = generate_random_secret();
        let secret2 = generate_random_secret();

        // Should be 64 hex characters
        assert_eq!(secret1.len(), 64);
        assert_eq!(secret2.len(), 64);

        // Should be different
        assert_ne!(secret1, secret2);

        // Should only contain hex characters
        assert!(secret1.chars().all(|c| c.is_ascii_hexdigit()));
    }
}
