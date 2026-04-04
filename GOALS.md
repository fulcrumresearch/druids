# Goals - Capabilities Ratchet

This file records behavioral capabilities that MUST continue working as we translate Druids to Rust.

## Core Capabilities (To Be Verified)

These will be populated as workers complete and verify their work. Each entry represents a tested, working feature in the Rust implementation.

### Database
- [ ] SQLite connection with WAL mode
- [ ] Execution records CRUD
- [ ] User records CRUD
- [ ] Devbox records CRUD
- [ ] Secret encryption/decryption
- [ ] Alembic-compatible migrations

### Server API
- [ ] POST /executions - create execution
- [ ] GET /executions - list executions
- [ ] GET /executions/{slug} - get execution
- [ ] GET /executions/{slug}/stream - SSE event stream
- [ ] POST /executions/{slug}/stop - stop execution
- [ ] GET /me - user info
- [ ] POST /runtime/agents - provision agent
- [ ] POST /runtime/tool-calls/{id}/result - tool result
- [ ] WebSocket /bridge/pull - stdin long-poll
- [ ] POST /bridge/push - stdout batch

### Client CLI
- [ ] druids auth set-key
- [ ] druids exec {program}
- [ ] druids execution ls
- [ ] druids execution status {slug}
- [ ] druids execution stop {slug}
- [ ] druids execution send {slug} {message}
- [ ] druids devbox create
- [ ] druids devbox snapshot

### Runtime
- [ ] HTTP server on localhost:9100
- [ ] POST /call - receive tool calls
- [ ] POST /event - receive client events
- [ ] ctx.agent() - create agent
- [ ] ctx.connect() - define topology
- [ ] ctx.done() - complete execution
- [ ] agent.send() - message agent
- [ ] agent.on() - register handler

### Bridge
- [ ] POST /start - start ACP subprocess
- [ ] GET /status - process health
- [ ] POST /stop - terminate process
- [ ] Stdout batching (256 lines)
- [ ] Stdin long-polling (20s timeout)

### Execution Engine
- [ ] In-memory execution registry
- [ ] Agent provisioning (async)
- [ ] Tool call dispatch
- [ ] Client event routing
- [ ] Topology enforcement
- [ ] Trace logging (JSONL)

### Machine Abstraction
- [ ] Git clone
- [ ] Git branch creation
- [ ] Bridge deployment
- [ ] Package installation
- [ ] Child VM provisioning

### Sandbox (Docker)
- [ ] Container create/start
- [ ] File write via tar
- [ ] File read via tar
- [ ] Command execution
- [ ] Container stop/remove
- [ ] Container snapshot (commit)

### Connection Layer
- [ ] BridgeRelayHub (reverse relay)
- [ ] AgentConnection (ACP wrapper)
- [ ] Queue-based stdin/stdout
- [ ] Permission auto-approval

### Security
- [ ] JWT token minting
- [ ] JWT token validation
- [ ] Secret encryption (Fernet equivalent)
- [ ] GitHub token refresh

### Utilities
- [ ] Execution slug generation
- [ ] Trace append (JSONL)
- [ ] Template variable resolution
- [ ] GitHub installation token fetch

## Test Coverage Targets

- Server: 80%+ line coverage
- Client: 70%+ line coverage
- Runtime: 80%+ line coverage
- Bridge: 80%+ line coverage

## Performance Benchmarks

Once baseline Rust implementation is working, we'll establish benchmarks and ensure all optimizations maintain or improve:

- Server startup time
- Execution creation latency
- Tool call round-trip time
- Trace write throughput
- Memory footprint (idle server)
- Concurrent execution capacity

## Non-Goals

These are explicitly out of scope:
- Frontend translation (stays Vue.js)
- .druids programs (stay Python, executed via runtime)
- ACP agent binaries (external dependency)
- MorphCloud SDK (external dependency)
