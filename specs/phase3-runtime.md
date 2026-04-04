# Phase 3: Runtime SDK

**Target**: `druids-runtime` crate
**Dependencies**: Phase 1 (core-types)

## Task: Implement Program Runtime SDK

Create the runtime SDK that user programs use to spawn and coordinate agents.

### Reference Files
- `runtime/druids_runtime/*.py` - Python runtime
- `.druids/build.py`, `.druids/review.py` - Example programs

### Deliverables

**1. Program Context** (`crates/druids-runtime/src/context.rs`):
```rust
pub struct ProgramContext {
    execution_id: Uuid,
    base_url: String,
    // ... state
}

impl ProgramContext {
    pub async fn agent(
        &mut self,
        name: &str,
        prompt: Option<&str>,
        git: Option<GitMode>,
        share_machine_with: Option<&str>,
    ) -> Result<AgentHandle>;

    pub async fn done(&self, result: serde_json::Value) -> Result<()>;

    pub async fn connect(
        &mut self,
        agent1: &str,
        agent2: &str,
    ) -> Result<()>;
}
```

**2. Agent Handle** (`crates/druids-runtime/src/agent.rs`):
```rust
pub struct AgentHandle {
    name: String,
    context: Arc<Mutex<ProgramContext>>,
}

impl AgentHandle {
    pub async fn send(&self, message: &str) -> Result<()>;

    pub async fn fork(&self) -> Result<AgentHandle>;

    pub fn on<F>(&mut self, event: &str, handler: F)
    where
        F: Fn(EventData) -> BoxFuture<'static, ()> + Send + 'static;
}
```

**3. Event System** (`crates/druids-runtime/src/events.rs`):
- Event registration
- Event handler storage
- Event dispatching from server
- Custom event types

**4. Runtime Server** (`crates/druids-runtime/src/server.rs`):
- HTTP server (using `axum` or `warp`)
- Endpoints for server to call:
  - POST /ready - program ready signal
  - POST /events - receive events from agents
  - POST /client_events - receive client events
- Long-lived connection to main server

**5. Program Interface** (`crates/druids-runtime/src/program.rs`):
```rust
#[async_trait]
pub trait Program: Send + Sync {
    async fn run(&mut self, ctx: ProgramContext) -> Result<()>;
}
```

**6. Runtime State** (`crates/druids-runtime/src/state.rs`):
- Track spawned agents
- Agent topology
- Event handler registry
- Completion state

**7. Communication** (`crates/druids-runtime/src/comm.rs`):
- HTTP client to talk to main server
- Register program start
- Send topology updates
- Report events

### Success Criteria
- Programs can spawn agents
- Event handlers work
- Agent messaging works
- `ctx.done()` completes program
- File transfer between agents works
- Integration test with mock server
- Example program runs successfully

### Notes
- Match Python runtime API exactly
- Use async/await throughout
- Event handlers must be thread-safe
- Support both binary and library usage
- Programs should be able to use this as a dependency
