"""LocalMachine / LocalImage: run on the host that started the runtime."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any, Mapping

from ramure.machines.base import Image, Machine, _decode
from ramure.types import ExecResult


class LocalMachine(Machine):
    """Machine implementation backed by the local host."""

    def __init__(
        self,
        workdir: str | Path | None = None,
        env: Mapping[str, str] | None = None,
    ):
        self.workdir = Path(workdir or os.getcwd())
        self.env = dict(env or {})
        self.workdir.mkdir(parents=True, exist_ok=True)

    def describe(self) -> dict[str, Any]:
        return {"kind": "LocalMachine", "workdir": str(self.workdir)}

    def _resolve_path(self, path: str) -> Path:
        target = Path(path)
        if target.is_absolute():
            return target
        return (self.workdir / target).resolve()

    async def exec(
        self,
        command: str,
        *,
        user: str = "agent",
        timeout: int | None = None,
    ) -> ExecResult:
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
        return await asyncio.to_thread(self._resolve_path(path).read_bytes)

    async def stop(self) -> None:
        return None


class LocalImage(Image):
    """Image that spawns a local working directory machine."""

    def __init__(
        self,
        workdir: str | Path | None = None,
        env: Mapping[str, str] | None = None,
    ):
        self.workdir = Path(workdir or os.getcwd())
        self.env = dict(env or {})

    async def spawn(self) -> LocalMachine:
        return LocalMachine(self.workdir, self.env)
