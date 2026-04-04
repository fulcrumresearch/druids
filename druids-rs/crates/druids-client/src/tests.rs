//! Integration tests for the Druids client.

#[cfg(test)]
mod tests {
    use crate::client::DruidsClient;
    use mockito::{Matcher, Server};
    use std::collections::HashMap;
    use url::Url;

    async fn setup_client(server: &Server) -> DruidsClient {
        let base_url = Url::parse(&server.url()).unwrap();
        DruidsClient::with_token(base_url, "test-token".to_string()).unwrap()
    }

    #[tokio::test]
    async fn test_create_execution() {
        let mut server = Server::new_async().await;

        let mock = server
            .mock("POST", "/api/executions")
            .match_header("authorization", "Bearer test-token")
            .match_body(Matcher::Json(serde_json::json!({
                "program_source": "async def program(ctx): pass",
            })))
            .with_status(200)
            .with_header("content-type", "application/json")
            .with_body(r#"{
                "execution_slug": "test-slug",
                "execution_id": "123e4567-e89b-12d3-a456-426614174000"
            }"#)
            .create_async()
            .await;

        let client = setup_client(&server).await;

        let response = client
            .create_execution(
                "async def program(ctx): pass".to_string(),
                None,
                None,
                None,
                None,
                None,
                None,
            )
            .await
            .unwrap();

        assert_eq!(response.execution_slug, "test-slug");
        assert_eq!(response.execution_id, "123e4567-e89b-12d3-a456-426614174000");

        mock.assert_async().await;
    }

    #[tokio::test]
    async fn test_get_execution() {
        let mut server = Server::new_async().await;

        let mock = server
            .mock("GET", "/api/executions/test-slug")
            .match_header("authorization", "Bearer test-token")
            .with_status(200)
            .with_header("content-type", "application/json")
            .with_body(r#"{
                "execution_id": "123e4567-e89b-12d3-a456-426614174000",
                "execution_slug": "test-slug",
                "spec": "implement feature X",
                "repo_full_name": "owner/repo",
                "status": "running",
                "agents": ["builder"],
                "exposed_services": [],
                "client_events": [],
                "edges": []
            }"#)
            .create_async()
            .await;

        let client = setup_client(&server).await;

        let execution = client.get_execution("test-slug").await.unwrap();

        assert_eq!(execution.execution_slug, "test-slug");
        assert_eq!(execution.spec, "implement feature X");
        assert_eq!(execution.status, "running");

        mock.assert_async().await;
    }

    #[tokio::test]
    async fn test_get_execution_not_found() {
        let mut server = Server::new_async().await;

        let mock = server
            .mock("GET", "/api/executions/nonexistent")
            .match_header("authorization", "Bearer test-token")
            .with_status(404)
            .with_body("Execution 'nonexistent' not found")
            .create_async()
            .await;

        let client = setup_client(&server).await;

        let result = client.get_execution("nonexistent").await;

        assert!(result.is_err());
        if let Err(crate::error::ClientError::NotFound { resource_type, identifier }) = result {
            assert_eq!(resource_type, "Execution");
            assert_eq!(identifier, "nonexistent");
        } else {
            panic!("Expected NotFound error");
        }

        mock.assert_async().await;
    }

    #[tokio::test]
    async fn test_list_executions() {
        let mut server = Server::new_async().await;

        let mock = server
            .mock("GET", "/api/executions")
            .match_header("authorization", "Bearer test-token")
            .with_status(200)
            .with_header("content-type", "application/json")
            .with_body(r#"{
                "executions": [
                    {
                        "id": "123e4567-e89b-12d3-a456-426614174000",
                        "slug": "test-slug-1",
                        "spec": "task 1",
                        "repo_full_name": "owner/repo",
                        "status": "running"
                    },
                    {
                        "id": "223e4567-e89b-12d3-a456-426614174001",
                        "slug": "test-slug-2",
                        "spec": "task 2",
                        "repo_full_name": "owner/repo",
                        "status": "completed"
                    }
                ]
            }"#)
            .create_async()
            .await;

        let client = setup_client(&server).await;

        let executions = client.list_executions(true).await.unwrap();

        assert_eq!(executions.len(), 2);
        assert_eq!(executions[0].slug, "test-slug-1");
        assert_eq!(executions[1].slug, "test-slug-2");

        mock.assert_async().await;
    }

