"""Migration 0005 (Screen view) in a database of its own: upgrade, downgrade to 0004 and upgrade again, with the models
matching the schema afterwards (what ``alembic check`` verifies in CI)."""

from __future__ import annotations

import secrets

import psycopg
from alembic import command
from sqlalchemy.engine import make_url

from dome_api.db.migrate import alembic_config
from tests.conftest import TEST_DB_URL, sql

SCREEN_TABLES = {
    "screen_accounts",
    "screen_view_windows",
    "screen_reservations",
    "turn_credentials",
    "turn_usage_days",
    "turn_pending_revocations",
    "turn_mint_days",
    "turn_reconcile_passes",
}


def tables(url: str) -> set[str]:
    return {t for (t,) in sql(url, "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")}


def columns(url: str, table: str) -> set[str]:
    return {
        c for (c,) in sql(url, "SELECT column_name FROM information_schema.columns WHERE table_name = %s", (table,))
    }


def test_migration_0005_downgrade_upgrade_roundtrip() -> None:
    base = make_url(TEST_DB_URL)
    name = f"dome_mig_{secrets.token_hex(4)}"
    admin = base.set(database="postgres").set(drivername="postgresql").render_as_string(hide_password=False)
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{name}"')
    url = base.set(database=name).render_as_string(hide_password=False)
    try:
        cfg = alembic_config(url)
        command.upgrade(cfg, "head")
        assert SCREEN_TABLES <= tables(url)
        assert "pc_key_mac" in columns(url, "pairing_sessions")
        # no column anywhere in the new tables could hold SDP, an address or a credential
        for table in SCREEN_TABLES:
            assert not {"sdp", "offer_sdp", "answer_sdp", "credential", "address", "ip"} & columns(url, table), table
        command.downgrade(cfg, "0004")
        assert not SCREEN_TABLES & tables(url)
        assert "pc_key_mac" not in columns(url, "pairing_sessions")
        command.upgrade(cfg, "head")
        assert SCREEN_TABLES <= tables(url)
        command.check(cfg)  # raises when the models and the migrated schema differ
    finally:
        with psycopg.connect(admin, autocommit=True) as conn:
            conn.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s AND pid <> pg_backend_pid()",
                (name,),
            )
            conn.execute(f'DROP DATABASE "{name}"')
