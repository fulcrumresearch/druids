"""Pytest configuration and fixtures."""

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from dotenv import load_dotenv
from druids_server.config import get_settings
from druids_server.paths import ENV_FILE
from fastapi.testclient import TestClient
from pydantic import SecretStr


load_dotenv(ENV_FILE)


_SECRET_FIELDS = {
    "morph_api_key",
    "secret_key",
    "anthropic_api_key",
    "forwarding_token_secret",
    "openai_api_key",
    "github_app_private_key",
    "github_client_secret",
    "github_pat",
}

_GITHUB_APP_FIELDS = (
    "github_client_id",
    "github_app_id",
    "github_app_private_key",
    "github_app_slug",
    "github_client_secret",
)

_GITHUB_APP_DEFAULTS = {
    "github_client_id": "Iv1.test",
    "github_app_id": 12345,
    "github_app_private_key": SecretStr("test-private-key"),
    "github_app_slug": "test-app",
    "github_client_secret": SecretStr("test-client-secret"),
}


def _coerce_setting_value(name: str, value: object) -> object:
    """Wrap string values for secret fields in SecretStr."""
    if name in _SECRET_FIELDS and isinstance(value, str):
        return SecretStr(value)
    return value


def make_settings(**overrides) -> SimpleNamespace:
    """Create a lightweight settings object for tests."""
    source = get_settings()
    values = {name: getattr(source, name) for name in type(source).model_fields}
    values["has_github_app"] = source.has_github_app

    requested_has_github_app = overrides.pop("has_github_app", None)
    for name, value in overrides.items():
        values[name] = _coerce_setting_value(name, value)

    if requested_has_github_app is True:
        for name, default in _GITHUB_APP_DEFAULTS.items():
            if values.get(name) is None:
                values[name] = default
        values["has_github_app"] = True
    elif requested_has_github_app is False:
        for name in _GITHUB_APP_FIELDS:
            values[name] = None
        if "github_pat" not in overrides and values.get("github_pat") is None:
            values["github_pat"] = SecretStr("ghp_testtoken")
        values["has_github_app"] = False
    else:
        values["has_github_app"] = all(values.get(name) is not None for name in _GITHUB_APP_FIELDS)

    return SimpleNamespace(**values)


@contextmanager
def patch_settings(**overrides):
    """Patch `druids_server.config.get_settings` for a test."""
    settings = make_settings(**overrides)
    with patch("druids_server.config.get_settings", return_value=settings):
        yield settings


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


