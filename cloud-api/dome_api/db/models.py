"""SQLAlchemy 2.0 typed models. Every account-owned table carries ``account_id``; queries are
always scoped by it (there is deliberately no "fetch by id" helper without an account filter).

No table stores command parameters, results, media titles, pairing codes, plaintext tokens or
credentials: only digests and lifecycle metadata.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
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
    created_at: Mapped[datetime] = created_at_col()
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


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


# ----- Phase C tables: migrated now so the schema is complete; written by Phase C code only ------


class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[uuid.UUID] = uuid_pk()
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="stripe")
    provider_customer_id: Mapped[str | None] = mapped_column(String(128), index=True)
    provider_subscription_id: Mapped[str | None] = mapped_column(String(128), unique=True)
    price_id: Mapped[str | None] = mapped_column(String(128))
    plan: Mapped[str] = mapped_column(String(32), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    current_period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    grace_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class BillingEvent(Base):
    __tablename__ = "billing_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="stripe")
    provider_event_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    account_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("accounts.id", ondelete="SET NULL"))
    received_at: Mapped[datetime] = created_at_col()
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="received")
    error: Mapped[str | None] = mapped_column(Text)
    payload_digest: Mapped[str | None] = mapped_column(String(43))


class UsagePeriod(Base):
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
