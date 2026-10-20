"""Migration 0004 (billing) in a database of its own: upgrade, downgrade to 0003 and upgrade again, with the models
matching the schema afterwards (what ``alembic check`` verifies in CI)."""

from __future__ import annotations

import secrets

import psycopg
from alembic import command
from sqlalchemy.engine import make_url

from dome_api.db.migrate import alembic_config
from tests.conftest import TEST_DB_URL, sql

BILLING_TABLES = {"usage_windows", "ai_balances", "topup_purchases"}


def tables(url: str) -> set[str]:
    return {t for (t,) in sql(url, "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")}


def test_migration_0004_downgrade_upgrade_roundtrip() -> None:
    base = make_url(TEST_DB_URL)
    name = f"dome_mig_{secrets.token_hex(4)}"
    admin = base.set(database="postgres").set(drivername="postgresql").render_as_string(hide_password=False)
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{name}"')
    url = base.set(database=name).render_as_string(hide_password=False)
    try:
        cfg = alembic_config(url)
        command.upgrade(cfg, "head")
        assert BILLING_TABLES <= tables(url)
        command.downgrade(cfg, "0003")
        assert not BILLING_TABLES & tables(url)
        columns = {
            c for (c,) in sql(url, "SELECT column_name FROM information_schema.columns WHERE table_name = 'pcs'")
        }
        assert "plan_disabled_at" not in columns
        command.upgrade(cfg, "head")
        assert BILLING_TABLES <= tables(url)
        command.check(cfg)  # raises when the models and the migrated schema differ
    finally:
        with psycopg.connect(admin, autocommit=True) as conn:
            conn.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s AND pid <> pg_backend_pid()",
                (name,),
            )
            conn.execute(f'DROP DATABASE "{name}"')
