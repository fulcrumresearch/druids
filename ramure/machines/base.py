"""Abstract ``Machine`` and ``Image`` contracts + shared dataclasses.

A ``Machine`` is a running environment an agent can run inside; an ``Image``
is a spec that can spawn one. Backends (local, morph, ...) live in sibling
modules and are re-exported from :mod:`ramure.machines`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from ramure.types import ExecResult


def _decode(data: bytes | None) -> str:
    return (data or b"").decode("utf-8", errors="replace")


@dataclass(frozen=True)
class SSHCredentials:
    """SSH credentials for connecting to a machine."""

    host: str
    port: int
    username: str
    private_key: str
    password: str | None = None


class Machine(ABC):
    """A running environment."""

    @abstractmethod
    async def exec(
        self, command: str, *, user: str = "agent", timeout: int | None = None
    ) -> ExecResult:
        raise NotImplementedError

    @abstractmethod
    async def write_file(self, path: str, content: bytes | str) -> None:
        raise NotImplementedError

    @abstractmethod
    async def read_file(self, path: str) -> bytes:
        raise NotImplementedError

    @abstractmethod
    async def stop(self) -> None:
        raise NotImplementedError

    def describe(self) -> dict[str, Any]:
        """JSON-safe summary for logging. Subclasses extend as needed."""
        return {"kind": type(self).__name__}

    async def ssh_credentials(self) -> SSHCredentials | None:
        """SSH credentials, if the backend supports SSH. Default: no SSH."""
        return None

    async def fork(self, **kwargs: Any) -> "Machine":
        """Create a copy of this running machine.

        Backends that support cheap state duplication (e.g. MorphCloud COW
        branches) override this. The default raises ``NotImplementedError``.
        Keyword arguments are backend-specific.
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not support forking"
        )

    async def snapshot(self, **kwargs: Any) -> "Image":
        """Freeze current state into a reusable :class:`Image`.

        Backends that support snapshotting (e.g. MorphCloud) override this.
        The returned ``Image`` can be passed to ``agent(..., image=...)`` or
        any other API that takes an ``Image`` to respawn the frozen state.
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not support snapshots"
        )


class Image(ABC):
    """A snapshot that can spawn into a running machine."""

    @abstractmethod
    async def spawn(self) -> Machine:
        raise NotImplementedError
