"""Pytest configuration and fixtures."""

import pytest
from dotenv import load_dotenv
from fastapi.testclient import TestClient
from orpheus.paths import ENV_FILE


load_dotenv(ENV_FILE)


@pytest.fixture
def client():
    """Create a test client for the API."""
    from orpheus.app import app

    return TestClient(app)


@pytest.fixture
def morph_snapshot_id():
    """Get or create a test snapshot. Session-scoped for efficiency."""
    # TODO: Implement when needed for integration tests
    pytest.skip("Morph integration tests not yet configured")
