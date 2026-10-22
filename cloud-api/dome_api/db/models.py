"""SQLAlchemy 2.0 typed models. Every account-owned table carries ``account_id``; queries are
always scoped by it (there is deliberately no "fetch by id" helper without an account filter).

No table stores command parameters, results, media titles, pairing codes, plaintext tokens or
credentials: only digests and lifecycle metadata.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

LIFECYCLE_STATES = (
    "created",
    "awaiting_confirmation",
    "accepted",
    "executing",
    "succeeded",
    "failed",
    "expired",
    "canceled",
    "outcome_unknown",
)
IN_FLIGHT_STATES = ("created", "accepted", "executing", "awaiting_confirmation")
TERMINAL_STATES = ("succeeded", "failed", "expired", "canceled", "outcome_unknown")
LINK_STATES = ("pending", "approved", "denied", "consumed", "expired")
PAIRING_STATES = ("open", "claimed", "approved", "declined", "expired")
SEVERITIES = ("info", "notice", "warning", "critical")
ACTORS = ("account", "pc", "controller", "system")
PLATFORMS = ("windows", "development")
SUPPORT_TICKET_STATUSES = ("received", "in_review", "answered", "closed")
# assistant ledger outcomes (docs/design/assistant.md §3.12); only `answered` is ever charged
ASSISTANT_TURN_STATUSES = (
    "pending",
    "answered",
    "invalid_output",
    "refused",
    "provider_error",
    "timeout",
    "cancelled",
    "abandoned",
)
# billing (docs/BILLING.md §4): the subscription row's state, webhook receipt statuses, AI accounting
SUBSCRIPTION_STATES = (
    "checkout_pending",
    "incomplete",
    "trialing",
    "active",
    "cancel_scheduled",
    "past_due",
    "unpaid",
    "canceled",
)
# states in which the account holds a paid plan (past_due only until grace_until, decided in billing/state.py)
SUBSCRIPTION_PAID_STATES = ("trialing", "active", "cancel_scheduled", "past_due")
BILLING_EVENT_STATUSES = ("received", "processed", "ignored", "failed")
USAGE_WINDOW_KINDS = ("window", "week")
TOPUP_STATUSES = ("pending", "credited", "expired", "failed")
AI_SOURCES = ("taste", "window", "topup")
# Screen view (docs/design/screen-view.md §10, §13): a reservation's life and a phone TURN credential's life
SCREEN_RESERVATION_STATES = ("reserved", "live", "settled", "swept")
# revoke_pending: the provider refused or timed out; the username waits in turn_pending_revocations for a retry
TURN_CREDENTIAL_STATES = ("outstanding", "bound", "revoked", "revoke_pending", "expired")
SUPPORT_CATEGORIES = (
    "connection",
    "pairing",
    "media",
    "input",
    "apps",
    "power",
    "install",
    "billing",
    "account",
    "other",
)


def _enum_check(column: str, values: tuple[str, ...], name: str) -> CheckConstraint:
    quoted = ", ".join(f"'{v}'" for v in values)
    return CheckConstraint(f"{column} IN ({quoted})", name=name)


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSONB, datetime: DateTime(timezone=True)}


def uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def created_at_col() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class Account(Base):
    __tablename__ = "accounts"
    __table_args__ = (UniqueConstraint("issuer", "subject", name="uq_accounts_issuer_subject"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    issuer: Mapped[str] = mapped_column(String(512), nullable=False)
    subject: Mapped[str] = mapped_column(String(256), nullable=False)
    email: Mapped[str] = mapped_column(String(254), nullable=False, default="")
    display_name: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    plan: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'free'"))
    # set when a plan change lowered a device limit (the selection window runs from it); cleared on re-upgrade
    plan_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at_col()
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # AI assistant consent (account-level, versioned; checked on every turn; the history is in security events)
    assistant_consent_version: Mapped[str | None] = mapped_column(String(16))
    assistant_consented_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = uuid_pk()
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False, unique=True)
    csrf_token: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = created_at_col()
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    absolute_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    user_agent_hash: Mapped[bytes | None] = mapped_column(LargeBinary(32))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuthFlow(Base):
    __tablename__ = "auth_flows"

    id: Mapped[uuid.UUID] = uuid_pk()
    state: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    nonce: Mapped[str] = mapped_column(String(128), nullable=False)
    code_verifier: Mapped[str] = mapped_column(String(128), nullable=False)
    return_to: Mapped[str] = mapped_column(String(1024), nullable=False, default="/")
    created_at: Mapped[datetime] = created_at_col()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PC(Base):
    __tablename__ = "pcs"
    __table_args__ = (_enum_check("platform", PLATFORMS, "ck_pcs_platform"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    public_jwk: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    kid: Mapped[str] = mapped_column(String(43), nullable=False, unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # disabled by a plan change (not by the owner): re-enabled first when the limits grow again
    plan_disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    remote_enabled_reported: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    platform: Mapped[str] = mapped_column(String(16), nullable=False)
    agent_version: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    created_at: Mapped[datetime] = created_at_col()
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_power_request: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PCCredential(Base):
    __tablename__ = "pc_credentials"

    id: Mapped[uuid.UUID] = uuid_pk()
    pc_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("pcs.id", ondelete="CASCADE"), nullable=False, index=True)
    credential_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False, unique=True)
    created_at: Mapped[datetime] = created_at_col()
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PCAccessToken(Base):
    __tablename__ = "pc_access_tokens"

    id: Mapped[uuid.UUID] = uuid_pk()
    pc_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("pcs.id", ondelete="CASCADE"), nullable=False, index=True)
    token_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False, unique=True)
    created_at: Mapped[datetime] = created_at_col()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DeviceLinkCode(Base):
    __tablename__ = "device_link_codes"
    __table_args__ = (
        _enum_check("state", LINK_STATES, "ck_device_link_codes_state"),
        _enum_check("platform", PLATFORMS, "ck_device_link_codes_platform"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    device_code_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False, unique=True)
    user_code: Mapped[str] = mapped_column(String(9), nullable=False, unique=True)
    pc_public_jwk: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    kid: Mapped[str] = mapped_column(String(43), nullable=False)
    agent_version: Mapped[str] = mapped_column(String(64), nullable=False)
    platform: Mapped[str] = mapped_column(String(16), nullable=False)
    pc_name_hint: Mapped[str | None] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    account_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("accounts.id", ondelete="SET NULL"))
    pc_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("pcs.id", ondelete="SET NULL"))
    pc_name: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = created_at_col()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_polled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    requester_ip_hash: Mapped[bytes | None] = mapped_column(LargeBinary(32))


class Controller(Base):
    __tablename__ = "controllers"
    __table_args__ = (UniqueConstraint("account_id", "kid", name="uq_controllers_account_kid"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kid: Mapped[str] = mapped_column(String(43), nullable=False)
    public_jwk: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    display_name: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = created_at_col()
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # chosen through PUT /v1/account/plan-selection: ordered before last_seen_at when the plan limits controllers
    plan_selected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Grant(Base):
    __tablename__ = "grants"
    __table_args__ = (
        Index(
            "uq_grants_live_controller_pc",
            "controller_id",
            "pc_id",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    controller_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("controllers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    pc_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("pcs.id", ondelete="CASCADE"), nullable=False, index=True)
    capabilities: Mapped[list[str]] = mapped_column(ARRAY(String(32)), nullable=False)
    created_at: Mapped[datetime] = created_at_col()
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PairingSession(Base):
    __tablename__ = "pairing_sessions"
    __table_args__ = (
        _enum_check("state", PAIRING_STATES, "ck_pairing_sessions_state"),
        Index(
            "uq_pairing_sessions_open_code_hash",
            "code_hash",
            unique=True,
            postgresql_where=text("state IN ('open', 'claimed')"),
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    pc_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("pcs.id", ondelete="CASCADE"), nullable=False, index=True)
    code_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    controller_kid: Mapped[str | None] = mapped_column(String(43))
    controller_public_jwk: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    controller_display_name: Mapped[str | None] = mapped_column(String(64))
    requested_capabilities: Mapped[list[str] | None] = mapped_column(ARRAY(String(32)))
    controller_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("controllers.id", ondelete="SET NULL"))
    grant_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("grants.id", ondelete="SET NULL"))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = created_at_col()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # rules.pc_key_pinning (1.4): the MAC the agent computed with the pairing code over its identity key; stored
    # verbatim on approve, returned with the PC's public key once approved. Not a secret (only the code verifies it).
    pc_key_mac: Mapped[str | None] = mapped_column(String(43))


class Command(Base):
    """Lifecycle only. ``digest`` is SHA-256 of the signed payload bytes (duplicate detection)."""

    __tablename__ = "commands"
    __table_args__ = (
        _enum_check("state", LIFECYCLE_STATES, "ck_commands_state"),
        Index("ix_commands_pc_created", "pc_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    controller_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("controllers.id", ondelete="CASCADE"), nullable=False)
    pc_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("pcs.id", ondelete="CASCADE"), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    digest: Mapped[str] = mapped_column(String(43), nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False, default="created")
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = created_at_col()
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    corrected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SecurityEvent(Base):
    __tablename__ = "security_events"
    __table_args__ = (
        _enum_check("severity", SEVERITIES, "ck_security_events_severity"),
        _enum_check("actor", ACTORS, "ck_security_events_actor"),
        Index("ix_security_events_account_id_id", "account_id", "id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    account_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    actor: Mapped[str] = mapped_column(String(16), nullable=False)
    subject_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    ip_hash: Mapped[bytes | None] = mapped_column(LargeBinary(32))
    created_at: Mapped[datetime] = created_at_col()


class ActivationEvent(Base):
    """Product measurement (spec §16): activation funnel only, never command content."""

    __tablename__ = "activation_events"
    __table_args__ = (Index("ix_activation_events_account_kind", "account_id", "kind"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    account_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("accounts.id", ondelete="SET NULL"))
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    subject_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = created_at_col()


# ----- billing tables (docs/BILLING.md §3): written only by dome_api.billing -----------------------


class Subscription(Base):
    """The backend's cache of the provider's subscription plus DoMe's own grace decision. At most one row per
    account is live (``state <> 'canceled'``); ``accounts.plan`` is derived from it by ``billing/state.py`` and
    written nowhere else. Provider ids only: no email, no card data, no amounts."""

    __tablename__ = "subscriptions"
    __table_args__ = (
        _enum_check("state", SUBSCRIPTION_STATES, "ck_subscriptions_state"),
        Index(
            "uq_subscriptions_live_account",
            "account_id",
            unique=True,
            postgresql_where=text("state <> 'canceled'"),
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="stripe")
    provider_customer_id: Mapped[str | None] = mapped_column(String(128), index=True)
    provider_subscription_id: Mapped[str | None] = mapped_column(String(128), unique=True)
    price_id: Mapped[str | None] = mapped_column(String(128))
    plan: Mapped[str] = mapped_column(String(32), nullable=False)
    interval: Mapped[str | None] = mapped_column(String(16))
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    # the open Checkout Session while state is checkout_pending; the idempotency key is stored before the call
    provider_checkout_session_id: Mapped[str | None] = mapped_column(String(128), unique=True)
    checkout_idempotency_key: Mapped[str | None] = mapped_column(String(128))
    checkout_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    provider_status: Mapped[str | None] = mapped_column(String(32))
    provider_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    current_period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    grace_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class BillingEvent(Base):
    """Durable webhook receipt (deduplication by ``provider_event_id``). The raw payload is never stored; the handler
    re-fetches the object from the provider. ``kind`` groups events for operators (subscription, topup, other)."""

    __tablename__ = "billing_events"
    __table_args__ = (
        _enum_check("status", BILLING_EVENT_STATUSES, "ck_billing_events_status"),
        Index("ix_billing_events_status_received", "status", "received_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="stripe")
    provider_event_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    kind: Mapped[str | None] = mapped_column(String(16))
    # the subscription or Checkout Session id the event is about, so a failed event can be re-fetched and retried
    provider_object_id: Mapped[str | None] = mapped_column(String(128))
    account_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("accounts.id", ondelete="SET NULL"))
    received_at: Mapped[datetime] = created_at_col()
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="received")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    error: Mapped[str | None] = mapped_column(Text)
    payload_digest: Mapped[str | None] = mapped_column(String(43))


class UsageWindow(Base):
    """One rolling AI allowance bucket (rules.ai_allowance): the 5-hour ``window`` or the weekly ``week`` of one
    account, opened at the first reservation while none is open. Counts and micro-dollars only; the cost cap and
    the failed-turn limit are checked on the ``week`` row."""

    __tablename__ = "usage_windows"
    __table_args__ = (
        UniqueConstraint("account_id", "kind", "window_start", name="uq_usage_windows_account_kind_start"),
        _enum_check("kind", USAGE_WINDOW_KINDS, "ck_usage_windows_kind"),
        Index("ix_usage_windows_account_kind_end", "account_id", "kind", "window_end"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(8), nullable=False)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    requests_used: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    requests_reserved: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    cost_used_micro_usd: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    cost_reserved_micro_usd: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    failed_turns: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    created_at: Mapped[datetime] = created_at_col()


class AiBalance(Base):
    """Per-account AI balances that never roll: Free's one-time taste and the purchased top-up requests. One row
    per account, locked FOR UPDATE by every reservation, settlement and credit of that account."""

    __tablename__ = "ai_balances"

    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), primary_key=True)
    taste_used: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    taste_reserved: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    topup_remaining: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    topup_reserved: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class TopupPurchase(Base):
    """A top-up Checkout Session: pending until the provider reports it paid, credited exactly once (the session id
    is unique), expired or failed. Amounts in cents for the receipt reference only; no card data."""

    __tablename__ = "topup_purchases"
    __table_args__ = (_enum_check("status", TOPUP_STATUSES, "ck_topup_purchases_status"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider_checkout_session_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    provider_payment_intent_id: Mapped[str | None] = mapped_column(String(128))
    requests: Mapped[int] = mapped_column(Integer, nullable=False)
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    created_at: Mapped[datetime] = created_at_col()
    credited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UsagePeriod(Base):
    """The calendar-month AI accounting of protocol 1.2. No longer written since the rolling windows of 1.3
    (``usage_windows``); kept for the history it holds (docs/DATA_RETENTION.md)."""

    __tablename__ = "usage_periods"
    __table_args__ = (UniqueConstraint("account_id", "period_start", name="uq_usage_periods_account_start"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ai_interpretations_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ai_transcription_seconds_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ai_interpretations_reserved: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ai_transcription_seconds_reserved: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # AI assistant metering (docs/design/assistant.md §3.7): counts and micro-dollars only, never text
    ai_cost_used_micro_usd: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    ai_cost_reserved_micro_usd: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    ai_input_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    ai_output_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    ai_cache_read_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    ai_cache_write_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    ai_failed_turns: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))


class AiSpendMonth(Base):
    """The operator's AI spend per calendar month (UTC) across all accounts, against DOME_AI_MONTHLY_CEILING_CENTS.
    No account data."""

    __tablename__ = "ai_spend_months"

    month_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    spent_micro_usd: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reserved_micro_usd: Mapped[int] = mapped_column(BigInteger, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class AssistantTurn(Base):
    """The assistant ledger: one row per AI turn, reserved before the provider call and settled after it.
    Deliberately no text column of any kind: never the words, the recent lines, the context, the reply or the
    steps (docs/design/assistant.md §3.10, §3.12). ``model_served`` and ``provider_request_id`` come from the
    provider's response metadata."""

    __tablename__ = "assistant_turns"
    __table_args__ = (
        UniqueConstraint("account_id", "client_turn_id", name="uq_assistant_turns_account_turn"),
        _enum_check("status", ASSISTANT_TURN_STATUSES, "ck_assistant_turns_status"),
        CheckConstraint("source IS NULL OR source IN ('taste', 'window', 'topup')", name="ck_assistant_turns_source"),
        Index("ix_assistant_turns_status_created", "status", "created_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    client_turn_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    charged: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    # the bucket the turn was taken from (rules.ai_allowance) and the 5-hour window it belongs to; period_start is
    # the weekly window's start (it was the calendar month before protocol 1.3), month_start the operator's month
    source: Mapped[str | None] = mapped_column(String(8))
    window_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    month_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reserved_micro_usd: Mapped[int] = mapped_column(BigInteger, nullable=False)
    cost_micro_usd: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    input_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    output_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    cache_read_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    cache_write_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    model_served: Mapped[str | None] = mapped_column(String(64))
    fallback_used: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    provider_request_id: Mapped[str | None] = mapped_column(String(128))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = created_at_col()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# ----- Screen view (docs/design/screen-view.md §10, §13; rules.screen_metering): written only by dome_api.screen ------


class ScreenAccount(Base):
    """One row per account that ever used Screen view: the lock every reservation and every TURN mint of that account
    takes FOR UPDATE (so two reservations of one account cannot open two windows), and the per-account monthly mint
    counter. Created on first use."""

    __tablename__ = "screen_accounts"

    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), primary_key=True)
    turn_mints_month_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    turn_mints_this_month: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class ScreenViewWindow(Base):
    """One rolling Free allowance window of one account (plans.json screen_view_window_hours), opened at the first
    reservation while none is open. Seconds only, settled on the relay's clock; ``zero_charged_attempts`` counts the
    failed starts this window forgave (at most three)."""

    __tablename__ = "screen_view_windows"
    __table_args__ = (
        UniqueConstraint("account_id", "window_start", name="uq_screen_view_windows_account_start"),
        Index("ix_screen_view_windows_account_end", "account_id", "window_end"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_seconds: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    reserved_seconds: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    zero_charged_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    # starts refused at the PC (Deny, no answer) that this window forgave, counted apart from the connection failures
    consent_forgiven: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    # secure-desktop pause seconds discounted in this window so far (capped per window, not only per session)
    pause_discount_seconds: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    created_at: Mapped[datetime] = created_at_col()


class ScreenReservation(Base):
    """One Screen view start as the relay saw it: the allowance reserved for it in 60 s chunks and the wall time the
    relay measured. ``outstanding_seconds`` is what is reserved and not yet settled in the current window;
    ``segment_started_at`` is where the current charging segment began (the forward, the first `started` frame, or
    the previous window's end). Ids and seconds only: no SDP, no address, no name."""

    __tablename__ = "screen_reservations"
    __table_args__ = (
        _enum_check("state", SCREEN_RESERVATION_STATES, "ck_screen_reservations_state"),
        Index("ix_screen_reservations_state_until", "state", "reserved_until"),
        Index("ix_screen_reservations_account_created", "account_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    pc_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("pcs.id", ondelete="CASCADE"), nullable=False)
    controller_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("controllers.id", ondelete="CASCADE"), nullable=False)
    command_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, unique=True)
    screen_session_id: Mapped[str | None] = mapped_column(String(22), index=True)
    # the window the outstanding chunks belong to; NULL on plans with an unlimited allowance
    window_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    unlimited: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    forwarded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    segment_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reserved_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    outstanding_seconds: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    charged_seconds: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    pause_discount_seconds: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # set once the relay told the PC no further ticket will come (controller_offline, plan_disabled)
    final_reason: Mapped[str | None] = mapped_column(String(32))
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="reserved")
    end_reason: Mapped[str | None] = mapped_column(String(32))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at_col()


class TurnCredential(Base):
    """The one TURN credential a controller may hold (rules.screen_signaling (1)): the primary key is the controller,
    so minting replaces the previous row after revoking it at the provider. Only the provider's username is kept
    (for revocation and reconciliation); the credential itself is handed to the phone once and never stored."""

    __tablename__ = "turn_credentials"
    __table_args__ = (
        _enum_check("state", TURN_CREDENTIAL_STATES, "ck_turn_credentials_state"),
        Index("ix_turn_credentials_account_minted", "account_id", "minted_at"),
        Index("ix_turn_credentials_state_minted", "state", "minted_at"),
    )

    controller_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("controllers.id", ondelete="CASCADE"), primary_key=True)
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    username: Mapped[str] = mapped_column(String(256), nullable=False)
    minted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ttl_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    command_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    screen_session_id: Mapped[str | None] = mapped_column(String(22))
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="outstanding")
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoke_reason: Mapped[str | None] = mapped_column(String(32))


