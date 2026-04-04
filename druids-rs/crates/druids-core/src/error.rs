//! Error types for Druids

/// Result type alias for Druids operations
pub type Result<T> = std::result::Result<T, Error>;

/// Common error type for Druids
#[derive(Debug, thiserror::Error)]
pub enum Error {
    #[error("configuration error: {0}")]
    Config(String),

    #[error("validation error: {0}")]
    Validation(String),

    #[error("I/O error: {0}")]
    Io(#[from] std::io::Error),

    #[error("JSON error: {0}")]
    Json(#[from] serde_json::Error),

    #[error("{0}")]
    Other(String),
}

/// Configuration error type
#[derive(Debug, thiserror::Error)]
pub enum ConfigError {
    #[error("missing required configuration: {0}")]
    MissingRequired(String),

    #[error("invalid value for {field}: {message}")]
    InvalidValue { field: String, message: String },

    #[error("I/O error: {0}")]
    IoError(#[from] std::io::Error),

    #[error("environment file error: {0}")]
    EnvFileError(String),
}

impl From<dotenvy::Error> for ConfigError {
    fn from(err: dotenvy::Error) -> Self {
        match err {
            dotenvy::Error::Io(e) => ConfigError::IoError(e),
            e => ConfigError::EnvFileError(e.to_string()),
        }
    }
}

impl From<Error> for ConfigError {
    fn from(err: Error) -> Self {
        match err {
            Error::Config(msg) | Error::Validation(msg) => ConfigError::InvalidValue {
                field: "unknown".to_string(),
                message: msg,
            },
            Error::Io(e) => ConfigError::IoError(e),
            Error::Json(e) => ConfigError::InvalidValue {
                field: "json".to_string(),
                message: e.to_string(),
            },
            Error::Other(msg) => ConfigError::InvalidValue {
                field: "unknown".to_string(),
                message: msg,
            },
        }
    }
}

impl From<serde_json::Error> for ConfigError {
    fn from(err: serde_json::Error) -> Self {
        ConfigError::InvalidValue {
            field: "json".to_string(),
            message: err.to_string(),
        }
    }
}

impl Error {
    /// Create a configuration error
    pub fn config<S: Into<String>>(msg: S) -> Self {
        Error::Config(msg.into())
    }

    /// Create a validation error
    pub fn validation<S: Into<String>>(msg: S) -> Self {
        Error::Validation(msg.into())
    }

    /// Create a generic error
    pub fn other<S: Into<String>>(msg: S) -> Self {
        Error::Other(msg.into())
    }
}
