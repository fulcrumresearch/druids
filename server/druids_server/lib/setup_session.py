"""State machine for managing setup session lifecycle.

This module implements the setup wizard flow as a state machine that
handles provisioning, configuration, verification, and snapshot creation.

States:
    INIT: Session created, ready to start
    PROVISIONING: Creating sandbox and cloning repo
    CONFIGURING: User is configuring the environment (sandbox running)
    VERIFYING: Running verification checks
    SAVING: Snapshotting the environment
    COMPLETED: Setup finished successfully
    ERROR: Setup failed, error message stored
"""

from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from druids_server.db.models.devbox import Devbox
from druids_server.db.models.setup_session import (
    SetupSession,
    get_setup_session,
    update_session_state,
)
from druids_server.lib.sandbox.base import Sandbox

logger = logging.getLogger(__name__)


class SetupSessionError(Exception):
    """Raised when a setup session operation fails."""

    def __init__(self, message: str, step: str):
        self.message = message
        self.step = step
        super().__init__(message)


async def transition_to_provisioning(
    db: AsyncSession, session_id: UUID, sandbox: Sandbox,
) -> SetupSession:
    """Transition session to PROVISIONING state and store instance_id.

    Args:
        db: Database session
        session_id: Setup session ID
        sandbox: Created sandbox instance

    Returns:
        Updated session

    Raises:
        SetupSessionError: If session not found or in wrong state
    """
    session = await get_setup_session(db, session_id)
    if not session:
        raise SetupSessionError("Session not found", "PROVISIONING")

    if session.state != "INIT":
        raise SetupSessionError(f"Cannot provision from state {session.state}", "PROVISIONING")

    updated = await update_session_state(
        db, session_id, "PROVISIONING", instance_id=sandbox.instance_id,
    )
    if not updated:
        raise SetupSessionError("Failed to update session state", "PROVISIONING")

    logger.info("Session %s -> PROVISIONING (instance=%s)", session_id, sandbox.instance_id)
    return updated


async def transition_to_configuring(db: AsyncSession, session_id: UUID) -> SetupSession:
    """Transition session to CONFIGURING state.

    This happens after the sandbox is provisioned and repo is cloned.
    The user can now configure the environment interactively.

    Args:
        db: Database session
        session_id: Setup session ID

    Returns:
        Updated session

    Raises:
        SetupSessionError: If session not found or in wrong state
    """
    session = await get_setup_session(db, session_id)
    if not session:
        raise SetupSessionError("Session not found", "CONFIGURING")

    if session.state != "PROVISIONING":
        raise SetupSessionError(f"Cannot configure from state {session.state}", "CONFIGURING")

    updated = await update_session_state(db, session_id, "CONFIGURING")
    if not updated:
        raise SetupSessionError("Failed to update session state", "CONFIGURING")

    logger.info("Session %s -> CONFIGURING", session_id)
    return updated


async def transition_to_verifying(db: AsyncSession, session_id: UUID) -> SetupSession:
    """Transition session to VERIFYING state.

    This happens when the user has finished configuring and wants to
    verify the environment before snapshotting.

    Args:
        db: Database session
        session_id: Setup session ID

    Returns:
        Updated session

    Raises:
        SetupSessionError: If session not found or in wrong state
    """
    session = await get_setup_session(db, session_id)
    if not session:
        raise SetupSessionError("Session not found", "VERIFYING")

    if session.state != "CONFIGURING":
        raise SetupSessionError(f"Cannot verify from state {session.state}", "VERIFYING")

    updated = await update_session_state(db, session_id, "VERIFYING")
    if not updated:
        raise SetupSessionError("Failed to update session state", "VERIFYING")

    logger.info("Session %s -> VERIFYING", session_id)
    return updated


async def transition_to_saving(db: AsyncSession, session_id: UUID) -> SetupSession:
    """Transition session to SAVING state.

    This happens when verification is complete and we're ready to
    snapshot the environment.

    Args:
        db: Database session
        session_id: Setup session ID

    Returns:
        Updated session

    Raises:
        SetupSessionError: If session not found or in wrong state
    """
    session = await get_setup_session(db, session_id)
    if not session:
        raise SetupSessionError("Session not found", "SAVING")

    # Allow transition from VERIFYING or CONFIGURING (user may skip verification)
    if session.state not in ("VERIFYING", "CONFIGURING"):
        raise SetupSessionError(f"Cannot save from state {session.state}", "SAVING")

    updated = await update_session_state(db, session_id, "SAVING")
    if not updated:
        raise SetupSessionError("Failed to update session state", "SAVING")

    logger.info("Session %s -> SAVING", session_id)
    return updated


