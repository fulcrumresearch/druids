"""User model."""

from datetime import datetime, timezone
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import Field, SQLModel


class User(SQLModel, table=True):
    """A GitHub-authenticated user."""

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    github_id: int = Field(unique=True, index=True)
    github_login: str | None = Field(default=None)
    access_token: str = Field(index=True)
    stripe_customer_id: str | None = Field(default=None, index=True)
    subscription_status: str | None = Field(default=None)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=sa.Column(sa.DateTime(timezone=True)),
    )


async def get_or_create_user(
    db: AsyncSession, github_id: int, access_token: str, github_login: str | None = None
) -> User:
    """Get existing user by github_id or create new one. Updates access_token and github_login if changed."""
    result = await db.execute(select(User).where(User.github_id == github_id))
    user = result.scalar_one_or_none()
    if user:
        changed = False
        if user.access_token != access_token:
            user.access_token = access_token
            changed = True
        if github_login is not None and user.github_login != github_login:
            user.github_login = github_login
            changed = True
        if changed:
            await db.flush()
            await db.refresh(user)
    else:
        user = User(github_id=github_id, access_token=access_token, github_login=github_login)
        db.add(user)
        await db.flush()
        await db.refresh(user)
    return user


async def get_user(db: AsyncSession, user_id: UUID) -> User | None:
    """Get user by ID."""
    result = await db.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()


async def get_user_by_token(db: AsyncSession, access_token: str) -> User | None:
    """Get user by access token."""
    result = await db.execute(select(User).where(User.access_token == access_token))
    return result.scalar_one_or_none()


async def get_user_by_stripe_customer(db: AsyncSession, customer_id: str) -> User | None:
    """Get user by Stripe customer ID."""
    result = await db.execute(select(User).where(User.stripe_customer_id == customer_id))
    return result.scalar_one_or_none()
