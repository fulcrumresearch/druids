//! SSE (Server-Sent Events) streaming for execution events.

use crate::error::{ClientError, Result};
use async_stream::stream;
use eventsource_client::{Client as EventSourceClient, SSE};
use futures::stream::Stream;
use serde_json::Value;
use std::pin::Pin;
use url::Url;

/// Activity event from the execution stream.
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct ActivityEvent {
    /// Event ID (line number in trace file).
    pub event_id: usize,

    /// Event payload.
    pub payload: Value,
}

/// Stream execution trace events via SSE.
///
/// Returns a stream that yields activity events as they appear. The stream
/// ends when the server sends a "done" event or the connection closes.
pub fn stream_execution(
    base_url: Url,
    execution_slug: String,
    user_access_token: Option<String>,
    raw: bool,
) -> Pin<Box<dyn Stream<Item = Result<ActivityEvent>> + Send>> {
    Box::pin(stream! {
        let stream_url = base_url
            .join(&format!("/api/executions/{}/stream", execution_slug))
            .unwrap();

        let mut client_builder = EventSourceClient::for_url(stream_url.as_str())
            .map_err(|e| ClientError::Stream(format!("failed to create SSE client: {}", e)))?;

        // Add authorization header if token is present
        if let Some(token) = user_access_token {
            client_builder = client_builder.header("Authorization", &format!("Bearer {}", token))?;
        }

        // Add raw parameter if requested
        if raw {
            let mut url_with_params = stream_url;
            url_with_params.set_query(Some("raw=true"));
            client_builder = EventSourceClient::for_url(url_with_params.as_str())
                .map_err(|e| ClientError::Stream(format!("failed to create SSE client: {}", e)))?;

            if let Some(token) = user_access_token {
                client_builder = client_builder.header("Authorization", &format!("Bearer {}", token))?;
            }
        }

        let mut stream = client_builder.build();

        loop {
            match stream.next().await {
                Ok(SSE::Event(event)) => {
                    // Check event type
                    if event.event_type == "done" {
                        break;
                    }

                    if event.event_type == "activity" {
                        // Parse the data as JSON
                        match serde_json::from_str::<Value>(&event.data) {
                            Ok(payload) => {
                                let event_id = event.id.parse::<usize>().unwrap_or(0);
                                yield Ok(ActivityEvent { event_id, payload });
                            }
                            Err(e) => {
                                yield Err(ClientError::Json(e));
                                break;
                            }
                        }
                    }
                }
                Ok(SSE::Comment(_)) => {
                    // Ignore comments (keepalive)
                    continue;
                }
                Err(e) => {
                    yield Err(ClientError::Stream(format!("SSE stream error: {}", e)));
                    break;
                }
            }
        }
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_activity_event_serialization() {
        let event = ActivityEvent {
            event_id: 42,
            payload: serde_json::json!({
                "type": "tool_use",
                "agent": "test-agent",
                "tool": "bash",
            }),
        };

        let json = serde_json::to_string(&event).unwrap();
        let deserialized: ActivityEvent = serde_json::from_str(&json).unwrap();

        assert_eq!(deserialized.event_id, 42);
        assert_eq!(deserialized.payload["type"], "tool_use");
    }
}
