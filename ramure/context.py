"""Context variables shared across the runtime and process modules.

Extracted so ``ramure.runtime`` doesn't need a lazy import of
``ramure.process`` to reach the active process scope.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ramure.process import ProcessScope


_current_process: ContextVar["ProcessScope | None"] = ContextVar(
    "ramure_current_process", default=None
)
