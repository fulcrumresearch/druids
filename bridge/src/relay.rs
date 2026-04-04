//! Stdout/stdin relay tasks.

use std::{collections::VecDeque, sync::Arc};
use tokio::{
    io::{AsyncBufReadExt, AsyncWriteExt, BufReader},
    process::{ChildStderr, ChildStdin, ChildStdout},
    sync::{mpsc, RwLock},
};

const STDOUT_BUFFER_MAX_SIZE: usize = 1000;

/// Read lines from process stdout and append to buffer with LRU eviction.
pub async fn relay_stdout_to_buffer(
    stdout: ChildStdout,
    buffer: Arc<RwLock<VecDeque<String>>>,
) {
    let mut reader = BufReader::new(stdout);
    let mut line = String::new();
    let mut line_count = 0;

    loop {
        line.clear();
        match reader.read_line(&mut line).await {
            Ok(0) => {
                // EOF reached
                tracing::info!("Stdout relay: pipe closed after {} lines", line_count);
                break;
            }
            Ok(_) => {
                line_count += 1;
                let mut buf = buffer.write().await;

                // Add line to buffer
                buf.push_back(line.clone());

                // LRU eviction: keep only last STDOUT_BUFFER_MAX_SIZE lines
                while buf.len() > STDOUT_BUFFER_MAX_SIZE {
                    buf.pop_front();
                }
            }
            Err(e) => {
                tracing::error!("Error reading stdout: {}", e);
                break;
            }
        }
    }
}

/// Read messages from channel and write to process stdin.
pub async fn relay_stdin_from_channel(
    mut stdin: ChildStdin,
    mut receiver: mpsc::UnboundedReceiver<String>,
) {
    let mut msg_count = 0;

    while let Some(msg) = receiver.recv().await {
        msg_count += 1;

        // Write to stdin
        match stdin.write_all(msg.as_bytes()).await {
            Ok(_) => {
                if let Err(e) = stdin.flush().await {
                    tracing::warn!("Stdin relay: flush error after {} msgs: {}", msg_count, e);
                    break;
                }
            }
            Err(e) => {
                tracing::warn!("Stdin relay: write error after {} msgs: {}", msg_count, e);
                break;
            }
        }
    }

    tracing::info!("Stdin relay: stopped after {} messages", msg_count);
}

/// Drain stderr to prevent the OS pipe buffer from filling and blocking the process.
pub async fn drain_stderr(stderr: ChildStderr) {
    let mut reader = BufReader::new(stderr);
    let mut line = String::new();
    let mut line_count = 0;

    loop {
        line.clear();
        match reader.read_line(&mut line).await {
            Ok(0) => {
                tracing::info!("Stderr drain: pipe closed after {} lines", line_count);
                break;
            }
            Ok(_) => {
                line_count += 1;
                tracing::debug!("stderr: {}", line.trim_end());
            }
            Err(e) => {
                tracing::error!("Error reading stderr: {}", e);
                break;
            }
        }
    }
}
