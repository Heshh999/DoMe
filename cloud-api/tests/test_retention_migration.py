"""Migration 0008 (DECISIONS #122) in a database of its own: the financial records (``subscriptions``,
``topup_purchases``) keep their rows when an account is hard-deleted (``account_id`` nullable, ``ON DELETE SET NULL``);
downgrade to 0007 restores ``NOT NULL`` and ``CASCADE`` and refuses while rows of hard-deleted accounts exist; upgrade
again, with the models matching the schema afterwards (what ``alembic check`` verifies in CI)."""

from __future__ import annotations

import secrets
import uuid
from collections.abc import Iterator

import psycopg
import pytest
from alembic import command
from sqlalchemy.engine import make_url

from dome_api.db.migrate import alembic_config
from tests.conftest import TEST_DB_URL, sql

FINANCIAL = ("subscriptions", "topup_purchases", "billing_events")


@pytest.fixture
def database() -> Iterator[str]:
    base = make_url(TEST_DB_URL)
    name = f"dome_mig_{secrets.token_hex(4)}"
    admin = base.set(database="postgres").set(drivername="postgresql").render_as_string(hide_password=False)
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{name}"')
    try:
        yield base.set(database=name).render_as_string(hide_password=False)
    finally:
        with psycopg.connect(admin, autocommit=True) as conn:
            conn.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s AND pid <> pg_backend_pid()",
                (name,),
            )
            conn.execute(f'DROP DATABASE "{name}"')


def account_fks(url: str) -> dict[str, tuple[str, str]]:
    """Per financial table: (ON DELETE action, is_nullable) of its account_id."""
    rows = sql(
        url,
        "SELECT c.conrelid::regclass::text, c.confdeltype, col.is_nullable FROM pg_constraint c "
        "JOIN information_schema.columns col ON col.table_name = c.conrelid::regclass::text "
        "AND col.column_name = 'account_id' "
        "WHERE c.contype = 'f' AND c.confrelid = 'accounts'::regclass AND c.conrelid::regclass::text = ANY(%s)",
        (list(FINANCIAL),),
    )
    action = {"c": "CASCADE", "n": "SET NULL"}
    return {table: (action[str(kind)], str(nullable)) for table, kind, nullable in rows}


def seed(url: str) -> tuple[str, str, str]:
    """One account with a cancelled subscription, a credited top-up and a billing event."""
    account = str(uuid.uuid4())
    sql(
        url,
        "INSERT INTO accounts (id, issuer, subject, email, display_name) VALUES (%s, 'i', %s, '', '')",
        (account, account),
    )
    sub = str(uuid.uuid4())
    sql(
        url,
        "INSERT INTO subscriptions (id, account_id, provider, plan, state, cancel_at_period_end, provider_subscription_id) "
        "VALUES (%s, %s, 'stripe', 'pro', 'canceled', false, %s)",
        (sub, account, "sub_" + sub[:8]),
    )
    topup = str(uuid.uuid4())
    sql(
        url,
        "INSERT INTO topup_purchases (id, account_id, provider_checkout_session_id, requests, amount_cents, currency, status) "
        "VALUES (%s, %s, %s, 100, 299, 'USD', 'credited')",
        (topup, account, "cs_" + topup[:8]),
    )
    sql(
        url,
        "INSERT INTO billing_events (provider, provider_event_id, event_type, account_id, status) "
        "VALUES ('stripe', %s, 'invoice.paid', %s, 'processed')",
        ("evt_" + account[:8], account),
    )
    return account, sub, topup


def test_migration_0008_financial_records_outlive_the_account(database: str) -> None:
    url = database
    cfg = alembic_config(url)
    command.upgrade(cfg, "0007")
    assert account_fks(url) == {
        "subscriptions": ("CASCADE", "NO"),
        "topup_purchases": ("CASCADE", "NO"),
        "billing_events": ("SET NULL", "YES"),
    }
    command.upgrade(cfg, "head")
    assert account_fks(url) == {
        "subscriptions": ("SET NULL", "YES"),
        "topup_purchases": ("SET NULL", "YES"),
        "billing_events": ("SET NULL", "YES"),
    }
    command.check(cfg)  # raises when the models and the migrated schema differ
    # a hard delete keeps all three rows, without the account
    account, sub, topup = seed(url)
    sql(url, "DELETE FROM accounts WHERE id = %s", (account,))
    assert sql(url, "SELECT account_id FROM subscriptions WHERE id = %s", (sub,)) == [(None,)]
    assert sql(url, "SELECT account_id FROM topup_purchases WHERE id = %s", (topup,)) == [(None,)]
    assert sql(url, "SELECT count(*) FROM billing_events WHERE account_id IS NULL") == [(1,)]
    # the downgrade would have to delete those records: refused, and the schema stays where it was (the head: the
    # whole downgrade is one transaction, so the later migrations it undid first are back as well)
    head = sql(url, "SELECT version_num FROM alembic_version")
    assert head >= [("0008",)]
    with pytest.raises(RuntimeError, match="hard-deleted accounts"):
        command.downgrade(cfg, "0007")
    assert sql(url, "SELECT version_num FROM alembic_version") == head
    assert account_fks(url)["subscriptions"] == ("SET NULL", "YES")
    # without such rows it restores NOT NULL and CASCADE, and the upgrade applies again
    sql(url, "DELETE FROM subscriptions WHERE account_id IS NULL")
    sql(url, "DELETE FROM topup_purchases WHERE account_id IS NULL")
    command.downgrade(cfg, "0007")
    assert account_fks(url)["subscriptions"] == ("CASCADE", "NO")
    assert account_fks(url)["topup_purchases"] == ("CASCADE", "NO")
    command.upgrade(cfg, "head")
    assert account_fks(url)["subscriptions"] == ("SET NULL", "YES")
    command.check(cfg)
