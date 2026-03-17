"""Fernet encryption for secrets stored in the database."""

from __future__ import annotations

from cryptography.fernet import Fernet

from druids_server.config import settings


def _fernet() -> Fernet:
    return Fernet(settings.secret_key.get_secret_value().encode())


def encrypt(plaintext: str) -> str:
    """Encrypt a string. Returns a base64-encoded ciphertext."""
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    """Decrypt a base64-encoded ciphertext back to a string."""
    return _fernet().decrypt(ciphertext.encode()).decode()
