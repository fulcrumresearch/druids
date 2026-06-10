"""Docker backend for ramure.

The backend shells out to the host ``docker`` CLI, so it does not add a
Python dependency. Containers used for agents must contain the pieces ramure
needs inside the container: ``bash``, ``tmux``, the ``pi`` CLI, and an
``agent`` user (because :meth:`Machine.exec` defaults to ``user="agent"``).
"""

from __future__ import annotations

import asyncio
import logging
import os
import posixpath
import shlex
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from ramure.machines.base import Image, Machine, _decode
from ramure.types import ExecResult

logger = logging.getLogger(__name__)

_DEFAULT_COMMAND = ("/bin/sh", "-lc", "while true; do sleep 3600; done")
_DEFAULT_LABELS = {"ramure": "true"}


async def _run_docker(
    args: Sequence[str],
    *,
    input_bytes: bytes | None = None,
    timeout: int | float | None = None,
) -> ExecResult:
    """Run ``docker`` with ``args`` and return a decoded ``ExecResult``."""
    argv = ["docker", *(str(arg) for arg in args)]
    command = shlex.join(argv)
    try:
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE if input_bytes is not None else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:  # pragma: no cover - environment specific
        raise RuntimeError(
            "The Docker backend requires the `docker` CLI to be installed "
            "and available on PATH."
        ) from exc

    try:
        stdout, stderr = await (
            asyncio.wait_for(process.communicate(input_bytes), timeout=timeout)
            if timeout is not None
            else process.communicate(input_bytes)
        )
    except asyncio.TimeoutError:
        process.kill()
        stdout, stderr = await process.communicate()
        stderr_text = _decode(stderr)
        if stderr_text:
            stderr_text += "\n"
        stderr_text += f"Timed out after {timeout}s"
        return ExecResult(
            exit_code=124,
            stdout=_decode(stdout),
            stderr=stderr_text,
            command=command,
        )

    return ExecResult(
        exit_code=process.returncode or 0,
        stdout=_decode(stdout),
        stderr=_decode(stderr),
        command=command,
    )


def _raise_if_failed(result: ExecResult, action: str) -> None:
    if result.ok:
        return
    message = result.stderr.strip() or result.stdout.strip() or f"Docker {action} failed"
    raise RuntimeError(message)


class DockerMachine(Machine):
    """Machine implementation backed by a running Docker container."""

    def __init__(
        self,
        container_id: str,
        *,
        image: str | None = None,
        workdir: str | None = None,
        env: Mapping[str, str] | None = None,
        remove_on_stop: bool = True,
    ) -> None:
        container_id = container_id.strip()
        if not container_id:
            raise ValueError("container_id must not be empty")
        self.container_id = container_id
        self.image = image
        self.workdir = workdir
        self.env = dict(env or {})
        self.remove_on_stop = remove_on_stop
        self._stopped = False

    @classmethod
    async def attach(
        cls,
        container_id: str,
        *,
        workdir: str | None = None,
        env: Mapping[str, str] | None = None,
        remove_on_stop: bool = False,
    ) -> "DockerMachine":
        """Attach to an existing container.

        Attached containers are not removed by :meth:`stop` unless
        ``remove_on_stop=True`` is passed explicitly.
        """
        return cls(
            container_id,
            workdir=workdir,
            env=env,
            remove_on_stop=remove_on_stop,
        )

    def describe(self) -> dict[str, Any]:
        return {
            "kind": "DockerMachine",
            "container_id": self.container_id,
            "image": self.image,
            "workdir": self.workdir,
        }

    def _resolve_path(self, path: str) -> str:
        if posixpath.isabs(path):
            return posixpath.normpath(path)
        base = self.workdir or "/"
        return posixpath.normpath(posixpath.join(base, path))

    def _exec_args(self, command: str, *, user: str | None) -> list[str]:
        args = ["exec"]
        if user:
            args.extend(["--user", user])
        if self.workdir:
            args.extend(["--workdir", self.workdir])
        for key, value in self.env.items():
            args.extend(["--env", f"{key}={value}"])
        args.extend([self.container_id, "/bin/bash", "-lc", command])
        return args

    async def exec(
        self,
        command: str,
        *,
        user: str = "agent",
        timeout: int | None = None,
    ) -> ExecResult:
        result = await _run_docker(
            self._exec_args(command, user=user),
            timeout=timeout,
        )
        return ExecResult(
            exit_code=result.exit_code,
            stdout=result.stdout,
            stderr=result.stderr,
            command=command,
        )

    async def write_file(self, path: str, content: bytes | str) -> None:
        resolved = self._resolve_path(path)
        parent = posixpath.dirname(resolved) or "/"
        mkdir = await _run_docker(
            ["exec", "--user", "root", self.container_id, "mkdir", "-p", parent]
        )
        _raise_if_failed(mkdir, "mkdir")

        data = content.encode("utf-8") if isinstance(content, str) else content
        fd, local_path = tempfile.mkstemp(prefix="ramure-docker-")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            copied = await _run_docker(
                ["cp", local_path, f"{self.container_id}:{resolved}"]
            )
            _raise_if_failed(copied, "copy to container")
            chmod = await _run_docker(
                ["exec", "--user", "root", self.container_id, "chmod", "a+r", resolved]
            )
            _raise_if_failed(chmod, "chmod copied file")
        finally:
            try:
                os.unlink(local_path)
            except FileNotFoundError:
                pass

    async def read_file(self, path: str) -> bytes:
        resolved = self._resolve_path(path)
        with tempfile.TemporaryDirectory(prefix="ramure-docker-") as tmpdir:
            local_path = Path(tmpdir) / "content"
            copied = await _run_docker(
                ["cp", f"{self.container_id}:{resolved}", str(local_path)]
            )
            _raise_if_failed(copied, "copy from container")
            return await asyncio.to_thread(local_path.read_bytes)

    async def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        if not self.remove_on_stop:
            return
        result = await _run_docker(["rm", "-f", self.container_id], timeout=10)
        # ``stop`` should be safe to call during best-effort cleanup even if the
        # container was already removed by a user or by Docker.
        output = f"{result.stdout}\n{result.stderr}"
        if not result.ok and "No such container" not in output:
            _raise_if_failed(result, "remove container")
        logger.info("DockerMachine.stop container=%s", self.container_id)


