"""Shared fixtures for API tests."""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from druids_server.api.deps import Caller, get_caller, get_executions_registry
from druids_server.api.routes import router
from druids_server.db.models.user import User
from fastapi import FastAPI
from fastapi.testclient import TestClient


def make_mock_session():
    """Async context manager yielding a MagicMock session with synchronous add()."""
    mock_db = MagicMock()
    mock_db.add = MagicMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    result.scalars.return_value.first.return_value = None
    mock_db.execute = AsyncMock(return_value=result)

    @asynccontextmanager
    async def _session():
        yield mock_db

    return _session


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
    ex.all_agent_names = MagicMock(side_effect=lambda: set(ex.agents))
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
def app(mock_user, mock_execution):
    """Create test app with mock execution in shared registry."""
    app = FastAPI()
    app.include_router(router)

    registry = get_executions_registry()
    registry.clear()
    user_id = str(mock_user.id)
    registry[user_id] = {mock_execution.slug: mock_execution}

    app.dependency_overrides[get_caller] = lambda: Caller(user=mock_user)

    yield app

    app.dependency_overrides.clear()
    registry.clear()


@pytest.fixture
def client(app):
    """Create test client."""
    return TestClient(app)


@pytest.fixture
def app_with_auth(mock_user):
    """Create test app with mocked auth (no execution in registry)."""
    app = FastAPI()
    app.include_router(router)

    registry = get_executions_registry()
    registry.clear()
    user_id = str(mock_user.id)
    registry[user_id] = {}

    yield app, mock_user

    registry.clear()
