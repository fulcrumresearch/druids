from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from ramure import DockerImage, DockerMachine
from ramure.machines import docker as docker_mod
from ramure.types import ExecResult


def test_docker_image_id_and_validation() -> None:
    image = DockerImage("example/ramure:latest")
    assert image.id == "docker:example/ramure:latest"
    assert image.network == "host"

    restored = DockerImage(id=image.id)
    assert restored.image == "example/ramure:latest"

    with pytest.raises(ValueError, match="conflicting"):
        DockerImage("other:latest", id=image.id)

    with pytest.raises(ValueError, match="requires an image"):
        DockerImage()


def test_docker_image_spawn_builds_docker_run(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    async def fake_run_docker(args, **kwargs):
        calls.append(list(args))
        if args[0] == "run":
            return ExecResult(0, "container123\n", "")
        return ExecResult(0, "", "")

    monkeypatch.setattr(docker_mod, "_run_docker", fake_run_docker)

    async def run() -> None:
        image = DockerImage(
            "example/ramure:latest",
            workdir="/home/agent",
            env={"A": "B"},
            name="ramure-unit",
            network="bridge",
            volumes=["/host:/container:ro"],
            extra_hosts=["host.docker.internal:host-gateway"],
            labels={"purpose": "unit"},
            extra_run_args=["--cpus", "1"],
            command="echo hold",
            pull=True,
        )
        machine = await image.spawn()
        assert isinstance(machine, DockerMachine)
        assert machine.container_id == "container123"
        assert machine.image == "example/ramure:latest"
        assert machine.workdir == "/home/agent"

    asyncio.run(run())

    assert calls == [
        ["pull", "example/ramure:latest"],
        [
            "run",
            "-d",
            "--name",
            "ramure-unit",
            "--label",
            "ramure=true",
            "--label",
            "purpose=unit",
            "--workdir",
            "/home/agent",
            "--env",
            "A=B",
            "--volume",
            "/host:/container:ro",
            "--network",
            "bridge",
            "--add-host",
            "host.docker.internal:host-gateway",
            "--cpus",
            "1",
            "example/ramure:latest",
            "/bin/sh",
            "-lc",
            "echo hold",
        ],
    ]


def test_docker_machine_exec_builds_docker_exec(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[list[str], int | float | None]] = []

    async def fake_run_docker(args, **kwargs):
        calls.append((list(args), kwargs.get("timeout")))
        return ExecResult(0, "hello\n", "", command="docker exec ...")

    monkeypatch.setattr(docker_mod, "_run_docker", fake_run_docker)

    async def run() -> None:
        machine = DockerMachine("container123", workdir="/work", env={"A": "B"})
        result = await machine.exec("echo $A", user="agent", timeout=7)
        assert result.ok
        assert result.stdout == "hello\n"
        assert result.command == "echo $A"

    asyncio.run(run())

    assert calls == [
        (
            [
                "exec",
                "--user",
                "agent",
                "--workdir",
                "/work",
                "--env",
                "A=B",
                "container123",
                "/bin/bash",
                "-lc",
                "echo $A",
            ],
            7,
        )
    ]


def test_docker_machine_files_and_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    copied_payloads: list[bytes] = []

    async def fake_run_docker(args, **kwargs):
        args = list(args)
        calls.append(args)
        if args[0] == "cp" and args[1] == "container123:/work/data.bin":
            Path(args[2]).write_bytes(b"from-container")
        elif args[0] == "cp" and args[2] == "container123:/work/data.bin":
            copied_payloads.append(Path(args[1]).read_bytes())
        return ExecResult(0, "", "")

    monkeypatch.setattr(docker_mod, "_run_docker", fake_run_docker)

    async def run() -> None:
        machine = DockerMachine("container123", workdir="/work")
        await machine.write_file("data.bin", b"to-container")
        assert await machine.read_file("data.bin") == b"from-container"
        await machine.stop()
        await machine.stop()

    asyncio.run(run())

    assert copied_payloads == [b"to-container"]
    assert calls[0] == [
        "exec",
        "--user",
        "root",
        "container123",
        "mkdir",
        "-p",
        "/work",
    ]
    assert calls[1][0] == "cp"
    assert calls[1][2] == "container123:/work/data.bin"
    assert calls[2] == [
        "exec",
        "--user",
        "root",
        "container123",
        "chmod",
        "a+r",
        "/work/data.bin",
    ]
    assert calls[3] == ["cp", "container123:/work/data.bin", calls[3][2]]
    assert calls[4] == ["rm", "-f", "container123"]
    assert [call[0] for call in calls].count("rm") == 1
