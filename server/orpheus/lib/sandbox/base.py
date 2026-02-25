"""Sandbox -- abstract interface for isolated execution environments.

A Sandbox provides command execution, file I/O, and lifecycle management
for a single isolated environment. Backends (Docker, MorphCloud) implement
this interface. The rest of the system programs against it without knowing
which backend is active.
"""

from __future__ import annotations

import posixpath
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class ExecResult:
    """Result of running a command inside a sandbox."""

    command: str
    exit_code: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


class Sandbox(ABC):
    """Abstract base for sandbox implementations.

    Each Sandbox instance owns one isolated environment (a Docker container,
    a MorphCloud VM, etc.). The interface is intentionally minimal: exec,
    file I/O, stop. Backend-specific capabilities (port exposure, forking,
    SSH) live on the concrete subclasses.
    """

    def __init__(self, instance_id: str, workdir: str | None = None) -> None:
        self.instance_id = instance_id
        self.workdir = workdir

    def _resolve_path(self, path: str) -> str:
        """Resolve path relative to workdir if not absolute."""
        if posixpath.isabs(path):
            return posixpath.normpath(path)
        base = self.workdir or "/"
        return posixpath.normpath(posixpath.join(base, path))

    @abstractmethod
    async def exec(self, command: str, *, timeout: int = 120, user: str | None = None) -> ExecResult:
        """Run a shell command inside the sandbox.

        Args:
            command: Shell command string.
            timeout: Seconds before the command is killed. Returns exit_code=124 on timeout.
            user: Run as this user (backend-specific, may be ignored).
        """

    @abstractmethod
    async def read_file(self, path: str) -> bytes:
        """Read a file from inside the sandbox. Path resolved relative to workdir."""

    @abstractmethod
    async def write_file(self, path: str, content: str | bytes) -> None:
        """Write a file into the sandbox. Creates parent directories as needed."""

    @abstractmethod
    async def stop(self) -> None:
        """Stop and clean up the sandbox."""
