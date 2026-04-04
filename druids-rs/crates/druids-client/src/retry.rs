//! Retry logic with exponential backoff.

use crate::error::{ClientError, Result};
use std::time::Duration;
use backoff::ExponentialBackoff;
use backoff::future::retry;

/// Retry policy for HTTP requests.
#[derive(Debug, Clone)]
pub struct RetryPolicy {
    /// Maximum number of retry attempts.
    max_retries: u32,

    /// Initial backoff duration.
    initial_interval: Duration,

    /// Maximum backoff duration.
    max_interval: Duration,

    /// Backoff multiplier.
    multiplier: f64,
}

impl Default for RetryPolicy {
    fn default() -> Self {
        Self {
            max_retries: 3,
            initial_interval: Duration::from_millis(500),
            max_interval: Duration::from_secs(30),
            multiplier: 2.0,
        }
    }
}

impl RetryPolicy {
    /// Create a new retry policy.
    pub fn new(
        max_retries: u32,
        initial_interval: Duration,
        max_interval: Duration,
        multiplier: f64,
    ) -> Self {
        Self {
            max_retries,
            initial_interval,
            max_interval,
            multiplier,
        }
    }

    /// Execute an async operation with retry logic.
    pub async fn execute<F, Fut, T>(&self, mut operation: F) -> Result<T>
    where
        F: FnMut() -> Fut,
        Fut: std::future::Future<Output = std::result::Result<T, reqwest::Error>>,
    {
        let backoff = ExponentialBackoff {
            initial_interval: self.initial_interval,
            max_interval: self.max_interval,
            multiplier: self.multiplier,
            max_elapsed_time: None,
            ..Default::default()
        };

        let mut attempts = 0;
        let max_retries = self.max_retries;

        let result = retry(backoff, || async {
            attempts += 1;

            match operation().await {
                Ok(value) => Ok(value),
                Err(e) => {
                    // Check if we should retry
                    if attempts >= max_retries {
                        return Err(backoff::Error::Permanent(e));
                    }

                    // Retry on transient errors
                    if is_transient_error(&e) {
                        Err(backoff::Error::Transient {
                            err: e,
                            retry_after: None,
                        })
                    } else {
                        Err(backoff::Error::Permanent(e))
                    }
                }
            }
        })
        .await;

        match result {
            Ok(value) => Ok(value),
            Err(e) => Err(ClientError::Http(e)),
        }
    }
}

/// Check if an error is transient and should be retried.
fn is_transient_error(error: &reqwest::Error) -> bool {
    // Retry on network errors, timeouts, and server errors (5xx)
    if error.is_timeout() || error.is_connect() {
        return true;
    }

    // Retry on 429 (Too Many Requests) and 5xx server errors
    if let Some(status) = error.status() {
        let status_code = status.as_u16();
        if status_code == 429 || (500..600).contains(&status_code) {
            return true;
        }
    }

    false
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicU32, Ordering};
    use std::sync::Arc;

    #[tokio::test]
    async fn test_retry_succeeds_after_failures() {
        let counter = Arc::new(AtomicU32::new(0));
        let counter_clone = counter.clone();

        let policy = RetryPolicy::default();

        let result = policy
            .execute(|| {
                let c = counter_clone.clone();
                async move {
                    let count = c.fetch_add(1, Ordering::SeqCst);
                    if count < 2 {
                        // Fail first two attempts with timeout
                        Err(reqwest::Error::from(std::io::Error::new(
                            std::io::ErrorKind::TimedOut,
                            "timeout",
                        )))
                    } else {
                        Ok(42)
                    }
                }
            })
            .await;

        assert!(result.is_ok());
        assert_eq!(result.unwrap(), 42);
        assert_eq!(counter.load(Ordering::SeqCst), 3);
    }

    #[tokio::test]
    async fn test_retry_exhausted() {
        let counter = Arc::new(AtomicU32::new(0));
        let counter_clone = counter.clone();

        let policy = RetryPolicy::new(
            2,
            Duration::from_millis(10),
            Duration::from_millis(100),
            2.0,
        );

        let result = policy
            .execute(|| {
                let c = counter_clone.clone();
                async move {
                    c.fetch_add(1, Ordering::SeqCst);
                    // Always fail with timeout
                    Err(reqwest::Error::from(std::io::Error::new(
                        std::io::ErrorKind::TimedOut,
                        "timeout",
                    )))
                }
            })
            .await;

        assert!(result.is_err());
        assert_eq!(counter.load(Ordering::SeqCst), 2);
    }
}
