"""Self-service account deletion (``DELETE /v1/account``; docs/BILLING.md §11, docs/DATA_RETENTION.md, DECISIONS #121).

Two steps, each committed before the next, so that a crash part-way leaves a record the purge job finishes:

1. :func:`mark_deleted` locks the account row, sets ``deleted_at`` and blanks the email and the display name at once.
   From that commit on the account is gone for every path: its sessions and PC tokens resolve to nothing
   (``resolve_session``, ``resolve_agent_token``), and a sign-in with its identity lands on the "account deleted"
   notice. ``issuer`` and ``subject`` stay until the hard delete 30 days later, so that sign-in is recognised.
2. :func:`finish` cancels the subscription (``BillingService.cancel_for_account_deletion``: a provider failure leaves a
   ``pending_deletions`` row the billing reconciler retries, and the purge job never hard-deletes the account while it
   is open), closes every Checkout page of the account that could still be paid
   (``BillingService.close_checkouts_for_deletion``: a page paid meanwhile is never applied, its subscription is
   cancelled and the payment left to the operator), then in ONE transaction revokes every session, phone, grant, PC
   credential and PC token, soft-deletes every PC, revokes the phones' TURN credentials, withdraws the AI assistant
   consent, deletes the support diagnostics, soft-deletes the layouts and routines, and writes the one
   ``account_deleted`` security event. That event is the record that the step is done: the purge job runs
   :func:`finish` again for a deleted account without it. Only then are the live sockets told
   (``revoked{account_deleted}``) and closed.

``DELETE /v1/account`` closes the sockets already between the two steps (:func:`close_sockets`), so they never
outlive step 1 even when step 2 fails; the close at the end of :func:`finish` is for the purge job's path.

Logs and the event carry ids and counts only: never an email, a name or a provider id.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dome_api.db.engine import transaction
from dome_api.db.models import (
    PC,
    Account,
    Controller,
    Grant,
    Layout,
    PairingSession,
    PCAccessToken,
    PCCredential,
    Routine,
    SecurityEvent,
    Session,
    SupportDiagnostic,
)
from dome_api.logging import get_logger
from dome_api.security import events
from dome_api.util import utcnow

if TYPE_CHECKING:
    from dome_api.billing.service import BillingService
    from dome_api.relay.manager import ConnectionManager

log = get_logger("dome_api.account_deletion")

# rest.schema.json#/$defs/account_delete_request (the body is validated against it; kept here for the docs and tests)
CONFIRM_PHRASE = "delete my account"
# the provider must have confirmed a password entry for the session this recently (else 403 REAUTH_REQUIRED)
REAUTH_WINDOW = timedelta(minutes=10)
# the security event that records a finished deletion, and the reason in every revoked frame it sends
FINISHED_EVENT = "account_deleted"
REVOKED_REASON = "account_deleted"


@dataclass(slots=True)
class DeletionResult:
    subscription: Literal["canceled", "pending", "none"]
    finished_now: bool  # False: the account is not deleted, or an earlier call already finished it
    counts: dict[str, int] = field(default_factory=dict)
    sockets: tuple[int, int] = (0, 0)  # (phone sockets, PC sockets) closed


def session_is_recent(authenticated_at: datetime | None, now: datetime) -> bool:
    """The session's ``authenticated_at`` (the provider's ``auth_time`` of a checked re-authentication) is within
    :data:`REAUTH_WINDOW` (an exact boundary still counts). None, a session no re-authentication created, never is."""
    return authenticated_at is not None and now - authenticated_at <= REAUTH_WINDOW


async def mark_deleted(db: AsyncSession, account_id: uuid.UUID, now: datetime) -> bool:
    """Step 1, in the caller's transaction (the caller commits): ``deleted_at`` set, email and display name blanked
    (both columns are NOT NULL, so they become empty strings). False when the account is gone or already deleted (a
    second request racing the first waits on the row lock and then changes nothing)."""
    account = await db.scalar(
        select(Account).where(Account.id == account_id).with_for_update().execution_options(populate_existing=True)
    )
    if account is None or account.deleted_at is not None:
        return False
    account.deleted_at = now
    account.email = ""
    account.display_name = ""
    log.info("account.deletion_started", account_id=str(account_id))
    return True


def _rows(result: Any) -> int:
    return int(getattr(result, "rowcount", 0) or 0)


async def close_sockets(relay: ConnectionManager, account_id: uuid.UUID, *, best_effort: bool = False) -> None:
    """Tell every open phone and PC socket of a deleted account ``revoked{account_deleted}`` and close it (their Screen
    view sessions end and the phones' TURN credentials are revoked). Right after step 1 commits: from then on the
    account's sessions and PC tokens resolve to nothing, so no new socket opens, and an open one must not keep
    controlling a PC while step 2 runs or after it failed. ``best_effort``: a failure is logged, not raised."""
    try:
        phones, pcs = await relay.close_account_sockets(account_id, REVOKED_REASON)
    except Exception as exc:  # noqa: BLE001 - re-raised unless best_effort
        if not best_effort:
            raise
        log.warning("account.deletion_sockets_failed", account_id=str(account_id), error_class=type(exc).__name__)
        return
    log.info("account.deletion_sockets_closed", account_id=str(account_id), phone_sockets=phones, pc_sockets=pcs)


async def _revoke_everything(
    db: AsyncSession, account: Account, relay: ConnectionManager, now: datetime
) -> tuple[dict[str, int], set[uuid.UUID]]:
    """The database half of step 2 (the caller's transaction). Returns the counts for the event and the log line, and
    the ids of the phones revoked here (their Screen view sessions end when the sockets are closed)."""
    account_id = account.id
    sessions = _rows(
        await db.execute(
            update(Session)
            .where(Session.account_id == account_id, Session.revoked_at.is_(None))
            .values(revoked_at=now)
            .execution_options(synchronize_session=False)
        )
    )
    controllers = set(
        (
            await db.execute(
                update(Controller)
                .where(Controller.account_id == account_id, Controller.revoked_at.is_(None))
                .values(revoked_at=now)
                .returning(Controller.id)
                .execution_options(synchronize_session=False)
            )
        ).scalars()
    )
    counts: dict[str, int] = {"sessions": sessions, "phones": len(controllers)}
    counts["grants"] = _rows(
        await db.execute(
            update(Grant)
            .where(Grant.account_id == account_id, Grant.revoked_at.is_(None))
            .values(revoked_at=now)
            .execution_options(synchronize_session=False)
        )
    )
    account_pcs = select(PC.id).where(PC.account_id == account_id)
    counts["pc_credentials"] = _rows(
        await db.execute(
            update(PCCredential)
            .where(PCCredential.pc_id.in_(account_pcs), PCCredential.revoked_at.is_(None))
            .values(revoked_at=now)
            .execution_options(synchronize_session=False)
        )
    )
    await db.execute(
        update(PCAccessToken)
        .where(PCAccessToken.pc_id.in_(account_pcs), PCAccessToken.revoked_at.is_(None))
        .values(revoked_at=now)
        .execution_options(synchronize_session=False)
    )
    await db.execute(
        update(PairingSession)
        .where(PairingSession.account_id == account_id, PairingSession.state.in_(("open", "claimed")))
        .values(state="expired", decided_at=now)
        .execution_options(synchronize_session=False)
    )
    counts["pcs"] = _rows(
        await db.execute(
            update(PC)
            .where(PC.account_id == account_id, PC.deleted_at.is_(None))
            .values(deleted_at=now, enabled=False)
            .execution_options(synchronize_session=False)
        )
    )
    # every phone TURN credential of the account, as unlink and controller revocation do (a refusal at the provider
    # waits in turn_pending_revocations, which has no foreign key and so outlives the hard delete)
    counts["turn_credentials"] = (
        await relay.screen.turn.revoke_account(db, account_id, REVOKED_REASON, now) if relay.screen is not None else 0
    )
    counts["assistant_consent"] = int(account.assistant_consent_version is not None)
    account.assistant_consent_version = None
    account.assistant_consented_at = None
    counts["diagnostics"] = _rows(
        await db.execute(
            delete(SupportDiagnostic)
            .where(SupportDiagnostic.account_id == account_id)
            .execution_options(synchronize_session=False)
        )
    )
    for name, model in (("layouts", Layout), ("routines", Routine)):
        counts[name] = _rows(
            await db.execute(
                update(model)
                .where(model.account_id == account_id, model.deleted_at.is_(None))
                .values(deleted_at=now)
                .execution_options(synchronize_session=False)
            )
        )
    return counts, controllers


async def finish(
    *,
    db: async_sessionmaker[AsyncSession],
    billing: BillingService,
    relay: ConnectionManager,
    account_id: uuid.UUID,
    actor: Literal["account", "system"] = "account",
    now: datetime | None = None,
) -> DeletionResult:
    """Step 2. Idempotent: the purge job calls it again for a deleted account that has no ``account_deleted`` event
    (a crash between the steps). Does nothing to an account that is not deleted."""
    now = now or utcnow()
    async with db() as session:
        account = await session.get(Account, account_id)
        if account is None or account.deleted_at is None:
            return DeletionResult(subscription="none", finished_now=False)
        subscription = await billing.cancel_for_account_deletion(session, account_id)
        await session.commit()
    await billing.close_checkouts_for_deletion(account_id)
    counts: dict[str, int] = {}
    controllers: set[uuid.UUID] = set()
    finished_now = False
    async with transaction(db) as session:
        account = await session.scalar(
            select(Account).where(Account.id == account_id).with_for_update().execution_options(populate_existing=True)
        )
        assert account is not None and account.deleted_at is not None
        done = await session.scalar(
            select(SecurityEvent.id)
            .where(SecurityEvent.account_id == account_id, SecurityEvent.kind == FINISHED_EVENT)
            .limit(1)
        )
        if done is None:
            counts, controllers = await _revoke_everything(session, account, relay, now)
            events.record(
                session,
                account_id=account_id,
                kind=FINISHED_EVENT,
                severity="notice",
                actor=actor,
                detail={"subscription": subscription, **counts},
            )
            finished_now = True
    sockets = await relay.close_account_sockets(account_id, REVOKED_REASON, controller_ids=controllers)
    log.info(
        "account.deletion_finished",
        account_id=str(account_id),
        subscription=subscription,
        finished_now=finished_now,
        phone_sockets=sockets[0],
        pc_sockets=sockets[1],
        **counts,
    )
    return DeletionResult(subscription=subscription, finished_now=finished_now, counts=counts, sockets=sockets)
