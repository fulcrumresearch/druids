"""Context variables shared across the runtime and process modules.

Extracted so ``ramure.runtime`` doesn't need a lazy import of
``ramure.process`` to reach the active process scope.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import TYPE_CHECKING, Any, Awaitable, Callable

if TYPE_CHECKING:
    from ramure.process import ProcessScope


_current_process: ContextVar["ProcessScope | None"] = ContextVar(
    "ramure_current_process", default=None
)


async def _invoke_in_scope(
    scope: "ProcessScope",
    handler: Callable[..., Awaitable[Any]],
    kwargs: dict[str, Any],
) -> Any:
    """Run ``handler(**kwargs)`` with ``scope`` as the active process scope.

    Lives here, not in :mod:`ramure.process`, so the control server
    can dispatch external endpoint calls without dragging the whole
    process module through a top-level import (which would cycle:
    runtime -> control -> process -> runtime).
    """
    token = _current_process.set(scope)
    try:
        return await handler(**kwargs)
    finally:
        _current_process.reset(token)