class DockerImage(Image):
    """Image that spawns a Docker container.

    By default containers run with ``--network host`` so agents can connect
    back to ramure's local WebSocket server at ``127.0.0.1``. Pass
    ``network=None`` to use Docker's default bridge networking, in which case
    the agent process usually needs an explicit ``host=`` / ``base_url=``.
    """

    def __init__(
        self,
        image: str | None = None,
        *,
        id: str | None = None,
        workdir: str | None = None,
        env: Mapping[str, str] | None = None,
        name: str | None = None,
        network: str | None = "host",
        volumes: Sequence[str] | None = None,
        extra_hosts: Sequence[str] | None = None,
        labels: Mapping[str, str] | None = None,
        extra_run_args: Sequence[str] | None = None,
        command: str | Sequence[str] | None = None,
        pull: bool = False,
        remove_on_stop: bool = True,
    ) -> None:
        if id is not None:
            image_from_id = id[len("docker:") :] if id.startswith("docker:") else id
            if image is not None and image != image_from_id:
                raise ValueError("DockerImage received conflicting image and id")
            image = image_from_id
        if image is None:
            raise ValueError("DockerImage requires an image name")

        self.image = image
        self.workdir = workdir
        self.env = dict(env or {})
        self.name = name
        self.network = network
        self.volumes = [str(volume) for volume in (volumes or [])]
        self.extra_hosts = [str(host) for host in (extra_hosts or [])]
        self.labels = dict(_DEFAULT_LABELS)
        self.labels.update(labels or {})
        self.extra_run_args = [str(arg) for arg in (extra_run_args or [])]
        self.command = command
        self.pull = pull
        self.remove_on_stop = remove_on_stop

    @property
    def id(self) -> str:
        return f"docker:{self.image}"

    def _command_args(self) -> list[str]:
        if self.command is None:
            return list(_DEFAULT_COMMAND)
        if isinstance(self.command, str):
            return ["/bin/sh", "-lc", self.command]
        return [str(part) for part in self.command]

    async def spawn(self) -> DockerMachine:
        if self.pull:
            pulled = await _run_docker(["pull", self.image])
            _raise_if_failed(pulled, "pull image")

        args: list[str] = ["run", "-d"]
        if self.name:
            args.extend(["--name", self.name])
        for key, value in self.labels.items():
            args.extend(["--label", f"{key}={value}"])
        if self.workdir:
            args.extend(["--workdir", self.workdir])
        for key, value in self.env.items():
            args.extend(["--env", f"{key}={value}"])
        for volume in self.volumes:
            args.extend(["--volume", volume])
        if self.network is not None:
            args.extend(["--network", self.network])
        for host in self.extra_hosts:
            args.extend(["--add-host", host])
        args.extend(self.extra_run_args)
        args.append(self.image)
        args.extend(self._command_args())

        result = await _run_docker(args)
        _raise_if_failed(result, "run container")
        container_id = result.stdout.strip()
        if not container_id:
            raise RuntimeError("Docker run succeeded but did not return a container id")

        logger.info("DockerImage.spawn container=%s image=%s", container_id, self.image)
        return DockerMachine(
            container_id,
            image=self.image,
            workdir=self.workdir,
            env=self.env,
            remove_on_stop=self.remove_on_stop,
        )
