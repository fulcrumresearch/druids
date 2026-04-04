//! ACP process management.

use crate::app::BridgeState;
use anyhow::Result;
use std::time::Instant;
use tokio::{
    process::{Child, Command},
    sync::mpsc,
    task::JoinHandle,
};

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
    pub stderr_task: Option<JoinHandle<()>>,
}

/// Spawn an ACP process with stdout/stdin relay tasks.
/// Returns the process handle and the stdin sender channel.
pub async fn spawn_acp_process(
    config: AcpConfig,
    state: BridgeState,
) -> Result<(ProcessHandle, mpsc::UnboundedSender<String>)> {
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
    let stderr = child.stderr.take().expect("Failed to capture stderr");

    // Create stdin channel
    let (stdin_sender, stdin_receiver) = mpsc::unbounded_channel();

    // Spawn stdout relay task
    let stdout_task = tokio::spawn(crate::relay::relay_stdout_to_buffer(
        stdout,
        state.stdout_buffer.clone(),
    ));

    // Spawn stdin relay task
    let stdin_task = tokio::spawn(crate::relay::relay_stdin_from_channel(stdin, stdin_receiver));

    // Spawn stderr drain task
    let stderr_task = tokio::spawn(crate::relay::drain_stderr(stderr));

    Ok((
        ProcessHandle {
            child,
            pid,
            started_at: Instant::now(),
            stdout_task: Some(stdout_task),
            stdin_task: Some(stdin_task),
            stderr_task: Some(stderr_task),
        },
        stdin_sender,
    ))
}
