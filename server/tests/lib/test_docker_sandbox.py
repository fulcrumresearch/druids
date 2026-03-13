"""Tests for DockerSandbox.

End-to-end tests require a running Docker daemon. They create real containers,
execute commands, transfer files, set up SSH, and verify connectivity.
Unit tests for socat process tracking use mocks.
"""

from __future__ import annotations

import asyncio
import subprocess
from unittest.mock import MagicMock, patch

import pytest
from druids_server.lib.sandbox.base import SSHCredentials
from druids_server.lib.sandbox.docker import DockerSandbox


pytestmark = pytest.mark.asyncio


def docker_available() -> bool:
    """Check if Docker daemon is reachable."""
    try:
        import docker

        client = docker.from_env()
        client.ping()
        return True
    except Exception:
        return False


skip_no_docker = pytest.mark.skipif(not docker_available(), reason="Docker daemon not available")


@pytest.mark.slow
@skip_no_docker
class TestDockerSandboxBasics:
    """Basic container lifecycle: create, exec, file I/O, stop."""

    async def test_create_and_exec(self):
        sandbox = await DockerSandbox.create("ubuntu:22.04")
        try:
            result = await sandbox.exec("echo hello")
            assert result.ok
            assert result.stdout.strip() == "hello"
            assert result.exit_code == 0
        finally:
            await sandbox.stop()

    async def test_exec_as_user(self):
        sandbox = await DockerSandbox.create("ubuntu:22.04")
        try:
            # Create the agent user first
            await sandbox.exec("useradd -m agent", user="root")
            result = await sandbox.exec("whoami", user="agent")
            assert result.ok
            assert result.stdout.strip() == "agent"
        finally:
            await sandbox.stop()

    async def test_exec_nonzero_exit(self):
        sandbox = await DockerSandbox.create("ubuntu:22.04")
        try:
            result = await sandbox.exec("exit 42")
            assert not result.ok
            assert result.exit_code == 42
        finally:
            await sandbox.stop()

    async def test_exec_timeout(self):
        sandbox = await DockerSandbox.create("ubuntu:22.04")
        try:
            result = await sandbox.exec("sleep 60", timeout=1)
            assert not result.ok
            assert result.exit_code == 124
            assert "timed out" in result.stderr
        finally:
            await sandbox.stop()

    async def test_write_and_read_file(self):
        sandbox = await DockerSandbox.create("ubuntu:22.04")
        try:
            await sandbox.write_file("/tmp/test.txt", "hello world")
            data = await sandbox.read_file("/tmp/test.txt")
            assert data == b"hello world"
        finally:
            await sandbox.stop()

    async def test_write_binary_file(self):
        sandbox = await DockerSandbox.create("ubuntu:22.04")
        try:
            payload = bytes(range(256))
            await sandbox.write_file("/tmp/binary.dat", payload)
            data = await sandbox.read_file("/tmp/binary.dat")
            assert data == payload
        finally:
            await sandbox.stop()

    async def test_read_nonexistent_file(self):
        sandbox = await DockerSandbox.create("ubuntu:22.04")
        try:
            with pytest.raises(FileNotFoundError):
                await sandbox.read_file("/tmp/does_not_exist.txt")
        finally:
            await sandbox.stop()

    async def test_stop_removes_container(self):
        sandbox = await DockerSandbox.create("ubuntu:22.04")
        container_id = sandbox.instance_id
        await sandbox.stop()

        # Container should be gone
        import docker

        client = docker.from_env()
        with pytest.raises(docker.errors.NotFound):
            client.containers.get(container_id)


@pytest.mark.slow
@skip_no_docker
class TestDockerSandboxAttach:
    """Attach to existing containers."""

    async def test_attach_to_existing_container(self):
        # Create a container manually
        import docker

        client = docker.from_env()
        container = client.containers.run("ubuntu:22.04", "tail -f /dev/null", detach=True)
        try:
            sandbox = await DockerSandbox.from_container_id(container.id)
            result = await sandbox.exec("echo attached")
            assert result.ok
            assert result.stdout.strip() == "attached"

            # stop() should NOT remove the container since it's not owned
            await sandbox.stop()

            # Container should still exist
            container.reload()
            assert container.status == "running"
        finally:
            container.stop()
            container.remove(force=True)


