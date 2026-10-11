"""AI assistant, step 1 (docs/design/assistant.md §3.12): account-level consent columns, the metering columns on
usage_periods, the operator's monthly spend row and the assistant_turns ledger. No column anywhere holds the owner's
words, the recent lines, the context, the model's reply or the proposed steps: counts, costs, outcome codes, the
served model id and the provider request id only.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-11 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_USAGE_BIGINTS = (
    "ai_cost_used_micro_usd",
    "ai_cost_reserved_micro_usd",
    "ai_input_tokens",
    "ai_output_tokens",
    "ai_cache_read_tokens",
    "ai_cache_write_tokens",
)


def upgrade() -> None:
    op.add_column("accounts", sa.Column("assistant_consent_version", sa.String(length=16), nullable=True))
    op.add_column("accounts", sa.Column("assistant_consented_at", sa.DateTime(timezone=True), nullable=True))
    for name in _USAGE_BIGINTS:
        op.add_column("usage_periods", sa.Column(name, sa.BigInteger(), server_default=sa.text("0"), nullable=False))
    op.add_column(
        "usage_periods", sa.Column("ai_failed_turns", sa.Integer(), server_default=sa.text("0"), nullable=False)
    )
    op.create_table(
        "ai_spend_months",
        sa.Column("month_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("spent_micro_usd", sa.BigInteger(), nullable=False),
        sa.Column("reserved_micro_usd", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("month_start"),
    )
    op.create_table(
        "assistant_turns",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("account_id", sa.UUID(), nullable=False),
        sa.Column("client_turn_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("charged", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("month_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reserved_micro_usd", sa.BigInteger(), nullable=False),
        sa.Column("cost_micro_usd", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("input_tokens", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("output_tokens", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("cache_read_tokens", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("cache_write_tokens", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("model_served", sa.String(length=64), nullable=True),
        sa.Column("fallback_used", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("provider_request_id", sa.String(length=128), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'answered', 'invalid_output', 'refused', 'provider_error', 'timeout', "
            "'cancelled', 'abandoned')",
            name="ck_assistant_turns_status",
        ),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("account_id", "client_turn_id", name="uq_assistant_turns_account_turn"),
    )
    op.create_index(op.f("ix_assistant_turns_account_id"), "assistant_turns", ["account_id"], unique=False)
    op.create_index("ix_assistant_turns_status_created", "assistant_turns", ["status", "created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_assistant_turns_status_created", table_name="assistant_turns")
    op.drop_index(op.f("ix_assistant_turns_account_id"), table_name="assistant_turns")
    op.drop_table("assistant_turns")
    op.drop_table("ai_spend_months")
    op.drop_column("usage_periods", "ai_failed_turns")
    for name in reversed(_USAGE_BIGINTS):
        op.drop_column("usage_periods", name)
    op.drop_column("accounts", "assistant_consented_at")
    op.drop_column("accounts", "assistant_consent_version")
