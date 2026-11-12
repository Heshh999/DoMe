"""Migration 0009 (DECISIONS #121) in a database of its own: ``auth_flows.reauth`` (false for every attempt that
existed before) and ``sessions.authenticated_at`` (NULL for every session that existed before, so none of them can
delete an account), the downgrade drops both, and the upgrade applies again with the models matching the schema."""

from __future__ import annotations

import uuid

from alembic import command

from dome_api.db.migrate import alembic_config
from tests.conftest import sql
from tests.test_retention_migration import database

__all__ = ["database"]


def columns(url: str, table: str) -> set[str]:
    return {
        str(name)
        for (name,) in sql(url, "SELECT column_name FROM information_schema.columns WHERE table_name = %s", (table,))
    }


def test_migration_0009_records_reauthentication(database: str) -> None:
    url = database
    cfg = alembic_config(url)
    command.upgrade(cfg, "0008")
    assert "reauth" not in columns(url, "auth_flows") and "authenticated_at" not in columns(url, "sessions")
    account = str(uuid.uuid4())
    sql(
        url,
        "INSERT INTO accounts (id, issuer, subject, email, display_name) VALUES (%s, 'i', %s, '', '')",
        (account, account),
    )
    sql(
        url,
        "INSERT INTO sessions (id, account_id, token_hash, csrf_token, expires_at, absolute_expires_at) "
        "VALUES (%s, %s, %s, 'c', now() + interval '1 day', now() + interval '30 days')",
        (str(uuid.uuid4()), account, uuid.uuid4().bytes * 2),
    )
    sql(
        url,
        "INSERT INTO auth_flows (id, state, nonce, code_verifier, return_to, expires_at) "
        "VALUES (%s, 's', 'n', 'v', '/', now() + interval '10 minutes')",
        (str(uuid.uuid4()),),
    )
    command.upgrade(cfg, "0009")
    # what existed before is neither a re-authentication attempt nor a re-authenticated session
    assert sql(url, "SELECT reauth FROM auth_flows") == [(False,)]
    assert sql(url, "SELECT authenticated_at FROM sessions") == [(None,)]
    command.upgrade(cfg, "head")
    command.check(cfg)  # raises when the models and the migrated schema differ
    command.downgrade(cfg, "0008")
    assert "reauth" not in columns(url, "auth_flows") and "authenticated_at" not in columns(url, "sessions")
    assert sql(url, "SELECT count(*) FROM sessions") == [(1,)]
    command.upgrade(cfg, "head")
    command.check(cfg)
