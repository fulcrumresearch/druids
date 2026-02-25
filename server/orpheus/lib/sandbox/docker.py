"""Docker sandbox backend.

Runs each sandbox as a Docker container on the local host. The container
stays alive via `tail -f /dev/null` and commands are executed with
`container.exec_run()`.
"""

from __future__ import annotations

import asyncio
import io
import logging
import posixpath
import shlex
import tarfile
import time
from typing import Any

import docker
from docker.errors import ImageNotFound
from docker.models.containers import Container

from orpheus.lib.sandbox.base import ExecResult, Sandbox


logger = logging.getLogger(__name__)


class DockerSandbox(Sandbox):
    """Sandbox backed by a local Docker container."""

    def __init__(self, instance_id: str, container: Container, workdir: str | None = None) -> None:
        super().__init__(instance_id=instance_id, workdir=workdir)
        self.container = container

    @classmethod
    async def create(
        cls,
        image: str,
        *,
        service: dict[str, Any] | None = None,
        workdir: str | None = None,
    ) -> DockerSandbox:
        """Create a new Docker container sandbox.

        Args:
            image: Docker image name or ID.
            service: Optional Docker Compose-style service spec for ports, volumes, env, etc.
            workdir: Working directory inside the container. If None, uses the image default.
        """
        loop = asyncio.get_event_loop()

        def _create():
            client = docker.from_env()

            # Pull or get image
            try:
                client.images.get(image)
            except ImageNotFound:
                client.images.pull(image)

            run_params: dict[str, Any] = {
                "image": image,
                "command": "tail -f /dev/null",
                "detach": True,
                "tty": True,
                "stdin_open": True,
            }

            if service:
                parsed = _parse_service_spec(service)
                parsed["image"] = image
                parsed.setdefault("detach", True)
                parsed.setdefault("tty", True)
                parsed.setdefault("stdin_open", True)
                if "command" not in parsed:
                    parsed["command"] = "tail -f /dev/null"
                run_params = parsed

            if workdir:
                run_params["working_dir"] = workdir

            container = client.containers.run(**run_params)

            # Resolve workdir from container if not specified
            resolved_workdir = workdir
            if not resolved_workdir:
                resolved_workdir = container.attrs["Config"]["WorkingDir"] or None

            return container, resolved_workdir

        container, resolved_workdir = await loop.run_in_executor(None, _create)
        logger.info("DockerSandbox.create container=%s image=%s workdir=%s", container.id[:12], image, resolved_workdir)
        return cls(instance_id=container.id, container=container, workdir=resolved_workdir)

    async def exec(self, command: str, *, timeout: int = 120, user: str | None = None) -> ExecResult:
        """Run a shell command inside the container."""

        def _exec():
            kwargs: dict[str, Any] = {
                "cmd": ["/bin/sh", "-c", command],
                "stdout": True,
                "stderr": True,
                "demux": True,
            }
            if self.workdir:
                kwargs["workdir"] = self.workdir
            if user:
                kwargs["user"] = user

            res = self.container.exec_run(**kwargs)
            stdout, stderr = res.output if isinstance(res.output, tuple) else (res.output, b"")
            return ExecResult(
                command=command,
                exit_code=res.exit_code,
                stdout=(stdout or b"").decode("utf-8"),
                stderr=(stderr or b"").decode("utf-8"),
            )

        try:
            loop = asyncio.get_event_loop()
            return await asyncio.wait_for(loop.run_in_executor(None, _exec), timeout=timeout)
        except asyncio.TimeoutError:
            return ExecResult(
                command=command,
                exit_code=124,
                stdout="",
                stderr=f"Command timed out after {timeout} seconds",
            )

    async def read_file(self, path: str) -> bytes:
        """Read a file from the container via Docker tar API."""
        resolved = self._resolve_path(path)

        def _read():
            import docker.errors

            try:
                stream, _stat = self.container.get_archive(resolved)
                data = b"".join(stream)
            except docker.errors.NotFound:
                raise FileNotFoundError(f"File not found: {path}")

            with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as tf:
                for member in tf.getmembers():
                    if member.isfile():
                        fobj = tf.extractfile(member)
                        if fobj is None:
                            raise IOError(f"Could not read {member.name} from archive")
                        return fobj.read()
            raise FileNotFoundError(f"No file found in archive for {path}")

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _read)

    async def write_file(self, path: str, content: str | bytes) -> None:
        """Write a file into the container via Docker tar API."""
        resolved = self._resolve_path(path)
        parent = posixpath.dirname(resolved) or "/"
        basename = posixpath.basename(resolved)

        if isinstance(content, str):
            payload = content.encode("utf-8")
        else:
            payload = content

        def _write():
            # Ensure parent directory
            self.container.exec_run(["/bin/sh", "-c", f"mkdir -p {shlex.quote(parent)}"])

            # Create tar with single file
            buf = io.BytesIO()
            with tarfile.open(fileobj=buf, mode="w") as tf:
                ti = tarfile.TarInfo(name=basename)
                ti.size = len(payload)
                ti.mode = 0o644
                ti.mtime = int(time.time())
                tf.addfile(ti, io.BytesIO(payload))
            buf.seek(0)

            ok = self.container.put_archive(parent, buf.getvalue())
            if not ok:
                raise IOError(f"put_archive failed for {resolved}")

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _write)

    async def stop(self) -> None:
        """Stop and remove the container."""

        def _stop():
            self.container.stop()
            self.container.remove(force=True)

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _stop)
        logger.info("DockerSandbox.stop container=%s removed", self.instance_id[:12])


