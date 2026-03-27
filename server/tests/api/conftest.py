"""Shared fixtures for API tests."""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from druids_server.api import routes
from druids_server.api.deps import Caller, get_caller, get_executions_registry
from druids_server.db.models.user import User
from fastapi import FastAPI
from fastapi.testclient import TestClient


def make_api_app() -> FastAPI:
    """Build a fresh FastAPI app with the API router mounted."""
    app = FastAPI()
    app.include_router(routes.create_router())
    return app


def make_mock_session(
    *,
    scalar_one_or_none: object | None = None,
    scalars_first: object | None = None,
    db: MagicMock | None = None,
    include_db: bool = False,
):
    """Build an async session factory backed by a MagicMock database session."""
    mock_db = db if db is not None else MagicMock()
    mock_db.add = MagicMock()
    mock_db.flush = AsyncMock()
    mock_db.refresh = AsyncMock()
    mock_db.delete = AsyncMock()

    if db is None or scalar_one_or_none is not None or scalars_first is not None:
        result = MagicMock()
        result.scalar_one_or_none.return_value = scalar_one_or_none
        result.scalars.return_value.first.return_value = scalars_first
        mock_db.execute = AsyncMock(return_value=result)

    @asynccontextmanager
    async def _session():
        yield mock_db

    if include_db:
        return _session, mock_db
    return _session


def make_execution_record(**overrides) -> SimpleNamespace:
    """Build a lightweight execution record with sensible API-test defaults."""
    record = {
        "id": uuid4(),
        "slug": SLUG,
        "user_id": uuid4(),
        "spec": "test spec",
        "repo_full_name": "test/repo",
        "status": "running",
        "error": None,
        "metadata_": {},
        "branch_name": None,
        "pr_url": None,
        "program_id": None,
        "started_at": None,
        "stopped_at": None,
        "agents": [],
        "edges": [],
    }
    record.update(overrides)
    return SimpleNamespace(**record)


SLUG = "exec-123"


@pytest.fixture
def mock_user():
    """Create a mock user."""
    return User(
        id=uuid4(),
        github_id=12345,
    )


@pytest.fixture
def mock_execution():
    """Create a mock Execution instance with common fields."""
    ex = MagicMock()
    ex.id = uuid4()
    ex.slug = SLUG
    ex.agents = {}
    ex.has_agent = MagicMock(side_effect=lambda name: name in ex.agents)
    ex.all_agent_names = MagicMock(side_effect=lambda: list(ex.agents))
    ex.send = AsyncMock()
    ex.shutdown_agent = AsyncMock()
    ex.exposed_services = []
    return ex


@pytest.fixture
def mock_agent():
    """Create a mock Agent with a Machine."""
    agent = MagicMock()
    agent.name = "worker"
    mock_machine = MagicMock()
    mock_machine.instance_id = "instance_1"
    mock_machine.stop = AsyncMock()
    mock_machine.ssh_credentials = AsyncMock()
    mock_machine.expose_http_service = AsyncMock()
    agent.machine = mock_machine
    return agent


@pytest.fixture
def execution_registry():
    """Clear and yield the shared execution registry for a test."""
    registry = get_executions_registry()
    registry.clear()
    yield registry
    registry.clear()


@pytest.fixture
def api_app(execution_registry):
    """Create a fresh app with no auth overrides."""
    app = make_api_app()
    yield app
    app.dependency_overrides.clear()


@pytest.fixture
def authed_app(api_app, execution_registry, mock_user):
    """Create an app with a mock caller and an empty user-scoped registry."""
    execution_registry[str(mock_user.id)] = {}
    api_app.dependency_overrides[get_caller] = lambda: Caller(user=mock_user)
    return api_app


@pytest.fixture
def authed_client(authed_app):
    """Create a test client for an authenticated app."""
    return TestClient(authed_app)


@pytest.fixture
def unauthed_client(api_app):
    """Create a test client for an app without auth overrides."""
    return TestClient(api_app)


@pytest.fixture
def app(authed_app, execution_registry, mock_user, mock_execution):
    """Create an authenticated app with one mock execution in the registry."""
    execution_registry[str(mock_user.id)][mock_execution.slug] = mock_execution
    return authed_app


@pytest.fixture
def client(app):
    """Create test client."""
    return TestClient(app)


@pytest.fixture
def app_with_auth(api_app, execution_registry, mock_user):
    """Create a test app with a seeded registry but no auth override."""
    execution_registry[str(mock_user.id)] = {}
    yield api_app, mock_user
