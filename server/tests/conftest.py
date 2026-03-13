"""Pytest configuration and fixtures."""

import pytest
from dotenv import load_dotenv
from druids_server.paths import ENV_FILE
from fastapi.testclient import TestClient


load_dotenv(ENV_FILE)


def pytest_addoption(parser):
    """Add --slow flag to include slow tests."""
    parser.addoption("--slow", action="store_true", default=False, help="include slow tests")


def pytest_collection_modifyitems(config, items):
    """Skip tests marked @pytest.mark.slow unless --slow is passed."""
    if config.getoption("--slow"):
        return
    skip = pytest.mark.skip(reason="slow test, use --slow to run")
    for item in items:
        if "slow" in item.keywords:
            item.add_marker(skip)


@pytest.fixture
def client():
    """Create a test client for the API."""
    from druids_server.app import app

    return TestClient(app)