@pytest.mark.slow
@skip_no_docker
class TestDockerSandboxSSH:
    """SSH via the bastion (no sshd inside containers)."""

    async def test_ssh_credentials_returns_valid_creds(self):
        sandbox = await DockerSandbox.create("ubuntu:22.04")
        try:
            creds = await sandbox.ssh_credentials()
            assert isinstance(creds, SSHCredentials)
            assert creds.host == "localhost"
            assert creds.port > 0
            assert creds.username == sandbox.instance_id  # username = container ID
            assert "PRIVATE KEY" in creds.private_key
            assert creds.password is None  # bastion uses key auth only
        finally:
            await sandbox.stop()

    async def test_ssh_credentials_cached(self):
        sandbox = await DockerSandbox.create("ubuntu:22.04")
        try:
            creds1 = await sandbox.ssh_credentials()
            creds2 = await sandbox.ssh_credentials()
            assert creds1 is creds2  # Same object, not regenerated
        finally:
            await sandbox.stop()

    async def test_ssh_actually_connects(self):
        """Verify we can SSH into the container via the bastion and run a command."""
        sandbox = await DockerSandbox.create("ubuntu:22.04")
        try:
            creds = await sandbox.ssh_credentials()

            import asyncssh

            private_key = asyncssh.import_private_key(creds.private_key)
            async with asyncssh.connect(
                creds.host,
                port=creds.port,
                username=creds.username,
                client_keys=[private_key],
                known_hosts=None,
            ) as conn:
                result = await conn.run("echo hello-from-bastion", check=True)
                assert result.stdout.strip() == "hello-from-bastion"
        finally:
            await sandbox.stop()

    async def test_ssh_write_file_via_ssh(self):
        """Write a file via SSH and read it back via the sandbox API."""
        sandbox = await DockerSandbox.create("ubuntu:22.04")
        try:
            creds = await sandbox.ssh_credentials()

            import asyncssh

            private_key = asyncssh.import_private_key(creds.private_key)
            async with asyncssh.connect(
                creds.host,
                port=creds.port,
                username=creds.username,
                client_keys=[private_key],
                known_hosts=None,
            ) as conn:
                await conn.run("echo 'written via ssh' > /tmp/ssh_test.txt", check=True)

            # Read it back via the sandbox API
            data = await sandbox.read_file("/tmp/ssh_test.txt")
            assert data.strip() == b"written via ssh"
        finally:
            await sandbox.stop()


@pytest.mark.slow
@skip_no_docker
class TestDockerSandboxExposePort:
    """Port exposure via socat forwarding."""

    async def test_expose_http_service(self):
        sandbox = await DockerSandbox.create("ubuntu:22.04")
        try:
            # Start a simple HTTP server inside the container
            await sandbox.exec(
                "apt-get update -qq && apt-get install -y -qq python3 >/dev/null 2>&1",
                user="root",
                timeout=60,
            )
            await sandbox.exec(
                "nohup python3 -m http.server 8080 --directory /tmp > /dev/null 2>&1 &",
                user="root",
            )
            await asyncio.sleep(1)

            # Expose the port
            url = await sandbox.expose_http_service("test-http", 8080)
            assert "localhost" in url or "127.0.0.1" in url

            # Poll until the forwarded port is reachable (socat needs a moment)
            import urllib.request

            last_err = None
            for _ in range(10):
                try:
                    resp = urllib.request.urlopen(url, timeout=5)
                    assert resp.status == 200
                    break
                except (ConnectionRefusedError, OSError) as e:
                    last_err = e
                    await asyncio.sleep(0.5)
            else:
                raise AssertionError(f"Could not reach {url} after retries: {last_err}")
        finally:
            await sandbox.stop()


