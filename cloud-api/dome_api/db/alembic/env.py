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


def _url() -> str:
    url = config.attributes.get("database_url") or os.environ.get("DOME_DATABASE_URL")
    if not url:
        raise RuntimeError("DOME_DATABASE_URL is not set")
    return str(url)


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