async def transition_to_completed(db: AsyncSession, session_id: UUID) -> SetupSession:
    """Transition session to COMPLETED state.

    This happens after the snapshot is created successfully.
    The instance_id is cleared since the sandbox is stopped.

    Args:
        db: Database session
        session_id: Setup session ID

    Returns:
        Updated session

    Raises:
        SetupSessionError: If session not found or in wrong state
    """
    session = await get_setup_session(db, session_id)
    if not session:
        raise SetupSessionError("Session not found", "COMPLETED")

    if session.state != "SAVING":
        raise SetupSessionError(f"Cannot complete from state {session.state}", "COMPLETED")

    updated = await update_session_state(db, session_id, "COMPLETED", instance_id=None)
    if not updated:
        raise SetupSessionError("Failed to update session state", "COMPLETED")

    logger.info("Session %s -> COMPLETED", session_id)
    return updated


async def transition_to_error(
    db: AsyncSession, session_id: UUID, error_message: str, failed_step: str,
) -> SetupSession:
    """Transition session to ERROR state.

    This happens when any step fails. The error message and failed step
    are stored so the user can understand what went wrong and retry.

    Args:
        db: Database session
        session_id: Setup session ID
        error_message: Description of what failed
        failed_step: Which step failed (state name)

    Returns:
        Updated session

    Raises:
        SetupSessionError: If session not found
    """
    session = await get_setup_session(db, session_id)
    if not session:
        raise SetupSessionError("Session not found", "ERROR")

    updated = await update_session_state(
        db, session_id, "ERROR",
        error_message=error_message,
        failed_step=failed_step,
    )
    if not updated:
        raise SetupSessionError("Failed to update session state", "ERROR")

    logger.error("Session %s -> ERROR (step=%s): %s", session_id, failed_step, error_message)
    return updated


async def cleanup_on_error(db: AsyncSession, session: SetupSession) -> None:
    """Clean up VM resources when a session fails.

    Stops the sandbox if one is running and transitions to ERROR state.

    Args:
        db: Database session
        session: Setup session that failed
    """
    if session.instance_id:
        try:
            sandbox = await Sandbox.get(session.instance_id, owned=True)
            await sandbox.stop()
            logger.info("Stopped sandbox %s after session %s error", session.instance_id, session.id)
        except Exception:
            logger.exception("Failed to stop sandbox %s during cleanup", session.instance_id)


async def retry_session(db: AsyncSession, session_id: UUID) -> SetupSession:
    """Retry a failed session by clearing error state.

    The session returns to INIT state so it can be retried from the beginning.
    Any running sandbox is preserved (it may not exist if the error was early).

    Args:
        db: Database session
        session_id: Setup session ID

    Returns:
        Updated session

    Raises:
        SetupSessionError: If session not found or not in ERROR state
    """
    session = await get_setup_session(db, session_id)
    if not session:
        raise SetupSessionError("Session not found", "RETRY")

    if session.state != "ERROR":
        raise SetupSessionError(f"Cannot retry from state {session.state}", "RETRY")

    # Clear error state and return to INIT
    updated = await update_session_state(
        db, session_id, "INIT",
        error_message=None,
        failed_step=None,
    )
    if not updated:
        raise SetupSessionError("Failed to update session state", "RETRY")

    logger.info("Session %s -> INIT (retry)", session_id)
    return updated


async def handle_provision_failure(
    db: AsyncSession, session_id: UUID, error: Exception, sandbox: Sandbox | None = None,
) -> SetupSession:
    """Handle a provisioning failure by cleaning up and transitioning to ERROR.

    Args:
        db: Database session
        session_id: Setup session ID
        error: The exception that caused the failure
        sandbox: The sandbox to stop, if it was created

    Returns:
        Updated session in ERROR state
    """
    error_msg = str(error)
    logger.error("Provisioning failed for session %s: %s", session_id, error_msg)

    if sandbox:
        try:
            await sandbox.stop()
            logger.info("Stopped sandbox %s after provision failure", sandbox.instance_id)
        except Exception:
            logger.exception("Failed to stop sandbox during error cleanup")

    return await transition_to_error(db, session_id, error_msg, "PROVISIONING")


async def handle_snapshot_failure(
    db: AsyncSession, session_id: UUID, error: Exception, sandbox: Sandbox,
) -> SetupSession:
    """Handle a snapshot failure by cleaning up and transitioning to ERROR.

    Args:
        db: Database session
        session_id: Setup session ID
        error: The exception that caused the failure
        sandbox: The sandbox to stop

    Returns:
        Updated session in ERROR state
    """
    error_msg = str(error)
    logger.error("Snapshot failed for session %s: %s", session_id, error_msg)

    try:
        await sandbox.stop()
        logger.info("Stopped sandbox %s after snapshot failure", sandbox.instance_id)
    except Exception:
        logger.exception("Failed to stop sandbox during error cleanup")

    return await transition_to_error(db, session_id, error_msg, "SAVING")
