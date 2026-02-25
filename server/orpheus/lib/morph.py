"""
MorphCloud helpers for Orpheus.
"""

from __future__ import annotations

import logging
from uuid import uuid4

from morphcloud.api import Instance, MorphCloudClient, Snapshot

from orpheus.config import settings


logger = logging.getLogger(__name__)


# Unique identifier for this server process. Each server run tags the MorphCloud
# instances it creates with this ID so that shutdown cleanup only stops instances
# belonging to the current process, not instances from other concurrent servers
# or previous runs whose agents may still be working.
SERVER_RUN_ID: str = uuid4().hex[:12]

_client: MorphCloudClient | None = None


class MorphError(Exception):
    """Error communicating with MorphCloud."""

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


def get_client() -> MorphCloudClient:
    """Get the shared MorphCloud client (created once, reused)."""
    global _client
    if _client is None:
        _client = MorphCloudClient(api_key=settings.morph_api_key.get_secret_value())
    return _client


def get_instance(instance_id: str | None) -> Instance | None:
    """Get a MorphCloud instance by ID (sync). Returns None if not found."""
    if not instance_id:
        return None
    client = get_client()
    try:
        return client.instances.get(instance_id)
    except Exception:
        return None


async def aget_instance(instance_id: str) -> Instance:
    """Get a MorphCloud instance by ID (async). Raises MorphError if not found."""
    client = get_client()
    try:
        return await client.instances.aget(instance_id)
    except Exception as e:
        raise MorphError(f"Instance {instance_id} not found: {e}") from e


async def start_instance(
    snapshot_id: str,
    ttl_seconds: int = 3600,
    ttl_action: str = "pause",
    metadata: dict[str, str] | None = None,
) -> Instance:
    """Start a new MorphCloud instance from a snapshot."""
    client = get_client()
    logger.info("start_instance snapshot=%s ttl=%ds metadata=%s", snapshot_id, ttl_seconds, metadata)
    instance = await client.instances.astart(
        snapshot_id,
        ttl_seconds=ttl_seconds,
        ttl_action=ttl_action,
        metadata=metadata,
    )
    logger.info("start_instance instance=%s created, waiting until ready", instance.id)
    await instance.await_until_ready()
    logger.info("start_instance instance=%s ready", instance.id)
    return instance


async def stop_instance(instance_id: str | None) -> bool:
    """Stop a MorphCloud instance. Returns True if stopped, False if not found."""
    if not instance_id:
        return False
    inst = get_instance(instance_id)
    if not inst:
        return False
    await inst.astop()
    return True


async def resume_instance(inst: Instance) -> None:
    """Resume a paused instance and wait until ready."""
    await inst.aresume()
    await inst.await_until_ready()


async def branch_instance(inst: Instance, *, metadata: dict | None = None) -> Instance:
    """Create a copy-on-write branch from an instance.

    Branches the parent, sets metadata on the child, and waits for it to be ready.
    """
    _, children = await inst.abranch(1)
    child = children[0]
    if metadata:
        await child.aset_metadata(metadata)
    await child.await_until_ready()
    return child


async def build_snapshot(
    recipe: list[str],
    image_id: str = "morphvm-minimal",
    vcpus: int = 2,
    memory: int = 4096,
    disk_size: int = 10240,
    digest: str = "float-base-v1",
) -> Snapshot:
    """Build a snapshot with layered caching."""
    client = get_client()
    base = await client.snapshots.acreate(
        image_id=image_id,
        vcpus=vcpus,
        memory=memory,
        disk_size=disk_size,
        digest=digest,
    )
    return await base.abuild(recipe)


async def delete_snapshot(snapshot_id: str) -> None:
    """Delete a MorphCloud snapshot by ID. Ignores errors if already deleted."""
    client = get_client()
    try:
        snapshot = await client.snapshots.aget(snapshot_id)
        await snapshot.adelete()
    except Exception:
        pass
