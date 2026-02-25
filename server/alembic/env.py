import asyncio
import os
from logging.config import fileConfig

# Import all models so SQLModel.metadata is populated
import orpheus.db.models.devbox  # noqa: F401
import orpheus.db.models.execution  # noqa: F401
import orpheus.db.models.spec  # noqa: F401
import orpheus.db.models.task  # noqa: F401
import orpheus.db.models.user  # noqa: F401
import orpheus.db.models.user_spec  # noqa: F401
from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config
from sqlmodel import SQLModel


# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Override sqlalchemy.url from environment. The application uses asyncpg but
# Alembic needs the synchronous psycopg driver.
database_url = os.environ.get("ORPHEUS_DATABASE_URL", "postgresql+asyncpg://postgres@localhost/orpheus")
config.set_main_option("sqlalchemy.url", database_url.replace("+asyncpg", "+psycopg"))

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = SQLModel.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """In this scenario we need to create an Engine
    and associate a connection with the context.
    """
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
