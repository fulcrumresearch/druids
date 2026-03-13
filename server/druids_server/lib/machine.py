"""Machine -- agent setup and orchestration on top of a Sandbox.

A Machine owns a Sandbox and layers agent-specific concerns on top:
bridge deployment, git checkout, GitHub token refresh, package installation.
The Sandbox handles raw compute (exec, file I/O, lifecycle). Machine handles
the "make this sandbox ready to run an agent" logic.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from packaging.version import Version

from druids_server.api.github import GIT_PERMISSIONS, get_installation_token
from druids_server.config import settings
from druids_server.lib.sandbox.base import ExecResult, Sandbox
from druids_server.lib.sandbox.docker import DockerSandbox
from druids_server.paths import BRIDGE_DIR, CLIENT_WHEEL_DIR


logger = logging.getLogger(__name__)

BRIDGE_PORT = 7462


def _find_client_wheel() -> tuple[Path, bytes]:
    """Find the latest client wheel in client/dist/.

    The client must be built before the server starts (``cd client && uv build``).
    Returns (wheel_path, wheel_bytes).
    """
    wheels = list(CLIENT_WHEEL_DIR.glob("druids-*.whl"))
    if not wheels:
        raise FileNotFoundError(f"No client wheel found in {CLIENT_WHEEL_DIR}. Run 'cd client && uv build' first.")
    wheel = max(wheels, key=lambda p: Version(p.stem.split("-", 2)[1]))
    return wheel, wheel.read_bytes()


class ExecError(Exception):
    """Raised when a command fails on the sandbox and check=True."""

    def __init__(self, result: ExecResult):
        self.result = result
        super().__init__(f"Command failed (exit {result.exit_code}): {result.command}\n{result.stderr}")


@dataclass
class Machine:
    """Agent orchestration layer on top of a Sandbox.

    Owns a Sandbox instance and adds bridge deployment, git operations,
    package installation, and GitHub token refresh. The rest of the system
    (Execution, Agent) talks to Machine. Machine talks to Sandbox.
    """

    sandbox: Sandbox | None = None
    snapshot_id: str = ""

    # Git (optional -- only set when the agent opts into git access)
    repo_full_name: str = ""
    git_branch: str | None = None
    git_permissions: str | None = None  # "read", "post", "write", or None for no token

    # Runtime state
    _bridge_deployed: bool = field(default=False, repr=False)
    _bridge_ports: list[int] = field(default_factory=list, repr=False)

    @property
    def instance_id(self) -> str | None:
        return self.sandbox.instance_id if self.sandbox else None

    def next_bridge_port(self) -> int:
        """Return the next available bridge port on this machine."""
        if not self._bridge_ports:
            return BRIDGE_PORT
        return max(self._bridge_ports) + 1

    # ------------------------------------------------------------------
    # Command execution (delegates to sandbox)
    # ------------------------------------------------------------------

    async def exec(
        self, command: str, *, check: bool = True, user: str = "agent", timeout: float | None = None
    ) -> ExecResult:
        """Run a command on the sandbox.

        Args:
            command: Shell command to run.
            check: If True (default), raise ExecError on non-zero exit.
            user: Run as this user (passed to sandbox backend).
            timeout: Seconds to wait. None means use sandbox default.
        """
        if not self.sandbox:
            raise RuntimeError("Machine has no running sandbox")
        kwargs = {"user": user}
        if timeout is not None:
            kwargs["timeout"] = int(timeout)
        result = await self.sandbox.exec(command, **kwargs)
        if check and not result.ok:
            raise ExecError(result)
        return result

    # ------------------------------------------------------------------
    # Initialization
    # ------------------------------------------------------------------

    async def write_cli_config(self, base_url: str) -> None:
        """Write ~/.druids/config.json so the CLI knows where the server is.

        Only writes machine-level config (base_url). Per-agent credentials
        come from the DRUIDS_ACCESS_TOKEN env var set at bridge start time.
        """
        config = json.dumps({"base_url": base_url})
        await self.exec("mkdir -p /home/agent/.druids")
        await self.sandbox.write_file("/home/agent/.druids/config.json", config)

    async def init(self) -> None:
        """One-time setup after sandbox is provisioned."""
        await asyncio.gather(
            self._sync_clock(),
            self._kill_stale_processes(),
            self._install_packages(),
        )
        if self.git_permissions and self.repo_full_name:
            await self.exec(
                "git config --global --add safe.directory /home/agent/repo",
                check=False,
                user="root",
            )
            await self._refresh_and_write_token()

    async def _sync_clock(self) -> None:
        """Sync system clock via NTP. Snapshots freeze the clock at snapshot time."""
        await self.exec("timeout 10 ntpdate -s pool.ntp.org", check=False, user="root")

    async def _kill_stale_processes(self) -> None:
        """Kill any bridge processes left over from a snapshot or fork."""
        await self.exec("pkill -f 'bridge.py'", check=False, user="root")

    async def _install_packages(self) -> None:
        """Install the druids CLI wheel.

        Claude Code, ACP wrappers, and uv are baked into the base image.
        The CLI wheel changes with each server deployment so it's the only
        package that needs per-boot installation. Skips if the current
        version is already installed (checked via a marker file that
        survives COW forks).
        """
        wheel_path, wheel_bytes = _find_client_wheel()
        wheel_name = wheel_path.name
        wheel_version = wheel_path.stem.split("-", 2)[1]

        result = await self.exec("cat /tmp/.druids_wheel_version 2>/dev/null", check=False)
        if result.ok and result.stdout.strip() == wheel_version:
            return

        await self.sandbox.write_file(f"/tmp/{wheel_name}", wheel_bytes)
        await self.exec(
            f"uv pip install --system --break-system-packages --quiet /tmp/{wheel_name}; "
            f"rm -f /tmp/{wheel_name}; "
            f"echo '{wheel_version}' > /tmp/.druids_wheel_version",
            user="root",
        )

    # ------------------------------------------------------------------
    # Child provisioning
    # ------------------------------------------------------------------

    async def create_child(
        self,
        *,
        metadata: dict[str, str] | None = None,
        repo_full_name: str | None = None,
        git_branch: str | None = None,
        git_permissions: str | None = None,
        ttl_seconds: int | None = None,
    ) -> Machine:
        """Create a child Machine from this machine's snapshot."""
        import time as _time

        t0 = _time.monotonic()
        child_sandbox = await Sandbox.create(
            snapshot_id=self.snapshot_id or None,
            metadata=metadata,
        )

        t1 = _time.monotonic()
        child = Machine(
            sandbox=child_sandbox,
            snapshot_id=self.snapshot_id,
            repo_full_name=repo_full_name or "",
            git_branch=git_branch,
            git_permissions=git_permissions,
        )
        await child.init()
        t2 = _time.monotonic()
        logger.info(
            "Machine.create_child timing: sandbox_provision=%.2fs init=%.2fs total=%.2fs",
            t1 - t0,
            t2 - t1,
            t2 - t0,
        )
        return child

    # ------------------------------------------------------------------
    # Bridge
    # ------------------------------------------------------------------

    async def _deploy_bridge(self) -> None:
        """Deploy the bridge source to the sandbox.

        The venv and dependencies are pre-cached in the base snapshot.
        This only writes the bridge script itself.
        """
        bridge_script = (BRIDGE_DIR / "bridge.py").read_text()
        await self.sandbox.write_file("/opt/druids/bridge.py", bridge_script)

    async def ensure_bridge(
        self,
        config,
        working_directory: str = "/home/agent",
        port: int | None = None,
    ) -> tuple[str, str]:
        """Deploy a bridge, start the ACP process, and poll until ready.

        Each call starts a new bridge on the given port (or the next available
        port). Multiple bridges can coexist on the same machine, each running
        its own ACP process. Returns (bridge_id, bridge_token).
        """
        import time as _time

        t0 = _time.monotonic()
        if port is None:
            port = self.next_bridge_port()
        self._bridge_ports.append(port)

        logger.info("Machine.ensure_bridge instance=%s port=%d", self.instance_id, port)

        if not self._bridge_deployed:
            await self._deploy_bridge()
            self._bridge_deployed = True

        t1 = _time.monotonic()
        log_file = f"/tmp/bridge-{port}.log"
        bridge_cmd = f"cd /opt/druids && nohup .venv/bin/python3 bridge.py --port {port} > {log_file} 2>&1 &"
        try:
            await self.exec(bridge_cmd, check=False, timeout=2)
        except (TimeoutError, asyncio.TimeoutError):
            pass  # Expected -- exec blocks on backgrounded process

        for attempt in range(15):
            status_result = await self.exec(
                f"curl -fsS http://127.0.0.1:{port}/status >/dev/null 2>&1",
                check=False,
            )
            if status_result.ok:
                logger.info("Machine.ensure_bridge port=%d ready after %d attempts", port, attempt + 1)
                break
            await asyncio.sleep(0.5)
        else:
            log_result = await self.exec(f"cat {log_file} 2>/dev/null || echo '(no log)'", check=False)
            logger.error("Machine.ensure_bridge not ready, log: %s", log_result.stdout)
            raise RuntimeError(f"Bridge not ready on {self.instance_id}. Log: {log_result.stdout}")

        t2 = _time.monotonic()
        if not self.instance_id:
            raise RuntimeError("Machine has no instance_id")

        bridge_id = f"{self.instance_id}:{port}"
        bridge_token = uuid4().hex
        payload = config.to_bridge_start(working_directory)
        payload["relay_url"] = str(settings.base_url)
        payload["bridge_id"] = bridge_id
        payload["bridge_token"] = bridge_token
        payload_json = json.dumps(payload)
        tmp_path = f"/tmp/bridge_start_{port}.json"
        await self.sandbox.write_file(tmp_path, payload_json)
        start_cmd = (
            f"curl -fsS -X POST http://127.0.0.1:{port}/start "
            "-H 'Content-Type: application/json' "
            f"--data-binary @{tmp_path} >/tmp/bridge_started_{port}.json"
        )
        start_result = await self.exec(start_cmd, check=False)
        if not start_result.ok:
            log_result = await self.exec(f"cat {log_file} 2>/dev/null || echo '(no log)'", check=False)
            logger.error("Bridge start failed, bridge log: %s", log_result.stdout)
            raise RuntimeError(f"Bridge start failed: {start_result.stderr or start_result.stdout}")

        t3 = _time.monotonic()
        logger.info(
            "Machine.ensure_bridge timing: deploy=%.2fs poll_ready=%.2fs start_cmd=%.2fs total=%.2fs",
            t1 - t0,
            t2 - t1,
            t3 - t2,
            t3 - t0,
        )
        return bridge_id, bridge_token

    # ------------------------------------------------------------------
    # Git
    # ------------------------------------------------------------------

    async def git_pull(self, working_directory: str, *, preserve_local_changes: bool = False) -> None:
        """Fetch and checkout the target branch on the sandbox."""
        if not self.repo_full_name:
            return

        branch = self.git_branch
        wd = working_directory
        plain_url = f"https://github.com/{self.repo_full_name}.git"

        if not branch:
            result = await self.exec(
                f"cd {wd} && git symbolic-ref refs/remotes/origin/HEAD | sed 's@^refs/remotes/origin/@@'",
                check=False,
            )
            if result.ok and result.stdout.strip():
                branch = result.stdout.strip()
            else:
                logger.warning("Machine.git_pull could not detect default branch, skipping")
                return

        await self.exec(f"cd {wd} && git remote set-url origin {plain_url}", check=False)

        result = await self.exec(f"cd {wd} && git fetch origin {branch}", check=False)
        if not result.ok:
            logger.warning("Machine.git_pull fetch failed: %s", result.stderr.strip())
            return

        if preserve_local_changes:
            await self.exec(f"cd {wd} && git checkout -B {branch} FETCH_HEAD", check=False)
        else:
            await self.exec(f"cd {wd} && git reset --hard && git checkout -B {branch} FETCH_HEAD", check=False)

    # ------------------------------------------------------------------
    # SSH, file transfer (delegate to sandbox where possible)
    # ------------------------------------------------------------------

    async def ssh_credentials(self):
        """Get SSH credentials for this machine's sandbox."""
        if not self.sandbox:
            return None
        return await self.sandbox.ssh_credentials()

    async def expose_http_service(self, name: str, port: int) -> str:
        """Expose a port as a public URL via the sandbox backend."""
        if isinstance(self.sandbox, DockerSandbox):
            return await self.sandbox.expose_http_service(name, port)
        raise RuntimeError("expose_http_service not supported on this sandbox backend")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def snapshot(self) -> str:
        """Snapshot the sandbox and return the snapshot ID."""
        if not self.sandbox:
            raise RuntimeError("Machine has no running sandbox")
        return await self.sandbox.snapshot()

    async def stop(self) -> None:
        """Stop the sandbox."""
        if not self.sandbox:
            return
        try:
            await self.sandbox.stop()
            logger.info("Machine.stop instance=%s stopped", self.instance_id)
        except Exception:
            logger.warning("Machine.stop failed for instance=%s", self.instance_id, exc_info=True)

    # ------------------------------------------------------------------
    # GitHub token management
    # ------------------------------------------------------------------

    async def _refresh_and_write_token(self) -> None:
        """Fetch a fresh GitHub installation token and push it to the sandbox."""
        if not self.repo_full_name or not self.git_permissions:
            return
        try:
            permissions = GIT_PERMISSIONS.get(self.git_permissions)
            token = await get_installation_token(self.repo_full_name, permissions=permissions)
            await self.exec(
                f"echo '{token}' | gh auth login --with-token && gh auth setup-git",
                check=False,
            )
        except Exception:
            logger.warning("Machine._refresh_and_write_token failed for %s", self.repo_full_name, exc_info=True)
