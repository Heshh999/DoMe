"""Migrations never hang silently (dome_api/db/alembic/env.py): a database that does not answer and a lock held elsewhere
both end in an error within seconds instead of waiting out Fly's five-minute release timeout (Deploy #9, 2026-10-26)."""

from __future__ import annotations

import secrets
import socket
import threading
import time
from collections.abc import Iterator

import psycopg
import pytest
from alembic import command
from sqlalchemy.engine import make_url

from dome_api.db.migrate import alembic_config
from tests.conftest import TEST_DB_URL, sql


@pytest.fixture
def silent_server() -> Iterator[int]:
    """A TCP port that accepts a connection and never says a word, like a database that does not wake."""
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(4)
    accepted: list[socket.socket] = []
    stop = threading.Event()

    def accept() -> None:
        server.settimeout(0.2)
        while not stop.is_set():
            try:
                conn, _ = server.accept()
            except OSError:
                continue
            accepted.append(conn)

    thread = threading.Thread(target=accept, daemon=True)
    thread.start()
    yield server.getsockname()[1]
    stop.set()
    thread.join(5)
    for conn in accepted:
        conn.close()
    server.close()


@pytest.fixture
def migrated_database() -> Iterator[str]:
    base = make_url(TEST_DB_URL)
    name = f"dome_mig_{secrets.token_hex(4)}"
    admin = base.set(database="postgres").set(drivername="postgresql").render_as_string(hide_password=False)
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{name}"')
    url = base.set(database=name).render_as_string(hide_password=False)
    try:
        command.upgrade(alembic_config(url), "head")
        yield url
    finally:
        with psycopg.connect(admin, autocommit=True) as conn:
            conn.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s AND pid <> pg_backend_pid()",
                (name,),
            )
            conn.execute(f'DROP DATABASE "{name}"')


def test_a_database_that_never_answers_fails_within_the_connect_timeout(silent_server: int) -> None:
    cfg = alembic_config(f"postgresql+psycopg://dome@127.0.0.1:{silent_server}/dome_test")
    cfg.attributes["connect_timeout_seconds"] = 2  # libpq's smallest
    started = time.monotonic()
    with pytest.raises(Exception, match="timeout"):
        command.upgrade(cfg, "head")
    assert time.monotonic() - started < 15


def test_a_lock_held_elsewhere_fails_within_the_lock_timeout(migrated_database: str) -> None:
    plain = make_url(migrated_database).set(drivername="postgresql").render_as_string(hide_password=False)
    with psycopg.connect(plain) as holder:  # a long transaction elsewhere, holding what the migration must read
        holder.execute("LOCK TABLE alembic_version IN ACCESS EXCLUSIVE MODE")
        cfg = alembic_config(migrated_database)
        cfg.attributes["lock_timeout"] = "500ms"
        started = time.monotonic()
        with pytest.raises(Exception, match="lock timeout"):
            command.upgrade(cfg, "head")
        assert time.monotonic() - started < 15
        holder.rollback()


def test_the_lock_timeout_does_not_stop_the_migration_from_committing(migrated_database: str) -> None:
    # env.py commits its session setting before Alembic's own transaction: a downgrade and an upgrade still persist
    cfg = alembic_config(migrated_database)
    command.downgrade(cfg, "0004")
    assert sql(migrated_database, "SELECT version_num FROM alembic_version") == [("0004",)]
    command.upgrade(cfg, "head")
    assert sql(migrated_database, "SELECT version_num FROM alembic_version") != [("0004",)]
