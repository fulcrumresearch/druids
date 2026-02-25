# Code Style

<!-- Last updated: 2c4dc6e (2026-02-06) -->

This document codifies the coding patterns used in the Orpheus codebase. See [CLAUDE.md](CLAUDE.md) for project overview, commands, and architecture.

## Python Version

Python 3.10+. Use modern syntax: `X | None` instead of `Optional[X]`, `list[str]` instead of `List[str]`, `dict[str, X]` instead of `Dict[str, X]`. Do not import `Optional`, `Union`, `List`, `Dict`, or `Tuple` from `typing`.

Use `from __future__ import annotations` in all files.

## Line Length

120 characters. Enforced by ruff.

## Naming

### Variables and Parameters

`snake_case`. Standard abbreviations:

| Abbreviation | Meaning |
|---|---|
| `inst` | MorphCloud instance |
| `conn` | agent connection |
| `msg` | message |
| `proc` | subprocess |
| `req` | request object |
| `resp` | response object |
| `db` | database session |
| `ex` | execution |

Spell out all other names. No single-letter variables except loop indices or lambdas.

### Functions

Verb-based names:
- `get_*` retrieves existing object, returns object or `None`
- `create_*` creates and persists (involves I/O)
- `make_*` constructs in memory without I/O (pure factory)
- `ensure_*` creates if absent, returns existing if present (idempotent)
- `start_*` / `stop_*` manage lifecycle transitions
- `build_*` assembles from parts
- `generate_*` produces value algorithmically
- `list_*` returns collection
- `update_*` modifies existing object
- `mark_*` sets status flag
- `send_*` transmits over connection
- `is_*` / `has_*` for boolean-returning functions

Async functions do not use an `a` prefix (the MorphCloud SDK does, but Orpheus code does not).

### Classes

`PascalCase`. No `Manager`, `Handler`, or `Service` suffixes. Use module-level functions and dataclasses instead.

Exception classes end with `Error`: `AuthError`, `NotFoundError`, `APIError`.

Request/response models use verb-noun: `CreateTaskRequest`, `StartSetupResponse`, `SendMessageRequest`.

### Private/Internal

Single leading underscore for module-private functions (`_get_execution`, `_discover_programs`, `_verify_github_signature`), class-private fields (`_branch_from`), and module-level singletons (`_client`, `_config`, `_executions`). No double-underscore name mangling.

### Constants

`UPPER_CASE` at module level: `ADJECTIVES`, `PIECES`, `BRIDGE_PATH`, `SHUTDOWN_TIMEOUT_SECONDS`, `EXECUTIONS_DIR`. Type aliases use `PascalCase`: `InstanceSource`, `CurrentUser`, `TokenResponse`.

## Type Annotations

Annotate all function parameters and return types. Exception: test fixture return types may be omitted.

Modern union syntax: `str | None`, not `Optional[str]`. Lowercase generics: `list[str]`, `dict[str, X]`, `tuple[int, ...]`.

`Literal` types instead of `Enum` for constrained strings:
```python
InstanceSource = Literal["branch", "sandbox", "base"]
```

`TYPE_CHECKING` guards to break circular imports:
```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .agent import Agent
```

See `core/execution.py:17-19` and `core/morph.py:22-23` for examples.

`Annotated` with `Depends` for FastAPI dependency types (see `api/deps.py:68-85`).

When `Any` is necessary (dynamic payloads, callback signatures), it is acceptable. Comment if the reason is not obvious.

## Data Modeling

**Domain objects**: `@dataclass` for runtime objects (`Program`, `Agent`, `Execution`, `Message`, `AgentConnection`). Order fields: required first, optional with defaults second, private/internal last. Use `field(default_factory=...)` for mutable defaults. No frozen dataclasses.

**API models**: `BaseModel` (Pydantic) for HTTP request/response schemas. Use `model_dump()` for serialization, `model_dump(by_alias=True)` when serializing for external APIs (see ACP schema usage in `connection.py:138,176`). Use `model_validate()` for deserialization.

**Database models**: `SQLModel` with `table=True`. Foreign keys use qualified suffixes: `user_id`, `task_id`. Timestamps: `created_at`, `updated_at`, `started_at`, `stopped_at`, `completed_at`. Use `sa_column=sa.Column(sa.DateTime(timezone=True))` for timezone-aware datetime columns. JSON fields use `sa_column=sa.Column(sa.JSON)` with `default_factory=dict`. No ORM relationship fields; use explicit joins.

**Configuration**: `BaseSettings` (pydantic-settings). One `Settings` class per package. `SecretStr` for sensitive values. Server config uses `ORPHEUS_` env prefix with `validation_alias` for external service keys that have standard names. Access via `from orpheus.config import settings`.