    #[tokio::test]
    async fn test_stop_execution() {
        let mut server = Server::new_async().await;

        let mock = server
            .mock("PATCH", "/api/executions/test-slug")
            .match_header("authorization", "Bearer test-token")
            .match_body(Matcher::Json(serde_json::json!({
                "status": "stopped"
            })))
            .with_status(200)
            .with_header("content-type", "application/json")
            .with_body(r#"{
                "execution_id": "123e4567-e89b-12d3-a456-426614174000",
                "execution_slug": "test-slug",
                "spec": "test task",
                "repo_full_name": "owner/repo",
                "status": "stopped",
                "agents": [],
                "exposed_services": [],
                "client_events": [],
                "edges": []
            }"#)
            .create_async()
            .await;

        let client = setup_client(&server).await;

        let execution = client.stop_execution("test-slug").await.unwrap();

        assert_eq!(execution.status, "stopped");

        mock.assert_async().await;
    }

    #[tokio::test]
    async fn test_send_agent_message() {
        let mut server = Server::new_async().await;

        let mock = server
            .mock("POST", "/api/executions/test-slug/agents/builder/message")
            .match_header("authorization", "Bearer test-token")
            .match_body(Matcher::Json(serde_json::json!({
                "text": "Hello agent!"
            })))
            .with_status(200)
            .with_header("content-type", "application/json")
            .with_body(r#"{"status": "sent"}"#)
            .create_async()
            .await;

        let client = setup_client(&server).await;

        let response = client
            .send_agent_message("test-slug", "builder", "Hello agent!".to_string())
            .await
            .unwrap();

        assert_eq!(response.status, "sent");

        mock.assert_async().await;
    }

    #[tokio::test]
    async fn test_list_devboxes() {
        let mut server = Server::new_async().await;

        let mock = server
            .mock("GET", "/api/devboxes")
            .match_header("authorization", "Bearer test-token")
            .with_status(200)
            .with_header("content-type", "application/json")
            .with_body(r#"{
                "devboxes": [
                    {
                        "repo_full_name": "owner/repo1",
                        "name": "devbox1",
                        "created_at": "2026-01-01T00:00:00Z"
                    },
                    {
                        "repo_full_name": "owner/repo2",
                        "name": "devbox2",
                        "created_at": "2026-01-02T00:00:00Z"
                    }
                ]
            }"#)
            .create_async()
            .await;

        let client = setup_client(&server).await;

        let devboxes = client.list_devboxes().await.unwrap();

        assert_eq!(devboxes.len(), 2);
        assert_eq!(devboxes[0].name, "devbox1");
        assert_eq!(devboxes[1].name, "devbox2");

        mock.assert_async().await;
    }

    #[tokio::test]
    async fn test_set_secrets() {
        let mut server = Server::new_async().await;

        let mut secrets = HashMap::new();
        secrets.insert("API_KEY".to_string(), "secret-value".to_string());

        let mock = server
            .mock("POST", "/api/secrets")
            .match_header("authorization", "Bearer test-token")
            .match_body(Matcher::Json(serde_json::json!({
                "devbox_name": "my-devbox",
                "secrets": {
                    "API_KEY": "secret-value"
                }
            })))
            .with_status(200)
            .with_header("content-type", "application/json")
            .with_body(r#"{"status": "ok", "count": 1}"#)
            .create_async()
            .await;

        let client = setup_client(&server).await;

        let response = client
            .set_secrets(secrets, Some("my-devbox".to_string()), None)
            .await
            .unwrap();

        assert_eq!(response.status, "ok");
        assert_eq!(response.count, 1);

        mock.assert_async().await;
    }

    #[tokio::test]
    async fn test_list_secrets() {
        let mut server = Server::new_async().await;

        let mock = server
            .mock("GET", "/api/secrets?devbox_name=my-devbox")
            .match_header("authorization", "Bearer test-token")
            .with_status(200)
            .with_header("content-type", "application/json")
            .with_body(r#"{
                "secrets": [
                    {"name": "API_KEY", "updated_at": "2026-01-01T00:00:00Z"},
                    {"name": "DB_PASSWORD", "updated_at": "2026-01-01T00:00:00Z"}
                ]
            }"#)
            .create_async()
            .await;

        let client = setup_client(&server).await;

        let secrets = client
            .list_secrets(Some("my-devbox".to_string()), None)
            .await
            .unwrap();

        assert_eq!(secrets.len(), 2);
        assert_eq!(secrets[0].name, "API_KEY");
        assert_eq!(secrets[1].name, "DB_PASSWORD");

        mock.assert_async().await;
    }

    #[tokio::test]
    async fn test_unauthorized() {
        let mut server = Server::new_async().await;

        let mock = server
            .mock("GET", "/api/executions")
            .with_status(401)
            .with_body("Unauthorized")
            .create_async()
            .await;

        let base_url = Url::parse(&server.url()).unwrap();
        let client = DruidsClient::with_token(base_url, "invalid-token".to_string()).unwrap();

        let result = client.list_executions(true).await;

        assert!(result.is_err());
        if let Err(crate::error::ClientError::Unauthorized) = result {
            // Expected
        } else {
            panic!("Expected Unauthorized error");
        }

        mock.assert_async().await;
    }
}
