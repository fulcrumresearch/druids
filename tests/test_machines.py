from __future__ import annotations

from pathlib import Path

from druids import LocalImage


def test_local_image_machine_exec_and_files(tmp_path: Path) -> None:
    machine = LocalImage(workdir=tmp_path).spawn()
    machine.write_file("hello.txt", b"world")
    assert machine.read_file("hello.txt") == b"world"

    result = machine.exec("cat hello.txt")
    assert result.ok
    assert result.stdout == "world"