## Functions

No hard line limit. Split when a function has distinct logical phases, when a block could be named, or when readability suffers. If you add section comments inside a function, extract instead.

Keep parameter count to 1-4. For 5+, provide defaults for optional parameters. Prefer explicit parameters over `*args`/`**kwargs` unless genuinely wrapping a variadic interface.

Prefer early returns and guard clauses:
```python
if not instance_id:
    return None
# main logic here
```

## Error Handling

Prefer specific exception types. Broad `except Exception` only at true API boundaries (cleanup, shutdown, optional protocol features) and must include a comment.

Do not catch exceptions just to log and re-raise. Let exceptions propagate.

Validate inputs at API boundaries (route handlers, CLI entry points). Trust inputs in internal code. Use `HTTPException` with descriptive messages that include the resource identifier:
```python
raise HTTPException(404, f"Execution {slug} not found")
raise HTTPException(400, f"Unknown program(s): {', '.join(unknown)}")
```

Custom exception classes follow the `Error` suffix convention. The CLI defines `APIError`, `NotFoundError`, and `AuthError` in `cli/orpheus/client.py` and `cli/orpheus/auth.py`. The server defines `AuthError` in `api/auth.py`.

Do not use bare `assert` in production code. Assertions are for tests only.

`try`/`except` patterns are only used when an error is an unmovable part of some API. Do not use them superfluously.

## Logging

Use the `logging` module. Initialize at module level:
```python
logger = logging.getLogger(__name__)
```

Do not use `print()` for any output in server code. Use `logger.info()` for normal operations, `logger.warning()` for unexpected but recoverable situations, `logger.error()` for failures. Log at error boundaries: if an exception is caught and handled (not re-raised), log it.

Known cleanup: `execution.py` and `connection.py` currently have `print()` calls with `[Execution]` and `[Connection]` prefixes. These should be migrated to `logger.info()` or `logger.debug()`.

## Async

All server I/O must be async: database queries, HTTP requests, SSE streams, file I/O.

**Fire-and-forget**: `asyncio.create_task()` is acceptable for background work that does not need to be awaited (e.g., sending init prompt in `execution.py:146`). Add a comment explaining why fire-and-forget is appropriate.

**Timeouts**: Add explicit timeouts on external I/O where a hang is plausible: HTTP calls to external services, SSE streams, subprocess operations. Use `asyncio.wait_for()` for timeout enforcement (see `app.py` shutdown logic).

**Concurrency**: No explicit locks or semaphores needed (single-threaded event loop). Use `asyncio.gather()` with `return_exceptions=True` for parallel operations where partial failure is acceptable (see `morph.py:78` and `app.py` shutdown).

## Module Organization

### Import Order

1. `from __future__ import annotations`
2. Standard library imports
3. Third-party imports
4. Local package imports

Separate each group with a blank line. No relative imports (enforced by ruff). Exception: `core/__init__.py` uses relative imports for re-exports, and some core modules use relative imports within the package.

### Package Structure

Lower-level modules must not import higher-level modules. Dependency flow: `config` -> `core` / `db` -> `api`.

`core/__init__.py` re-exports the public API with `__all__`. Other `__init__.py` files should be minimal.

### Singleton Pattern

Module-level singletons use a private variable with a getter function:
```python
_client: MorphCloudClient | None = None

def get_client() -> MorphCloudClient:
    global _client
    if _client is None:
        _client = MorphCloudClient(api_key=settings.morph_api_key.get_secret_value())
    return _client
```

See `core/morph.py:31-39` for the MorphCloud client, `api/deps.py:17-22` for the executions registry, and `cli/orpheus/config.py` for the CLI config cache.

## Strings

Double quotes (enforced by ruff/formatter). f-strings for interpolation. `.format()` only for multi-line template strings where f-string readability would suffer (e.g., heredoc-style shell commands in `morph.py:152-178`).

## Docstrings

All public functions and methods must have a docstring. A function is public if it is exported from its module (not underscore-prefixed) or called from outside its defining file.

Simple functions: one-line docstring. Complex functions: Google-style with Args/Returns sections. See `core/morph.py:82-94` for a good example.

Internal/private functions do not require docstrings but add one if non-obvious.

## Pattern Matching

Prefer `match`/`case` over `if`/`elif` chains when dispatching on a value with a known set of cases. This makes the structure clearer and the cases easier to scan.

Examples:
- `cli/orpheus/auth.py:38-48` dispatches on error types
- `server/programs/program_utils.py` dispatches on agent type
- `server/orpheus/api/routes/webhooks.py` dispatches on `(event_type, action)` tuples
