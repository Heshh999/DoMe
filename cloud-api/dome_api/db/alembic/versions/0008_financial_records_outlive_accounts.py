"""Account deletion and the retention purge (DECISIONS #121, #122): the financial records outlive an account's hard
delete. ``subscriptions.account_id`` and ``topup_purchases.account_id`` become nullable and their foreign keys
``ON DELETE SET NULL`` (they were ``CASCADE``), as ``billing_events.account_id`` already was. How long the records are
kept after that (7 years proposed) is for counsel to confirm (docs/DATA_RETENTION.md).

The downgrade restores ``NOT NULL`` and ``CASCADE``. It refuses while rows of hard-deleted accounts exist (it would
otherwise have to delete financial records); nothing else is lost.

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-12 18:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("subscriptions", "topup_purchases")


def _foreign_key(table: str, ondelete: str) -> None:
    op.drop_constraint(f"{table}_account_id_fkey", table, type_="foreignkey")
    op.create_foreign_key(f"{table}_account_id_fkey", table, "accounts", ["account_id"], ["id"], ondelete=ondelete)


def upgrade() -> None:
    for table in TABLES:
        op.alter_column(table, "account_id", existing_type=sa.UUID(), nullable=True)
        _foreign_key(table, "SET NULL")


def downgrade() -> None:
    bind = op.get_bind()
    for table in TABLES:
        orphans = bind.execute(sa.text(f"SELECT count(*) FROM {table} WHERE account_id IS NULL")).scalar_one()
        if orphans:
            raise RuntimeError(
                f"{table} holds {orphans} row(s) of hard-deleted accounts; downgrading below 0008 would have to delete "
                "these financial records, so it is refused"
            )
    for table in TABLES:
        _foreign_key(table, "CASCADE")
        op.alter_column(table, "account_id", existing_type=sa.UUID(), nullable=False)
