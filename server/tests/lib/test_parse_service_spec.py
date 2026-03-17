"""Unit tests for _parse_service_spec in the Docker sandbox module."""

from __future__ import annotations

import logging

import pytest
from druids_server.lib.sandbox.docker import _parse_service_spec


class TestParseServiceSpec:
    """Tests for Docker Compose service spec parsing."""

    def test_strips_privileged(self, caplog: pytest.LogCaptureFixture):
        """The privileged field must be stripped and a warning logged."""
        spec = {"image": "ubuntu:22.04", "privileged": True}
        with caplog.at_level(logging.WARNING):
            result = _parse_service_spec(spec)
        assert "privileged" not in result
        assert any("privileged" in rec.message for rec in caplog.records)

    def test_strips_cap_add(self, caplog: pytest.LogCaptureFixture):
        """The cap_add field must be stripped and a warning logged."""
        spec = {"image": "ubuntu:22.04", "cap_add": ["SYS_ADMIN", "NET_ADMIN"]}
        with caplog.at_level(logging.WARNING):
            result = _parse_service_spec(spec)
        assert "cap_add" not in result
        assert any("cap_add" in rec.message for rec in caplog.records)

    def test_strips_both_privileged_and_cap_add(self, caplog: pytest.LogCaptureFixture):
        """Both fields stripped when present together."""
        spec = {"image": "ubuntu:22.04", "privileged": True, "cap_add": ["SYS_ADMIN"]}
        with caplog.at_level(logging.WARNING):
            result = _parse_service_spec(spec)
        assert "privileged" not in result
        assert "cap_add" not in result
        warnings = [rec.message for rec in caplog.records if rec.levelno >= logging.WARNING]
        assert len(warnings) == 2

    def test_allowed_fields_pass_through(self):
        """Fields that are not blocked should be passed through normally."""
        spec = {
            "image": "ubuntu:22.04",
            "command": "echo hi",
            "user": "root",
            "hostname": "test-host",
            "labels": {"app": "test"},
            "cap_drop": ["ALL"],
            "devices": ["/dev/fuse"],
        }
        result = _parse_service_spec(spec)
        assert result["image"] == "ubuntu:22.04"
        assert result["command"] == "echo hi"
        assert result["user"] == "root"
        assert result["hostname"] == "test-host"
        assert result["labels"] == {"app": "test"}
        assert result["cap_drop"] == ["ALL"]
        assert result["devices"] == ["/dev/fuse"]

    def test_environment_as_list(self):
        """Environment specified as a list of K=V strings."""
        spec = {"environment": ["FOO=bar", "BAZ=qux"]}
        result = _parse_service_spec(spec)
        assert result["environment"] == {"FOO": "bar", "BAZ": "qux"}

    def test_environment_as_dict(self):
        """Environment specified as a dict."""
        spec = {"environment": {"FOO": "bar"}}
        result = _parse_service_spec(spec)
        assert result["environment"] == {"FOO": "bar"}

    def test_ports_parsing(self):
        """Ports in host:container format."""
        spec = {"ports": ["8080:80", "9090:443/tcp"]}
        result = _parse_service_spec(spec)
        assert result["ports"] == {"80/tcp": 8080, "443/tcp": 9090}

    def test_volumes_parsing(self):
        """Volumes in host:container:mode format."""
        spec = {"volumes": ["/host/path:/container/path:ro"]}
        result = _parse_service_spec(spec)
        assert result["volumes"] == {"/host/path": {"bind": "/container/path", "mode": "ro"}}

    def test_no_warning_when_fields_absent(self, caplog: pytest.LogCaptureFixture):
        """No warnings when privileged and cap_add are not in the spec."""
        spec = {"image": "ubuntu:22.04", "user": "root"}
        with caplog.at_level(logging.WARNING):
            _parse_service_spec(spec)
        blocked_warnings = [rec for rec in caplog.records if "privileged" in rec.message or "cap_add" in rec.message]
        assert len(blocked_warnings) == 0

    def test_empty_spec(self):
        """An empty spec returns an empty params dict."""
        result = _parse_service_spec({})
        assert result == {}
