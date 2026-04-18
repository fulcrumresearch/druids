"""Tests for the MorphCloud backend.

These are light, SDK-agnostic tests: we verify construction, validation, and
that instantiating a ``MorphMachine`` without ``morphcloud`` installed raises
a clear ImportError at the point where the SDK is actually needed.

A full end-to-end test that actually boots a VM is intentionally left out so
CI does not need a MorphCloud account. If ``MORPH_API_KEY`` is set and
``morphcloud`` is installed, we do run a tiny smoke test.
"""

from __future__ import annotations

import asyncio
import importlib
import os

import pytest

from ramure.machines.base import SSHCredentials
from ramure.machines.morph import (
    DEFAULT_MORPH_RECIPE,
    DEFAULT_MORPH_RECIPE_VERSION,
    MorphImage,
    MorphMachine,
)


def test_morph_image_default_uses_pi_recipe() -> None:
    img = MorphImage()
    assert img.snapshot_id is None
    assert img.recipe == DEFAULT_MORPH_RECIPE
    assert img.version == DEFAULT_MORPH_RECIPE_VERSION
    # The default recipe must install the pieces druids needs on the VM.
    for token in ("npm install -g", "pi-coding-agent", "tmux", "useradd -m"):
        assert token in DEFAULT_MORPH_RECIPE


def test_morph_image_with_snapshot_id() -> None:
    img = MorphImage(snapshot_id="snap_abc")
    assert img.snapshot_id == "snap_abc"
    assert img.base_image == MorphImage.DEFAULT_BASE_IMAGE
    assert img.recipe is None


def test_morph_image_recipe_fields() -> None:
    img = MorphImage(recipe="echo hi", version="unit-test-v1", vcpus=1, memory=512, disk_size=2048)
    assert img.recipe == "echo hi"
    assert img.version == "unit-test-v1"
    assert img.vcpus == 1
    assert img.memory == 512
    assert img.disk_size == 2048


def test_ssh_credentials_dataclass_frozen() -> None:
    creds = SSHCredentials(host="h", port=22, username="u", private_key="k")
    with pytest.raises(Exception):
        creds.host = "other"  # type: ignore[misc]


def test_morphcloud_missing_raises_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """If morphcloud is not importable, calling spawn/attach must fail clearly."""
    from ramure.machines import morph as morph_mod

    # Pretend morphcloud is not installed by forcing the import to fail.
    real_import = (
        __builtins__["__import__"]
        if isinstance(__builtins__, dict)
        else __builtins__.__import__
    )

    def fake_import(name, *args, **kwargs):
        if name == "morphcloud" or name.startswith("morphcloud."):
            raise ImportError("morphcloud missing (test)")
        return real_import(name, *args, **kwargs)

    # Reset the client pool so _get_client actually tries to import.
    morph_mod._CLIENTS.clear()

    monkeypatch.setattr("builtins.__import__", fake_import)

    with pytest.raises(ImportError) as excinfo:
        morph_mod._get_client()
    assert "morphcloud" in str(excinfo.value).lower()


@pytest.mark.skipif(
    importlib.util.find_spec("morphcloud") is None or not os.environ.get("MORPH_API_KEY"),
    reason="morphcloud SDK or MORPH_API_KEY not available",
)
def test_morph_smoke_boot_exec_stop() -> None:
    """Optional end-to-end smoke: build tiny snapshot, exec, stop.

    Skipped automatically unless ``morphcloud`` is installed AND
    ``MORPH_API_KEY`` is set in the environment.
    """

    async def run() -> None:
        image = MorphImage(
            recipe="set -e\napt-get update -qq\nsync",
            version="druids-smoke-v1",
            vcpus=1,
            memory=512,
            disk_size=2048,
            ttl_seconds=300,
        )
        machine: MorphMachine = await image.spawn()
        try:
            result = await machine.exec("echo hello", user="root")
            assert result.ok
            assert "hello" in result.stdout

            await machine.write_file("/tmp/druids.txt", "ok")
            assert (await machine.read_file("/tmp/druids.txt")).strip() == b"ok"
        finally:
            await machine.stop()

    asyncio.run(run())


@pytest.mark.skipif(
    importlib.util.find_spec("morphcloud") is None
    or not os.environ.get("MORPH_API_KEY")
    or not os.environ.get("ANTHROPIC_API_KEY")
    or not os.environ.get("RAMURE_BASE_URL"),
    reason=(
        "End-to-end agent-on-Morph test requires morphcloud + MORPH_API_KEY + "
        "ANTHROPIC_API_KEY + RAMURE_BASE_URL (a ws/wss URL that your reverse "
        "proxy forwards to this host on RAMURE_BIND_PORT, default 8002)."
    ),
)
def test_morph_agent_end_to_end() -> None:
    """Boot a Morph VM, launch a real pi agent on it, round-trip a result.

    Requires public reachability from the VM back to the host: set
    ``RAMURE_BASE_URL=wss://your.host`` and, optionally, ``RAMURE_BIND_PORT``
    (default ``8002``) to match your proxy's backend port.
    """
    from ramure import agent, agent_process, done, wait

    base_url = os.environ["RAMURE_BASE_URL"]
    bind_port = int(os.environ.get("RAMURE_BIND_PORT", "8002"))

    @agent_process(
        image=MorphImage(ttl_seconds=1200),
        timeout=600,
        host="0.0.0.0",
        port=bind_port,
        base_url=base_url,
    )
    async def greet(name: str) -> str:
        worker = await agent("worker")

        @worker.on("finish")
        async def on_finish(greeting: str) -> str:
            """Call this with the greeting once you are done."""
            done(greeting)
            return "Done."

        await worker.send(
            f"Write a short greeting for {name}, then call finish with it."
        )
        return await wait()

    result = asyncio.run(greet("druids"))
    assert isinstance(result, str) and result.strip(), result
