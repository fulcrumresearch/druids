"""Tests for forwarding token mint/validate."""

from __future__ import annotations

import time

import jwt
import pytest
from orpheus.lib.forwarding_tokens import TOKEN_LIFETIME_SECONDS, mint_token, validate_token


def test_roundtrip():
    token = mint_token(user_id="user-1", execution_slug="exec-1", agent_name="agent-1")
    claims = validate_token(token)
    assert claims["sub"] == "user-1"
    assert claims["execution_slug"] == "exec-1"
    assert claims["agent_name"] == "agent-1"


def test_has_expiry():
    token = mint_token(user_id="user-1", execution_slug="exec-1", agent_name="agent-1")
    claims = validate_token(token)
    assert "exp" in claims
    assert "iat" in claims
    assert claims["exp"] - claims["iat"] == TOKEN_LIFETIME_SECONDS


def test_expired_token_rejected(monkeypatch):
    """A token minted in the past beyond the lifetime window is rejected."""
    past = time.time() - TOKEN_LIFETIME_SECONDS - 60
    monkeypatch.setattr(time, "time", lambda: past)
    token = mint_token(user_id="user-1", execution_slug="exec-1", agent_name="agent-1")
    # Restore real time -- now the token is expired.
    monkeypatch.undo()
    with pytest.raises(jwt.ExpiredSignatureError):
        validate_token(token)


def test_wrong_secret():
    token = jwt.encode(
        {"sub": "x", "iss": "orpheus"},
        "wrong-secret-that-is-32-bytes!!!",
        algorithm="HS256",
    )
    with pytest.raises(jwt.InvalidTokenError):
        validate_token(token)
