"""Screen view round 2a (protocol 1.5; DECISIONS #117): the phone's TURN usernames that still carry a live screen
session after the phone minted its next credential (a Next screen switch keeps the old session and restores it when
the switch fails), revoked when that session ends. No foreign key, as for ``turn_pending_revocations``. No column holds
a credential or an address: ids, provider usernames and timestamps only.

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-09 18:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "turn_deferred_revocations",
        sa.Column("username", sa.String(length=256), nullable=False),
        sa.Column("screen_session_id", sa.String(length=22), nullable=False),
        sa.Column("account_id", sa.UUID(), nullable=False),
        sa.Column("controller_id", sa.UUID(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("username"),
    )
    for column in ("screen_session_id", "account_id", "controller_id", "expires_at"):
        op.create_index(
            op.f(f"ix_turn_deferred_revocations_{column}"), "turn_deferred_revocations", [column], unique=False
        )


def downgrade() -> None:
    for column in ("expires_at", "controller_id", "account_id", "screen_session_id"):
        op.drop_index(op.f(f"ix_turn_deferred_revocations_{column}"), table_name="turn_deferred_revocations")
    op.drop_table("turn_deferred_revocations")