# ---------------------------------------------------------------------------
# Service spec parsing (Docker Compose subset)
# ---------------------------------------------------------------------------


def _parse_service_spec(service: dict[str, Any]) -> dict[str, Any]:
    """Convert a Docker Compose service spec to docker-py container.run() parameters."""
    params: dict[str, Any] = {}

    if "image" in service:
        params["image"] = service["image"]
    if "command" in service:
        params["command"] = service["command"]
    if "entrypoint" in service:
        params["entrypoint"] = service["entrypoint"]
    if "working_dir" in service:
        params["working_dir"] = service["working_dir"]

    # Environment: list ["K=V"] or dict {K: V}
    if "environment" in service:
        env = service["environment"]
        if isinstance(env, list):
            params["environment"] = dict(item.split("=", 1) for item in env)
        else:
            params["environment"] = env

    # Ports: ["host:container"] -> {"container/tcp": host}
    if "ports" in service:
        ports: dict[str, int | None] = {}
        for mapping in service["ports"]:
            if isinstance(mapping, str):
                parts = mapping.split(":")
                if len(parts) == 2:
                    host_port, container_port = parts
                    if "/" in container_port:
                        key = container_port
                    else:
                        key = f"{container_port}/tcp"
                    ports[key] = int(host_port)
                elif len(parts) == 1:
                    key = f"{parts[0]}/tcp" if "/" not in parts[0] else parts[0]
                    ports[key] = None
        params["ports"] = ports

    # Volumes: ["host:container:mode"] -> {host: {bind: container, mode: mode}}
    if "volumes" in service:
        volumes: dict[str, dict[str, str]] = {}
        for vol in service["volumes"]:
            if isinstance(vol, str):
                parts = vol.split(":")
                if len(parts) >= 2:
                    mode = parts[2] if len(parts) > 2 else "rw"
                    volumes[parts[0]] = {"bind": parts[1], "mode": mode}
        params["volumes"] = volumes

    for key in ("labels", "privileged", "user", "hostname", "dns", "extra_hosts", "cap_add", "cap_drop", "devices"):
        if key in service:
            params[key] = service[key]

    if "mem_limit" in service:
        params["mem_limit"] = service["mem_limit"]
    if "cpu_shares" in service:
        params["cpu_shares"] = service["cpu_shares"]

    return params
