# v001: rust-bridge-skeleton

## What Changed

Implemented the initial Rust bridge component (`druids-bridge`) with proper async I/O and all HTTP endpoints.

### Key Improvements

1. **Non-blocking stdin relay**: Replaced polling-based stdin queue with `tokio::sync::mpsc::unbounded_channel`. The relay task now yields properly on `receiver.recv().await` instead of busy-waiting with 10ms sleep intervals.

2. **Stderr drainage**: Added dedicated `drain_stderr` task that continuously reads stderr lines and logs them. Prevents OS pipe buffers from filling up and blocking the child process.

3. **O(1) buffer eviction**: Changed `stdout_buffer` from `Vec<String>` to `VecDeque<String>` with `pop_front()` eviction. Eliminates O(n) shift operations when the buffer exceeds `STDOUT_BUFFER_MAX_SIZE` (1000 lines).

4. **Complete stdin endpoint**: Added `POST /stdin` endpoint accepting `{"data": "string"}` JSON payload. Routes data through the mpsc channel to the stdin relay task, completing the stdin infrastructure.

5. **Clean process lifecycle**: `ProcessHandle` now tracks all three tasks (stdout, stdin, stderr) and aborts them on stop. The `stdin_sender` is cleared before killing the process to close the channel cleanly.

### Implementation Details

- `BridgeState.stdin_sender: Arc<RwLock<Option<mpsc::UnboundedSender<String>>>>`
- `spawn_acp_process` returns `(ProcessHandle, UnboundedSender<String>)` tuple
- `ProcessHandle` contains `stdout_task`, `stdin_task`, and `stderr_task` join handles
- All tasks are properly aborted when the process stops

### Files Changed

- `druids-rs/crates/druids-bridge/src/main.rs` - Updated state and handlers
- `druids-rs/crates/druids-bridge/src/relay.rs` - New module with channel-based relay functions
- `druids-rs/crates/druids-bridge/Cargo.toml` - Removed unused reqwest dev-dependency

## Why

The original implementation had several structural issues that would cause problems in production:

- Polling stdin queue with 10ms sleep wastes CPU cycles
- Missing stderr drain could cause child process to block indefinitely
- O(n) buffer trimming becomes expensive with high stdout volume
- Incomplete stdin endpoint meant the relay infrastructure was dead code
- Missing task cleanup could lead to dangling background tasks

## Verification

All endpoints tested with running `cat` process:
- Status endpoint shows correct state and buffer size
- Start endpoint spawns process successfully
- Stdin endpoint sends data through the channel
- Stop endpoint cleans up all tasks
- Build succeeds with zero warnings

## New Goals

- [x] druids-bridge stdin relay uses tokio::sync::mpsc::unbounded_channel; relay_stdin_from_channel yields on receiver.recv().await with no polling
- [x] druids-bridge drains stderr via a dedicated drain_stderr task to prevent OS pipe buffer from blocking the child process
- [x] druids-bridge stdout_buffer is Arc<RwLock<VecDeque<String>>> with O(1) pop_front eviction at STDOUT_BUFFER_MAX_SIZE (1000 lines)
- [x] druids-bridge exposes POST /stdin accepting {"data": "string"} and routing through the mpsc channel
- [x] druids-bridge ProcessHandle tracks stdout_task, stdin_task, and stderr_task; all three are aborted on stop
- [x] druids-bridge stdin_sender is cleared (set to None) before killing the process on stop, closing the channel cleanly
