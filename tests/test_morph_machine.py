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

from ramure.machines.base import Image, Machine, SSHCredentials
from ramure.machines.local import LocalMachine
from ramure.machines.morph import (
    DEFAULT_MORPH_RECIPE,
    DEFAULT_MORPH_RECIPE_VERSION,
    MorphImage,
    MorphMachine,
)


def test_morph_image_default_uses_pi_recipe() -> None:
    img = MorphImage()
    assert img.id is None
    assert img.recipe == DEFAULT_MORPH_RECIPE
    assert img.version == DEFAULT_MORPH_RECIPE_VERSION
    # The default recipe must install the pieces ramure needs on the VM.
    for token in ("npm install -g", "pi-coding-agent", "tmux", "useradd -m"):
        assert token in DEFAULT_MORPH_RECIPE


def test_morph_image_with_id() -> None:
    img = MorphImage(id="snap_abc")
    assert img.id == "snap_abc"
    assert img.base_image == MorphImage.DEFAULT_BASE_IMAGE
    assert img.recipe is None
    # Resource args default to None so the snapshot's baked-in spec
    # wins (regression: they used to default to 2 / 4096 / 10240,
    # silently downsizing snapshots built for more).
    assert (img.vcpus, img.memory, img.disk_size) == (None, None, None)


def test_morph_image_recipe_fields() -> None:
    img = MorphImage(recipe="echo hi", version="unit-test-v1", vcpus=1, memory=512, disk_size=2048)
    assert img.recipe == "echo hi"
    assert img.version == "unit-test-v1"
    assert img.vcpus == 1
    assert img.memory == 512
    assert img.disk_size == 2048


def test_base_machine_fork_and_snapshot_not_implemented() -> None:
    """``Machine.fork`` / ``Machine.snapshot`` default to NotImplementedError."""

    async def run() -> None:
        m = LocalMachine()
        with pytest.raises(NotImplementedError, match="LocalMachine"):
            await m.fork()
        with pytest.raises(NotImplementedError, match="LocalMachine"):
            await m.snapshot()

    asyncio.run(run())


class _FakeSpec:
    def __init__(self, vcpus: int = 3, memory: int = 1024, disk_size: int = 5120) -> None:
        self.vcpus = vcpus
        self.memory = memory
        self.disk_size = disk_size


class _FakeSnapshot:
    def __init__(self, snap_id: str) -> None:
        self.id = snap_id


class _FakeInstance:
    """Minimal async fake of ``morphcloud.api.Instance`` for offline tests."""

    def __init__(
        self,
        *,
        instance_id: str = "morphvm_test",
        children: list["_FakeInstance"] | None = None,
        snapshot_id: str = "snapshot_fake",
        status: str = "ready",
    ) -> None:
        self.id = instance_id
        self.status = status
        self.spec = _FakeSpec()
        self.exec_calls: list[str] = []
        self._children = children or []
        self._snapshot_id = snapshot_id
        self.branched = 0
        self.snapshotted = 0

    async def aexec(self, command: str, timeout: int | None = None):
        self.exec_calls.append(command)

        class _R:
            exit_code = 0
            stdout = ""
            stderr = ""

        return _R()

    async def abranch(self, n: int):
        self.branched += n
        return self, list(self._children[:n])

    async def asnapshot(self) -> _FakeSnapshot:
        self.snapshotted += 1
        return _FakeSnapshot(self._snapshot_id)

    async def aset_metadata(self, metadata: dict) -> None:
        self.metadata = metadata

    async def aset_ttl(self, **kwargs) -> None:
        self.ttl = kwargs

    async def await_until_ready(self) -> None:  # pragma: no cover - trivial
        return None

    async def _refresh_async(self) -> None:  # pragma: no cover - trivial
        return None


# ---------------------------------------------------------------------------
# Spawn forwards resource args (or None) to aboot.
# ---------------------------------------------------------------------------


