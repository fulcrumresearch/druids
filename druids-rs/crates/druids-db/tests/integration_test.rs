//! Integration tests for druids-db.
//!
//! These tests require a running PostgreSQL database.
//! Run with: cargo test --package druids-db -- --ignored

use druids_db::{create_pool, models::*, Pool};
use uuid::Uuid;

async fn setup_test_pool() -> Pool {
    let database_url = std::env::var("DATABASE_URL")
        .expect("DATABASE_URL must be set for integration tests");
    create_pool(&database_url)
        .await
        .expect("Failed to create pool")
}

fn test_secret_key() -> String {
    std::env::var("SECRET_KEY")
        .unwrap_or_else(|_| base64::engine::general_purpose::STANDARD.encode(&[0u8; 32]))
}

#[tokio::test]
#[ignore]
async fn test_user_lifecycle() {
    let pool = setup_test_pool().await;

    let github_id = rand::random::<i32>().abs();
    let github_login = format!("testuser{}", github_id);

    // Create user
    let user1 = get_or_create_user(&pool, github_id, Some(&github_login))
        .await
        .expect("Failed to create user");

    assert_eq!(user1.github_id, github_id);
    assert_eq!(user1.github_login.as_deref(), Some(github_login.as_str()));

    // Get same user again
    let user2 = get_or_create_user(&pool, github_id, Some(&github_login))
        .await
        .expect("Failed to get user");

    assert_eq!(user1.id, user2.id);

    // Get by ID
    let user3 = get_user(&pool, user1.id)
        .await
        .expect("Failed to get user by ID")
        .expect("User not found");

    assert_eq!(user1.id, user3.id);
}

#[tokio::test]
#[ignore]
async fn test_execution_lifecycle() {
    let pool = setup_test_pool().await;

    // Create test user
    let github_id = rand::random::<i32>().abs();
    let user = get_or_create_user(&pool, github_id, Some("testuser"))
        .await
        .expect("Failed to create user");

    // Create execution
    let execution = create_execution(
        &pool,
        user.id,
        "Test spec",
        Some("owner/repo"),
        None,
        None,
    )
    .await
    .expect("Failed to create execution");

    assert_eq!(execution.user_id, user.id);
    assert_eq!(execution.spec, "Test spec");
    assert_eq!(execution.repo_full_name.as_deref(), Some("owner/repo"));
    assert_eq!(execution.status, "starting");

    // Get by slug
    let fetched = get_execution_by_slug(&pool, user.id, &execution.slug)
        .await
        .expect("Failed to get execution by slug")
        .expect("Execution not found");

    assert_eq!(fetched.id, execution.id);

    // Update status
    let updated = update_execution(
        &pool,
        execution.id,
        UpdateExecution {
            status: Some("running"),
            ..Default::default()
        },
    )
    .await
    .expect("Failed to update execution")
    .expect("Execution not found");

    assert_eq!(updated.status, "running");

    // Increment usage
    increment_usage(&pool, execution.id, 100, 50, 10, 5)
        .await
        .expect("Failed to increment usage");

    let after_usage = get_execution(&pool, execution.id)
        .await
        .expect("Failed to get execution")
        .expect("Execution not found");

    assert_eq!(after_usage.input_tokens, 100);
    assert_eq!(after_usage.output_tokens, 50);
}

#[tokio::test]
#[ignore]
async fn test_devbox_lifecycle() {
    let pool = setup_test_pool().await;

    let github_id = rand::random::<i32>().abs();
    let user = get_or_create_user(&pool, github_id, Some("testuser"))
        .await
        .expect("Failed to create user");

    let repo = format!("owner/repo{}", github_id);

    // Create devbox
    let devbox = get_or_create_devbox(&pool, user.id, &repo)
        .await
        .expect("Failed to create devbox");

    assert_eq!(devbox.user_id, user.id);
    assert_eq!(devbox.repo_full_name, repo);

    // Get same devbox
    let fetched = get_devbox(&pool, user.id, &repo)
        .await
        .expect("Failed to get devbox")
        .expect("Devbox not found");

    assert_eq!(fetched.id, devbox.id);
}

#[tokio::test]
#[ignore]
async fn test_secret_lifecycle() {
    let pool = setup_test_pool().await;
    let secret_key = test_secret_key();

    let github_id = rand::random::<i32>().abs();
    let user = get_or_create_user(&pool, github_id, Some("testuser"))
        .await
        .expect("Failed to create user");

    let repo = format!("owner/repo{}", github_id);
    let devbox = get_or_create_devbox(&pool, user.id, &repo)
        .await
        .expect("Failed to create devbox");

    // Set secret
    let secret = set_secret(&pool, devbox.id, "API_KEY", "secret-value", &secret_key)
        .await
        .expect("Failed to set secret");

    assert_eq!(secret.name, "API_KEY");

    // Get secret
    let fetched = get_secret_by_name(&pool, devbox.id, "API_KEY")
        .await
        .expect("Failed to get secret")
        .expect("Secret not found");

    assert_eq!(fetched.id, secret.id);

    // Decrypt value
    let decrypted = fetched.get_value(&secret_key).expect("Failed to decrypt");
    assert_eq!(decrypted, "secret-value");

    // Get all decrypted secrets
    let all_secrets = get_decrypted_secrets(&pool, devbox.id, &secret_key)
        .await
        .expect("Failed to get decrypted secrets");

    assert_eq!(all_secrets.get("API_KEY"), Some(&"secret-value".to_string()));

    // Delete secret
    let deleted = delete_secret(&pool, devbox.id, "API_KEY")
        .await
        .expect("Failed to delete secret");

    assert!(deleted);

    // Verify deleted
    let not_found = get_secret_by_name(&pool, devbox.id, "API_KEY")
        .await
        .expect("Failed to get secret");

    assert!(not_found.is_none());
}

#[tokio::test]
#[ignore]
async fn test_program_lifecycle() {
    let pool = setup_test_pool().await;

    let github_id = rand::random::<i32>().abs();
    let user = get_or_create_user(&pool, github_id, Some("testuser"))
        .await
        .expect("Failed to create user");

    let source = "print('hello world')";

    // Create program
    let program1 = get_or_create_program(&pool, user.id, source)
        .await
        .expect("Failed to create program");

    assert_eq!(program1.source, source);

    // Get same program (deduplication)
    let program2 = get_or_create_program(&pool, user.id, source)
        .await
        .expect("Failed to get program");

    assert_eq!(program1.id, program2.id);

    // Get by ID
    let fetched = get_program(&pool, program1.id)
        .await
        .expect("Failed to get program")
        .expect("Program not found");

    assert_eq!(fetched.id, program1.id);
}
