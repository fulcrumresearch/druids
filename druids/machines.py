from __future__ import annotations

import asyncio
import os
import shlex
import shutil
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Mapping, Sequence

from druids.types import ExecResult


def _decode(data: bytes | None) -> str:
    return (data or b"").decode("utf-8", errors="replace")


async def _run_exec(command: Sequence[str], *, timeout: int | None = None) -> ExecResult:
    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await (
            asyncio.wait_for(process.communicate(), timeout=timeout)
            if timeout is not None
            else process.communicate()
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
            command=" ".join(command),
        )

    return ExecResult(
        exit_code=process.returncode or 0,
        stdout=_decode(stdout),
        stderr=_decode(stderr),
        command=" ".join(command),
    )


class Machine(ABC):
    """A running environment."""

    @abstractmethod
    async def exec(self, command: str, *, user: str = "agent", timeout: int | None = None) -> ExecResult:
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


class Image(ABC):
    """A snapshot that can spawn into a running machine."""

    @abstractmethod
    async def spawn(self) -> Machine:
        raise NotImplementedError

    def server_url_for(self, port: int) -> str:
        return f"http://127.0.0.1:{port}"


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

    async def exec(self, command: str, *, user: str = "agent", timeout: int | None = None) -> ExecResult:
        env = os.environ.copy()
        env.update(self.env)
        process = await asyncio.create_subprocess_shell(
            command,
            executable="/bin/bash",
            cwd=str(self.workdir),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await (
                asyncio.wait_for(process.communicate(), timeout=timeout)
                if timeout is not None
                else process.communicate()
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

    async def write_file(self, path: str, content: bytes | str) -> None:
        target = self._resolve_path(path)

        def _write() -> None:
            target.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, str):
                target.write_text(content)
            else:
                target.write_bytes(content)

        await asyncio.to_thread(_write)

    async def read_file(self, path: str) -> bytes:
        target = self._resolve_path(path)
        return await asyncio.to_thread(target.read_bytes)

    async def stop(self) -> None:
        return None


class LocalImage(Image):
    """Image that spawns a local working directory machine."""

    def __init__(self, workdir: str | Path | None = None, env: Mapping[str, str] | None = None):
        self.workdir = Path(workdir or os.getcwd())
        self.env = dict(env or {})

    async def spawn(self) -> Machine:
        return LocalMachine(self.workdir, self.env)


class DockerMachine(Machine):
    """Machine backed by a long-lived Docker container."""

    def __init__(self, container_id: str, *, workdir: str | Path | None = None, env: Mapping[str, str] | None = None):
        self.container_id = container_id
        self.workdir = str(workdir or "/workspace")
        self.env = dict(env or {})

    async def exec(self, command: str, *, user: str = "agent", timeout: int | None = None) -> ExecResult:
        docker_command = ["docker", "exec"]
        if user:
            docker_command.extend(["-u", user])
        if self.workdir:
            docker_command.extend(["-w", self.workdir])
        for key, value in self.env.items():
            docker_command.extend(["-e", f"{key}={value}"])
        docker_command.extend([self.container_id, "/bin/bash", "-lc", command])
        result = await _run_exec(docker_command, timeout=timeout)
        return ExecResult(
            exit_code=result.exit_code,
            stdout=result.stdout,
            stderr=result.stderr,
            command=command,
        )

    async def write_file(self, path: str, content: bytes | str) -> None:
        path_obj = Path(path)
        target = str(path_obj if path_obj.is_absolute() else Path(self.workdir) / path_obj)
        parent = str(Path(target).parent)
        mkdir_result = await self.exec(f"mkdir -p {shlex.quote(parent)}", user="root")
        if not mkdir_result.ok:
            raise RuntimeError(mkdir_result.stderr.strip() or mkdir_result.stdout.strip() or "docker mkdir failed")

        def _write_temp() -> Path:
            with tempfile.NamedTemporaryFile(delete=False) as handle:
                temp_path = Path(handle.name)
            if isinstance(content, str):
                temp_path.write_text(content)
            else:
                temp_path.write_bytes(content)
            return temp_path

        temp_path = await asyncio.to_thread(_write_temp)
        try:
            result = await _run_exec(["docker", "cp", str(temp_path), f"{self.container_id}:{target}"])
            if not result.ok:
                raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "docker cp failed")
        finally:
            await asyncio.to_thread(lambda: temp_path.unlink(missing_ok=True))

    async def read_file(self, path: str) -> bytes:
        path_obj = Path(path)
        source = str(path_obj if path_obj.is_absolute() else Path(self.workdir) / path_obj)

        def _make_temp() -> Path:
            with tempfile.NamedTemporaryFile(delete=False) as handle:
                return Path(handle.name)

        temp_path = await asyncio.to_thread(_make_temp)
        try:
            result = await _run_exec(["docker", "cp", f"{self.container_id}:{source}", str(temp_path)])
            if not result.ok:
                raise FileNotFoundError(result.stderr.strip() or result.stdout.strip() or f"File not found: {source}")
            return await asyncio.to_thread(temp_path.read_bytes)
        finally:
            await asyncio.to_thread(lambda: temp_path.unlink(missing_ok=True))

    async def stop(self) -> None:
        await _run_exec(["docker", "rm", "-f", self.container_id])


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

    async def spawn(self) -> Machine:
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
        result = await _run_exec(command)
        if not result.ok:
            if self.fallback_to_local:
                return LocalMachine(self.workdir or os.getcwd(), self.env)
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "docker run failed")
        return DockerMachine(result.stdout.strip(), workdir=workdir, env=self.env)

    def server_url_for(self, port: int) -> str:
        return f"http://host.docker.internal:{port}"
