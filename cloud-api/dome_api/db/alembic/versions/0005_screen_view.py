"""Screen view, Phase 1 (docs/design/screen-view.md §10, §12, §13; protocol 1.4): the PC key MAC on pairing rows, the
Free-minutes ledger settled on the relay's clock (``screen_accounts`` lock and mint counter, ``screen_view_windows``,
``screen_reservations``), the phone's TURN credentials (one row per controller, usernames only) and the provider's
reconciled usage per account and day, the revocations the provider has not confirmed yet, the per-day mint counters
the provisional guard reads and the reconciliation passes. No column holds SDP, an address, a credential or a name:
ids, provider usernames, seconds, bytes, states and timestamps only.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-21 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("pairing_sessions", sa.Column("pc_key_mac", sa.String(length=43), nullable=True))

    op.create_table(
        "screen_accounts",
        sa.Column("account_id", sa.UUID(), nullable=False),
        sa.Column("turn_mints_month_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("turn_mints_this_month", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("account_id"),
    )

    op.create_table(
        "screen_view_windows",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("account_id", sa.UUID(), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_seconds", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("reserved_seconds", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("zero_charged_attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("consent_forgiven", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("pause_discount_seconds", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("account_id", "window_start", name="uq_screen_view_windows_account_start"),
    )
    op.create_index(op.f("ix_screen_view_windows_account_id"), "screen_view_windows", ["account_id"], unique=False)
    op.create_index(
        "ix_screen_view_windows_account_end", "screen_view_windows", ["account_id", "window_end"], unique=False
    )

    op.create_table(
        "screen_reservations",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("account_id", sa.UUID(), nullable=False),
        sa.Column("pc_id", sa.UUID(), nullable=False),
        sa.Column("controller_id", sa.UUID(), nullable=False),
        sa.Column("command_id", sa.UUID(), nullable=False),
        sa.Column("screen_session_id", sa.String(length=22), nullable=True),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("unlimited", sa.Boolean(), nullable=False),
        sa.Column("forwarded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("segment_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reserved_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("outstanding_seconds", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("charged_seconds", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("pause_discount_seconds", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("final_reason", sa.String(length=32), nullable=True),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("end_reason", sa.String(length=32), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("state IN ('reserved', 'live', 'settled', 'swept')", name="ck_screen_reservations_state"),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["controller_id"], ["controllers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["pc_id"], ["pcs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("command_id"),
    )
    op.create_index(op.f("ix_screen_reservations_account_id"), "screen_reservations", ["account_id"], unique=False)
    op.create_index(
        op.f("ix_screen_reservations_screen_session_id"), "screen_reservations", ["screen_session_id"], unique=False
    )
    op.create_index(
        "ix_screen_reservations_state_until", "screen_reservations", ["state", "reserved_until"], unique=False
    )
    op.create_index(
        "ix_screen_reservations_account_created", "screen_reservations", ["account_id", "created_at"], unique=False
    )

    op.create_table(
        "turn_credentials",
        sa.Column("controller_id", sa.UUID(), nullable=False),
        sa.Column("account_id", sa.UUID(), nullable=False),
        sa.Column("username", sa.String(length=256), nullable=False),
        sa.Column("minted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ttl_seconds", sa.Integer(), nullable=False),
        sa.Column("command_id", sa.UUID(), nullable=True),
        sa.Column("screen_session_id", sa.String(length=22), nullable=True),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoke_reason", sa.String(length=32), nullable=True),
        sa.CheckConstraint(
            "state IN ('outstanding', 'bound', 'revoked', 'revoke_pending', 'expired')",
            name="ck_turn_credentials_state",
        ),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["controller_id"], ["controllers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("controller_id"),
    )
    op.create_index(op.f("ix_turn_credentials_account_id"), "turn_credentials", ["account_id"], unique=False)
    op.create_index("ix_turn_credentials_account_minted", "turn_credentials", ["account_id", "minted_at"], unique=False)
    op.create_index("ix_turn_credentials_state_minted", "turn_credentials", ["state", "minted_at"], unique=False)

    op.create_table(
        "turn_pending_revocations",
        sa.Column("username", sa.String(length=256), nullable=False),
        sa.Column("account_id", sa.UUID(), nullable=False),
        sa.Column("controller_id", sa.UUID(), nullable=False),
        sa.Column("reason", sa.String(length=32), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("last_error", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("username"),
    )
    op.create_index(
        op.f("ix_turn_pending_revocations_expires_at"), "turn_pending_revocations", ["expires_at"], unique=False
    )

    op.create_table(
        "turn_mint_days",
        sa.Column("account_id", sa.UUID(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("minted_ttl_seconds", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("released_ttl_seconds", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("account_id", "day"),
    )

    op.create_table(
        "turn_reconcile_passes",
        sa.Column("reconciled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rows", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.PrimaryKeyConstraint("reconciled_at"),
    )

    op.create_table(
        "turn_usage_days",
        sa.Column("account_id", sa.UUID(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("egress_bytes", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("reconciled_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("account_id", "day"),
    )


def downgrade() -> None:
    op.drop_table("turn_usage_days")
    op.drop_table("turn_reconcile_passes")
    op.drop_table("turn_mint_days")
    op.drop_index(op.f("ix_turn_pending_revocations_expires_at"), table_name="turn_pending_revocations")
    op.drop_table("turn_pending_revocations")
    op.drop_index("ix_turn_credentials_state_minted", table_name="turn_credentials")
    op.drop_index("ix_turn_credentials_account_minted", table_name="turn_credentials")
    op.drop_index(op.f("ix_turn_credentials_account_id"), table_name="turn_credentials")
    op.drop_table("turn_credentials")
    op.drop_index("ix_screen_reservations_account_created", table_name="screen_reservations")
    op.drop_index("ix_screen_reservations_state_until", table_name="screen_reservations")
    op.drop_index(op.f("ix_screen_reservations_screen_session_id"), table_name="screen_reservations")
    op.drop_index(op.f("ix_screen_reservations_account_id"), table_name="screen_reservations")
    op.drop_table("screen_reservations")
    op.drop_index("ix_screen_view_windows_account_end", table_name="screen_view_windows")
    op.drop_index(op.f("ix_screen_view_windows_account_id"), table_name="screen_view_windows")
    op.drop_table("screen_view_windows")
    op.drop_table("screen_accounts")
    op.drop_column("pairing_sessions", "pc_key_mac")