def test_morph_image_spawn_forwards_resource_args(monkeypatch) -> None:
    """Regression for the silent-downsize footgun: when the user
    doesn't pass resource args, ``aboot`` must receive ``None`` so
    morphcloud falls back to the snapshot's baked-in spec rather
    than overriding it with hardcoded numbers. When the user does
    pass them, they flow through.
    """
    from ramure.machines import morph as morph_mod

    calls: list[dict[str, Any]] = []

    class _Instances:
        async def aboot(self, snapshot_id: str, **kwargs):
            calls.append({"snapshot_id": snapshot_id, **kwargs})
            return _FakeInstance()

    class _Client:
        instances = _Instances()

    async def _passthrough(fn, *_a, **_k):
        return await fn()

    monkeypatch.setattr(morph_mod, "_get_client", lambda *_a, **_k: _Client())
    monkeypatch.setattr(morph_mod, "_retry", _passthrough)

    async def run() -> None:
        await MorphImage(id="snap_xyz").spawn()
        await MorphImage(id="snap_xyz", vcpus=8, memory=16384).spawn()

    asyncio.run(run())

    assert calls[0]["vcpus"] is None
    assert calls[0]["memory"] is None
    assert calls[0]["disk_size"] is None

    assert calls[1]["vcpus"] == 8
    assert calls[1]["memory"] == 16384
    assert calls[1]["disk_size"] is None


def test_morph_machine_snapshot_returns_morph_image() -> None:
    """``MorphMachine.snapshot()`` must return a respawnable ``MorphImage``."""

    async def run() -> None:
        inst = _FakeInstance(snapshot_id="snapshot_fromstate")
        m = MorphMachine(inst, workdir="/work")
        image = await m.snapshot()

        # Structural guarantees.
        assert isinstance(image, Image)
        assert isinstance(image, MorphImage)
        # Must carry the snapshot id produced by the VM.
        assert image.id == "snapshot_fromstate"
        # Should inherit workdir by default.
        assert image.workdir == "/work"
        # Should carry over the VM's resource spec.
        assert image.vcpus == inst.spec.vcpus
        assert image.memory == inst.spec.memory
        assert image.disk_size == inst.spec.disk_size
        # sync must run before snapshot so disk state is durable.
        assert inst.exec_calls[0] == "sync"
        assert inst.snapshotted == 1

        # workdir override wins over inherited default.
        other = await m.snapshot(workdir="/override")
        assert other.workdir == "/override"

    asyncio.run(run())


def test_morph_machine_fork_kills_ramure_sessions() -> None:
    """``fork(clean_agents=True)`` must target only ``ramure-*`` tmux sessions."""

    async def run() -> None:
        child = _FakeInstance(instance_id="morphvm_child")
        parent = _FakeInstance(instance_id="morphvm_parent", children=[child])
        m = MorphMachine(parent, workdir="/work")

        forked = await m.fork()
        assert isinstance(forked, MorphMachine)
        assert forked.instance_id == "morphvm_child"
        assert forked.workdir == "/work"

        # The cleanup must have run on the CHILD, not the parent.
        assert not any("tmux" in c for c in parent.exec_calls)
        cleanup = [c for c in child.exec_calls if "tmux" in c]
        assert len(cleanup) == 1
        # It must be scoped to ramure-* and must not blow up on empty output.
        assert "ramure-" in cleanup[0]
        assert "kill-session" in cleanup[0]
        assert "|| true" in cleanup[0]

    asyncio.run(run())


def test_morph_machine_fork_clean_agents_false_skips_cleanup() -> None:
    """``clean_agents=False`` must leave the forked VM's processes alone."""

    async def run() -> None:
        child = _FakeInstance(instance_id="morphvm_child")
        parent = _FakeInstance(instance_id="morphvm_parent", children=[child])
        m = MorphMachine(parent)

        await m.fork(clean_agents=False)
        assert not any("tmux" in c for c in child.exec_calls)

    asyncio.run(run())


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
