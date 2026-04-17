"""Context variables shared across the runtime and process modules.

Extracted so ``druids.runtime`` doesn't need a lazy import of
``druids.process`` to reach the active process scope.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from druids.process import ProcessScope


_current_process: ContextVar["ProcessScope | None"] = ContextVar(
    "druids_current_process", default=None
)
