"""Alembic environment. Runs synchronously through the psycopg driver (the same dialect the app
uses asynchronously). The URL comes from ``config.attributes['database_url']`` when the app
invokes migrations programmatically, otherwise from ``DOME_DATABASE_URL``."""

from __future__ import annotations

import os

from alembic import context
from sqlalchemy import create_engine, pool

from dome_api.db.models import Base

config = context.config
target_metadata = Base.metadata

# Fly runs migrations in a release machine that prints nothing while it waits and is cut off after five minutes: a
# database that never answers (a Neon compute that does not wake) or a lock held by a long transaction elsewhere would
# hang silently until then (Deploy #9, 2026-10-26). Both become an error in the release log within a minute instead.
# Tests pass smaller values through ``config.attributes``.
CONNECT_TIMEOUT_SECONDS = 20
LOCK_TIMEOUT = "60s"


def _url() -> str:
    url = config.attributes.get("database_url") or os.environ.get("DOME_DATABASE_URL")
    if not url:
        raise RuntimeError("DOME_DATABASE_URL is not set")
    return str(url)


def run_migrations_offline() -> None:
    context.configure(
        url=_url(), target_metadata=target_metadata, literal_binds=True, dialect_opts={"paramstyle": "named"}
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connect_timeout = int(config.attributes.get("connect_timeout_seconds", CONNECT_TIMEOUT_SECONDS))
    lock_timeout = str(config.attributes.get("lock_timeout", LOCK_TIMEOUT))
    engine = create_engine(_url(), poolclass=pool.NullPool, connect_args={"connect_timeout": connect_timeout})
    with engine.connect() as connection:
        # a session setting, committed before the migration's own transaction begins (Alembic only commits a
        # transaction it began itself)
        connection.exec_driver_sql("SELECT set_config('lock_timeout', %s, false)", (lock_timeout,))
        connection.commit()
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
