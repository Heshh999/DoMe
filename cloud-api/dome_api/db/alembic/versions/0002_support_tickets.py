"""Support tickets (spec section 11A): customer-initiated, account-scoped requests with a human-readable
reference, the already-redacted diagnostics text and an operator answer slot. No column can carry
an executable instruction for a PC.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-09 10:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "support_tickets",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("account_id", sa.UUID(), nullable=False),
        sa.Column("reference", sa.String(length=11), nullable=False),
        sa.Column("category", sa.String(length=16), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("diagnostics_redacted", sa.Text(), nullable=True),
        sa.Column("app_version", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("answer", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "status IN ('received', 'in_review', 'answered', 'closed')", name="ck_support_tickets_status"
        ),
        sa.CheckConstraint(
            "category IN ('connection', 'pairing', 'media', 'input', 'apps', 'power', 'install', 'billing', "
            "'account', 'other')",
            name="ck_support_tickets_category",
        ),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("reference"),
    )
    op.create_index(op.f("ix_support_tickets_account_id"), "support_tickets", ["account_id"], unique=False)
    op.create_index("ix_support_tickets_account_created", "support_tickets", ["account_id", "created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_support_tickets_account_created", table_name="support_tickets")
    op.drop_index(op.f("ix_support_tickets_account_id"), table_name="support_tickets")
    op.drop_table("support_tickets")