class TurnPendingRevocation(Base):
    """A phone TURN username whose revocation the provider refused or timed out (design §13 "revoked on every path"):
    kept apart from ``turn_credentials`` (whose row the controller's next mint replaces) and retried by the sweeper
    until the provider confirms or the credential expires. No foreign key: a deleted account's or phone's credential
    must still be revoked. Ids, a username, a reason and timestamps only."""

    __tablename__ = "turn_pending_revocations"

    username: Mapped[str] = mapped_column(String(256), primary_key=True)
    account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    controller_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    reason: Mapped[str] = mapped_column(String(32), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    last_error: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TurnMintDay(Base):
    """Counters per account and UTC day of mint, only ever added to: the TTL seconds of every credential minted, and
    the seconds that can no longer relay outside a session (the whole TTL once the credential is bound to a session,
    whose ticketed seconds count instead; the unused rest once a revocation is confirmed). The provisional guard counts
    ``minted - released`` at the relayed cap; unlike the per-controller ``turn_credentials`` row, nothing here is
    replaced by the next mint."""

    __tablename__ = "turn_mint_days"

    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    minted_ttl_seconds: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    released_ttl_seconds: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))


class TurnReconcilePass(Base):
    """One successful reconciliation pass (the provider's figures cover activity up to about here). The guard trusts
    the reconciled figures only while the latest pass is recent, and persists that across restarts."""

    __tablename__ = "turn_reconcile_passes"

    reconciled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    rows: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))


