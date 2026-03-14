"""Setup session model for managing devbox setup flow."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import Field, SQLModel


class SetupSession(SQLModel, table=True):
    """Track state for a single devbox setup session.

    A setup session moves through several states:
    - INIT: Session created, ready to start
    - PROVISIONING: Creating sandbox and cloning repo
    - CONFIGURING: User is configuring the environment (sandbox running)
    - VERIFYING: Running verification checks
    - SAVING: Snapshotting the environment
    - COMPLETED: Setup finished successfully
    - ERROR: Setup failed, error message stored

    On error, the session stores which step failed and the error message.
    The retry endpoint can resume from the failed step.
    """

    __tablename__ = "setup_session"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: UUID = Field(foreign_key="user.id", index=True)
    devbox_id: UUID = Field(foreign_key="devbox.id", index=True)

    # Current state
    state: str = Field(default="INIT", index=True)

    # Instance ID of running sandbox (set during PROVISIONING, cleared on SAVING)
    instance_id: str | None = Field(default=None)

    # Error tracking
    error_message: str | None = Field(default=None, sa_column=sa.Column(sa.Text()))
    failed_step: str | None = Field(default=None)

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=sa.Column(sa.DateTime(timezone=True)),
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=sa.Column(sa.DateTime(timezone=True)),
    )
    completed_at: datetime | None = Field(default=None, sa_column=sa.Column(sa.DateTime(timezone=True)))


async def create_setup_session(db: AsyncSession, user_id: UUID, devbox_id: UUID) -> SetupSession:
    """Create a new setup session."""
    session = SetupSession(user_id=user_id, devbox_id=devbox_id)
    db.add(session)
    await db.flush()
    await db.refresh(session)
    return session


async def get_setup_session(db: AsyncSession, session_id: UUID) -> SetupSession | None:
    """Get setup session by ID."""
    result = await db.execute(select(SetupSession).where(SetupSession.id == session_id))
    return result.scalar_one_or_none()


async def get_active_session_for_devbox(db: AsyncSession, devbox_id: UUID) -> SetupSession | None:
    """Get active (non-completed, non-error) setup session for a devbox."""
    result = await db.execute(
        select(SetupSession)
        .where(
            SetupSession.devbox_id == devbox_id,
            SetupSession.state.not_in(["COMPLETED", "ERROR"]),
        )
        .order_by(SetupSession.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def update_session_state(
    db: AsyncSession,
    session_id: UUID,
    state: str,
    instance_id: str | None = None,
    error_message: str | None = None,
    failed_step: str | None = None,
) -> SetupSession | None:
    """Update session state and related fields."""
    session = await get_setup_session(db, session_id)
    if not session:
        return None

    session.state = state
    session.updated_at = datetime.now(timezone.utc)

    if instance_id is not None:
        session.instance_id = instance_id

    if error_message is not None:
        session.error_message = error_message

    if failed_step is not None:
        session.failed_step = failed_step

    if state == "COMPLETED":
        session.completed_at = datetime.now(timezone.utc)

    await db.flush()
    await db.refresh(session)
    return session
