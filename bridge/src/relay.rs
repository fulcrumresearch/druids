//! Stdout/stdin relay tasks.

use std::{collections::VecDeque, sync::Arc};
use tokio::{
    io::{AsyncBufReadExt, AsyncWriteExt, BufReader},
    process::{ChildStdin, ChildStdout},
    sync::RwLock,
};

const STDOUT_BUFFER_MAX_SIZE: usize = 1000;

/// Read lines from process stdout and append to buffer with LRU eviction.
pub async fn relay_stdout_to_buffer(
    stdout: ChildStdout,
    buffer: Arc<RwLock<Vec<String>>>,
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
                buf.push(line.clone());

                // LRU eviction: keep only last STDOUT_BUFFER_MAX_SIZE lines
                let buf_len = buf.len();
                if buf_len > STDOUT_BUFFER_MAX_SIZE {
                    buf.drain(0..(buf_len - STDOUT_BUFFER_MAX_SIZE));
                }
            }
            Err(e) => {
                tracing::error!("Error reading stdout: {}", e);
                break;
            }
        }
    }
}

/// Read messages from queue and write to process stdin.
pub async fn relay_stdin_from_queue(
    mut stdin: ChildStdin,
    queue: Arc<RwLock<VecDeque<String>>>,
) {
    let mut msg_count = 0;

    loop {
        // Wait for a message in the queue
        let msg = {
            let mut q = queue.write().await;
            q.pop_front()
        };

        if let Some(msg) = msg {
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
        } else {
            // No message available, sleep briefly
            tokio::time::sleep(tokio::time::Duration::from_millis(10)).await;
        }
    }

    tracing::info!("Stdin relay: stopped after {} messages", msg_count);
}
