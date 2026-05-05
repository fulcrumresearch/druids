"""MorphCloud backend.

Adapted from ``druids_server.lib.sandbox.morph`` in
github.com/fulcrumresearch/druids (the production-grade server-side
MorphCloud Sandbox), fitted to codex-druids' lightweight
``Machine`` / ``Image`` interface.

The ``morphcloud`` SDK is imported lazily so it stays an optional extra
(``pip install ramure[morph]``).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import posixpath
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping

from ramure.machines.base import Image, Machine, SSHCredentials
from ramure.types import ExecResult

if TYPE_CHECKING:
    from morphcloud.api import Instance, MorphCloudClient

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Default "pi-ready" recipe
# ---------------------------------------------------------------------------

#: Shell recipe used by ``MorphImage()`` when no ``recipe`` / ``id``
#: is provided. Installs node, tmux, pi + pi-coding-agent, uv, gh, and a
#: non-root ``agent`` user that can sudo -- everything druids needs on the
#: VM to launch an agent. Adapted from the ``_DRUIDS_BASE_RECIPE`` in
#: fulcrumresearch/druids.
DEFAULT_MORPH_RECIPE = """\
set -e
apt-get update && apt-get install -y git curl ca-certificates sudo vim tmux python3-pip
curl -fsSL https://deb.nodesource.com/setup_lts.x | bash -
apt-get install -y nodejs

# uv for Python package management
curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh

# GitHub CLI
curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg | dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg
chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" | tee /etc/apt/sources.list.d/github-cli.list > /dev/null
apt-get update && apt-get install -y gh

# pi coding agent (ships the `pi` CLI druids uses to launch agents) + claude-code
npm install -g @anthropic-ai/claude-code@latest @mariozechner/pi-coding-agent@latest

# Non-root user with passwordless sudo (druids launches agents as `agent`)
useradd -m -s /bin/bash agent
echo 'agent ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/agent
chmod 440 /etc/sudoers.d/agent

