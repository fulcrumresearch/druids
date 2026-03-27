"""SSH bastion for Docker containers.

A single asyncssh server that accepts SSH connections and routes them to
Docker containers based on the username. The username is the container ID.
No sshd is installed inside the containers -- the bastion runs
`docker exec` and pipes the streams through the SSH channel.

Usage:
    bastion = DockerSSHBastion()
    await bastion.start()  # binds to a port
    bastion.register(container_id, container, authorized_key)
    # user connects: ssh <container_id>@localhost -p <bastion.port>
    bastion.unregister(container_id)
    await bastion.stop()

A single bastion instance is shared across all DockerSandbox instances.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

import asyncssh


logger = logging.getLogger(__name__)


@dataclass
class _ContainerEntry:
    """Registered container with its authorized public key."""

    container_id: str
    authorized_key: asyncssh.SSHKey


class _BastionServer(asyncssh.SSHServer):
    """Per-connection SSH server callbacks."""

    def __init__(self, bastion: DockerSSHBastion) -> None:
        self._bastion = bastion
        self._container_id: str | None = None

    def connection_made(self, conn: asyncssh.SSHServerConnection) -> None:
        self._conn = conn

    def begin_auth(self, username: str) -> bool:
        self._container_id = username
        if username not in self._bastion._containers:
            logger.warning("SSH auth: unknown container %s", username)
            return True  # require auth, which will fail
        return True  # require auth

    def public_key_auth_supported(self) -> bool:
        return True

    def validate_public_key(self, username: str, key: asyncssh.SSHKey) -> bool:
        entry = self._bastion._containers.get(username)
        if not entry:
            return False
        # Compare the public key data
        return key.public_data == entry.authorized_key.public_data

    def password_auth_supported(self) -> bool:
        return False


async def _handle_session(process: asyncssh.SSHServerProcess, bastion: DockerSSHBastion) -> None:
    """Handle a shell or exec request by proxying to docker exec."""
    username = process.get_extra_info("username")
    entry = bastion._containers.get(username)

    if not entry:
        process.stderr.write(f"Container {username} not found\n")
        process.exit(1)
        return

    container_id = entry.container_id

    # Build docker exec command
    command = process.command
    if command:
        docker_cmd = ["docker", "exec", "-i", container_id, "/bin/sh", "-c", command]
    else:
        # Interactive shell
        docker_cmd = ["docker", "exec", "-i", container_id, "/bin/bash", "-l"]

    term_type = process.get_terminal_type()
    term_size = process.get_terminal_size()
    use_pty = term_type is not None

    if use_pty:
        docker_cmd.insert(2, "-t")
        env_prefix = []
        if term_type:
            env_prefix = ["-e", f"TERM={term_type}"]
        if term_size:
            cols, rows = term_size[0], term_size[1]
            env_prefix += ["-e", f"COLUMNS={cols}", "-e", f"LINES={rows}"]
        # Insert env flags after "exec"
        idx = docker_cmd.index("-i")
        for i, flag in enumerate(env_prefix):
            docker_cmd.insert(idx + i, flag)

    try:
        proc = await asyncio.create_subprocess_exec(
            *docker_cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except Exception as e:
        process.stderr.write(f"Failed to exec in container: {e}\n")
        process.exit(1)
        return

    # Set the SSH channel to binary/encoding passthrough so we can write
    # raw bytes from docker exec directly. asyncssh channels default to
    # text mode (str), so we decode ourselves.
    async def _pipe_stdin():
        try:
            async for data in process.stdin:
                if proc.stdin and not proc.stdin.is_closing():
                    proc.stdin.write(data.encode("utf-8") if isinstance(data, str) else data)
                    await proc.stdin.drain()
        except (asyncio.CancelledError, ConnectionError, BrokenPipeError):
            pass
        finally:
            if proc.stdin and not proc.stdin.is_closing():
                proc.stdin.close()

    async def _pipe_stdout():
        try:
            while True:
                data = await proc.stdout.read(4096)
                if not data:
                    break
                process.stdout.write(data.decode("utf-8", errors="replace"))
        except (asyncio.CancelledError, ConnectionError, BrokenPipeError):
            pass

    async def _pipe_stderr():
        try:
            while True:
                data = await proc.stderr.read(4096)
                if not data:
                    break
                process.stderr.write(data.decode("utf-8", errors="replace"))
        except (asyncio.CancelledError, ConnectionError, BrokenPipeError):
            pass

    stdin_task = asyncio.create_task(_pipe_stdin())
    stdout_task = asyncio.create_task(_pipe_stdout())
    stderr_task = asyncio.create_task(_pipe_stderr())

    try:
        exit_code = await proc.wait()
    except asyncio.CancelledError:
        proc.kill()
        exit_code = 1

    stdin_task.cancel()
    # Give the output pipes a moment to drain before exiting
    await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)

    process.exit(exit_code or 0)


@dataclass
class DockerSSHBastion:
    """SSH bastion that routes connections to Docker containers.

    Call `start()` once, then `register()` for each container that should
    be reachable via SSH. The bastion listens on a single port and routes
    based on the SSH username (which is the container ID).
    """

    host: str = "0.0.0.0"
    _bind_port: int = 0  # 0 = pick a free port
    _containers: dict[str, _ContainerEntry] = field(default_factory=dict, repr=False)
    _server: asyncssh.SSHAcceptor | None = field(default=None, repr=False)
    _host_key: asyncssh.SSHKey | None = field(default=None, repr=False)
    _actual_port: int = field(default=0, repr=False)

    @property
    def port(self) -> int:
        """The port the bastion is listening on (resolved after start)."""
        return self._actual_port

    async def start(self) -> int:
        """Start the SSH bastion server. Returns the port it is listening on."""
        if self._server:
            return self._actual_port

        # Generate a host key for the bastion
        self._host_key = asyncssh.generate_private_key("ssh-ed25519")

        bastion = self

        def _create_server():
            return _BastionServer(bastion)

        async def _handle_client(process: asyncssh.SSHServerProcess) -> None:
            await _handle_session(process, bastion)

        self._server = await asyncssh.create_server(
            _create_server,
            self.host,
            self._bind_port,
            server_host_keys=[self._host_key],
            process_factory=_handle_client,
        )

        # Get the actual port (important when _bind_port=0)
        sockets = self._server.sockets
        if sockets:
            self._actual_port = sockets[0].getsockname()[1]
        else:
            raise RuntimeError("SSH bastion failed to bind")

        logger.info("DockerSSHBastion started on port %d", self._actual_port)
        return self._actual_port

    def register(self, container_id: str, authorized_key: asyncssh.SSHKey) -> None:
        """Register a container so SSH connections can reach it."""
        self._containers[container_id] = _ContainerEntry(
            container_id=container_id,
            authorized_key=authorized_key,
        )
        logger.info("DockerSSHBastion registered container=%s", container_id[:12])

    def unregister(self, container_id: str) -> None:
        """Remove a container from the bastion."""
        self._containers.pop(container_id, None)
        logger.info("DockerSSHBastion unregistered container=%s", container_id[:12])

    async def stop(self) -> None:
        """Stop the bastion server."""
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
            logger.info("DockerSSHBastion stopped")


# ---------------------------------------------------------------------------
# Singleton bastion instance
# ---------------------------------------------------------------------------

_bastion: DockerSSHBastion | None = None
_bastion_loop: asyncio.AbstractEventLoop | None = None


async def get_bastion() -> DockerSSHBastion:
    """Get or create the shared bastion instance.

    If the event loop has changed (e.g. between pytest-asyncio tests),
    the old bastion is discarded and a fresh one is created on the
    current loop.
    """
    global _bastion, _bastion_loop
    loop = asyncio.get_running_loop()
    if _bastion is not None and _bastion_loop is not loop:
        # Stale bastion from a previous event loop -- discard it.
        _bastion = None
        _bastion_loop = None
    if _bastion is None:
        _bastion = DockerSSHBastion()
        await _bastion.start()
        _bastion_loop = loop
    return _bastion
