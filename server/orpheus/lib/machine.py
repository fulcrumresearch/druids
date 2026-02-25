"""Machine -- agent setup and orchestration on top of a Sandbox.

A Machine owns a Sandbox and layers agent-specific concerns on top:
bridge deployment, git checkout, GitHub token refresh, package installation.
The Sandbox handles raw compute (exec, file I/O, lifecycle). Machine handles
the "make this sandbox ready to run an agent" logic.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from packaging.version import Version

from orpheus.config import settings
from orpheus.lib.sandbox.base import ExecResult, Sandbox
from orpheus.paths import BRIDGE_DIR, CLI_WHEEL_DIR


logger = logging.getLogger(__name__)

BRIDGE_PORT = 7462
TOKEN_REFRESH_SECONDS = 45 * 60  # 45 minutes


def _find_cli_wheel() -> tuple[Path, bytes]:
    """Find the latest CLI wheel in cli/dist/.

    The CLI must be built before the server starts (``cd cli && uv build``).
    Returns (wheel_path, wheel_bytes).
    """
    wheels = list(CLI_WHEEL_DIR.glob("orpheus_cli-*.whl"))
    if not wheels:
        raise FileNotFoundError(f"No CLI wheel found in {CLI_WHEEL_DIR}. Run 'cd cli && uv build' first.")
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
    repo_full_name: str = ""
    git_branch: str | None = None

    # Runtime state
    _refresh_task: asyncio.Task | None = field(default=None, repr=False)
    _bridge_id: str | None = field(default=None, repr=False)
    _bridge_token: str | None = field(default=None, repr=False)

    @property
    def instance_id(self) -> str | None:
        return self.sandbox.instance_id if self.sandbox else None

    @property
    def bridge_id(self) -> str | None:
        return self._bridge_id

    @property
    def bridge_token(self) -> str | None:
        return self._bridge_token

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

    async def init(self) -> None:
        """One-time setup after sandbox is provisioned."""
        await self._sync_clock()
        await self._kill_stale_processes()
        await self._install_packages()
        await self._refresh_and_write_token()
        self._start_token_refresh()

    async def _sync_clock(self) -> None:
        """Sync system clock via NTP. Snapshots freeze the clock at snapshot time."""
        await self.exec("ntpdate -s pool.ntp.org || true", check=False, user="root")

    async def _kill_stale_processes(self) -> None:
        """Kill any bridge processes left over from a snapshot or fork."""
        await self.exec("pkill -f 'bridge.py' || true", check=False, user="root")

    async def _install_packages(self) -> None:
        """Install volatile packages that must be fresh on every boot."""
        # uv
        await self.exec(
            "command -v uv || (curl -LsSf https://astral.sh/uv/install.sh | env INSTALLER_NO_MODIFY_PATH=1 sh"
            " && cp /root/.local/bin/uv /usr/local/bin/uv && cp /root/.local/bin/uvx /usr/local/bin/uvx)",
            user="root",
        )
        # Claude Code
        await self.exec(
            "curl -fsSL https://storage.googleapis.com/anthropic-cdn/claude-code/install.sh | bash",
            user="root",
        )
        # ACP wrappers
        await self.exec(
            "command -v claude-code-acp > /dev/null 2>&1 && command -v codex-acp > /dev/null 2>&1"
            " || npm install -g @zed-industries/claude-code-acp @zed-industries/codex-acp",
            user="root",
        )
        # Orpheus CLI
        try:
            wheel_path, wheel_bytes = _find_cli_wheel()
            wheel_b64 = base64.b64encode(wheel_bytes).decode()
            wheel_name = wheel_path.name
            await self.exec(
                f"base64 -d > /tmp/{wheel_name} << 'WHEELEOF'\n{wheel_b64}\nWHEELEOF",
                user="root",
            )
            await self.exec(
                f"pip install --break-system-packages --quiet /tmp/{wheel_name} && rm /tmp/{wheel_name}",
                user="root",
            )
        except FileNotFoundError:
            logger.warning("CLI wheel not found, skipping orpheus CLI install")

    # ------------------------------------------------------------------
    # Child provisioning
    # ------------------------------------------------------------------

    async def create_child(
        self,
        *,
        metadata: dict[str, str] | None = None,
        repo_full_name: str | None = None,
        git_branch: str | None = None,
    ) -> Machine:
        """Create a child Machine: fork if possible, otherwise start fresh from snapshot.

        Tries to fork the current sandbox (MorphCloud COW branch) for speed.
        Falls back to creating a new sandbox from snapshot_id if forking fails
        or the sandbox backend doesn't support it.
        """
        from orpheus.lib.sandbox.morph import MorphSandbox

        child_sandbox = None

        if self.sandbox and isinstance(self.sandbox, MorphSandbox):
            try:
                child_sandbox = await self.sandbox.fork(metadata=metadata)
            except Exception:
                logger.info("Machine.create_child fork failed, falling back to fresh start")

        if child_sandbox is None:
            if not self.snapshot_id:
                raise ValueError("No sandbox to fork and no snapshot_id to start from")
            child_sandbox = await MorphSandbox.create(self.snapshot_id, metadata=metadata)

        child = Machine(
            sandbox=child_sandbox,
            snapshot_id=self.snapshot_id,
            repo_full_name=repo_full_name or self.repo_full_name,
            git_branch=git_branch or self.git_branch,
        )
        await child.init()
        return child

    # ------------------------------------------------------------------
    # Bridge
    # ------------------------------------------------------------------

    async def _deploy_bridge(self) -> None:
        """Deploy the bridge project to the sandbox and install dependencies."""
        bridge_script = (BRIDGE_DIR / "bridge.py").read_text()
        pyproject = (BRIDGE_DIR / "pyproject.toml").read_text()
        lock = (BRIDGE_DIR / "uv.lock").read_text()

        await self.exec("mkdir -p /opt/orpheus", user="root")
        await self.exec(
            f"cat > /opt/orpheus/bridge.py << 'BRIDGE_EOF'\n{bridge_script}\nBRIDGE_EOF",
            user="root",
        )
        await self.exec(
            f"cat > /opt/orpheus/pyproject.toml << 'PYPROJECT_EOF'\n{pyproject}\nPYPROJECT_EOF",
            user="root",
        )
        await self.exec(
            f"cat > /opt/orpheus/uv.lock << 'UVLOCK_EOF'\n{lock}\nUVLOCK_EOF",
            user="root",
        )
        await self.exec("cd /opt/orpheus && uv sync --frozen -q", user="root")

    async def ensure_bridge(
        self, config, monitor_prompt: str | None = None, working_directory: str = "/home/agent"
    ) -> None:
        """Deploy the bridge, start the ACP process, and poll until ready."""
        logger.info("Machine.ensure_bridge instance=%s port=%d", self.instance_id, BRIDGE_PORT)

        await self._deploy_bridge()
        await self.exec("pkill -f 'bridge.py' || true", check=False, user="root")
        await asyncio.sleep(0.5)

        bridge_cmd = (
            f"cd /opt/orpheus && nohup .venv/bin/python3 bridge.py --port {BRIDGE_PORT} > /tmp/bridge.log 2>&1 &"
        )
        try:
            await self.exec(bridge_cmd, check=False, timeout=1)
        except TimeoutError:
            logger.info("Machine.ensure_bridge aexec timed out (expected for backgrounded process)")

        # Poll local bridge status endpoint (bridge is not exposed publicly).
        for attempt in range(15):
            status_result = await self.exec(
                f"curl -fsS http://127.0.0.1:{BRIDGE_PORT}/status >/dev/null",
                check=False,
                user="root",
            )
            if status_result.ok:
                logger.info("Machine.ensure_bridge ready after %ds", attempt + 1)
                break
            await asyncio.sleep(1)
        else:
            log_result = await self.exec("cat /tmp/bridge.log 2>/dev/null || echo '(no log)'", check=False, user="root")
            logger.error("Machine.ensure_bridge not ready after 15s, log: %s", log_result.stdout)
            raise RuntimeError(f"Bridge not ready after 15s on {self.instance_id}. Log: {log_result.stdout}")

        if not self.instance_id:
            raise RuntimeError("Machine has no instance_id")
        bridge_id = self.instance_id
        bridge_token = uuid4().hex
        payload = config.to_bridge_start(working_directory, monitor_prompt)
        payload["relay_url"] = str(settings.base_url)
        payload["bridge_id"] = bridge_id
        payload["bridge_token"] = bridge_token
        payload_json = json.dumps(payload)
        start_cmd = (
            f"cat > /tmp/bridge_start_payload.json << 'EOF'\n{payload_json}\nEOF\n"
            f"curl -fsS -X POST http://127.0.0.1:{BRIDGE_PORT}/start "
            "-H 'Content-Type: application/json' "
            "--data-binary @/tmp/bridge_start_payload.json >/tmp/bridge_start.json"
        )
        start_result = await self.exec(start_cmd, check=False, user="root")
        if not start_result.ok:
            raise RuntimeError(f"Bridge start failed: {start_result.stderr or start_result.stdout}")

        self._bridge_id = bridge_id
        self._bridge_token = bridge_token

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

    async def ssh_key(self):
        """Get SSH credentials. Only available on MorphCloud sandboxes."""
        from orpheus.lib.sandbox.morph import MorphSandbox

        if isinstance(self.sandbox, MorphSandbox):
            return await self.sandbox.ssh_key()
        return None

    async def expose_http_service(self, name: str, port: int) -> str:
        """Expose a port as a public HTTPS URL. Only available on MorphCloud sandboxes."""
        from orpheus.lib.sandbox.morph import MorphSandbox

        if isinstance(self.sandbox, MorphSandbox):
            return await self.sandbox.expose_http_service(name, port)
        raise RuntimeError("expose_http_service not supported on this sandbox backend")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def stop(self) -> None:
        """Stop the sandbox and cancel background tasks."""
        if self._refresh_task and not self._refresh_task.done():
            self._refresh_task.cancel()
            self._refresh_task = None
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
        if not self.repo_full_name:
            return
        try:
            from orpheus.api.github import get_installation_token

            token = await get_installation_token(self.repo_full_name)
            await self.exec(
                f"echo '{token}' | gh auth login --with-token && gh auth setup-git",
                check=False,
            )
        except Exception:
            logger.warning("Machine._refresh_and_write_token failed for %s", self.repo_full_name, exc_info=True)

    def _start_token_refresh(self) -> None:
        """Start background token refresh loop."""
        if not self.repo_full_name:
            return
        self._refresh_task = asyncio.create_task(self._token_refresh_loop())

    async def _token_refresh_loop(self) -> None:
        """Background loop that refreshes the GitHub token periodically."""
        while True:
            await asyncio.sleep(TOKEN_REFRESH_SECONDS)
            try:
                await self._refresh_and_write_token()
            except asyncio.CancelledError:
                return
            except Exception:
                logger.warning("Machine._token_refresh_loop error", exc_info=True)
