//! Druids CLI binary.

use clap::{Parser, Subcommand};

#[derive(Parser)]
#[command(name = "druids")]
#[command(about = "Druids CLI", long_about = None)]
struct Cli {
    #[command(subcommand)]
    command: Commands,
}

#[derive(Subcommand)]
enum Commands {
    /// Execute a Druids program
    Exec {
        /// Program name
        program: String,
    },
    /// Manage executions
    Execution {
        #[command(subcommand)]
        subcommand: ExecutionCommands,
    },
}

#[derive(Subcommand)]
enum ExecutionCommands {
    /// List executions
    Ls,
    /// Get execution status
    Status { slug: String },
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    tracing_subscriber::fmt::init();

    let cli = Cli::parse();

    match cli.command {
        Commands::Exec { program } => {
            println!("Executing program: {}", program);
        }
        Commands::Execution { subcommand } => match subcommand {
            ExecutionCommands::Ls => {
                println!("Listing executions...");
            }
            ExecutionCommands::Status { slug } => {
                println!("Getting status for: {}", slug);
            }
        },
    }

    Ok(())
}