class TestSocatProcessCleanup:
    """Unit tests for socat process tracking and cleanup (no Docker required)."""

    def _make_sandbox(self) -> DockerSandbox:
        """Create a DockerSandbox with a mocked container."""
        container = MagicMock()
        container.id = "fake-container-id-1234"
        container.status = "running"
        return DockerSandbox(
            instance_id=container.id,
            container=container,
            workdir="/tmp",
            owned=True,
        )

    def test_socat_procs_initialized_empty(self):
        sandbox = self._make_sandbox()
        assert sandbox._socat_procs == []

    async def test_expose_tracks_socat_process(self):
        """Spawning socat via expose_http_service appends the process to _socat_procs."""
        sandbox = self._make_sandbox()
        sandbox.container.reload = MagicMock()
        sandbox.container.ports = {}
        sandbox.container.attrs = {
            "NetworkSettings": {"IPAddress": "172.17.0.2", "Networks": {}},
        }

        fake_proc = MagicMock(spec=subprocess.Popen)
        fake_proc.pid = 12345

        with (
            patch("druids_server.lib.sandbox.docker._find_free_port", return_value=9999),
            patch("subprocess.Popen", return_value=fake_proc) as mock_popen,
            patch("druids_server.lib.sandbox.docker.settings") as mock_settings,
        ):
            mock_settings.docker_host = "localhost"
            url = await sandbox.expose_http_service("test", 8080)

        assert url == "http://localhost:9999"
        assert len(sandbox._socat_procs) == 1
        assert sandbox._socat_procs[0] is fake_proc
        mock_popen.assert_called_once()

    async def test_stop_kills_socat_processes(self):
        """stop() kills all tracked socat processes and clears the list."""
        sandbox = self._make_sandbox()

        proc1 = MagicMock(spec=subprocess.Popen)
        proc2 = MagicMock(spec=subprocess.Popen)
        sandbox._socat_procs = [proc1, proc2]

        with patch("druids_server.lib.sandbox.docker.ssh_bastion"):
            await sandbox.stop()

        proc1.kill.assert_called_once()
        proc1.wait.assert_called_once()
        proc2.kill.assert_called_once()
        proc2.wait.assert_called_once()
        assert sandbox._socat_procs == []

    async def test_stop_handles_already_dead_socat(self):
        """stop() ignores OSError from processes that already exited."""
        sandbox = self._make_sandbox()

        proc = MagicMock(spec=subprocess.Popen)
        proc.kill.side_effect = OSError("No such process")
        sandbox._socat_procs = [proc]

        with patch("druids_server.lib.sandbox.docker.ssh_bastion"):
            await sandbox.stop()

        proc.kill.assert_called_once()
        assert sandbox._socat_procs == []

    async def test_multiple_expose_calls_track_all(self):
        """Multiple expose_http_service calls accumulate processes."""
        sandbox = self._make_sandbox()
        sandbox.container.reload = MagicMock()
        sandbox.container.ports = {}
        sandbox.container.attrs = {
            "NetworkSettings": {"IPAddress": "172.17.0.2", "Networks": {}},
        }

        procs = [MagicMock(spec=subprocess.Popen, pid=i) for i in range(3)]
        call_count = 0

        def make_proc(*args, **kwargs):
            nonlocal call_count
            p = procs[call_count]
            call_count += 1
            return p

        with (
            patch("druids_server.lib.sandbox.docker._find_free_port", side_effect=[9001, 9002, 9003]),
            patch("subprocess.Popen", side_effect=make_proc),
            patch("druids_server.lib.sandbox.docker.settings") as mock_settings,
        ):
            mock_settings.docker_host = "localhost"
            await sandbox.expose_http_service("svc1", 8080)
            await sandbox.expose_http_service("svc2", 8081)
            await sandbox.expose_http_service("svc3", 8082)

        assert len(sandbox._socat_procs) == 3
        assert sandbox._socat_procs == procs
