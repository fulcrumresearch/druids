//! Event system for runtime.

use futures::future::BoxFuture;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::sync::Arc;

/// Event data passed to handlers
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(untagged)]
pub enum EventData {
    Object(HashMap<String, serde_json::Value>),
    Value(serde_json::Value),
}

impl EventData {
    pub fn as_object(&self) -> Option<&HashMap<String, serde_json::Value>> {
        match self {
            EventData::Object(obj) => Some(obj),
            _ => None,
        }
    }

    pub fn get(&self, key: &str) -> Option<&serde_json::Value> {
        self.as_object()?.get(key)
    }

    pub fn get_str(&self, key: &str) -> Option<&str> {
        self.get(key)?.as_str()
    }

    pub fn get_i64(&self, key: &str) -> Option<i64> {
        self.get(key)?.as_i64()
    }

    pub fn get_bool(&self, key: &str) -> Option<bool> {
        self.get(key)?.as_bool()
    }
}

impl From<HashMap<String, serde_json::Value>> for EventData {
    fn from(map: HashMap<String, serde_json::Value>) -> Self {
        EventData::Object(map)
    }
}

impl From<serde_json::Value> for EventData {
    fn from(value: serde_json::Value) -> Self {
        EventData::Value(value)
    }
}

/// Event handler function type - uses Arc for cloning
pub type EventHandler = Arc<dyn Fn(EventData) -> BoxFuture<'static, ()> + Send + Sync>;

/// Registry of event handlers
#[derive(Default)]
pub struct EventRegistry {
    handlers: HashMap<String, Vec<EventHandler>>,
}

impl EventRegistry {
    pub fn new() -> Self {
        Self {
            handlers: HashMap::new(),
        }
    }

    pub fn register<F>(&mut self, event_name: impl Into<String>, handler: F)
    where
        F: Fn(EventData) -> BoxFuture<'static, ()> + Send + Sync + 'static,
    {
        let event_name = event_name.into();
        self.handlers
            .entry(event_name)
            .or_insert_with(Vec::new)
            .push(Arc::new(handler));
    }

    pub async fn dispatch(&self, event_name: &str, data: EventData) {
        if let Some(handlers) = self.handlers.get(event_name) {
            for handler in handlers {
                handler(data.clone()).await;
            }
        }
    }

    pub fn list_events(&self) -> Vec<String> {
        self.handlers.keys().cloned().collect()
    }

    /// Get handlers for a specific event (clones the Arc, cheap)
    pub fn get_handlers(&self, event_name: &str) -> Vec<EventHandler> {
        self.handlers
            .get(event_name)
            .cloned()
            .unwrap_or_default()
    }
}
