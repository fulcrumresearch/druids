# Druids Server

FastAPI application that orchestrates agents on MorphCloud VMs or Docker containers.

## Running

```
cd server
uv sync
uv run druids-server
```

The server starts on port 8000. It requires a `.env` file in this directory (see [the root README](../README.md) for setup).

## Server conventions

These supplement the shared Python conventions in [CLAUDE.md](../CLAUDE.md).

### Data modeling

Domain objects use `@dataclass`. API models use Pydantic `BaseModel`. Database models use `SQLModel` with `table=True`.

Constraints that are not obvious from the code:

- No ORM relationship fields on database models. Use explicit joins.
- Timestamps use `sa_column=sa.Column(sa.DateTime(timezone=True))`. Always timezone-aware.
- Use `model_dump(by_alias=True)` when serializing for external APIs (e.g. MorphCloud). Plain `model_dump()` for internal use.
- Configuration uses `BaseSettings` with `DRUIDS_` env prefix. External service keys use `validation_alias` to accept their standard env var names without the prefix.

### Error handling

`HTTPException` messages must include the resource identifier so the caller knows what failed:

```python
raise HTTPException(404, f"Execution {slug} not found")
```

Validate inputs at API boundaries (route handlers). Trust inputs in internal code.

### Module organization

Dependency flow: `config` -> `lib` / `db` -> `api`. Lower-level modules must not import higher-level modules. `lib/__init__.py` re-exports the public API with `__all__`.

## Testing

```
uv run pytest
```

Tests mirror source structure: `tests/lib/`, `tests/api/`, `tests/db/`, `tests/integration/`. Integration tests require a running server and sandbox VMs; they are skipped by default.

Gotchas:

- Prefer the shared API test helpers in `tests/api/conftest.py`: `make_api_app()`, `api_app`, `authed_app`, `authed_client`, `unauthed_client`, `execution_registry`, `make_mock_session()`, and `make_execution_record()`.
- The execution registry (`_executions` in `deps.py`) is shared mutable state. Use the `execution_registry` fixture so it is cleared before and after each test.
- Patch config through `tests.conftest.patch_settings(...)` or `make_settings(...)`, which override `druids_server.config.get_settings`. Do not patch module-local `settings` aliases.
- Shared app fixtures clear `app.dependency_overrides` for you. If a test builds a custom app manually, it still needs to clear overrides in teardown.
- Only tests that genuinely need a custom app should create their own `FastAPI()` instance. Most API tests should reuse the shared fixtures instead.

## Traces

Execution traces are logged to `~/.druids/executions/{user_id}/{slug}.jsonl`. Each line is a JSON object with timestamp, event type, agent name, and event-specific fields.
