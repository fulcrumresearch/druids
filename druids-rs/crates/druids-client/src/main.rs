//! Druids CLI binary

use clap::Parser;
use druids_client::ClientConfig;

#[derive(Parser)]
#[command(name = "druids")]
#[command(about = "Druids multi-agent orchestration CLI")]
struct Cli {
    #[command(subcommand)]
    command: Option<Command>,
}

#[derive(Parser)]
enum Command {
    /// Show configuration
    Config,
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let cli = Cli::parse();

    match cli.command {
        Some(Command::Config) => {
            let config = ClientConfig::load()?;
            println!("Base URL: {}", config.base_url);
            println!(
                "Access Token: {}",
                config
                    .user_access_token
                    .as_ref()
                    .map(|_| "[SET]")
                    .unwrap_or("[NOT SET]")
            );
            println!(
                "Execution Slug: {}",
                config
                    .execution_slug
                    .as_ref()
                    .unwrap_or(&"[NOT SET]".to_string())
            );
            println!(
                "Agent Name: {}",
                config
                    .agent_name
                    .as_ref()
                    .unwrap_or(&"[NOT SET]".to_string())
            );
        }
        None => {
            println!("Druids CLI - use --help for available commands");
        }
    }

    Ok(())
}
