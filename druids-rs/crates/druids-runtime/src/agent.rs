//! Agent handle implementation.

use crate::error::{Result, RuntimeError};
use crate::events::{EventData, EventHandler};
use druids_core::common::ExecResult;
use futures::future::BoxFuture;
use std::collections::HashMap;
use std::sync::Arc;
use tokio::sync::{Mutex, oneshot};

/// Handle to an agent in the execution
#[derive(Clone)]
pub struct AgentHandle {
    pub name: String,
    pub(crate) handlers: Arc<Mutex<HashMap<String, EventHandler>>>,
    pub(crate) ready_rx: Arc<Mutex<Option<oneshot::Receiver<Result<()>>>>>,
}

impl AgentHandle {
    pub(crate) fn new(name: String, ready_rx: oneshot::Receiver<Result<()>>) -> Self {
        Self {
            name,
            handlers: Arc::new(Mutex::new(HashMap::new())),
            ready_rx: Arc::new(Mutex::new(Some(ready_rx))),
        }
    }

    /// Wait for the agent to be fully provisioned
    pub(crate) async fn await_ready(&self) -> Result<()> {
        let mut rx_guard = self.ready_rx.lock().await;
        if let Some(rx) = rx_guard.take() {
            rx.await.map_err(|_| RuntimeError::other("ready channel closed"))??;
        }
        Ok(())
    }

    /// Register an event handler for this agent
    ///
    /// The handler will be called when the agent invokes the specified tool.
    pub async fn on<F>(&self, tool_name: impl Into<String>, handler: F)
    where
        F: Fn(EventData) -> BoxFuture<'static, ()> + Send + Sync + 'static,
    {
        let mut handlers = self.handlers.lock().await;
        handlers.insert(tool_name.into(), Arc::new(handler));
    }

    /// Get a handler for a tool call (returns Arc so it can be cloned)
    pub(crate) async fn get_handler(&self, tool_name: &str) -> Option<EventHandler> {
        let handlers = self.handlers.lock().await;
        handlers.get(tool_name).cloned()
    }

    /// List all registered tool names
    pub(crate) async fn list_tools(&self) -> Vec<String> {
        let handlers = self.handlers.lock().await;
        handlers.keys().cloned().collect()
    }
}

// Agent handle cannot provide send/exec/expose/fork/snapshot methods directly
// because it doesn't have a reference to ProgramContext. These operations
// must be performed through a ProgramContextHandle which wraps Arc<RwLock<ProgramContext>>.
// This is implemented in the lib.rs module.