# Flush filesystem buffers so the resulting snapshot sees all written files
sync
"""

#: Version tag bumped whenever ``DEFAULT_MORPH_RECIPE`` changes in a way that
#: requires rebuilding the snapshot.
DEFAULT_MORPH_RECIPE_VERSION = "druids-codex-agent-v1"


# ---------------------------------------------------------------------------
# Client pool + retry
# ---------------------------------------------------------------------------

_CLIENTS: list["MorphCloudClient"] = []
_CLIENT_IDX: int = 0
_POOL_SIZE = int(os.environ.get("MORPH_CLIENT_POOL_SIZE", "4"))


def _get_client(api_key: str | None = None) -> "MorphCloudClient":
    """Round-robin a pool of MorphCloud clients.

    Each client has its own httpx connection pool; spreading work across
    several prevents a single pool from becoming a bottleneck under high
    concurrency. The ``morphcloud`` package is imported here so it stays
    an optional dependency.
    """
    global _CLIENT_IDX
    try:
        from morphcloud.api import MorphCloudClient
    except ImportError as exc:  # pragma: no cover - exercised by extras tests
        raise ImportError(
            "The MorphCloud backend requires the 'morphcloud' package. "
            "Install it with `pip install ramure[morph]` or `pip install morphcloud`."
        ) from exc

    if not _CLIENTS:
        for _ in range(_POOL_SIZE):
            _CLIENTS.append(
                MorphCloudClient(api_key=api_key) if api_key else MorphCloudClient()
            )
    client = _CLIENTS[_CLIENT_IDX % len(_CLIENTS)]
    _CLIENT_IDX += 1
    return client


async def _retry(fn, retries: int = 3, backoff: float = 1.0):
    """Retry a MorphCloud API call on 502/503/timeout."""
    from morphcloud.api import ApiError

    for attempt in range(retries):
        try:
            return await fn()
        except ApiError as exc:
            if exc.status_code in (502, 503) and attempt < retries - 1:
                logger.warning(
                    "MorphCloud %d, retry %d/%d", exc.status_code, attempt + 1, retries
                )
                await asyncio.sleep(backoff * (attempt + 1))
                continue
            raise
        except (TimeoutError, OSError) as exc:
            if attempt < retries - 1:
                logger.warning(
                    "MorphCloud %s, retry %d/%d",
                    type(exc).__name__,
                    attempt + 1,
                    retries,
                )
                await asyncio.sleep(backoff * (attempt + 1))
                continue
            raise


def _shell_quote(s: str) -> str:
    """Quote a string for embedding in ``bash -c '...'``."""
    return "'" + s.replace("'", "'\"'\"'") + "'"


# ---------------------------------------------------------------------------
# MorphMachine
# ---------------------------------------------------------------------------


class MorphMachine(Machine):
    """Machine backed by a MorphCloud VM instance."""

    def __init__(
        self,
        instance: "Instance",
        *,
        workdir: str | None = None,
    ) -> None:
        self._instance = instance
        self.instance_id = instance.id
        self.workdir = workdir

    # -- factories ---------------------------------------------------------

    @classmethod
    async def attach(
        cls,
        instance_id: str,
        *,
        api_key: str | None = None,
        workdir: str | None = None,
    ) -> "MorphMachine":
        """Attach to an existing MorphCloud instance without booting a new one."""
        client = _get_client(api_key)
        instance = await client.instances.aget(instance_id)
        return cls(instance, workdir=workdir)

    # -- Machine interface -------------------------------------------------

    def describe(self) -> dict[str, Any]:
        return {
            "kind": "MorphMachine",
            "instance_id": self.instance_id,
            "workdir": self.workdir,
        }

    def _resolve_path(self, path: str) -> str:
        if posixpath.isabs(path):
            return posixpath.normpath(path)
        base = self.workdir or "/"
        return posixpath.normpath(posixpath.join(base, path))

    async def exec(
        self,
        command: str,
        *,
        user: str = "agent",
        timeout: int | None = None,
    ) -> ExecResult:
        await self._ensure_running()
        if user and user != "root":
            wrapped = f"sudo -u {user} bash -c {_shell_quote(command)}"
        else:
            wrapped = command

        exec_timeout = timeout if timeout is not None else 300
        try:
            result = await _retry(
                lambda: self._instance.aexec(wrapped, timeout=exec_timeout)
            )
        except asyncio.TimeoutError:
            return ExecResult(
                exit_code=124,
                stdout="",
                stderr=f"Timed out after {exec_timeout}s",
                command=command,
            )

        stdout = getattr(result, "stdout", None)
        stderr = getattr(result, "stderr", None)
        return ExecResult(
            exit_code=result.exit_code,
            stdout=stdout if stdout is not None else str(result),
            stderr=stderr if stderr is not None else "",
            command=command,
        )

    async def read_file(self, path: str) -> bytes:
        resolved = self._resolve_path(path)
        fd, local_path = tempfile.mkstemp(prefix="druids-morph-")
        os.close(fd)
        try:
            await self._instance.adownload(resolved, local_path)
            return await asyncio.to_thread(Path(local_path).read_bytes)
        finally:
            try:
                os.unlink(local_path)
            except FileNotFoundError:
                pass

    async def write_file(self, path: str, content: bytes | str) -> None:
        resolved = self._resolve_path(path)
        data = content.encode("utf-8") if isinstance(content, str) else content

        parent = resolved.rsplit("/", 1)[0] if "/" in resolved else "/"
        await self._instance.aexec(f"mkdir -p {parent}")

        fd, local_path = tempfile.mkstemp(prefix="druids-morph-")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            await self._instance.aupload(local_path, resolved)
        finally:
            try:
                os.unlink(local_path)
            except FileNotFoundError:
                pass

    async def stop(self) -> None:
        await self._instance.astop()
        logger.info("MorphMachine.stop instance=%s", self.instance_id)

    # -- Morph-specific capabilities --------------------------------------

    async def ssh_credentials(self) -> SSHCredentials:
        key = await self._instance.assh_key()
        return SSHCredentials(
            host="ssh.cloud.morph.so",
            port=22,
            username=self.instance_id,
            private_key=key.private_key,
            password=getattr(key, "password", None),
        )

    async def expose_http_service(self, name: str, port: int) -> str:
        """Expose ``port`` on the VM as a public HTTPS URL; idempotent."""
        from morphcloud.api import ApiError

        try:
            return await self._instance.aexpose_http_service(name, port)
        except ApiError as exc:
            if exc.status_code != 409:
                raise
            await self._instance._refresh_async()
            return next(
                svc.url
                for svc in self._instance.networking.http_services
                if svc.name == name
            )

    async def snapshot(self, **kwargs: Any) -> "MorphImage":
        """Freeze current VM state into a :class:`MorphImage`.

        The returned image can be passed to ``agent(image=...)`` or
        ``machine(image=...)`` to respawn the frozen state. The image
        inherits the VM's resource spec and this machine's ``workdir``
        (override via ``workdir=`` kwarg).
        """
        await self._instance.aexec("sync")
        snap = await _retry(lambda: self._instance.asnapshot())
        spec = getattr(self._instance, "spec", None)
        image_kwargs: dict[str, Any] = {
            "id": snap.id,
            "workdir": kwargs.pop("workdir", self.workdir),
        }
        if spec is not None:
            image_kwargs.setdefault("vcpus", getattr(spec, "vcpus", 2))
            image_kwargs.setdefault("memory", getattr(spec, "memory", 4096))
            image_kwargs.setdefault("disk_size", getattr(spec, "disk_size", 10240))
        image_kwargs.update(kwargs)
        return MorphImage(**image_kwargs)

    async def fork(
        self,
        *,
        metadata: dict[str, str] | None = None,
        workdir: str | None = None,
        ttl_seconds: int | None = None,
        ttl_action: str = "pause",
        clean_agents: bool = True,
    ) -> "MorphMachine":
        """Create a copy-on-write child VM from this one.

        The child inherits ``workdir`` unless overridden. Setting
        ``ttl_seconds`` is recommended so the child pauses itself if the
        caller forgets to stop it.

        ``clean_agents`` (default ``True``) kills any ramure agent tmux
        sessions left over on the forked VM from the parent's process
        tree. MorphCloud's ``abranch`` preserves running processes, so
        without this the child would inherit a zombie ``pi`` agent still
        trying to impersonate the parent agent on the runtime. User
        processes and non-ramure tmux sessions are left untouched.
        """
        await self._ensure_running()
        _, children = await _retry(lambda: self._instance.abranch(1))
        child = children[0]
        if metadata:
            await child.aset_metadata(metadata)
        if ttl_seconds is not None and ttl_seconds > 0:
            try:
                await child.aset_ttl(ttl_seconds=ttl_seconds, ttl_action=ttl_action)
            except Exception:  # pragma: no cover - best effort
                logger.warning(
                    "MorphMachine.fork: failed to set TTL on child %s",
                    child.id,
                    exc_info=True,
                )
        await child.await_until_ready()
        forked = MorphMachine(child, workdir=workdir or self.workdir)
        if clean_agents:
            await forked._kill_ramure_agents()
        logger.info(
            "MorphMachine.fork child=%s parent=%s ttl=%s",
            child.id,
            self.instance_id,
            ttl_seconds,
        )
        return forked

    async def _kill_ramure_agents(self) -> None:
        """Kill any ramure-* tmux sessions inherited from a COW parent.

        Best-effort: failures are logged but never raised. This only
        touches sessions whose name matches ``ramure-*``; anything else
        the user was running is preserved.
        """
        try:
            await self._instance.aexec(
                "tmux list-sessions -F '#{session_name}' 2>/dev/null "
                "| grep '^ramure-' "
                "| xargs -r -I{} tmux kill-session -t {} "
                "2>/dev/null || true"
            )
        except Exception:  # pragma: no cover - best effort
            logger.warning(
                "MorphMachine.fork: failed to clean ramure agents on %s",
                self.instance_id,
                exc_info=True,
            )

    async def resume(self) -> None:
        """Resume the instance if it is currently paused."""
        await self._instance._refresh_async()
        if self._instance.status == "paused":
            logger.info("MorphMachine.resume instance=%s", self.instance_id)
            await self._instance.aresume()
            await self._instance.await_until_ready()

    async def _ensure_running(self) -> None:
        await self._instance._refresh_async()
        if self._instance.status == "paused":
            await self.resume()


# ---------------------------------------------------------------------------
# MorphImage
# ---------------------------------------------------------------------------


class MorphImage(Image):
    """Image that spawns a MorphCloud VM.

    Construct with an ``id`` (the snapshot id) to boot a specific snapshot,
    or with a ``recipe`` (shell script) to build + cache a snapshot keyed
    on ``base_image`` + ``version`` + a hash of the recipe. With no
    arguments the default pi-ready recipe is used so ``MorphImage()``
    "just works".

    The :attr:`id` attribute is the Morph snapshot id and is the reload
    key: ``MorphImage(id=image.id)`` boots the same snapshot. For images
    constructed from a recipe, ``id`` is ``None`` until the recipe has
    been resolved (lazily, on first :meth:`spawn`).
    """

    #: Default base image used when building snapshots by recipe.
    DEFAULT_BASE_IMAGE = "morphvm-minimal"

    #: Resource defaults applied **only** when building a snapshot from a
    #: recipe. ``acreate`` needs concrete ints; an existing snapshot has its
    #: own baked-in spec we don't want to override silently.
    _RECIPE_BUILD_DEFAULTS = {"vcpus": 2, "memory": 4096, "disk_size": 10240}

    def __init__(
        self,
        *,
        id: str | None = None,
        recipe: str | None = None,
        version: str = "druids-v1",
        base_image: str = DEFAULT_BASE_IMAGE,
        vcpus: int | None = None,
        memory: int | None = None,
        disk_size: int | None = None,
        api_key: str | None = None,
        ttl_seconds: int | None = None,
        ttl_action: str = "pause",
        metadata: Mapping[str, str] | None = None,
        workdir: str | None = None,
    ) -> None:
        if id is None and recipe is None:
            # Fall back to the druids default agent recipe so `MorphImage()`
            # with no args boots a pi-ready VM.
            recipe = DEFAULT_MORPH_RECIPE
            if version == "druids-v1":
                version = DEFAULT_MORPH_RECIPE_VERSION
        # ``vcpus`` / ``memory`` / ``disk_size`` default to ``None`` so the
        # ``id=`` (existing snapshot) path inherits the snapshot's own
        # baked-in spec instead of silently overriding it with hardcoded
        # numbers. They were previously ``2 / 4096 / 10240`` -- which meant
        # ``MorphImage(id="snap_xyz")`` quietly booted a 2-vCPU / 4GB VM
        # regardless of what ``snap_xyz`` was built for. Recipe-built
        # snapshots fall back to ``_RECIPE_BUILD_DEFAULTS`` in
        # :meth:`_resolve_id` because ``acreate`` needs concrete ints.
        self.id: str | None = id
        self.recipe = recipe
        self.version = version
        self.base_image = base_image
        self.vcpus = vcpus
        self.memory = memory
        self.disk_size = disk_size
        self.api_key = api_key or os.environ.get("MORPH_API_KEY")
        self.ttl_seconds = ttl_seconds
        self.ttl_action = ttl_action
        self.metadata = dict(metadata or {})
        self.workdir = workdir

    async def _resolve_id(self) -> str:
        """Return the snapshot id, building from the recipe if needed.

        Memoizes on ``self.id`` so subsequent calls are free, and so the
        recipe-built snapshot id becomes visible to callers reading
        ``image.id`` after a spawn.

        Resource args fall back to :attr:`_RECIPE_BUILD_DEFAULTS` here
        because ``acreate`` needs concrete ints. The ``id=`` path
        doesn't go through this method; it inherits the snapshot's
        own spec via :meth:`spawn` passing ``None`` to ``aboot``.
        """
        if self.id:
            return self.id
        assert self.recipe is not None
        client = _get_client(self.api_key)
        digest = (
            f"{self.version}-"
            f"{hashlib.sha256(self.recipe.encode()).hexdigest()[:12]}"
        )
        defaults = self._RECIPE_BUILD_DEFAULTS
        base = await client.snapshots.acreate(
            image_id=self.base_image,
            vcpus=self.vcpus if self.vcpus is not None else defaults["vcpus"],
            memory=self.memory if self.memory is not None else defaults["memory"],
            disk_size=(
                self.disk_size if self.disk_size is not None else defaults["disk_size"]
            ),
            digest=digest,
        )
        snapshot = await base.abuild([self.recipe])
        self.id = snapshot.id
        return snapshot.id

    async def spawn(self) -> MorphMachine:
        snapshot_id = await self._resolve_id()
        client = _get_client(self.api_key)

        kwargs: dict[str, Any] = {"metadata": self.metadata or None}
        if self.ttl_seconds is not None and self.ttl_seconds > 0:
            kwargs["ttl_seconds"] = self.ttl_seconds
            kwargs["ttl_action"] = self.ttl_action

        # Pick the API call by what we have to say, not by how we got
        # here. ``aboot`` is the only one that accepts resource
        # overrides; ``astart`` boots the snapshot as-is. With nothing
        # to override, ``astart`` is the simpler path -- and the only
        # one a recipe-built snapshot needs, since ``acreate`` already
        # baked the spec in.
        overrides = {
            k: v
            for k, v in (
                ("vcpus", self.vcpus),
                ("memory", self.memory),
                ("disk_size", self.disk_size),
            )
            if v is not None
        }
        if overrides:
            instance = await _retry(
                lambda: client.instances.aboot(snapshot_id, **overrides, **kwargs)
            )
        else:
            instance = await _retry(
                lambda: client.instances.astart(snapshot_id, **kwargs)
            )
        await instance.await_until_ready()
        logger.info("MorphImage.spawn instance=%s snapshot=%s", instance.id, snapshot_id)
        return MorphMachine(instance, workdir=self.workdir)
