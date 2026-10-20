"""Plans and billing (docs/BILLING.md; protocol 1.3): the subscription row's checkout and provider-sync columns with
its state check and one-live-row index, webhook attempts and kinds, the rolling AI windows (``usage_windows``), the
per-account AI balances (taste, top-ups), top-up purchases, the plan-selection stamps on accounts, PCs and
controllers, and the turn ledger's source. No column holds an email, card data or a raw webhook payload: provider
ids, counts, states and timestamps only. ``usage_periods`` is left as it is (no longer written).

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-20 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SUBSCRIPTION_STATES = (
    "'checkout_pending', 'incomplete', 'trialing', 'active', 'cancel_scheduled', 'past_due', 'unpaid', 'canceled'"
)


def upgrade() -> None:
    op.add_column("accounts", sa.Column("plan_changed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("pcs", sa.Column("plan_disabled_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("controllers", sa.Column("plan_selected_at", sa.DateTime(timezone=True), nullable=True))

    op.add_column("subscriptions", sa.Column("interval", sa.String(length=16), nullable=True))
    op.add_column("subscriptions", sa.Column("provider_checkout_session_id", sa.String(length=128), nullable=True))
    op.add_column("subscriptions", sa.Column("checkout_idempotency_key", sa.String(length=128), nullable=True))
    op.add_column("subscriptions", sa.Column("checkout_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("subscriptions", sa.Column("provider_status", sa.String(length=32), nullable=True))
    op.add_column("subscriptions", sa.Column("provider_synced_at", sa.DateTime(timezone=True), nullable=True))
    op.create_unique_constraint(
        "uq_subscriptions_provider_checkout_session_id", "subscriptions", ["provider_checkout_session_id"]
    )
    op.create_check_constraint("ck_subscriptions_state", "subscriptions", f"state IN ({_SUBSCRIPTION_STATES})")
    op.create_index(
        "uq_subscriptions_live_account",
        "subscriptions",
        ["account_id"],
        unique=True,
        postgresql_where=sa.text("state <> 'canceled'"),
    )

    op.add_column("billing_events", sa.Column("kind", sa.String(length=16), nullable=True))
    op.add_column("billing_events", sa.Column("provider_object_id", sa.String(length=128), nullable=True))
    op.add_column("billing_events", sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False))
    op.create_check_constraint(
        "ck_billing_events_status", "billing_events", "status IN ('received', 'processed', 'ignored', 'failed')"
    )
    op.create_index("ix_billing_events_status_received", "billing_events", ["status", "received_at"], unique=False)

    op.create_table(
        "usage_windows",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("account_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.String(length=8), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("requests_used", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("requests_reserved", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("cost_used_micro_usd", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("cost_reserved_micro_usd", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("failed_turns", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("kind IN ('window', 'week')", name="ck_usage_windows_kind"),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("account_id", "kind", "window_start", name="uq_usage_windows_account_kind_start"),
    )
    op.create_index(op.f("ix_usage_windows_account_id"), "usage_windows", ["account_id"], unique=False)
    op.create_index(
        "ix_usage_windows_account_kind_end", "usage_windows", ["account_id", "kind", "window_end"], unique=False
    )

    op.create_table(
        "ai_balances",
        sa.Column("account_id", sa.UUID(), nullable=False),
        sa.Column("taste_used", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("taste_reserved", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("topup_remaining", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("topup_reserved", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("account_id"),
    )

    op.create_table(
        "topup_purchases",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("account_id", sa.UUID(), nullable=False),
        sa.Column("provider_checkout_session_id", sa.String(length=128), nullable=False),
        sa.Column("provider_payment_intent_id", sa.String(length=128), nullable=True),
        sa.Column("requests", sa.Integer(), nullable=False),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("credited_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('pending', 'credited', 'expired', 'failed')", name="ck_topup_purchases_status"),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider_checkout_session_id"),
    )
    op.create_index(op.f("ix_topup_purchases_account_id"), "topup_purchases", ["account_id"], unique=False)

    op.add_column("assistant_turns", sa.Column("source", sa.String(length=8), nullable=True))
    op.add_column("assistant_turns", sa.Column("window_start", sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint(
        "ck_assistant_turns_source", "assistant_turns", "source IS NULL OR source IN ('taste', 'window', 'topup')"
    )


def downgrade() -> None:
    op.drop_constraint("ck_assistant_turns_source", "assistant_turns", type_="check")
    op.drop_column("assistant_turns", "window_start")
    op.drop_column("assistant_turns", "source")
    op.drop_index(op.f("ix_topup_purchases_account_id"), table_name="topup_purchases")
    op.drop_table("topup_purchases")
    op.drop_table("ai_balances")
    op.drop_index("ix_usage_windows_account_kind_end", table_name="usage_windows")
    op.drop_index(op.f("ix_usage_windows_account_id"), table_name="usage_windows")
    op.drop_table("usage_windows")
    op.drop_index("ix_billing_events_status_received", table_name="billing_events")
    op.drop_constraint("ck_billing_events_status", "billing_events", type_="check")
    op.drop_column("billing_events", "attempts")
    op.drop_column("billing_events", "provider_object_id")
    op.drop_column("billing_events", "kind")
    op.drop_index("uq_subscriptions_live_account", table_name="subscriptions")
    op.drop_constraint("ck_subscriptions_state", "subscriptions", type_="check")
    op.drop_constraint("uq_subscriptions_provider_checkout_session_id", "subscriptions", type_="unique")
    for name in (
        "provider_synced_at",
        "provider_status",
        "checkout_expires_at",
        "checkout_idempotency_key",
        "provider_checkout_session_id",
        "interval",
    ):
        op.drop_column("subscriptions", name)
    op.drop_column("controllers", "plan_selected_at")
    op.drop_column("pcs", "plan_disabled_at")
    op.drop_column("accounts", "plan_changed_at")
