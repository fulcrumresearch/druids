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
///
/// # Security Note
///
/// This function uses `std::collections::hash_map::DefaultHasher` with multiple
/// entropy sources (system time, process ID, thread ID, stack address) to generate
/// secrets. While better than a simple LCG, this is NOT cryptographically secure.
///
/// For production use with security-critical secrets (database encryption keys,
/// token signing secrets), you should:
/// 1. Set the secret explicitly via environment variables, OR
/// 2. Add the `rand` crate and use `rand::thread_rng()` for cryptographically
///    secure random number generation.
///
/// This implementation is a fallback to avoid adding dependencies while still
/// providing reasonable unpredictability for development and testing scenarios.
pub fn generate_random_secret() -> String {
    use std::collections::hash_map::RandomState;
    use std::fmt::Write;
    use std::hash::{BuildHasher, Hash, Hasher};
    use std::time::{SystemTime, UNIX_EPOCH};

    // Gather multiple entropy sources
    let time_nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_nanos();
    let process_id = std::process::id();
    let thread_id = std::thread::current().id();
    let stack_addr = &time_nanos as *const u128 as usize;

    // Use RandomState (which uses random keys on creation) to mix entropy
    let random_state = RandomState::new();

    let mut secret = String::with_capacity(64);

    // Generate 4 hash values to get 32 bytes of output
    for i in 0..4 {
        let mut hasher = random_state.build_hasher();

        // Hash all entropy sources plus iteration counter
        time_nanos.hash(&mut hasher);
        process_id.hash(&mut hasher);
        thread_id.hash(&mut hasher);
        stack_addr.hash(&mut hasher);
        i.hash(&mut hasher);

        let hash = hasher.finish();

        // Convert hash (8 bytes) to 16 hex characters
        write!(&mut secret, "{:016x}", hash).unwrap();
    }

    secret
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
