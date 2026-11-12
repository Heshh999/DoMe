"""The retention purge (docs/DATA_RETENTION.md, DECISIONS #122): one periodic task that deletes what is past its
retention period, in bounded batches, and finishes account deletions a crash left half done.

One pass (``PurgeService.purge``; hourly, the first an hour after start-up, like the billing reconciler):

1. Account deletions a crash interrupted (``deleted_at`` set more than 10 minutes ago and no ``account_deleted``
   event): ``account_deletion.finish`` again.
2. Per table, at most ``batch_size`` rows each pass, oldest first, rows another transaction holds skipped: the rules
   in :data:`RULES` (each names the period DATA_RETENTION.md states).
3. Accounts deleted more than 30 days ago are hard-deleted with everything that cascades from them, except the
   financial records: ``subscriptions``, ``topup_purchases`` and ``billing_events`` keep their rows with
   ``account_id`` set to NULL (migration 0008; how long they are kept is for counsel to confirm). An account is never
   hard-deleted while its subscription cancel is pending, while a provider subscription of it is not cancelled, while
   a Checkout page of it is still pending, or before its deletion was finished.

Every table runs in its own transaction, so one failure costs that table's batch for this pass and nothing else. One
log line per pass, with counts per table only: never an id of a person, an email or a name.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import ColumnElement, and_, delete, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import InstrumentedAttribute

from dome_api import account_deletion
from dome_api.db.engine import transaction
from dome_api.db.models import (
    Account,
    ActivationEvent,
    AssistantTurn,
    AuthFlow,
    Command,
    DeviceLinkCode,
    Layout,
    PairingSession,
    PCAccessToken,
    PendingDeletion,
    Routine,
    ScreenReservation,
    ScreenViewWindow,
    SecurityEvent,
    Session,
    Subscription,
    SupportDiagnostic,
    TopupPurchase,
    TurnCredential,
)
from dome_api.logging import get_logger
from dome_api.settings import Settings
from dome_api.util import utcnow

if TYPE_CHECKING:
    from dome_api.billing.service import BillingService
    from dome_api.relay.manager import ConnectionManager

log = get_logger("dome_api.purge")

DAY = timedelta(days=1)
# a deletion younger than this may still be running in its request: the pass leaves it alone
FINISH_GRACE = timedelta(minutes=10)
ACCOUNT_HARD_DELETE_AFTER = timedelta(days=30)
# finishing a deletion asks the billing provider: fewer per pass than the plain deletes
FINISH_BATCH = 50


@dataclass(frozen=True, slots=True)
class Rule:
    """``DELETE FROM <table> WHERE <pk> IN (SELECT <pk> ... WHERE <due> ORDER BY <oldest> LIMIT n FOR UPDATE SKIP
    LOCKED)``."""

    table: str
    pk: InstrumentedAttribute[Any]
    due: Callable[[datetime], ColumnElement[bool]]
    oldest: InstrumentedAttribute[Any]


# Each rule is DATA_RETENTION.md's period for its table (§1 and §4).
RULES: tuple[Rule, ...] = (
    # 30 days after expiry or revocation
    Rule(
        "sessions",
        Session.id,
        lambda now: or_(Session.expires_at < now - 30 * DAY, Session.revoked_at < now - 30 * DAY),
        Session.created_at,
    ),
    # abandoned sign-in attempts, 24 h past expiry (a login start also sweeps expired ones)
    Rule("auth_flows", AuthFlow.id, lambda now: AuthFlow.expires_at < now - DAY, AuthFlow.expires_at),
    # 1-hour relay tokens, 24 h past expiry
    Rule(
        "pc_access_tokens",
        PCAccessToken.id,
        lambda now: PCAccessToken.expires_at < now - DAY,
        PCAccessToken.expires_at,
    ),
    # PC link codes (valid 10 minutes), 24 h past expiry
    Rule(
        "device_link_codes",
        DeviceLinkCode.id,
        lambda now: DeviceLinkCode.expires_at < now - DAY,
        DeviceLinkCode.expires_at,
    ),
    # pairings (valid 5 minutes), 7 days after they were decided or expired
    Rule(
        "pairing_sessions",
        PairingSession.id,
        lambda now: or_(PairingSession.decided_at < now - 7 * DAY, PairingSession.expires_at < now - 7 * DAY),
        PairingSession.expires_at,
    ),
    # command lifecycle history: 30 days
    Rule("commands", Command.id, lambda now: Command.created_at < now - 30 * DAY, Command.created_at),
    # 12 months (365 days); rows of no account (account_id NULL) 90 days
    Rule(
        "security_events",
        SecurityEvent.id,
        lambda now: or_(
            SecurityEvent.created_at < now - 365 * DAY,
            and_(SecurityEvent.account_id.is_(None), SecurityEvent.created_at < now - 90 * DAY),
        ),
        SecurityEvent.id,
    ),
    # raw rows 90 days (no event is emitted yet, so there is nothing to aggregate)
    Rule(
        "activation_events",
        ActivationEvent.id,
        lambda now: ActivationEvent.created_at < now - 90 * DAY,
        ActivationEvent.id,
    ),
    # at expires_at
    Rule(
        "support_diagnostics",
        SupportDiagnostic.id,
        lambda now: SupportDiagnostic.expires_at <= now,
        SupportDiagnostic.expires_at,
    ),
    # 30 days after the credential expired
    Rule(
        "turn_credentials",
        TurnCredential.controller_id,
        lambda now: TurnCredential.expires_at < now - 30 * DAY,
        TurnCredential.expires_at,
    ),
    # 30 days after the window ended
    Rule(
        "screen_view_windows",
        ScreenViewWindow.id,
        lambda now: ScreenViewWindow.window_end < now - 30 * DAY,
        ScreenViewWindow.window_end,
    ),
    # 90 days, settled rows only (the sweeper settles stale ones as `swept`)
    Rule(
        "screen_reservations",
        ScreenReservation.id,
        lambda now: and_(
            ScreenReservation.state.in_(("settled", "swept")), ScreenReservation.created_at < now - 90 * DAY
        ),
        ScreenReservation.created_at,
    ),
    # 400 days, settled rows only (a `pending` turn is settled as `abandoned` by the assistant's sweeper)
    Rule(
        "assistant_turns",
        AssistantTurn.id,
        lambda now: and_(AssistantTurn.status != "pending", AssistantTurn.created_at < now - 400 * DAY),
        AssistantTurn.created_at,
    ),
    # 30 days after the cancel completed (an open row is never deleted)
    Rule(
        "pending_deletions",
        PendingDeletion.id,
        lambda now: PendingDeletion.completed_at < now - 30 * DAY,
        PendingDeletion.completed_at,
    ),
    # soft-deleted 30 days, then purged
    Rule("layouts", Layout.id, lambda now: Layout.deleted_at < now - 30 * DAY, Layout.deleted_at),
    Rule("routines", Routine.id, lambda now: Routine.deleted_at < now - 30 * DAY, Routine.deleted_at),
)


def _finished() -> ColumnElement[bool]:
    return exists(
        select(SecurityEvent.id).where(
            SecurityEvent.account_id == Account.id, SecurityEvent.kind == account_deletion.FINISHED_EVENT
        )
    )


def _billing_open() -> ColumnElement[bool]:
    """Billing is not settled for the account: its subscription cancel is pending (an open ``pending_deletions``
    row), a provider subscription of it is not ``canceled``, or a Checkout page of it is still pending (a subscription
    row in ``checkout_pending``, a ``pending`` top-up), which the billing reconciler may still apply to the account."""
    return or_(
        exists(
            select(PendingDeletion.id).where(
                PendingDeletion.account_id == Account.id, PendingDeletion.completed_at.is_(None)
            )
        ),
        exists(
            select(Subscription.id).where(
                Subscription.account_id == Account.id,
                Subscription.state != "canceled",
                or_(Subscription.provider_subscription_id.is_not(None), Subscription.state == "checkout_pending"),
            )
        ),
        exists(
            select(TopupPurchase.id).where(TopupPurchase.account_id == Account.id, TopupPurchase.status == "pending")
        ),
    )


@dataclass(frozen=True, slots=True)
class PurgeConfig:
    enabled: bool  # DOME_PURGE_ENABLED
    interval_seconds: float = 3600.0
    batch_size: int = 500  # rows per table per pass (accounts too)
    clock: Callable[[], datetime] = utcnow

    @classmethod
    def from_settings(cls, settings: Settings) -> PurgeConfig:
        return cls(enabled=settings.purge_enabled)


class PurgeService:
    def __init__(
        self,
        *,
        config: PurgeConfig,
        db: async_sessionmaker[AsyncSession],
        billing: BillingService,
        relay: ConnectionManager,
    ) -> None:
        self.config = config
        self.db = db
        self.billing = billing
        self.relay = relay
        self._task: asyncio.Task[None] | None = None

    async def purge(self, now: datetime | None = None) -> dict[str, int]:
        """One pass. Returns the counts it logs: rows deleted per table, plus ``deletions_finished`` and
        ``accounts_kept_billing_open`` (accounts past 30 days kept because their billing is not settled)."""
        now = now or self.config.clock()
        counts: dict[str, int] = {"deletions_finished": await self._finish_deletions(now)}
        for rule in RULES:
            counts[rule.table] = await self._delete(rule, now)
        counts["accounts"], counts["accounts_kept_billing_open"] = await self._hard_delete_accounts(now)
        log.info("purge.run", **counts)
        return counts

    async def _delete(self, rule: Rule, now: datetime) -> int:
        try:
            async with transaction(self.db) as db:
                due = (
                    select(rule.pk)
                    .where(rule.due(now))
                    .order_by(rule.oldest)
                    .limit(self.config.batch_size)
                    .with_for_update(skip_locked=True)
                )
                result = await db.execute(
                    delete(rule.pk.class_).where(rule.pk.in_(due)).execution_options(synchronize_session=False)
                )
                return int(getattr(result, "rowcount", 0) or 0)
        except Exception as exc:  # noqa: BLE001 - the next pass retries this table; the others go on
            log.warning("purge.table_failed", table=rule.table, error_class=type(exc).__name__)
            return 0

    async def _finish_deletions(self, now: datetime) -> int:
        async with self.db() as db:
            ids = (
                await db.scalars(
                    select(Account.id)
                    .where(Account.deleted_at.is_not(None), Account.deleted_at < now - FINISH_GRACE, ~_finished())
                    .order_by(Account.deleted_at)
                    .limit(min(self.config.batch_size, FINISH_BATCH))
                )
            ).all()
        finished = 0
        for account_id in ids:
            try:
                result = await account_deletion.finish(
                    db=self.db, billing=self.billing, relay=self.relay, account_id=account_id, actor="system", now=now
                )
            except Exception as exc:  # noqa: BLE001 - the next pass tries this account again
                log.warning("purge.finish_failed", account_id=str(account_id), error_class=type(exc).__name__)
                continue
            finished += int(result.finished_now)
        return finished

    async def _hard_delete_accounts(self, now: datetime) -> tuple[int, int]:
        cutoff = now - ACCOUNT_HARD_DELETE_AFTER
        past = and_(Account.deleted_at.is_not(None), Account.deleted_at < cutoff)
        try:
            async with transaction(self.db) as db:
                kept = int(await db.scalar(select(func.count()).select_from(Account).where(past, _billing_open())) or 0)
                due = (
                    select(Account.id)
                    .where(past, _finished(), ~_billing_open())
                    .order_by(Account.deleted_at)
                    .limit(self.config.batch_size)
                    .with_for_update(skip_locked=True)
                )
                result = await db.execute(
                    delete(Account).where(Account.id.in_(due)).execution_options(synchronize_session=False)
                )
                return int(getattr(result, "rowcount", 0) or 0), kept
        except Exception as exc:  # noqa: BLE001 - the next pass retries
            log.warning("purge.table_failed", table="accounts", error_class=type(exc).__name__)
            return 0, 0

    # ----- lifecycle --------------------------------------------------------------------------------------------------
    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self.config.interval_seconds)
            if not self.config.enabled:
                continue
            try:
                await self.purge()
            except Exception as exc:  # noqa: BLE001 - the next pass retries; never let the loop die
                log.warning("purge.failed", error_class=type(exc).__name__)

    async def start(self) -> None:
        if self.config.enabled:
            log.info("purge.scheduled", interval_seconds=self.config.interval_seconds)
        self._task = asyncio.create_task(self._loop(), name="retention-purge")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
