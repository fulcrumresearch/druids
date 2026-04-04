//! Druids bridge binary.
//!
//! The bridge connects agent sandboxes to the Druids server.

use tracing::info;

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    tracing_subscriber::fmt::init();

    info!("Starting Druids bridge");

    // Placeholder main loop
    tokio::signal::ctrl_c().await?;

    info!("Shutting down");

    Ok(())
}
