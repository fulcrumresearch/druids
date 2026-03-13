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
import socket
import subprocess
import tarfile
import time
from typing import Any

import asyncssh
import docker.errors
from docker.errors import ImageNotFound
from docker.models.containers import Container

import docker
import druids_server.lib.sandbox.ssh_bastion as ssh_bastion
from druids_server.config import settings
from druids_server.lib.sandbox.base import ExecResult, Sandbox, SSHCredentials
from druids_server.lib.sandbox.ssh_bastion import get_bastion


logger = logging.getLogger(__name__)


class DockerSandbox(Sandbox):
    """Sandbox backed by a local Docker container."""

    def __init__(self, instance_id: str, container: Container, workdir: str | None = None, owned: bool = True) -> None:
        super().__init__(instance_id=instance_id, workdir=workdir)
        self.container = container
        self._owned = owned  # If True, stop() removes the container. False for attached containers.
        self._ssh_creds: SSHCredentials | None = None
        self._socat_procs: list[subprocess.Popen] = []

    @classmethod
    async def from_container_id(
        cls,
        container_id: str,
        *,
        workdir: str | None = None,
        owned: bool = False,
    ) -> DockerSandbox:
        """Attach to an existing Docker container.

        The container must already be running. Wraps an existing container
        without creating or stopping it.

        Args:
            container_id: Docker container ID.
            workdir: Working directory inside the container.
            owned: If True, stop() will remove the container. Default False
                since attached containers are typically not owned by the caller.
        """
        loop = asyncio.get_event_loop()

        def _attach():
            client = docker.from_env()
            container = client.containers.get(container_id)
            if container.status != "running":
                container.start()
            resolved_workdir = workdir
            if not resolved_workdir:
                resolved_workdir = container.attrs["Config"]["WorkingDir"] or None
            return container, resolved_workdir

        container, resolved_workdir = await loop.run_in_executor(None, _attach)
        logger.info(
            "DockerSandbox.from_container_id container=%s workdir=%s owned=%s",
            container.id[:12],
            resolved_workdir,
            owned,
        )
        return cls(instance_id=container.id, container=container, workdir=resolved_workdir, owned=owned)

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

    # ------------------------------------------------------------------
    # Snapshots
    # ------------------------------------------------------------------

    async def snapshot(self) -> str:
        """Commit the container to a Docker image and return the image tag.

        The returned tag can be passed to DockerSandbox.create() as the image
        argument to start new containers from this state.
        """
        loop = asyncio.get_event_loop()
        short_id = self.instance_id[:12]
        tag = f"druids-devbox:{short_id}"

        def _commit():
            self.container.commit(repository="druids-devbox", tag=short_id)

        await loop.run_in_executor(None, _commit)
        logger.info("DockerSandbox.snapshot container=%s image=%s", short_id, tag)
        return tag

    # ------------------------------------------------------------------
    # SSH support (via bastion -- no sshd inside the container)
    # ------------------------------------------------------------------

    async def ssh_credentials(self) -> SSHCredentials:
        """Get SSH credentials for connecting to this container.

        On first call, generates an SSH keypair, registers the container
        with the shared SSH bastion, and returns credentials. The bastion
        proxies SSH sessions into the container via `docker exec`. No sshd
        or extra packages are installed inside the container.

        Subsequent calls return cached credentials.
        """
        if self._ssh_creds:
            return self._ssh_creds

        # Generate a keypair on the host (not inside the container)
        private_key_obj = asyncssh.generate_private_key("ssh-ed25519")
        private_key_pem = private_key_obj.export_private_key("openssh").decode()
        if not private_key_pem.endswith("\n"):
            private_key_pem += "\n"

        # Register this container with the bastion
        bastion = await get_bastion()
        bastion.register(self.instance_id, private_key_obj)

        self._ssh_creds = SSHCredentials(
            host=settings.docker_host,
            port=bastion.port,
            username=self.instance_id,
            private_key=private_key_pem,
        )
        logger.info(
            "DockerSandbox.ssh_credentials container=%s bastion_port=%d",
            self.instance_id[:12],
            bastion.port,
        )
        return self._ssh_creds

    # ------------------------------------------------------------------
    # Port exposure
    # ------------------------------------------------------------------

    async def expose_http_service(self, name: str, port: int) -> str:
        """Expose a container port on the Docker host.

        Returns a URL like http://<docker_host>:<host_port>. For containers
        created without the port pre-mapped, uses socat to forward.
        """
        loop = asyncio.get_event_loop()

        def _expose():
            self.container.reload()
            ports = self.container.ports or {}
            key = f"{port}/tcp"
            bindings = ports.get(key)

            if bindings:
                host_port = int(bindings[0]["HostPort"])
            else:
                # Forward via socat
                host_port = _find_free_port()
                container_ip = self.container.attrs.get("NetworkSettings", {}).get("IPAddress")
                if not container_ip:
                    networks = self.container.attrs["NetworkSettings"]["Networks"]
                    for net in networks.values():
                        if net.get("IPAddress"):
                            container_ip = net["IPAddress"]
                            break
                if not container_ip:
                    raise RuntimeError("Cannot determine container IP for port exposure")

                proc = subprocess.Popen(
                    ["socat", f"TCP-LISTEN:{host_port},fork,reuseaddr", f"TCP:{container_ip}:{port}"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                self._socat_procs.append(proc)
                logger.info(
                    "DockerSandbox.expose %s socat %d -> %s:%d (pid=%d)", name, host_port, container_ip, port, proc.pid
                )

            return host_port

        host_port = await loop.run_in_executor(None, _expose)
        return f"http://{settings.docker_host}:{host_port}"

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def stop(self) -> None:
        """Stop and remove the container (only if we created it)."""
        # Kill all socat port-forwarding processes
        for proc in self._socat_procs:
            try:
                proc.kill()
                proc.wait()
            except OSError:
                pass  # Already dead
        self._socat_procs.clear()

        # Unregister from bastion if we were registered
        if ssh_bastion._bastion:
            ssh_bastion._bastion.unregister(self.instance_id)

        def _stop():
            if self._owned:
                self.container.stop()
                self.container.remove(force=True)
            # Attached containers are left running.

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _stop)
        if self._owned:
            logger.info("DockerSandbox.stop container=%s removed", self.instance_id[:12])
        else:
            logger.info("DockerSandbox.stop container=%s detached (not owned)", self.instance_id[:12])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find_free_port() -> int:
    """Find a free TCP port on the host."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


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

    # Strip security-sensitive fields that should not be controlled by program authors.
    for blocked_key in ("privileged", "cap_add"):
        if blocked_key in service:
            logger.warning("_parse_service_spec: ignoring disallowed field %r", blocked_key)

    for key in ("labels", "user", "hostname", "dns", "extra_hosts", "cap_drop", "devices"):
        if key in service:
            params[key] = service[key]

    if "mem_limit" in service:
        params["mem_limit"] = service["mem_limit"]
    if "cpu_shares" in service:
        params["cpu_shares"] = service["cpu_shares"]

    return params
