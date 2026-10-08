"""Initial schema: accounts, sessions, auth flows, PCs, credentials/tokens, device link codes,
controllers, grants, pairing sessions, command lifecycle rows, security/activation events, and the
Phase C tables (subscriptions, billing events, usage periods, layouts, routines, support diagnostics,
pending deletions, operator users/audit) created empty so the schema is complete.

Revision ID: 0001
Revises:
Create Date: 2026-10-08 09:27:45.369339
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "accounts",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("issuer", sa.String(length=512), nullable=False),
        sa.Column("subject", sa.String(length=256), nullable=False),
        sa.Column("email", sa.String(length=254), nullable=False),
        sa.Column("display_name", sa.String(length=128), nullable=False),
        sa.Column("plan", sa.String(length=32), server_default=sa.text("'free'"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("issuer", "subject", name="uq_accounts_issuer_subject"),
    )
    op.create_table(
        "auth_flows",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("state", sa.String(length=128), nullable=False),
        sa.Column("nonce", sa.String(length=128), nullable=False),
        sa.Column("code_verifier", sa.String(length=128), nullable=False),
        sa.Column("return_to", sa.String(length=1024), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("state"),
    )
    op.create_table(
        "operator_users",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("issuer", sa.String(length=512), nullable=False),
        sa.Column("subject", sa.String(length=256), nullable=False),
        sa.Column("email", sa.String(length=254), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("issuer", "subject", name="uq_operator_users_issuer_subject"),
    )
    op.create_table(
        "activation_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("account_id", sa.UUID(), nullable=True),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("subject_id", sa.UUID(), nullable=True),
        sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_activation_events_account_kind", "activation_events", ["account_id", "kind"], unique=False)
    op.create_table(
        "billing_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("provider_event_id", sa.String(length=128), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("account_id", sa.UUID(), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("payload_digest", sa.String(length=43), nullable=True),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider_event_id"),
    )
    op.create_table(
        "controllers",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("account_id", sa.UUID(), nullable=False),
        sa.Column("kid", sa.String(length=43), nullable=False),
        sa.Column("public_jwk", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("display_name", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("account_id", "kid", name="uq_controllers_account_kid"),
    )
    op.create_index(op.f("ix_controllers_account_id"), "controllers", ["account_id"], unique=False)
    op.create_table(
        "layouts",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("account_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("definition", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_layouts_account_id"), "layouts", ["account_id"], unique=False)
    op.create_table(
        "operator_audit",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("operator_id", sa.UUID(), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("target_account_id", sa.UUID(), nullable=True),
        sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["operator_id"], ["operator_users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "pcs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("account_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("public_jwk", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("kid", sa.String(length=43), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("remote_enabled_reported", sa.Boolean(), nullable=False),
        sa.Column("platform", sa.String(length=16), nullable=False),
        sa.Column("agent_version", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_power_request", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("platform IN ('windows', 'development')", name="ck_pcs_platform"),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("kid"),
    )
    op.create_index(op.f("ix_pcs_account_id"), "pcs", ["account_id"], unique=False)
    op.create_table(
        "pending_deletions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("account_id", sa.UUID(), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("provider_cancel_state", sa.String(length=32), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("account_id"),
    )
    op.create_table(
        "security_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("account_id", sa.UUID(), nullable=True),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("actor", sa.String(length=16), nullable=False),
        sa.Column("subject_id", sa.UUID(), nullable=True),
        sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("ip_hash", sa.LargeBinary(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("actor IN ('account', 'pc', 'controller', 'system')", name="ck_security_events_actor"),
        sa.CheckConstraint("severity IN ('info', 'notice', 'warning', 'critical')", name="ck_security_events_severity"),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_security_events_account_id_id", "security_events", ["account_id", "id"], unique=False)
    op.create_table(
        "sessions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("account_id", sa.UUID(), nullable=False),
        sa.Column("token_hash", sa.LargeBinary(length=32), nullable=False),
        sa.Column("csrf_token", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("absolute_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("user_agent_hash", sa.LargeBinary(length=32), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index(op.f("ix_sessions_account_id"), "sessions", ["account_id"], unique=False)
    op.create_table(
        "subscriptions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("account_id", sa.UUID(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("provider_customer_id", sa.String(length=128), nullable=True),
        sa.Column("provider_subscription_id", sa.String(length=128), nullable=True),
        sa.Column("price_id", sa.String(length=128), nullable=True),
        sa.Column("plan", sa.String(length=32), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("current_period_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_at_period_end", sa.Boolean(), nullable=False),
        sa.Column("grace_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider_subscription_id"),
    )
    op.create_index(op.f("ix_subscriptions_account_id"), "subscriptions", ["account_id"], unique=False)
    op.create_index(
        op.f("ix_subscriptions_provider_customer_id"), "subscriptions", ["provider_customer_id"], unique=False
    )
    op.create_table(
        "usage_periods",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("account_id", sa.UUID(), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ai_interpretations_used", sa.Integer(), nullable=False),
        sa.Column("ai_transcription_seconds_used", sa.Integer(), nullable=False),
        sa.Column("ai_interpretations_reserved", sa.Integer(), nullable=False),
        sa.Column("ai_transcription_seconds_reserved", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("account_id", "period_start", name="uq_usage_periods_account_start"),
    )
    op.create_index(op.f("ix_usage_periods_account_id"), "usage_periods", ["account_id"], unique=False)
    op.create_table(
        "commands",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("account_id", sa.UUID(), nullable=False),
        sa.Column("controller_id", sa.UUID(), nullable=False),
        sa.Column("pc_id", sa.UUID(), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("digest", sa.String(length=43), nullable=False),
        sa.Column("state", sa.String(length=24), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("corrected_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "state IN ('created', 'awaiting_confirmation', 'accepted', 'executing', 'succeeded', 'failed', 'expired', 'canceled', 'outcome_unknown')",
            name="ck_commands_state",
        ),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["controller_id"], ["controllers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["pc_id"], ["pcs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_commands_account_id"), "commands", ["account_id"], unique=False)
    op.create_index("ix_commands_pc_created", "commands", ["pc_id", "created_at"], unique=False)
    op.create_table(
        "device_link_codes",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("device_code_hash", sa.LargeBinary(length=32), nullable=False),
        sa.Column("user_code", sa.String(length=9), nullable=False),
        sa.Column("pc_public_jwk", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("kid", sa.String(length=43), nullable=False),
        sa.Column("agent_version", sa.String(length=64), nullable=False),
        sa.Column("platform", sa.String(length=16), nullable=False),
        sa.Column("pc_name_hint", sa.String(length=64), nullable=True),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("account_id", sa.UUID(), nullable=True),
        sa.Column("pc_id", sa.UUID(), nullable=True),
        sa.Column("pc_name", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_polled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("requester_ip_hash", sa.LargeBinary(length=32), nullable=True),
        sa.CheckConstraint("platform IN ('windows', 'development')", name="ck_device_link_codes_platform"),
        sa.CheckConstraint(
            "state IN ('pending', 'approved', 'denied', 'consumed', 'expired')", name="ck_device_link_codes_state"
        ),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["pc_id"], ["pcs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("device_code_hash"),
        sa.UniqueConstraint("user_code"),
    )
    op.create_table(
        "grants",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("account_id", sa.UUID(), nullable=False),
        sa.Column("controller_id", sa.UUID(), nullable=False),
        sa.Column("pc_id", sa.UUID(), nullable=False),
        sa.Column("capabilities", postgresql.ARRAY(sa.String(length=32)), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["controller_id"], ["controllers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["pc_id"], ["pcs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_grants_account_id"), "grants", ["account_id"], unique=False)
    op.create_index(op.f("ix_grants_controller_id"), "grants", ["controller_id"], unique=False)
    op.create_index(op.f("ix_grants_pc_id"), "grants", ["pc_id"], unique=False)
    op.create_index(
        "uq_grants_live_controller_pc",
        "grants",
        ["controller_id", "pc_id"],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.create_table(
        "pc_access_tokens",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("pc_id", sa.UUID(), nullable=False),
        sa.Column("token_hash", sa.LargeBinary(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["pc_id"], ["pcs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index(op.f("ix_pc_access_tokens_pc_id"), "pc_access_tokens", ["pc_id"], unique=False)
    op.create_table(
        "pc_credentials",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("pc_id", sa.UUID(), nullable=False),
        sa.Column("credential_hash", sa.LargeBinary(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["pc_id"], ["pcs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("credential_hash"),
    )
    op.create_index(op.f("ix_pc_credentials_pc_id"), "pc_credentials", ["pc_id"], unique=False)
    op.create_table(
        "routines",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("account_id", sa.UUID(), nullable=False),
        sa.Column("pc_id", sa.UUID(), nullable=True),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("steps", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["pc_id"], ["pcs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_routines_account_id"), "routines", ["account_id"], unique=False)
    op.create_table(
        "support_diagnostics",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("account_id", sa.UUID(), nullable=False),
        sa.Column("pc_id", sa.UUID(), nullable=True),
        sa.Column("redacted_bundle", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["pc_id"], ["pcs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_support_diagnostics_account_id"), "support_diagnostics", ["account_id"], unique=False)
    op.create_table(
        "pairing_sessions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("account_id", sa.UUID(), nullable=False),
        sa.Column("pc_id", sa.UUID(), nullable=False),
        sa.Column("code_hash", sa.LargeBinary(length=32), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("controller_kid", sa.String(length=43), nullable=True),
        sa.Column("controller_public_jwk", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("controller_display_name", sa.String(length=64), nullable=True),
        sa.Column("requested_capabilities", postgresql.ARRAY(sa.String(length=32)), nullable=True),
        sa.Column("controller_id", sa.UUID(), nullable=True),
        sa.Column("grant_id", sa.UUID(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "state IN ('open', 'claimed', 'approved', 'declined', 'expired')", name="ck_pairing_sessions_state"
        ),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["controller_id"], ["controllers.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["grant_id"], ["grants.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["pc_id"], ["pcs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_pairing_sessions_account_id"), "pairing_sessions", ["account_id"], unique=False)
    op.create_index(op.f("ix_pairing_sessions_pc_id"), "pairing_sessions", ["pc_id"], unique=False)
    op.create_index(
        "uq_pairing_sessions_open_code_hash",
        "pairing_sessions",
        ["code_hash"],
        unique=True,
        postgresql_where=sa.text("state IN ('open', 'claimed')"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_pairing_sessions_open_code_hash",
        table_name="pairing_sessions",
        postgresql_where=sa.text("state IN ('open', 'claimed')"),
    )
    op.drop_index(op.f("ix_pairing_sessions_pc_id"), table_name="pairing_sessions")
    op.drop_index(op.f("ix_pairing_sessions_account_id"), table_name="pairing_sessions")
    op.drop_table("pairing_sessions")
    op.drop_index(op.f("ix_support_diagnostics_account_id"), table_name="support_diagnostics")
    op.drop_table("support_diagnostics")
    op.drop_index(op.f("ix_routines_account_id"), table_name="routines")
    op.drop_table("routines")
    op.drop_index(op.f("ix_pc_credentials_pc_id"), table_name="pc_credentials")
    op.drop_table("pc_credentials")
    op.drop_index(op.f("ix_pc_access_tokens_pc_id"), table_name="pc_access_tokens")
    op.drop_table("pc_access_tokens")
    op.drop_index("uq_grants_live_controller_pc", table_name="grants", postgresql_where=sa.text("revoked_at IS NULL"))
    op.drop_index(op.f("ix_grants_pc_id"), table_name="grants")
    op.drop_index(op.f("ix_grants_controller_id"), table_name="grants")
    op.drop_index(op.f("ix_grants_account_id"), table_name="grants")
    op.drop_table("grants")
    op.drop_table("device_link_codes")
    op.drop_index("ix_commands_pc_created", table_name="commands")
    op.drop_index(op.f("ix_commands_account_id"), table_name="commands")
    op.drop_table("commands")
    op.drop_index(op.f("ix_usage_periods_account_id"), table_name="usage_periods")
    op.drop_table("usage_periods")
    op.drop_index(op.f("ix_subscriptions_provider_customer_id"), table_name="subscriptions")
    op.drop_index(op.f("ix_subscriptions_account_id"), table_name="subscriptions")
    op.drop_table("subscriptions")
    op.drop_index(op.f("ix_sessions_account_id"), table_name="sessions")
    op.drop_table("sessions")
    op.drop_index("ix_security_events_account_id_id", table_name="security_events")
    op.drop_table("security_events")
    op.drop_table("pending_deletions")
    op.drop_index(op.f("ix_pcs_account_id"), table_name="pcs")
    op.drop_table("pcs")
    op.drop_table("operator_audit")
    op.drop_index(op.f("ix_layouts_account_id"), table_name="layouts")
    op.drop_table("layouts")
    op.drop_index(op.f("ix_controllers_account_id"), table_name="controllers")
    op.drop_table("controllers")
    op.drop_table("billing_events")
    op.drop_index("ix_activation_events_account_kind", table_name="activation_events")
    op.drop_table("activation_events")
    op.drop_table("operator_users")
    op.drop_table("auth_flows")
    op.drop_table("accounts")
