from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ExecResult:
    """Result of running a command on a machine."""

    exit_code: int
    stdout: str
    stderr: str
    command: str = ""

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


class ExecutionFailed(RuntimeError):
    """Raised when a process ends via ``fail(...)``.

    The ``reason`` attribute contains the failure message."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class ToolCallError(RuntimeError):
    """An expected tool-call failure with an associated HTTP status code."""

    def __init__(self, message: str, *, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def to_jsonable(value: Any) -> Any:
    """Best-effort conversion for HTTP responses and log payloads."""

    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value):
        return {key: to_jsonable(val) for key, val in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): to_jsonable(val) for key, val in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [to_jsonable(item) for item in value]
    return str(value)
