"""Sandbox -- abstract interface for isolated execution environments.

A Sandbox provides command execution, file I/O, and lifecycle management
for a single isolated environment. Backends implement this interface.
The rest of the system programs against it without knowing which backend
is active.
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


@dataclass(frozen=True)
class SSHCredentials:
    """SSH credentials for connecting to a sandbox."""

    host: str
    port: int
    username: str
    private_key: str
    password: str | None = None


class Sandbox(ABC):
    """Abstract base for sandbox implementations.

    Each Sandbox instance owns one isolated environment (a Docker container,
    a local directory, etc.). The interface is intentionally minimal: exec,
    file I/O, stop. Backend-specific capabilities live on the concrete
    subclasses.
    """

    supports_cow: bool = False
    """Whether this backend supports copy-on-write cloning for fork()."""

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

    async def snapshot(self) -> str:
        """Snapshot the current state and return an ID usable as snapshot_id.

        For Docker this commits the container to a local image. The returned
        ID can be passed back to Sandbox.create(snapshot_id=...) to start
        new sandboxes from this state.
        """
        raise NotImplementedError(f"{type(self).__name__} does not support snapshots")

    async def ssh_credentials(self) -> SSHCredentials | None:
        """Get SSH credentials for connecting to this sandbox.

        Returns None if SSH is not available. Backends that support SSH
        should override this method.
        """
        return None

    @classmethod
    async def create(
        cls,
        *,
        snapshot_id: str | None = None,
        role: str = "agent",
        metadata: dict[str, str] | None = None,
        ttl_seconds: int = 7200,
        workdir: str | None = None,
    ) -> Sandbox:
        """Create a new sandbox using the configured backend.

        The `role` selects which base image to use when `snapshot_id` is not
        provided. "agent" is the full image (node, gh, Claude Code). "runtime"
        is minimal (just Python + HTTP libraries).
        """
        from druids_server.config import settings
        from druids_server.lib.sandbox.docker import DockerSandbox

        if settings.docker_container_id:
            return await DockerSandbox.from_container_id(settings.docker_container_id, workdir=workdir)
        image = snapshot_id or settings.docker_image
        return await DockerSandbox.create(image, workdir=workdir)

    @classmethod
    async def get(cls, instance_id: str, *, workdir: str | None = None, owned: bool = False) -> Sandbox:
        """Attach to an existing sandbox by instance/container ID.

        Args:
            instance_id: Backend-specific instance or container ID.
            workdir: Working directory inside the sandbox.
            owned: If True, stop() will tear down the sandbox. Use this when
                the caller is responsible for the sandbox lifecycle (e.g.
                setup finish). Default False for read-only attachment.
        """
        from druids_server.lib.sandbox.docker import DockerSandbox

        return await DockerSandbox.from_container_id(instance_id, workdir=workdir, owned=owned)
