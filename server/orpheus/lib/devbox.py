"""Devbox runtime operations.

Handles resolving and ensuring running instances for devboxes and agent VMs.
Also owns the Orpheus base snapshot builder (used by setup and launch).
"""

from __future__ import annotations

import hashlib
import logging

from morphcloud.api import Snapshot

from orpheus.lib.execution import Execution
from orpheus.lib.machine import Machine
from orpheus.lib.morph import build_snapshot, get_instance
from orpheus.lib.sandbox.morph import MorphSandbox
from orpheus.db.models.devbox import Devbox, get_devbox
from orpheus.db.models.user import User
from orpheus.db.session import get_session


logger = logging.getLogger(__name__)

DEVBOX_TTL_SECONDS = 3600  # 1 hour TTL for on-demand devbox instances


class InstanceNotFound(Exception):
    """Raised when a target VM instance cannot be resolved."""


async def machine_from_devbox(devbox: Devbox) -> Machine:
    """Reconstruct a Machine from a devbox DB record.

    The returned Machine may have a sandbox (if the devbox has a running
    instance) or just a snapshot_id for later provisioning via create_child().
    """
    m = Machine(snapshot_id=devbox.snapshot_id)
    if devbox.instance_id:
        try:
            sandbox = await MorphSandbox.from_instance_id(devbox.instance_id)
            m.sandbox = sandbox
        except Exception:
            logger.info("machine_from_devbox: instance %s not found, using snapshot only", devbox.instance_id)
    return m


async def create_machine(
    metadata: dict[str, str] | None = None,
    ttl_seconds: int = 7200,
) -> Machine:
    """Create a fresh Machine from the Orpheus base snapshot.

    Builds (or retrieves cached) the base snapshot, provisions a VM, and
    installs volatile packages. Returns a ready-to-use Machine.
    """
    snapshot = await get_orpheus_snapshot()
    sandbox = await MorphSandbox.create(snapshot.id, metadata=metadata, ttl_seconds=ttl_seconds)
    machine = Machine(sandbox=sandbox, snapshot_id=snapshot.id)
    await machine.init()
    return machine


async def resolve_instance(
    user: User,
    executions: dict[str, Execution],
    repo: str | None = None,
    execution_slug: str | None = None,
    agent_name: str | None = None,
):
    """Resolve a MorphCloud instance from either devbox or execution context.

    Devbox path: repo -> Devbox table -> instance_id (or start from snapshot).
    Execution path: execution_slug + agent_name -> execution -> program -> instance_id.

    Raises `InstanceNotFound` if the target cannot be resolved.
    Raises `ValueError` if neither targeting option is provided.
    """
    if execution_slug and agent_name:
        ex = executions.get(execution_slug)
        if not ex:
            raise InstanceNotFound(f"Execution {execution_slug} not found")
        program = ex.programs.get(agent_name)
        if not program or not program.is_agent:
            raise InstanceNotFound(f"Agent {agent_name} not found")
        if not program.machine or not program.machine.sandbox:
            raise InstanceNotFound(f"Instance for {agent_name} not found")
        if not isinstance(program.machine.sandbox, MorphSandbox):
            raise InstanceNotFound(f"Instance for {agent_name} not available (non-Morph sandbox)")
        return program.machine.sandbox._instance

    if repo:
        async with get_session() as db:
            devbox = await get_devbox(db, user.id, repo)
            if not devbox:
                raise InstanceNotFound(f"No devbox for {repo}. Run 'orpheus setup start' first.")

            # Try existing instance
            if devbox.instance_id:
                inst = get_instance(devbox.instance_id)
                if inst:
                    return inst

            # No live instance -- start one from snapshot
            if not devbox.snapshot_id:
                raise InstanceNotFound(f"No snapshot for {repo}. Run 'orpheus setup start' first.")

            machine = await create_machine(
                metadata={"orpheus:devbox": "true", "orpheus:repo": repo},
                ttl_seconds=DEVBOX_TTL_SECONDS,
            )
            devbox.instance_id = machine.instance_id
            db.add(devbox)
            if not isinstance(machine.sandbox, MorphSandbox):
                raise InstanceNotFound(f"No running instance for {repo}")
            return machine.sandbox._instance

    raise ValueError("Either repo or execution_slug+agent_name is required")


# ---------------------------------------------------------------------------
# Orpheus base snapshot
# ---------------------------------------------------------------------------


async def get_orpheus_snapshot() -> Snapshot:
    """Get or create an Orpheus-configured snapshot.

    Contains only stable infrastructure: system packages, node runtime, gh CLI,
    uv, and the agent user. Volatile packages (Claude Code, ACP wrappers, bridge
    deps) are installed at boot time by Machine._init() -> _install_packages().
    """
    recipe = """\
set -e
apt-get update && apt-get install -y git curl ca-certificates sudo vim python3-pip ntpdate
curl -fsSL https://deb.nodesource.com/setup_lts.x | bash -
apt-get install -y nodejs

# Install uv for Python package management
curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh

# Install GitHub CLI
curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg | dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg
chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" | tee /etc/apt/sources.list.d/github-cli.list > /dev/null
apt-get update && apt-get install -y gh

# Create non-root user with full sudo access
useradd -m -s /bin/bash agent
echo 'agent ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/agent
chmod 440 /etc/sudoers.d/agent"""

    digest = f"orpheus-v5-{hashlib.sha256(recipe.encode()).hexdigest()[:12]}"
    return await build_snapshot([recipe], digest=digest)
