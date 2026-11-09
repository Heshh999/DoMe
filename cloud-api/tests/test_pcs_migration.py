"""Migration 0006 (REST pc.protocol_versions, protocol 1.5) in a database of its own: upgrade, downgrade to 0005 and
upgrade again; a PC row that existed before the upgrade reads ``[]`` (the server default), and the models match the
schema afterwards (what ``alembic check`` verifies in CI)."""

from __future__ import annotations

import secrets
import uuid

import psycopg
from alembic import command
from sqlalchemy.engine import make_url

from dome_api.db.migrate import alembic_config
from tests.conftest import TEST_DB_URL, sql


def pcs_columns(url: str) -> dict[str, tuple[str, str, str | None]]:
    rows = sql(
        url,
        "SELECT column_name, data_type, is_nullable, column_default FROM information_schema.columns "
        "WHERE table_name = 'pcs'",
    )
    return {name: (kind, nullable, default) for (name, kind, nullable, default) in rows}


def test_migration_0006_downgrade_upgrade_roundtrip() -> None:
    base = make_url(TEST_DB_URL)
    name = f"dome_mig_{secrets.token_hex(4)}"
    admin = base.set(database="postgres").set(drivername="postgresql").render_as_string(hide_password=False)
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{name}"')
    url = base.set(database=name).render_as_string(hide_password=False)
    try:
        cfg = alembic_config(url)
        command.upgrade(cfg, "head")
        kind, nullable, default = pcs_columns(url)["protocol_versions"]
        assert kind == "jsonb" and nullable == "NO" and default is not None and "'[]'::jsonb" in default
        command.downgrade(cfg, "0005")
        assert "protocol_versions" not in pcs_columns(url)
        # a PC linked before the upgrade (no hello to a 1.5 relay yet) reads as an empty list afterwards
        account_id, pc_id = uuid.uuid4(), uuid.uuid4()
        sql(
            url,
            "INSERT INTO accounts (id, issuer, subject, email, display_name) VALUES (%s, 'iss', 'sub', 'a@b.test', 'A')",
            (account_id,),
        )
        sql(
            url,
            "INSERT INTO pcs (id, account_id, name, public_jwk, kid, enabled, remote_enabled_reported, platform, "
            "agent_version) VALUES (%s, %s, 'Desk PC', '{}'::jsonb, %s, true, false, 'development', '0.1.0')",
            (pc_id, account_id, secrets.token_hex(8)),
        )
        command.upgrade(cfg, "head")
        assert sql(url, "SELECT protocol_versions FROM pcs WHERE id = %s", (pc_id,)) == [([],)]
        command.check(cfg)  # raises when the models and the migrated schema differ
    finally:
        with psycopg.connect(admin, autocommit=True) as conn:
            conn.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s AND pid <> pg_backend_pid()",
                (name,),
            )
            conn.execute(f'DROP DATABASE "{name}"')
