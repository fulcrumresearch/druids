# Behavioral Capabilities to Protect

This file tracks capabilities that must continue working as the Python codebase is translated to Rust.

## Core Execution Flow
- [ ] Start server and accept program execution requests
- [ ] Parse and validate program files
- [ ] Provision sandboxed VMs/containers for agents
- [ ] Execute agent programs with proper isolation
- [ ] Stream execution events via SSE
- [ ] Handle agent lifecycle (start, stop, cleanup)

## CLI Capabilities
- [ ] `druids exec <program>` - execute a program
- [ ] `druids execution ls` - list executions
- [ ] `druids execution status <slug>` - check execution status
- [ ] `druids execution activity <slug>` - show agent activity
- [ ] `druids execution stop <slug>` - stop an execution
- [ ] `druids execution send <slug> <message>` - message agents
- [ ] `druids execution ssh <slug>` - SSH into agent VM
- [ ] `druids execution connect <slug>` - resume agent session
- [ ] `druids devbox create/snapshot/ls` - manage devboxes

## API Endpoints
- [ ] POST /executions - create new execution
- [ ] GET /executions - list executions
- [ ] GET /executions/{slug} - get execution details
- [ ] DELETE /executions/{slug} - stop execution
- [ ] POST /executions/{slug}/messages - send message to agent
- [ ] GET /executions/{slug}/events - SSE event stream
- [ ] POST /devboxes - create devbox
- [ ] POST /devboxes/{name}/snapshot - snapshot devbox
- [ ] GET /devboxes - list devboxes

## Program SDK Features
- [ ] `ctx.agent(name, prompt, git, share_machine_with)` - spawn agent
- [ ] `agent.on(event)` decorator - register event handlers
- [ ] `agent.send(message)` - message an agent
- [ ] `agent.fork()` - clone agent with copy-on-write
- [ ] `ctx.done(result)` - complete program with result
- [ ] `ctx.connect(agent1, agent2)` - enable file transfer

## Sandbox Management
- [ ] Docker backend - run agents in containers
- [ ] MorphCloud backend - run agents on cloud VMs
- [ ] Git repository cloning into sandboxes
- [ ] Dependency installation (uv, npm, etc.)
- [ ] Environment variable injection
- [ ] File transfer between agents
- [ ] Network isolation

## Data Persistence
- [ ] Store executions in database
- [ ] Store agent state and topology
- [ ] Write execution traces to JSONL
- [ ] Track devbox lifecycle
- [ ] Handle secrets securely

## Authentication & Authorization
- [ ] API token authentication
- [ ] User-scoped resources
- [ ] Token generation and validation

## Event System
- [ ] Agent-to-program custom events
- [ ] Execution lifecycle events
- [ ] Real-time event streaming to clients
- [ ] Event filtering and routing

## Error Handling
- [ ] Graceful agent failure handling
- [ ] Execution timeout handling
- [ ] Resource cleanup on errors
- [ ] Meaningful error messages to users

## Performance Characteristics
- [ ] Handle multiple concurrent executions
- [ ] Efficient event streaming (low latency)
- [ ] Resource cleanup (no leaked containers/VMs)
- [ ] Fast program startup (<5s for simple programs)

---

As work progresses, check off capabilities that have been verified working in the Rust implementation.

## Build Quality Gates
- [x] `cargo clippy --workspace --all-targets --all-features -- -D warnings` passes with no warnings
- [x] druids-server/src/lib.rs uses `pub mod api` (no wildcard re-exports)

## Configuration System
- [x] Rust workspace compiles cleanly with 6 crates: druids-core, druids-server, druids-client, druids-runtime, druids-bridge, druids-db
- [x] Secret<T> wrapper redacts sensitive values in Debug/Display while serializing correctly for config files
- [x] ServerConfig.anthropic_api_key is Option<SecretString>; None is the true absent value; validation rejects None at startup
- [x] ServerConfig loads from DRUIDS_-prefixed env vars, auto-generates secret_key and forwarding_token_secret when absent
- [x] ClientConfig resolves settings in priority order: env vars > ~/.druids/config.json > defaults; saves config files with 600 permissions
- [x] SandboxType enum supports Docker and MorphCloud variants with serde serialization and FromStr parsing
- [x] generate_random_secret uses RandomState with multiple entropy sources; documented as non-cryptographic fallback
- [x] Rust config system loads ServerConfig from environment variables with DRUIDS_ prefix (docker/morphcloud sandbox types, API keys, port, host, DB URL, TTL)
- [x] Rust config system loads ClientConfig with priority: env vars > ~/.druids/config.json > defaults
- [x] SavedConfig.base_url is Option<Url> so any explicitly configured URL (including the default) is honoured when loading from file
- [x] Secrets (ANTHROPIC_API_KEY, DRUIDS_SECRET_KEY, FORWARDING_TOKEN_SECRET, OPENAI_API_KEY, GITHUB_PAT) are stored as SecretString and redacted in Display output
- [x] SandboxType enum serializes to/from "docker" and "morphcloud" (lowercase)
- [x] ServerConfig.validate() rejects API keys not starting with "sk-ant-" and Fernet keys != 44 chars
- [x] Database URL password is masked in Display output (user:****@host format)
- [x] ConfigError implements From<dotenvy::Error> routing IO errors to IoError and other variants to EnvFileError
- [x] `cargo clippy -p druids-core -p druids-server --all-targets --all-features -- -D warnings` passes with no warnings after SandboxType Default derive fix
- [x] Config tests that mutate environment variables are marked `#[serial]` to prevent parallel-test races
- [x] `generate_random_secret` in druids-server uses `rand::random()` (cryptographically secure), not the RandomState fallback
- [x] `secrets.rs` custom Secret<T> type removed; `secrecy::SecretString` used consistently throughout druids-core and druids-server

## Rust Core Types (druids-core)
- [x] ExecutionRecordBuilder.build() fails with an error if `spec` is not provided — no silent empty-string fallback

## Database Layer (druids-db)
- [x] druids-db integration tests compile and pass against a real Postgres database (cargo test --package druids-db -- --ignored)
- [x] druids-db encryption is documented as AES-256-GCM (not "Fernet-compatible" — Python Fernet uses AES-128-CBC+HMAC-SHA256 and the two formats are not interchangeable)

## Bridge Component (druids-bridge)
- [x] druids-bridge stdin relay uses tokio::sync::mpsc::unbounded_channel; relay_stdin_from_channel yields on receiver.recv().await with no polling
- [x] druids-bridge drains stderr via a dedicated drain_stderr task to prevent OS pipe buffer from blocking the child process
- [x] druids-bridge stdout_buffer is Arc<RwLock<VecDeque<String>>> with O(1) pop_front eviction at STDOUT_BUFFER_MAX_SIZE (1000 lines)
- [x] druids-bridge exposes POST /stdin accepting {"data": "string"} and routing through the mpsc channel
- [x] druids-bridge ProcessHandle tracks stdout_task, stdin_task, and stderr_task; all three are aborted on stop
- [x] druids-bridge stdin_sender is cleared (set to None) before killing the process on stop, closing the channel cleanly
