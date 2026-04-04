//! ACP process management.

use crate::app::BridgeState;
use anyhow::Result;
use std::time::Instant;
use tokio::process::{Child, Command};
use tokio::task::JoinHandle;

/// Configuration for spawning an ACP process.
#[derive(Debug, Clone)]
pub struct AcpConfig {
    pub command: String,
    pub args: Vec<String>,
}

/// Handle to a running ACP process.
pub struct ProcessHandle {
    pub child: Child,
    pub pid: u32,
    pub started_at: Instant,
    pub stdout_task: Option<JoinHandle<()>>,
    pub stdin_task: Option<JoinHandle<()>>,
}

/// Spawn an ACP process with stdout/stdin relay tasks.
pub async fn spawn_acp_process(config: AcpConfig, state: BridgeState) -> Result<ProcessHandle> {
    tracing::info!("Spawning process: {} {:?}", config.command, config.args);

    // Spawn the subprocess
    let mut child = Command::new(&config.command)
        .args(&config.args)
        .stdin(std::process::Stdio::piped())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped())
        .kill_on_drop(true)
        .spawn()?;

    let pid = child.id().unwrap_or(0);
    tracing::info!("Process started with PID {}", pid);

    // Take ownership of stdio handles
    let stdout = child.stdout.take().expect("Failed to capture stdout");
    let stdin = child.stdin.take().expect("Failed to capture stdin");
    let _stderr = child.stderr.take(); // Drain stderr to prevent blocking

    // Spawn stdout relay task
    let stdout_task = tokio::spawn(crate::relay::relay_stdout_to_buffer(
        stdout,
        state.stdout_buffer.clone(),
    ));

    // Spawn stdin relay task
    let stdin_task = tokio::spawn(crate::relay::relay_stdin_from_queue(
        stdin,
        state.stdin_queue.clone(),
    ));

    Ok(ProcessHandle {
        child,
        pid,
        started_at: Instant::now(),
        stdout_task: Some(stdout_task),
        stdin_task: Some(stdin_task),
    })
}
