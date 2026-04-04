//! Encryption utilities for secrets.
//!
//! This module provides Fernet-compatible encryption for secrets stored in the database.
//! It uses AES-256-GCM for encryption, matching the Python cryptography.fernet implementation.

use aes_gcm::{
    aead::{Aead, KeyInit, Payload},
    Aes256Gcm, Nonce,
};
use base64::{engine::general_purpose::STANDARD as BASE64, Engine};

use crate::{DatabaseError, Result};

const NONCE_SIZE: usize = 12;

/// Encrypts a plaintext string using AES-256-GCM.
///
/// The secret key must be 32 bytes (256 bits), base64-encoded.
/// Returns a base64-encoded ciphertext in the format: nonce || ciphertext || tag.
///
/// # Arguments
///
/// * `plaintext` - The plaintext string to encrypt
/// * `secret_key` - Base64-encoded 32-byte encryption key
///
/// # Errors
///
/// Returns an error if the key is invalid or encryption fails.
pub fn encrypt(plaintext: &str, secret_key: &str) -> Result<String> {
    let key_bytes = BASE64
        .decode(secret_key)
        .map_err(|e| DatabaseError::Encryption(format!("invalid key encoding: {}", e)))?;

    if key_bytes.len() != 32 {
        return Err(DatabaseError::Encryption(format!(
            "key must be 32 bytes, got {}",
            key_bytes.len()
        )));
    }

    let cipher = Aes256Gcm::new_from_slice(&key_bytes)
        .map_err(|e| DatabaseError::Encryption(format!("failed to create cipher: {}", e)))?;

    // Generate a random nonce
    let nonce_bytes: [u8; NONCE_SIZE] = rand_nonce();
    let nonce = Nonce::from_slice(&nonce_bytes);

    // Encrypt the plaintext
    let ciphertext = cipher
        .encrypt(nonce, plaintext.as_bytes())
        .map_err(|e| DatabaseError::Encryption(format!("encryption failed: {}", e)))?;

    // Combine nonce + ciphertext (ciphertext includes the tag)
    let mut result = Vec::with_capacity(NONCE_SIZE + ciphertext.len());
    result.extend_from_slice(&nonce_bytes);
    result.extend_from_slice(&ciphertext);

    Ok(BASE64.encode(&result))
}

/// Decrypts a base64-encoded ciphertext using AES-256-GCM.
///
/// # Arguments
///
/// * `ciphertext` - Base64-encoded ciphertext (nonce || ciphertext || tag)
/// * `secret_key` - Base64-encoded 32-byte encryption key
///
/// # Errors
///
/// Returns an error if the key is invalid, ciphertext is malformed, or decryption fails.
pub fn decrypt(ciphertext: &str, secret_key: &str) -> Result<String> {
    let key_bytes = BASE64
        .decode(secret_key)
        .map_err(|e| DatabaseError::Decryption(format!("invalid key encoding: {}", e)))?;

    if key_bytes.len() != 32 {
        return Err(DatabaseError::Decryption(format!(
            "key must be 32 bytes, got {}",
            key_bytes.len()
        )));
    }

    let cipher = Aes256Gcm::new_from_slice(&key_bytes)
        .map_err(|e| DatabaseError::Decryption(format!("failed to create cipher: {}", e)))?;

    // Decode the base64 ciphertext
    let encrypted_data = BASE64
        .decode(ciphertext)
        .map_err(|e| DatabaseError::Decryption(format!("invalid base64: {}", e)))?;

    if encrypted_data.len() < NONCE_SIZE {
        return Err(DatabaseError::Decryption(
            "ciphertext too short".to_string(),
        ));
    }

    // Split nonce and ciphertext
    let (nonce_bytes, encrypted_bytes) = encrypted_data.split_at(NONCE_SIZE);
    let nonce = Nonce::from_slice(nonce_bytes);

    // Decrypt
    let plaintext_bytes = cipher
        .decrypt(nonce, encrypted_bytes)
        .map_err(|e| DatabaseError::Decryption(format!("decryption failed: {}", e)))?;

    String::from_utf8(plaintext_bytes)
        .map_err(|e| DatabaseError::Decryption(format!("invalid UTF-8: {}", e)))
}

/// Generates a random nonce for encryption.
fn rand_nonce() -> [u8; NONCE_SIZE] {
    use aes_gcm::aead::OsRng;
    use aes_gcm::aead::rand_core::RngCore;

    let mut nonce = [0u8; NONCE_SIZE];
    OsRng.fill_bytes(&mut nonce);
    nonce
}

#[cfg(test)]
mod tests {
    use super::*;

    // Generate a test key (32 bytes, base64-encoded)
    fn test_key() -> String {
        BASE64.encode(&[0u8; 32])
    }

    #[test]
    fn test_encrypt_decrypt_roundtrip() {
        let key = test_key();
        let plaintext = "Hello, World!";

        let ciphertext = encrypt(plaintext, &key).expect("encryption failed");
        let decrypted = decrypt(&ciphertext, &key).expect("decryption failed");

        assert_eq!(plaintext, decrypted);
    }

    #[test]
    fn test_encrypt_produces_different_ciphertexts() {
        let key = test_key();
        let plaintext = "same plaintext";

        let ct1 = encrypt(plaintext, &key).expect("encryption failed");
        let ct2 = encrypt(plaintext, &key).expect("encryption failed");

        // Ciphertexts should be different due to random nonces
        assert_ne!(ct1, ct2);

        // But both should decrypt to the same plaintext
        assert_eq!(decrypt(&ct1, &key).unwrap(), plaintext);
        assert_eq!(decrypt(&ct2, &key).unwrap(), plaintext);
    }

    #[test]
    fn test_decrypt_invalid_key() {
        let key = test_key();
        let plaintext = "secret data";

        let ciphertext = encrypt(plaintext, &key).expect("encryption failed");

        let wrong_key = BASE64.encode(&[1u8; 32]);
        let result = decrypt(&ciphertext, &wrong_key);

        assert!(result.is_err());
    }

    #[test]
    fn test_decrypt_invalid_ciphertext() {
        let key = test_key();
        let result = decrypt("invalid base64!", &key);
        assert!(result.is_err());
    }

    #[test]
    fn test_decrypt_short_ciphertext() {
        let key = test_key();
        let short_ct = BASE64.encode(&[0u8; 5]); // Too short
        let result = decrypt(&short_ct, &key);
        assert!(result.is_err());
    }
}