class TurnUsageDay(Base):
    """Relayed bytes per account per UTC day as the TURN provider reports them (the hourly reconciliation,
    docs/design/screen-view.md §13). Never from agent-reported figures."""

    __tablename__ = "turn_usage_days"

    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    egress_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    reconciled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Layout(Base):
    __tablename__ = "layouts"

    id: Mapped[uuid.UUID] = uuid_pk()
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    definition: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Routine(Base):
    __tablename__ = "routines"

    id: Mapped[uuid.UUID] = uuid_pk()
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    pc_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("pcs.id", ondelete="SET NULL"))
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    steps: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SupportDiagnostic(Base):
    __tablename__ = "support_diagnostics"

    id: Mapped[uuid.UUID] = uuid_pk()
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    pc_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("pcs.id", ondelete="SET NULL"))
    redacted_bundle: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = created_at_col()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SupportTicket(Base):
    """Customer-initiated support request (spec section 11A). ``diagnostics_redacted`` is the customer-reviewed
    bundle after the server's own redaction pass; the table never carries pairing material, credentials,
    typed text or media titles, and no row can make anything execute on a PC."""

    __tablename__ = "support_tickets"
    __table_args__ = (
        _enum_check("status", SUPPORT_TICKET_STATUSES, "ck_support_tickets_status"),
        _enum_check("category", SUPPORT_CATEGORIES, "ck_support_tickets_category"),
        Index("ix_support_tickets_account_created", "account_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    reference: Mapped[str] = mapped_column(String(11), nullable=False, unique=True)
    category: Mapped[str] = mapped_column(String(16), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64))
    message: Mapped[str] = mapped_column(Text, nullable=False)
    diagnostics_redacted: Mapped[str | None] = mapped_column(Text)
    app_version: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="received")
    answer: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class PendingDeletion(Base):
    __tablename__ = "pending_deletions"

    id: Mapped[uuid.UUID] = uuid_pk()
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    requested_at: Mapped[datetime] = created_at_col()
    provider_cancel_state: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OperatorUser(Base):
    __tablename__ = "operator_users"
    __table_args__ = (UniqueConstraint("issuer", "subject", name="uq_operator_users_issuer_subject"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    issuer: Mapped[str] = mapped_column(String(512), nullable=False)
    subject: Mapped[str] = mapped_column(String(256), nullable=False)
    email: Mapped[str] = mapped_column(String(254), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = created_at_col()
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OperatorAudit(Base):
    __tablename__ = "operator_audit"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    operator_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("operator_users.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    target_account_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = created_at_col()
