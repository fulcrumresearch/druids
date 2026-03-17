"""Tests for AgentConnection.prompt_nowait exception logging."""

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from druids_server.lib.connection import AgentConnection, _log_task_exception


@pytest.fixture
def conn():
    """Create an AgentConnection with mocked transport."""
    with patch.object(AgentConnection, "__post_init__"):
        c = AgentConnection.__new__(AgentConnection)
        c.bridge_id = "bridge-1"
        c.bridge_token = "token-1"
        c.session_id = "sess-1"
        c.connection = AsyncMock()
        c._handlers = {}
        return c


class TestLogTaskException:
    def test_logs_warning_on_exception(self):
        """_log_task_exception logs a warning when the task has an exception."""
        exc = ConnectionError("bridge lost")
        task = MagicMock()
        task.cancelled.return_value = False
        task.exception.return_value = exc

        with patch("druids_server.lib.connection.logger") as mock_logger:
            _log_task_exception(task)

            mock_logger.warning.assert_called_once()
            args, kwargs = mock_logger.warning.call_args
            assert "Background prompt task failed" in args[0]
            assert kwargs["exc_info"] is exc

    def test_no_log_on_success(self):
        """_log_task_exception does not log when the task succeeded."""
        task = MagicMock()
        task.cancelled.return_value = False
        task.exception.return_value = None

        with patch("druids_server.lib.connection.logger") as mock_logger:
            _log_task_exception(task)

            mock_logger.warning.assert_not_called()
            mock_logger.error.assert_not_called()

    def test_no_log_on_cancelled(self):
        """_log_task_exception does not log when the task was cancelled."""
        task = MagicMock()
        task.cancelled.return_value = True

        with patch("druids_server.lib.connection.logger") as mock_logger:
            _log_task_exception(task)

            mock_logger.warning.assert_not_called()
            task.exception.assert_not_called()


class TestPromptNowaitIntegration:
    @pytest.mark.asyncio
    async def test_failed_prompt_logs_exception(self, conn):
        """prompt_nowait logs exceptions from the background task via _log_task_exception."""
        conn.connection.send_request = AsyncMock(side_effect=ConnectionError("bridge lost"))

        # Attach a handler directly to the module logger to capture records from the callback.
        target_logger = logging.getLogger("druids_server.lib.connection")
        records: list[logging.LogRecord] = []
        handler = logging.Handler()
        handler.emit = lambda record: records.append(record)
        handler.setLevel(logging.WARNING)
        target_logger.addHandler(handler)
        try:
            await conn.prompt_nowait("do something")
            # First sleep lets the task complete; second lets the done callback fire.
            await asyncio.sleep(0)
            await asyncio.sleep(0)
        finally:
            target_logger.removeHandler(handler)

        assert len(records) == 1
        assert records[0].levelno == logging.WARNING
        assert "Background prompt task failed" in records[0].message
        assert "bridge lost" in records[0].message
        assert records[0].exc_info is not None
        assert isinstance(records[0].exc_info[1], ConnectionError)

    @pytest.mark.asyncio
    async def test_successful_prompt_does_not_log(self, conn):
        """prompt_nowait does not log when the prompt succeeds."""
        conn.connection.send_request = AsyncMock(return_value={"ok": True})

        target_logger = logging.getLogger("druids_server.lib.connection")
        records: list[logging.LogRecord] = []
        handler = logging.Handler()
        handler.emit = lambda record: records.append(record)
        handler.setLevel(logging.WARNING)
        target_logger.addHandler(handler)
        try:
            await conn.prompt_nowait("do something")
            await asyncio.sleep(0)
            await asyncio.sleep(0)
        finally:
            target_logger.removeHandler(handler)

        assert len(records) == 0
