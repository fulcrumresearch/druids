"""Secret model -- encrypted environment variables for devboxes."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import Field, SQLModel


# crypto is imported lazily inside set_value/get_value because a top-level
# import pulls in config.Settings, which requires the full set of app env
# vars. That would break alembic commands that only need the model metadata.


class Secret(SQLModel, table=True):
    """An encrypted environment variable associated with a devbox."""

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    devbox_id: UUID = Field(foreign_key="devbox.id", index=True)
    name: str = Field(index=True)
    encrypted_value: str = Field(default="")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=sa.Column(sa.DateTime(timezone=True)),
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=sa.Column(sa.DateTime(timezone=True)),
    )

    def set_value(self, plaintext: str) -> None:
        from druids_server.utils.crypto import encrypt

        self.encrypted_value = encrypt(plaintext)
        self.updated_at = datetime.now(timezone.utc)

    def get_value(self) -> str:
        from druids_server.utils.crypto import decrypt

        return decrypt(self.encrypted_value)


async def get_secrets(db: AsyncSession, devbox_id: UUID) -> list[Secret]:
    """Get all secrets for a devbox."""
    result = await db.execute(select(Secret).where(Secret.devbox_id == devbox_id).order_by(Secret.name))
    return list(result.scalars().all())


async def get_secret_by_name(db: AsyncSession, devbox_id: UUID, name: str) -> Secret | None:
    """Get a secret by devbox and name."""
    result = await db.execute(select(Secret).where(Secret.devbox_id == devbox_id, Secret.name == name))
    return result.scalar_one_or_none()


async def set_secret(db: AsyncSession, devbox_id: UUID, name: str, value: str) -> Secret:
    """Create or update a secret. Returns the secret."""
    secret = await get_secret_by_name(db, devbox_id, name)
    if secret:
        secret.set_value(value)
    else:
        secret = Secret(devbox_id=devbox_id, name=name)
        secret.set_value(value)
        db.add(secret)
    await db.flush()
    await db.refresh(secret)
    return secret


async def delete_secret(db: AsyncSession, devbox_id: UUID, name: str) -> bool:
    """Delete a secret by name. Returns True if it existed."""
    secret = await get_secret_by_name(db, devbox_id, name)
    if not secret:
        return False
    await db.delete(secret)
    return True


async def get_decrypted_secrets(db: AsyncSession, devbox_id: UUID) -> dict[str, str]:
    """Get all secrets for a devbox as a plaintext dict. Used during provisioning."""
    secrets = await get_secrets(db, devbox_id)
    return {s.name: s.get_value() for s in secrets}
