"""MorphCloud sandbox backend.

Runs each sandbox as a MorphCloud VM. The VM is provisioned from a snapshot,
commands run via the MorphCloud exec API, and files transfer via SFTP.
"""

from __future__ import annotations

import asyncio
import logging

from morphcloud.api import Instance, MorphCloudClient

from orpheus.lib.sandbox.base import ExecResult, Sandbox


logger = logging.getLogger(__name__)


_client: MorphCloudClient | None = None


def get_morph_client(api_key: str | None = None) -> MorphCloudClient:
    """Get or create the shared MorphCloud client."""
    global _client
    if _client is None:
        if api_key:
            _client = MorphCloudClient(api_key=api_key)
        else:
            _client = MorphCloudClient()
    return _client


class MorphSandbox(Sandbox):
    """Sandbox backed by a MorphCloud VM instance."""

    def __init__(self, instance_id: str, instance: Instance, workdir: str | None = None) -> None:
        super().__init__(instance_id=instance_id, workdir=workdir)
        self._instance = instance

    @classmethod
    async def create(
        cls,
        snapshot_id: str,
        *,
        api_key: str | None = None,
        ttl_seconds: int = 7200,
        metadata: dict[str, str] | None = None,
        workdir: str | None = None,
    ) -> MorphSandbox:
        """Provision a new VM from a MorphCloud snapshot.

        Args:
            snapshot_id: MorphCloud snapshot to start from.
            api_key: MorphCloud API key. Uses env var if not provided.
            ttl_seconds: Time-to-live before auto-pause.
            metadata: Key-value metadata to tag the instance.
            workdir: Default working directory for commands.
        """
        client = get_morph_client(api_key)
        instance = await client.instances.astart(
            snapshot_id,
            ttl_seconds=ttl_seconds,
            ttl_action="pause",
            metadata=metadata,
        )
        await instance.await_until_ready()
        logger.info("MorphSandbox.create instance=%s snapshot=%s", instance.id, snapshot_id)
        return cls(instance_id=instance.id, instance=instance, workdir=workdir)

    @classmethod
    async def from_instance_id(cls, instance_id: str, *, api_key: str | None = None, workdir: str | None = None) -> MorphSandbox:
        """Attach to an existing MorphCloud instance."""
        client = get_morph_client(api_key)
        instance = await client.instances.aget(instance_id)
        return cls(instance_id=instance.id, instance=instance, workdir=workdir)

    async def exec(self, command: str, *, timeout: int = 120, user: str | None = None) -> ExecResult:
        """Run a shell command on the VM."""
        await self._ensure_running()
        if user and user != "root":
            wrapped = f"sudo -u {user} bash -c {_shell_quote(command)}"
        else:
            wrapped = command

        result = await self._instance.aexec(wrapped, timeout=timeout)
        return ExecResult(
            command=command,
            exit_code=result.exit_code,
            stdout=result.stdout if hasattr(result, "stdout") else str(result),
            stderr=result.stderr if hasattr(result, "stderr") else "",
        )

    async def read_file(self, path: str) -> bytes:
        """Download a file from the VM."""
        resolved = self._resolve_path(path)
        return await self._instance.adownload(resolved)

    async def write_file(self, path: str, content: str | bytes) -> None:
        """Upload a file to the VM."""
        import tempfile
        import os

        resolved = self._resolve_path(path)

        if isinstance(content, str):
            content = content.encode("utf-8")

        # Ensure parent directory
        parent = resolved.rsplit("/", 1)[0] if "/" in resolved else "/"
        await self._instance.aexec(f"mkdir -p {parent}")

        # Write to local temp, upload, clean up
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(content)
            tmp_path = tmp.name

        try:
            await self._instance.aupload(tmp_path, resolved)
        finally:
            os.unlink(tmp_path)

    async def stop(self) -> None:
        """Stop the MorphCloud instance."""
        await self._instance.astop()
        logger.info("MorphSandbox.stop instance=%s", self.instance_id)

    # ------------------------------------------------------------------
    # MorphCloud-specific methods (not part of the Sandbox interface)
    # ------------------------------------------------------------------

    async def fork(self, *, metadata: dict[str, str] | None = None, workdir: str | None = None) -> MorphSandbox:
        """Create a copy-on-write child sandbox from this one.

        This is a MorphCloud-specific optimization. The caller must check
        `isinstance(sandbox, MorphSandbox)` before using this.
        """
        await self._ensure_running()
        _, children = await self._instance.abranch(1)
        child = children[0]
        if metadata:
            await child.aset_metadata(metadata)
        await child.await_until_ready()
        logger.info("MorphSandbox.fork child=%s parent=%s", child.id, self.instance_id)
        return MorphSandbox(
            instance_id=child.id,
            instance=child,
            workdir=workdir or self.workdir,
        )

    async def expose_http_service(self, name: str, port: int) -> str:
        """Expose a port as a public HTTPS URL via MorphCloud."""
        from morphcloud.api import ApiError

        try:
            return await self._instance.aexpose_http_service(name, port)
        except ApiError as e:
            if e.status_code != 409:
                raise
            # Already exposed -- return existing URL
            await self._instance._refresh_async()
            return next(svc.url for svc in self._instance.networking.http_services if svc.name == name)

    async def ssh_key(self):
        """Get SSH credentials for the VM."""
        return await self._instance.assh_key()

    async def resume(self) -> None:
        """Resume the instance if it is paused."""
        await self._instance._refresh_async()
        if self._instance.status == "paused":
            logger.info("MorphSandbox.resume instance=%s", self.instance_id)
            await self._instance.aresume()
            await self._instance.await_until_ready()

    async def snapshot(self) -> str:
        """Create a snapshot of the current VM state. Returns the snapshot ID."""
        snap = await self._instance.asnapshot()
        return snap.id

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _ensure_running(self) -> None:
        """Resume the instance if paused."""
        await self._instance._refresh_async()
        if self._instance.status == "paused":
            await self.resume()


def _shell_quote(s: str) -> str:
    """Quote a string for safe embedding in bash -c '...'."""
    return "'" + s.replace("'", "'\"'\"'") + "'"
