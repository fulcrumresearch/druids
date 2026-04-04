//! Druids server binary.

mod config;

use config::ServerConfig;

fn main() {
    println!("Druids server (Rust implementation)\n");

    // Load configuration
    match ServerConfig::load(None) {
        Ok(config) => {
            // Validate the configuration
            if let Err(e) = config.validate() {
                eprintln!("Configuration validation failed: {}", e);
                std::process::exit(1);
            }

            // Print the configuration
            println!("{}", config);
            println!("Configuration loaded and validated successfully!");
        }
        Err(e) => {
            eprintln!("Failed to load configuration: {}", e);
            eprintln!("\nMake sure ANTHROPIC_API_KEY is set in environment or .env file");
            std::process::exit(1);
        }
    }
}
