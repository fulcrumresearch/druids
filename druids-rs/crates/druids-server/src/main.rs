//! Druids Server binary

use druids_server::ServerConfig;

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    // Initialize tracing
    tracing_subscriber::fmt::init();

    // Load configuration
    let config = ServerConfig::from_env()?;

    tracing::info!(
        "Starting Druids server on {}:{}",
        config.host,
        config.port
    );
    tracing::info!("Sandbox type: {}", config.sandbox_type);
    tracing::info!("Base URL: {}", config.base_url);

    // TODO: Start the actual server
    tracing::warn!("Server implementation not yet complete");

    Ok(())
}
