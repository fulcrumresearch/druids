from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import tempfile
import threading
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Mapping

from druids.types import ExecResult


class Machine(ABC):
    """A running environment."""

    @abstractmethod
    def exec(self, command: str, *, user: str = "agent", timeout: int | None = None) -> ExecResult:
        raise NotImplementedError

    @abstractmethod
    def write_file(self, path: str, content: bytes | str) -> None:
        raise NotImplementedError

    @abstractmethod
    def read_file(self, path: str) -> bytes:
        raise NotImplementedError

    @abstractmethod
    def stop(self) -> None:
        raise NotImplementedError


class Image(ABC):
    """A snapshot that can spawn into a running machine."""

    @abstractmethod
    def spawn(self) -> Machine:
        raise NotImplementedError


class LocalMachine(Machine):
    """Machine implementation backed by the local host."""

    def __init__(self, workdir: str | Path | None = None, env: Mapping[str, str] | None = None):
        self.workdir = Path(workdir or os.getcwd())
        self.env = dict(env or {})
        self.workdir.mkdir(parents=True, exist_ok=True)

    def _resolve_path(self, path: str) -> Path:
        target = Path(path)
        if target.is_absolute():
            return target
        return (self.workdir / target).resolve()

    def exec(self, command: str, *, user: str = "agent", timeout: int | None = None) -> ExecResult:
        env = os.environ.copy()
        env.update(self.env)
        completed = subprocess.run(
            command,
            shell=True,
            executable="/bin/bash",
            cwd=self.workdir,
            env=env,
            timeout=timeout,
            capture_output=True,
            text=True,
        )
        return ExecResult(
            exit_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            command=command,
        )

    def write_file(self, path: str, content: bytes | str) -> None:
        target = self._resolve_path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, str):
            target.write_text(content)
        else:
            target.write_bytes(content)

    def read_file(self, path: str) -> bytes:
        return self._resolve_path(path).read_bytes()

    def stop(self) -> None:
        return None


class LocalImage(Image):
    """Image that spawns a local working directory machine."""

    def __init__(self, workdir: str | Path | None = None, env: Mapping[str, str] | None = None):
        self.workdir = Path(workdir or os.getcwd())
        self.env = dict(env or {})

    def spawn(self) -> Machine:
        return LocalMachine(self.workdir, self.env)


class DockerMachine(Machine):
    """Machine backed by a long-lived Docker container."""

    def __init__(self, container_id: str, *, workdir: str | Path | None = None, env: Mapping[str, str] | None = None):
        self.container_id = container_id
        self.workdir = str(workdir or "/workspace")
        self.env = dict(env or {})

    def exec(self, command: str, *, user: str = "agent", timeout: int | None = None) -> ExecResult:
        docker_command = ["docker", "exec"]
        if user:
            docker_command.extend(["-u", user])
        if self.workdir:
            docker_command.extend(["-w", self.workdir])
        for key, value in self.env.items():
            docker_command.extend(["-e", f"{key}={value}"])
        docker_command.extend([self.container_id, "/bin/bash", "-lc", command])
        completed = subprocess.run(
            docker_command,
            timeout=timeout,
            capture_output=True,
            text=True,
        )
        return ExecResult(
            exit_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            command=command,
        )

    def write_file(self, path: str, content: bytes | str) -> None:
        path_obj = Path(path)
        target = str(path_obj if path_obj.is_absolute() else Path(self.workdir) / path_obj)
        parent = str(Path(target).parent)
        self.exec(f"mkdir -p {shlex.quote(parent)}", user="root")
        with tempfile.NamedTemporaryFile(delete=False) as handle:
            temp_path = Path(handle.name)
            if isinstance(content, str):
                temp_path.write_text(content)
            else:
                temp_path.write_bytes(content)
        try:
            subprocess.run(["docker", "cp", str(temp_path), f"{self.container_id}:{target}"], check=True)
        finally:
            temp_path.unlink(missing_ok=True)

    def read_file(self, path: str) -> bytes:
        path_obj = Path(path)
        source = str(path_obj if path_obj.is_absolute() else Path(self.workdir) / path_obj)
        with tempfile.NamedTemporaryFile(delete=False) as handle:
            temp_path = Path(handle.name)
        try:
            subprocess.run(["docker", "cp", f"{self.container_id}:{source}", str(temp_path)], check=True)
            return temp_path.read_bytes()
        finally:
            temp_path.unlink(missing_ok=True)

    def stop(self) -> None:
        subprocess.run(["docker", "rm", "-f", self.container_id], capture_output=True, text=True)


class DockerImage(Image):
    """Docker-backed image with a local fallback when Docker is unavailable."""

    def __init__(
        self,
        image: str,
        *,
        workdir: str | Path | None = None,
        env: Mapping[str, str] | None = None,
        fallback_to_local: bool = True,
    ):
        self.image = image
        self.workdir = workdir
        self.env = dict(env or {})
        self.fallback_to_local = fallback_to_local

    def spawn(self) -> Machine:
        if shutil.which("docker") is None:
            if self.fallback_to_local:
                return LocalMachine(self.workdir or os.getcwd(), self.env)
            raise RuntimeError("docker is not installed")

        workdir = str(self.workdir or "/workspace")
        command = [
            "docker",
            "run",
            "-d",
            "--rm",
            "--add-host=host.docker.internal:host-gateway",
        ]
        if self.workdir and Path(self.workdir).exists():
            command.extend(["-v", f"{Path(self.workdir).resolve()}:{workdir}"])
        for key, value in self.env.items():
            command.extend(["-e", f"{key}={value}"])
        command.extend([self.image, "/bin/bash", "-lc", "while true; do sleep 3600; done"])
        completed = subprocess.run(command, capture_output=True, text=True)
        if completed.returncode != 0:
            if self.fallback_to_local:
                return LocalMachine(self.workdir or os.getcwd(), self.env)
            raise RuntimeError(completed.stderr.strip() or completed.stdout.strip() or "docker run failed")
        return DockerMachine(completed.stdout.strip(), workdir=workdir, env=self.env)


class ManagedMachine(Machine):
    """Thread-safe lazy machine handle.

    Wraps an Image and spawns the real Machine on first use, or wraps
    an already-started Machine when ``backend`` is provided.
    """

    def __init__(self, image: Image | None = None, *, backend: Machine | None = None):
        self._image = image
        self._backend = backend
        self._lock = threading.Lock()

    @property
    def backend(self) -> Machine | None:
        return self._backend

    def ensure_started(self) -> Machine:
        """Spawn the machine if not already running."""
        if self._backend is not None:
            return self._backend
        with self._lock:
            if self._backend is None:
                if self._image is None:
                    raise RuntimeError("ManagedMachine has no image and no backend")
                self._backend = self._image.spawn()
            return self._backend

    def exec(self, command: str, *, user: str = "agent", timeout: int | None = None) -> ExecResult:
        return self.ensure_started().exec(command, user=user, timeout=timeout)

    def write_file(self, path: str, content: bytes | str) -> None:
        self.ensure_started().write_file(path, content)

    def read_file(self, path: str) -> bytes:
        return self.ensure_started().read_file(path)

    def stop(self) -> None:
        if self._backend is not None:
            self._backend.stop()
