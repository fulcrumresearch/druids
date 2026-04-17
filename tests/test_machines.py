from __future__ import annotations

import asyncio
from pathlib import Path

from ramure import LocalImage


def test_local_image_machine_exec_and_files(tmp_path: Path) -> None:
    async def run() -> None:
        machine = await LocalImage(workdir=tmp_path).spawn()
        await machine.write_file("hello.txt", b"world")
        assert await machine.read_file("hello.txt") == b"world"

        result = await machine.exec("cat hello.txt")
        assert result.ok
        assert result.stdout == "world"

    asyncio.run(run())
