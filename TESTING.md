# Testing

<!-- Last updated: 2c4dc6e (2026-02-06) -->

Tests live in `server/tests/`, mirroring source: `tests/core/`, `tests/api/`. Files named `test_*.py`. Test classes named `Test{Feature}`, methods named `test_{action}_{condition}`.

## Fixtures

Shared fixtures in `conftest.py`. The root conftest (`tests/conftest.py`) loads `.env` at module level using manual parsing (not Pydantic). It provides a basic `client` fixture.

Most fixtures are defined per-test-file for isolation. Each API test file typically defines its own `mock_user`, `app`, and `client` fixtures. Known cleanup: `mock_user` is duplicated across test files.

## Mocking

`unittest.mock` (`MagicMock`, `AsyncMock`, `patch`). Use `AsyncMock` for any method that will be awaited. Use FastAPI's `dependency_overrides` for injecting test dependencies:

```python
app.dependency_overrides[get_current_user] = lambda: mock_user
# ... test ...
app.dependency_overrides.clear()  # Always clear in teardown
```

For complex test setup, nest `patch()` context managers. Use `side_effect` for dynamic behavior (e.g., returning different values on successive calls via `iter()`). See `tests/api/test_tasks.py` for examples.

## FastAPI Testing

Use `TestClient(app)` from `fastapi.testclient`. Create a fresh `FastAPI()` app in each test file, include only the router being tested, and set up dependency overrides. See `tests/api/test_mcp.py:53-65` for the standard fixture pattern.

The execution registry (`_executions` in `deps.py`) is shared state. Tests must populate it with mock executions and clear it in teardown.

## Async Tests

`@pytest.mark.asyncio` for async test functions. Config: `asyncio_mode = "auto"`, `asyncio_default_fixture_loop_scope = "function"` in `pyproject.toml`.

## Coverage

New code must have tests for API endpoints and domain logic. Trivial functions do not require tests. When modifying an untested module, add tests for the code you touch.
