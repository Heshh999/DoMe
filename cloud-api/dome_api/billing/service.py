"""The billing service (docs/BILLING.md §5–§8, §10–§11): the Billing page's status, Checkout and Portal pages,
top-ups, the webhook, the reconciler and account-deletion cancellation.

Rules that hold everywhere here: the plan is written only through ``state.apply``, from an object fetched from the
provider (never from an event payload or a return URL); a webhook is acknowledged after its receipt row is committed
and processed synchronously (a failure marks the row ``failed`` and answers 500 so the provider redelivers; the
reconciler retries as well); one log line per operation with codes, ids, plan names and counts, never an email, a URL
with secrets or a raw body. Everything a test needs to vary (enabled, mode, prices, clock) lives in the replaceable
:class:`BillingConfig` and the replaceable ``provider`` attribute.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dome_api.assistant import metering
from dome_api.billing import state
from dome_api.billing.provider import (
    BillingProvider,
    CheckoutSession,
    ProviderError,
    SubscriptionSnapshot,
    WebhookEvent,
)
from dome_api.db.engine import transaction
from dome_api.db.models import Account, BillingEvent, PendingDeletion, Subscription, TopupPurchase
from dome_api.errors import ApiError
from dome_api.logging import get_logger
from dome_api.plans import Plan, catalog, plan_for
from dome_api.security import events
from dome_api.security.ratelimit import SlidingWindowLimiter
from dome_api.settings import BillingMode, Settings
from dome_api.util import ts, ts_required, utcnow

if TYPE_CHECKING:  # the relay is a type here only; billing pushes snapshots through it and never routes frames
    from dome_api.assistant.service import AssistantService
    from dome_api.relay.manager import ConnectionManager

log = get_logger("dome_api.billing")

SUBSCRIPTION_EVENTS = frozenset(
    {
        "customer.subscription.created",
        "customer.subscription.updated",
        "customer.subscription.deleted",
        "invoice.paid",
        "invoice.payment_failed",
        "invoice.payment_action_required",
    }
)
CHECKOUT_DONE_EVENTS = frozenset({"checkout.session.completed", "checkout.session.async_payment_succeeded"})
OPERATOR_EVENTS = frozenset(
    {"charge.dispute.created", "charge.dispute.updated", "charge.dispute.closed", "charge.refunded"}
)
MAX_EVENT_ATTEMPTS = 5
STALE_RECEIVED_MINUTES = 10
RECONCILE_FETCH_LIMIT = 200
# a checkout row whose page is still being created (no session id yet) for this long belongs to a request in flight
CHECKOUT_CREATING_SECONDS = 30
# states in which a top-up may be bought (the balance stays spendable only while the plan allows it)
TOPUP_STATES = frozenset({"trialing", "active", "cancel_scheduled", "past_due"})
WebhookStatus = Literal["processed", "ignored", "failed", "duplicate"]


@dataclass(frozen=True, slots=True)
class BillingConfig:
    enabled: bool
    mode: BillingMode
    price_map: dict[str, tuple[str, str]]  # price id -> (plan id, interval)
    topup_price_id: str
    public_origin: str
    automatic_tax: bool
    checkout_pending_hours: int
    reconcile_interval_seconds: float
    requests_per_hour: int
    clock: Callable[[], datetime] = utcnow

    @classmethod
    def from_settings(cls, settings: Settings) -> BillingConfig:
        return cls(
            enabled=settings.billing_enabled,
            mode=settings.billing_mode,
            price_map=settings.price_map() if settings.billing_enabled else {},
            topup_price_id=settings.topup_price_id,
            public_origin=settings.public_origin,
            automatic_tax=settings.stripe_automatic_tax,
            checkout_pending_hours=settings.billing_checkout_pending_hours,
            reconcile_interval_seconds=settings.billing_reconcile_interval_seconds,
            requests_per_hour=settings.rate_billing_requests_per_hour,
        )

    def price_for(self, plan: str, interval: str) -> str | None:
        for price_id, (p, i) in self.price_map.items():
            if (p, i) == (plan, interval):
                return price_id
        return None

    def url(self, path: str) -> str:
        return self.public_origin + path


def build_provider(settings: Settings) -> BillingProvider | None:
    """The configured provider, or ``None`` while billing is off. Created once in the lifespan."""
    if not settings.billing_enabled:
        return None
    if settings.effective_billing_provider == "fake":
        if settings.env not in ("development", "test"):  # settings refuse it too; never reachable in production
            raise RuntimeError("the fake billing provider is for development and tests only")
        from dome_api.billing.fake_provider import FakeStripe

        return FakeStripe()
    from dome_api.billing.stripe_provider import StripeProvider

    return StripeProvider(settings)


@dataclass(slots=True)
class WebhookOutcome:
    status: WebhookStatus
    duplicate: bool = False

    def body(self) -> dict[str, Any]:
        """rest.schema.json#/$defs/billing_webhook_response"""
        return {"received": True, "duplicate": self.duplicate, "status": self.status}


class BillingService:
    def __init__(
        self,
        *,
        config: BillingConfig,
        provider: BillingProvider | None,
        db: async_sessionmaker[AsyncSession],
        relay: ConnectionManager,
        assistant: AssistantService | None = None,
    ) -> None:
        self.config = config
        self.provider = provider
        self.db = db
        self.relay = relay
        self.assistant = assistant  # read only to know whether AI requests can be used at all (top-ups)
        self._limiter = SlidingWindowLimiter(config.requests_per_hour, 3600)
        self._reconciler: asyncio.Task[None] | None = None

    # ----- state ----------------------------------------------------------------------------------------------------
    @property
    def enabled(self) -> bool:
        return self.config.enabled and self.provider is not None

    def _require_enabled(self) -> BillingProvider:
        if not self.enabled or self.provider is None:
            raise ApiError(503, "BILLING_NOT_AVAILABLE")
        return self.provider

    def _take_budget(self, account_id: uuid.UUID) -> None:
        if not self._limiter.allow(str(account_id)):
            raise ApiError(429, "RATE_LIMITED", headers={"Retry-After": "600"})

    @staticmethod
    def _provider_failed(exc: ProviderError, operation: str) -> ApiError:
        log.warning(
            "billing.provider_error",
            operation=operation,
            reason=exc.reason,
            status=exc.status,
            request_id=exc.request_id,
        )
        return ApiError(502, "BILLING_PROVIDER_ERROR", detail={"reason": exc.reason})

    def _selection(self, account: Account, now: datetime) -> dict[str, Any] | None:
        if account.plan_changed_at is None:
            return None
        deadline = account.plan_changed_at + timedelta(days=catalog().selection_window_days)
        return {"open": now < deadline, "deadline": ts_required(deadline)}

    @property
    def ai_usable(self) -> bool:
        return self.assistant is not None and self.assistant.configured

    def topup_purchasable(self, plan: Plan, row: Subscription | None, now: datetime) -> bool:
        """Top-ups may be bought on a plan that allows them while its subscription is in good standing and the
        assistant is switched on, so nobody buys requests they cannot use (BILLING §10)."""
        if not (self.enabled and self.ai_usable and plan.ai_topup_allowed):
            return False
        if row is None or row.state not in TOPUP_STATES:
            return False
        return row.state != "past_due" or row.grace_until is None or now < row.grace_until

    def checkout_plans(self) -> list[str]:
        """The paid plans a Free customer can buy now. Pro Plus differs from Pro only in AI requests, so it is offered
        only while the assistant is switched on (the top-up rule of BILLING §10 applied to subscriptions)."""
        if not self.enabled:
            return []
        return [
            p
            for p in catalog().paid_plan_ids
            if (p == "pro" or self.ai_usable) and self.config.price_for(p, "monthly") is not None
        ]

    async def _settle_paid_page(self, db: AsyncSession, account: Account, row: Subscription | None) -> bool:
        """While the row waits on a hosted page that can still be paid, ask the provider whether it already was. A
        completed page means the customer paid (or is paying) and only the webhook is outstanding: its subscription is
        fetched and applied now, so no second page is offered. True when the page is complete. A provider failure
        answers False; ``checkout`` asks again under the account lock before creating anything."""
        now = self.config.clock()
        if (
            not self.enabled
            or self.provider is None
            or row is None
            or row.state != "checkout_pending"
            or not row.provider_checkout_session_id
            or (row.checkout_expires_at is not None and now >= row.checkout_expires_at)
        ):
            return False
        try:
            session = await self.provider.retrieve_checkout(row.provider_checkout_session_id)
            if session.status != "complete":
                return False
            if session.subscription_id:
                await self._refresh(session.subscription_id, account.id)
        except ProviderError as exc:
            self._provider_failed(exc, "retrieve_checkout")
            return False
        db.expire_all()  # the apply ran in its own transaction: read the row and the plan again
        return True

    async def status_body(self, db: AsyncSession, account: Account) -> dict[str, Any]:
        """rest.schema.json#/$defs/billing_status_response"""
        now = self.config.clock()
        row = await state.live_subscription(db, account.id)
        paid_page = await self._settle_paid_page(db, account, row)
        if paid_page:
            await db.refresh(account)
            row = await state.live_subscription(db, account.id)
        plan = plan_for(account.plan)
        topup = catalog().topup
        subscription: dict[str, Any] | None = None
        shown_state = state.entitlement_state(row, now)
        # a row is shown while it means something to the customer: not an old ended one, not a page never created
        if row is not None and shown_state != "free":
            subscription = {
                "state": row.state,
                "plan": row.plan,
                "interval": row.interval,
                "current_period_end": ts(row.current_period_end),
                "cancel_at_period_end": bool(row.cancel_at_period_end),
                "grace_until": ts(row.grace_until),
                "checkout_expires_at": ts(row.checkout_expires_at),
            }
        # an open checkout page does not block another: the same page (or one for a different price) is offered again,
        # so a customer who left the page can come back; a page that was paid is never offered again
        live = row is not None and row.state not in ("canceled", "checkout_pending")
        offered = self.checkout_plans()
        return {
            "billing_enabled": self.enabled,
            "billing_mode": self.config.mode if self.enabled else "off",
            "plan": plan.id,
            "entitlement_state": shown_state,
            "subscription": subscription,
            "checkout_available": bool(offered) and not live and not paid_page,
            "checkout_plans": offered if not live and not paid_page else [],
            "portal_available": self.enabled and row is not None and bool(row.provider_customer_id),
            "pricing": catalog().pricing_defaults,
            "topup": {
                "allowed": self.topup_purchasable(plan, row, now),
                "balance_requests": await metering.topup_balance(db, account.id),
                "requests": topup["requests"],
                "price_cents": topup["price_cents"],
            },
            "selection": self._selection(account, now),
        }

    async def entitlement_state(self, db: AsyncSession, account_id: uuid.UUID) -> str:
        """For GET /v1/session: one query by account id."""
        return state.entitlement_state(await state.live_subscription(db, account_id), self.config.clock())

    # ----- checkout, portal, top-up ---------------------------------------------------------------------------------
    async def _customer(
        self, db: AsyncSession, provider: BillingProvider, account: Account, row: Subscription | None
    ) -> str:
        if row is not None and row.provider_customer_id:
            return row.provider_customer_id
        existing = await db.scalar(
            select(Subscription.provider_customer_id)
            .where(Subscription.account_id == account.id, Subscription.provider_customer_id.is_not(None))
            .order_by(Subscription.updated_at.desc())
            .limit(1)
        )
        if existing:
            return str(existing)
        return (await provider.ensure_customer(str(account.id), account.email)).id

    @staticmethod
    def _checkout_body(kind: str, session: CheckoutSession) -> dict[str, Any]:
        """rest.schema.json#/$defs/billing_checkout_response"""
        assert session.url is not None
        return {
            "kind": kind,
            "checkout_url": session.url,
            "session_id": session.id,
            "expires_at": ts_required(session.expires_at),
        }

    async def checkout(self, db: AsyncSession, account: Account, plan_id: str, interval: str) -> dict[str, Any]:
        provider = self._require_enabled()
        now = self.config.clock()
        price_id = self.config.price_for(plan_id, interval)
        if price_id is None:
            raise ApiError(503, "BILLING_NOT_AVAILABLE")
        if plan_id not in self.checkout_plans():
            # Pro Plus adds only AI requests: nobody pays for them while the assistant is off (BILLING §5)
            raise ApiError(403, "PLAN_NOT_AVAILABLE", detail={"plan": plan_id, "reason": "ai_off"})
        locked = await db.scalar(select(Account).where(Account.id == account.id).with_for_update())
        assert locked is not None
        row = await state.live_subscription(db, account.id, for_update=True)
        if row is not None and row.state not in ("canceled", "checkout_pending"):
            raise ApiError(409, "ALREADY_SUBSCRIBED", detail={"portal_available": bool(row.provider_customer_id)})
        if row is not None and row.state == "checkout_pending" and row.provider_checkout_session_id:
            try:
                open_session = await provider.retrieve_checkout(row.provider_checkout_session_id)
            except ProviderError as exc:
                raise self._provider_failed(exc, "retrieve_checkout") from None
            if open_session.status == "complete":
                # the customer paid this page and only the webhook is outstanding: never a second page (a second
                # subscription would be a second charge). The subscription it created is applied now.
                account_id = account.id  # read before the commit expires the loaded objects
                await db.commit()  # releases the account lock; the apply takes it in its own transaction
                if open_session.subscription_id:
                    try:
                        await self._refresh(open_session.subscription_id, account_id)
                    except ProviderError as exc:
                        raise self._provider_failed(exc, "retrieve_subscription") from None
                db.expire_all()
                row = await state.live_subscription(db, account_id)
                log.info("billing.checkout_already_paid", account_id=str(account_id))
                if row is not None and row.state not in ("canceled", "checkout_pending"):
                    raise ApiError(
                        409, "ALREADY_SUBSCRIBED", detail={"portal_available": bool(row.provider_customer_id)}
                    )
                raise ApiError(409, "CHECKOUT_PROCESSING")
            if (
                open_session.status == "open"
                and open_session.url
                and open_session.metadata.get("dome_price") == price_id
            ):
                log.info("billing.checkout", account_id=str(account.id), plan=plan_id, interval=interval, reused=True)
                return self._checkout_body("subscription", open_session)
            if open_session.status == "open":
                # a different choice replaces the open page; closing the old one first means the customer can never
                # pay two pages and end up with two subscriptions
                try:
                    await provider.expire_checkout(open_session.id)
                except ProviderError as exc:
                    raise self._provider_failed(exc, "expire_checkout") from None
        # a page another request is creating right now (a double tap, a second tab): the same choice replays that
        # request's idempotency key and expiry, so the provider answers with the same page; a different choice waits
        replay: tuple[str, datetime] | None = None
        if (
            row is not None
            and row.state == "checkout_pending"
            and not row.provider_checkout_session_id
            and row.checkout_idempotency_key
            and row.checkout_expires_at is not None
            and now - row.updated_at < timedelta(seconds=CHECKOUT_CREATING_SECONDS)
        ):
            if row.price_id != price_id:
                raise ApiError(409, "CHECKOUT_PROCESSING")
            replay = (row.checkout_idempotency_key, row.checkout_expires_at)
        self._take_budget(account.id)
        try:
            customer_id = await self._customer(db, provider, account, row)
        except ProviderError as exc:
            raise self._provider_failed(exc, "ensure_customer") from None
        if row is None or row.state == "canceled":
            row = Subscription(account_id=account.id, provider="stripe", plan=plan_id, state="checkout_pending")
            db.add(row)
        row.plan = plan_id
        row.interval = interval
        row.price_id = price_id
        row.state = "checkout_pending"
        row.provider_customer_id = customer_id
        row.provider_checkout_session_id = None
        if replay is None:
            replay = (
                f"checkout:{account.id}:{uuid.uuid4()}",
                now + timedelta(hours=self.config.checkout_pending_hours),
            )
            row.updated_at = now
        row.checkout_idempotency_key, expires_at = replay
        row.checkout_expires_at = expires_at
        # the key and the expiry are stored before the provider sees them, so a second request for the same choice
        # within CHECKOUT_CREATING_SECONDS sends the identical request and gets the same page back
        await db.commit()
        try:
            session = await provider.create_checkout(
                customer_id=customer_id,
                price_id=price_id,
                mode="subscription",
                success_url=self.config.url("/app/billing?checkout=return&session_id={CHECKOUT_SESSION_ID}"),
                cancel_url=self.config.url("/app/billing?checkout=cancel"),
                idempotency_key=row.checkout_idempotency_key,
                metadata={"dome_account_id": str(account.id), "dome_kind": "subscription", "dome_price": price_id},
                automatic_tax=self.config.automatic_tax,
                expires_at=expires_at,
            )
        except ProviderError as exc:
            raise self._provider_failed(exc, "create_checkout") from None
        row.provider_checkout_session_id = session.id
        row.checkout_expires_at = session.expires_at
        row.updated_at = now
        await db.commit()
        log.info("billing.checkout", account_id=str(account.id), plan=plan_id, interval=interval, reused=False)
        return self._checkout_body("subscription", session)

    async def portal(self, db: AsyncSession, account: Account) -> dict[str, Any]:
        provider = self._require_enabled()
        row = await state.live_subscription(db, account.id)
        if row is None or not row.provider_customer_id:
            raise ApiError(404, "BILLING_NO_CUSTOMER")
        self._take_budget(account.id)
        try:
            url = await provider.create_portal(row.provider_customer_id, self.config.url("/app/billing"))
        except ProviderError as exc:
            raise self._provider_failed(exc, "create_portal") from None
        log.info("billing.portal", account_id=str(account.id))
        return {"portal_url": url}

    async def topup(self, db: AsyncSession, account: Account) -> dict[str, Any]:
        provider = self._require_enabled()
        now = self.config.clock()
        plan = plan_for(account.plan)
        row = await state.live_subscription(db, account.id)
        if not self.topup_purchasable(plan, row, now) or row is None or not row.provider_customer_id:
            raise ApiError(403, "TOPUP_NOT_ALLOWED")
        topup = catalog().topup
        # pending pages count as held: nobody can open ten pages and bring the balance past the cap
        pending = (
            await db.scalars(
                select(TopupPurchase.requests).where(
                    TopupPurchase.account_id == account.id, TopupPurchase.status == "pending"
                )
            )
        ).all()
        held = await metering.topup_balance(db, account.id) + sum(int(r) for r in pending)
        if held + topup["requests"] > topup["max_balance_requests"]:
            raise ApiError(409, "TOPUP_BALANCE_LIMIT")
        self._take_budget(account.id)
        purchase_id = uuid.uuid4()
        try:
            session = await provider.create_checkout(
                customer_id=row.provider_customer_id,
                price_id=self.config.topup_price_id,
                mode="payment",
                success_url=self.config.url("/app/billing?topup=return&session_id={CHECKOUT_SESSION_ID}"),
                cancel_url=self.config.url("/app/billing?topup=cancel"),
                idempotency_key=f"topup:{purchase_id}",
                metadata={
                    "dome_account_id": str(account.id),
                    "dome_kind": "topup",
                    "dome_requests": str(topup["requests"]),
                },
                automatic_tax=self.config.automatic_tax,
                expires_at=now + timedelta(hours=self.config.checkout_pending_hours),
            )
        except ProviderError as exc:
            raise self._provider_failed(exc, "create_checkout") from None
        db.add(
            TopupPurchase(
                id=purchase_id,
                account_id=account.id,
                provider_checkout_session_id=session.id,
                requests=topup["requests"],
                amount_cents=topup["price_cents"],
                currency=str(catalog().pricing_defaults["currency"]),
                status="pending",
                created_at=now,
            )
        )
        await db.commit()
        log.info("billing.topup_checkout", account_id=str(account.id), requests=topup["requests"])
        return self._checkout_body("topup", session)

    # ----- webhook ----------------------------------------------------------------------------------------------------
    @staticmethod
    def _kind(event: WebhookEvent) -> str:
        if event.type.startswith("checkout.session."):
            return "checkout"
        if event.type in SUBSCRIPTION_EVENTS:
            return "subscription"
        return "other"

    async def webhook(self, raw: bytes, signature_header: str | None) -> WebhookOutcome:
        provider = self._require_enabled()
        try:
            event = provider.parse_webhook(raw, signature_header)
        except ProviderError:
            raise ApiError(400, "BILLING_WEBHOOK_INVALID") from None
        now = self.config.clock()
        async with transaction(self.db) as db:
            inserted = await db.scalar(
                pg_insert(BillingEvent)
                .values(
                    provider="stripe",
                    provider_event_id=event.id,
                    event_type=event.type[:128],
                    kind=self._kind(event),
                    provider_object_id=(event.checkout_session_id or event.subscription_id or None),
                    received_at=now,
                    status="received",
                    attempts=0,
                    payload_digest=event.payload_digest,
                )
                .on_conflict_do_nothing(index_elements=[BillingEvent.provider_event_id])
                .returning(BillingEvent.id)
            )
        if inserted is None:
            # the provider redelivers an event we answered 500 for: claim the failed row (one claimant wins) and
            # process it again; anything else is a true duplicate and is acknowledged without work
            async with transaction(self.db) as db:
                reclaimed = await db.scalar(
                    update(BillingEvent)
                    .where(
                        BillingEvent.provider_event_id == event.id,
                        BillingEvent.status == "failed",
                        BillingEvent.attempts < MAX_EVENT_ATTEMPTS,
                    )
                    .values(status="received")
                    .returning(BillingEvent.id)
                )
            if reclaimed is None:
                log.info("billing.webhook", event_type=event.type, status="duplicate")
                return WebhookOutcome("duplicate", duplicate=True)
            inserted = reclaimed
        return WebhookOutcome(await self._process(int(inserted), event, now))

    async def _process(self, row_id: int, event: WebhookEvent, now: datetime) -> WebhookStatus:
        """Dispatch one received event; the receipt row ends ``processed``, ``ignored`` or ``failed``."""
        try:
            status, account_id, error = await self._dispatch(event)
        except Exception as exc:  # noqa: BLE001 - recorded as failed; the provider redelivers, the reconciler retries
            log.warning("billing.webhook_failed", event_type=event.type, error_class=type(exc).__name__)
            await self._mark(row_id, "failed", None, type(exc).__name__, now)
            raise ApiError(500, "INTERNAL") from None
        await self._mark(row_id, status, account_id, error, now)
        log.info(
            "billing.webhook",
            event_type=event.type,
            status=status,
            account_id=str(account_id) if account_id else None,
            error=error,
        )
        return status

    async def _mark(
        self, row_id: int, status: str, account_id: uuid.UUID | None, error: str | None, now: datetime
    ) -> None:
        async with transaction(self.db) as db:
            row = await db.get(BillingEvent, row_id)
            if row is None:
                return
            row.status = status
            row.attempts += 1
            row.processed_at = now
            row.error = error
            if account_id is not None:
                row.account_id = account_id

    async def _dispatch(self, event: WebhookEvent) -> tuple[WebhookStatus, uuid.UUID | None, str | None]:
        provider = self._require_enabled()
        if event.type in CHECKOUT_DONE_EVENTS or event.type == "checkout.session.expired":
            if not event.checkout_session_id:
                return "ignored", None, "no_checkout_session"
            session = await provider.retrieve_checkout(event.checkout_session_id)
            account_id = await self._attribute(session.metadata.get("dome_account_id"), session.customer_id)
            if account_id is None:
                return "ignored", None, "unattributed"
            if event.type == "checkout.session.expired":
                return await self._checkout_expired(account_id, session)
            if session.mode == "payment":
                if session.metadata.get("dome_kind") != "topup":
                    return "ignored", account_id, "not_a_topup"
                if session.payment_status != "paid":
                    return "ignored", account_id, "unpaid"
                return await self._credit_topup(account_id, session)
            if not session.subscription_id:
                return "ignored", account_id, "no_subscription"
            return await self._refresh(session.subscription_id, account_id)
        if event.type in SUBSCRIPTION_EVENTS:
            if not event.subscription_id:
                return "ignored", None, "no_subscription"
            return await self._refresh(event.subscription_id, None, customer_id=event.customer_id)
        if event.type in OPERATOR_EVENTS:
            account_id = await self._attribute(None, event.customer_id)
            log.warning(
                "billing.needs_operator", event_type=event.type, account_id=str(account_id) if account_id else None
            )
            return "ignored", account_id, "operator_action"
        return "ignored", None, "unhandled_type"

    async def _attribute(self, account_hint: str | None, customer_id: str | None) -> uuid.UUID | None:
        """The account an object belongs to: our own metadata first, else the customer id of a row we wrote."""
        async with self.db() as db:
            account: Account | None = None
            if account_hint:
                try:
                    account = await db.get(Account, uuid.UUID(account_hint))
                except ValueError:
                    account = None
            if account is None and customer_id:
                owner = await db.scalar(
                    select(Subscription.account_id)
                    .where(Subscription.provider_customer_id == customer_id)
                    .order_by(Subscription.updated_at.desc())
                    .limit(1)
                )
                account = await db.get(Account, owner) if owner else None
            if account is None or account.deleted_at is not None:
                return None
            return account.id

    async def _refresh(
        self, subscription_id: str, account_id: uuid.UUID | None, *, customer_id: str | None = None
    ) -> tuple[WebhookStatus, uuid.UUID | None, str | None]:
        """Fetch the subscription as it is now and apply the state machine (BILLING §4.3). The event's own fields are
        never used, so duplicates and out-of-order deliveries converge."""
        provider = self._require_enabled()
        if account_id is None:
            async with self.db() as db:
                owner = await db.scalar(
                    select(Subscription.account_id).where(Subscription.provider_subscription_id == subscription_id)
                )
            if owner is None:
                # a subscription DoMe has not seen yet: its metadata or customer names the account. This fetch only
                # attributes it; the snapshot that is applied is fetched again under the account's lock below
                snapshot = await provider.retrieve_subscription(subscription_id)
                owner = await self._attribute(
                    snapshot.metadata.get("dome_account_id"), snapshot.customer_id or customer_id
                )
            account_id = owner
            if account_id is not None:
                async with self.db() as db:
                    account = await db.get(Account, account_id)
                    if account is None or account.deleted_at is not None:
                        account_id = None
        if account_id is None:
            return "ignored", None, "unattributed"
        touched = await self._apply_current(account_id, subscription_id, reason="webhook")
        await self._push(touched)
        return "processed", account_id, None

    async def _apply_current(self, account_id: uuid.UUID, subscription_id: str, *, reason: str) -> list[uuid.UUID]:
        """Fetch the subscription and apply it inside one transaction that holds a per-account advisory lock, so two
        handlers (two webhooks, or a webhook and the reconciler) apply in the order they fetched: an older snapshot
        can never land after a newer one."""
        provider = self._require_enabled()
        async with transaction(self.db) as db:
            await db.execute(select(func.pg_advisory_xact_lock(func.hashtext(f"dome.billing:{account_id}"))))
            snapshot: SubscriptionSnapshot = await provider.retrieve_subscription(subscription_id)
            return await state.apply(
                db,
                account_id,
                snapshot,
                reason=reason,
                now=self.config.clock(),
                price_map=self.config.price_map,
            )

    async def _apply_forced(self, account_id: uuid.UUID, *, reason: str, forced_state: str) -> list[uuid.UUID]:
        """The grace sweep and account deletion: a state without a provider object to fetch."""
        async with transaction(self.db) as db:
            return await state.apply(
                db,
                account_id,
                None,
                reason=reason,
                now=self.config.clock(),
                price_map=self.config.price_map,
                forced_state=forced_state,
            )

    async def _push(self, pc_ids: list[uuid.UUID]) -> None:
        for pc_id in pc_ids:
            await self.relay.push_grants_snapshot(pc_id)

    async def _checkout_expired(
        self, account_id: uuid.UUID, session: CheckoutSession
    ) -> tuple[WebhookStatus, uuid.UUID | None, str | None]:
        async with transaction(self.db) as db:
            if session.mode == "payment":
                purchase = await db.scalar(
                    select(TopupPurchase).where(TopupPurchase.provider_checkout_session_id == session.id)
                )
                if purchase is not None and purchase.status == "pending":
                    purchase.status = "expired"
            else:
                row = await db.scalar(
                    select(Subscription).where(
                        Subscription.account_id == account_id,
                        Subscription.provider_checkout_session_id == session.id,
                        Subscription.state == "checkout_pending",
                    )
                )
                if row is not None:
                    await db.delete(row)
        return "processed", account_id, None

    async def _credit_topup(
        self, account_id: uuid.UUID, session: CheckoutSession
    ) -> tuple[WebhookStatus, uuid.UUID | None, str | None]:
        """Credit a paid top-up exactly once: the purchase row's status flips under the account's balance lock."""
        now = self.config.clock()
        topup = catalog().topup
        async with transaction(self.db) as db:
            balance = await metering.lock_balance(db, account_id, now)
            purchase = await db.scalar(
                select(TopupPurchase).where(TopupPurchase.provider_checkout_session_id == session.id).with_for_update()
            )
            if purchase is None:
                # the page was created but our row never committed: the payment is real, so the purchase is recorded now
                requests = int(session.metadata.get("dome_requests") or topup["requests"])
                purchase = TopupPurchase(
                    account_id=account_id,
                    provider_checkout_session_id=session.id,
                    requests=requests,
                    amount_cents=topup["price_cents"],
                    currency=str(catalog().pricing_defaults["currency"]),
                    status="pending",
                    created_at=now,
                )
                db.add(purchase)
                await db.flush()
            if purchase.status == "credited":
                return "processed", account_id, "already_credited"
            purchase.status = "credited"
            purchase.credited_at = now
            purchase.provider_payment_intent_id = session.payment_intent_id
            balance.topup_remaining += purchase.requests
            balance.updated_at = now
            events.record(
                db,
                account_id=account_id,
                kind="topup_credited",
                severity="info",
                actor="system",
                subject_id=purchase.id,
                detail={"requests": purchase.requests, "balance": balance.topup_remaining},
            )
        log.info("billing.topup_credited", account_id=str(account_id), requests=purchase.requests)
        return "processed", account_id, None

    # ----- reconciliation ---------------------------------------------------------------------------------------------
    async def reconcile(self, now: datetime | None = None) -> dict[str, int]:
        """One pass (hourly, or ``dome-api --billing-reconcile``): settle stale checkouts with the provider, end
        exhausted grace, retry failed or stuck events and pending deletions, and re-apply every live row from the
        provider's current state."""
        now = now or self.config.clock()
        counts = {
            "checkouts_expired": 0,
            "checkouts_recovered": 0,
            "grace_ended": 0,
            "events_retried": 0,
            "deletions_retried": 0,
            "rows_refreshed": 0,
            "mismatches": 0,
        }
        expired, recovered = await self._settle_stale_checkouts(now)
        counts["checkouts_expired"], counts["checkouts_recovered"] = expired, recovered
        async with self.db() as db:
            ended = (
                await db.scalars(
                    select(Subscription.account_id).where(
                        Subscription.state == "past_due", Subscription.grace_until <= now
                    )
                )
            ).all()
        for account_id in ended:
            touched = await self._apply_forced(account_id, reason="grace_expired", forced_state="unpaid")
            await self._push(touched)
            counts["grace_ended"] += 1
        counts["events_retried"] = await self._retry_events(now)
        counts["deletions_retried"] = await self._retry_deletions(now)
        if self.enabled:
            refreshed, mismatches = await self._refresh_live_rows(now)
            counts["rows_refreshed"], counts["mismatches"] = refreshed, mismatches
        log.info("billing.reconcile", **counts)
        return counts

    async def _stale_page(self, session_id: str | None, now: datetime) -> CheckoutSession | Literal["gone", "keep"]:
        """What the provider says about a stale page: the session when it was completed, ``gone`` when it can no longer
        be paid (expired, past its expiry, or never created), ``keep`` while it is still payable or the provider
        cannot be asked (a failure, or billing switched off): such a row waits for the next pass."""
        if not session_id:
            return "gone"
        if not self.enabled or self.provider is None:
            return "keep"
        try:
            session = await self.provider.retrieve_checkout(session_id)
        except ProviderError as exc:
            if exc.reason == "not_found":
                return "gone"
            self._provider_failed(exc, "reconcile_checkout")
            return "keep"
        if session.status == "complete":
            return session
        if session.status == "open" and now < session.expires_at:
            return "keep"
        return "gone"

    async def _settle_stale_checkouts(self, now: datetime) -> tuple[int, int]:
        """Pending pages older than ``checkout_pending_hours``. The provider is asked about each before anything is
        deleted: a page that was paid while every webhook failed (a wrong signing secret answers each delivery 400, so
        no receipt row exists to retry) is applied or credited here, never dropped."""
        cutoff = now - timedelta(hours=self.config.checkout_pending_hours)
        expired = recovered = 0
        async with self.db() as db:
            stale = (
                await db.execute(
                    select(Subscription.id, Subscription.account_id, Subscription.provider_checkout_session_id)
                    .where(Subscription.state == "checkout_pending", Subscription.updated_at < cutoff)
                    .limit(RECONCILE_FETCH_LIMIT)
                )
            ).all()
            purchases = (
                await db.execute(
                    select(TopupPurchase.id, TopupPurchase.account_id, TopupPurchase.provider_checkout_session_id)
                    .where(TopupPurchase.status == "pending", TopupPurchase.created_at < cutoff)
                    .limit(RECONCILE_FETCH_LIMIT)
                )
            ).all()
        for row_id, account_id, session_id in stale:
            found = await self._stale_page(session_id, now)
            if found == "keep":
                continue
            if isinstance(found, CheckoutSession) and found.subscription_id:
                try:
                    await self._refresh(found.subscription_id, account_id)
                except ProviderError as exc:
                    self._provider_failed(exc, "reconcile_checkout")
                    continue
                log.warning("billing.webhook_missed", account_id=str(account_id))
                recovered += 1
                continue
            async with transaction(self.db) as db:
                row = await db.get(Subscription, row_id, with_for_update=True)
                if (
                    row is not None
                    and row.state == "checkout_pending"
                    and row.provider_checkout_session_id == session_id
                ):
                    await db.delete(row)
                    expired += 1
        for purchase_id, account_id, session_id in purchases:
            found = await self._stale_page(session_id, now)
            if found == "keep":
                continue
            if isinstance(found, CheckoutSession) and found.payment_status == "paid":
                await self._credit_topup(account_id, found)
                log.warning("billing.webhook_missed", account_id=str(account_id))
                recovered += 1
                continue
            async with transaction(self.db) as db:
                purchase = await db.get(TopupPurchase, purchase_id, with_for_update=True)
                if purchase is not None and purchase.status == "pending":
                    purchase.status = "expired"
        return expired, recovered

    async def _retry_events(self, now: datetime) -> int:
        if not self.enabled:
            return 0
        stale_received = now - timedelta(minutes=STALE_RECEIVED_MINUTES)
        async with self.db() as db:
            rows = (
                await db.execute(
                    select(BillingEvent.id, BillingEvent.event_type, BillingEvent.kind, BillingEvent.provider_object_id)
                    .where(
                        ((BillingEvent.status == "failed") & (BillingEvent.attempts < MAX_EVENT_ATTEMPTS))
                        | ((BillingEvent.status == "received") & (BillingEvent.received_at < stale_received))
                    )
                    .order_by(BillingEvent.received_at)
                    .limit(RECONCILE_FETCH_LIMIT)
                )
            ).all()
        retried = 0
        for row_id, event_type, kind, object_id in rows:
            if not object_id:
                await self._mark(int(row_id), "ignored", None, "no_object_id", now)
                continue
            event = WebhookEvent(
                id="",
                type=event_type,
                created=now,
                customer_id=None,
                subscription_id=object_id if kind == "subscription" else None,
                checkout_session_id=object_id if kind == "checkout" else None,
                invoice_id=None,
                payment_intent_id=None,
                payload_digest="",
            )
            try:
                status, account_id, error = await self._dispatch(event)
            except Exception as exc:  # noqa: BLE001 - counted, retried next pass up to the attempt limit
                await self._mark(int(row_id), "failed", None, type(exc).__name__, now)
                log.warning("billing.retry_failed", event_type=event_type, error_class=type(exc).__name__)
                continue
            await self._mark(int(row_id), status, account_id, error, now)
            retried += 1
        return retried

    async def _retry_deletions(self, now: datetime) -> int:
        async with self.db() as db:
            pending = (
                await db.scalars(select(PendingDeletion.account_id).where(PendingDeletion.completed_at.is_(None)))
            ).all()
        done = 0
        for account_id in pending:
            async with self.db() as db:
                if await self.cancel_for_account_deletion(db, account_id) != "pending":
                    done += 1
                await db.commit()
        return done

    async def _refresh_live_rows(self, now: datetime) -> tuple[int, int]:
        async with self.db() as db:
            live = (
                await db.execute(
                    select(
                        Subscription.account_id,
                        Subscription.provider_subscription_id,
                        Subscription.plan,
                        Subscription.state,
                        Subscription.grace_until,
                        Account.plan,
                    )
                    .join(Account, Account.id == Subscription.account_id)
                    .where(
                        Subscription.state.not_in(("canceled", "checkout_pending")),
                        Subscription.provider_subscription_id.is_not(None),
                        Account.deleted_at.is_(None),
                    )
                    .order_by(Subscription.provider_synced_at.asc().nulls_first())
                    .limit(RECONCILE_FETCH_LIMIT)
                )
            ).all()
        refreshed = mismatches = 0
        for account_id, sub_id, row_plan, row_state, grace_until, account_plan in live:
            expected = state.plan_for_state(row_state, row_plan, grace_until, now)
            if expected != account_plan:
                mismatches += 1
                log.warning(
                    "billing.entitlement_mismatch",
                    account_id=str(account_id),
                    row_plan=expected,
                    account_plan=account_plan,
                )
            try:
                touched = await self._apply_current(account_id, str(sub_id), reason="reconcile")
            except ProviderError as exc:
                self._provider_failed(exc, "reconcile")
                continue
            await self._push(touched)
            refreshed += 1
        return refreshed, mismatches

    # ----- account deletion (BILLING §11; the HTTP route is not built yet) -------------------------------------------
    async def cancel_for_account_deletion(
        self, db: AsyncSession, account_id: uuid.UUID
    ) -> Literal["canceled", "pending", "none"]:
        """Cancel the provider subscription at once. A failed provider call leaves a ``pending_deletions`` row the
        reconciler retries, so the product never claims a cancellation it did not finish."""
        now = self.config.clock()
        row = await state.live_subscription(db, account_id)
        if row is None or row.state == "canceled" or not row.provider_subscription_id:
            await self._complete_deletion(db, account_id, now)
            return "none"
        try:
            self._require_enabled()
            assert self.provider is not None
            await self.provider.cancel_subscription_now(row.provider_subscription_id)
        except (ProviderError, ApiError) as exc:
            reason = exc.reason if isinstance(exc, ProviderError) else "billing_not_available"
            pending = await db.scalar(
                select(PendingDeletion).where(PendingDeletion.account_id == account_id).with_for_update()
            )
            if pending is None:
                pending = PendingDeletion(
                    account_id=account_id, requested_at=now, provider_cancel_state="pending", attempts=0
                )
                db.add(pending)
            pending.attempts += 1
            pending.last_error = reason
            await db.flush()
            log.warning(
                "billing.deletion_pending", account_id=str(account_id), attempts=pending.attempts, reason=reason
            )
            return "pending"
        await db.commit()
        touched = await self._apply_forced(account_id, reason="account_deleted", forced_state="canceled")
        await self._push(touched)
        await self._complete_deletion(db, account_id, now)
        log.info("billing.deletion_canceled", account_id=str(account_id))
        return "canceled"

    async def _complete_deletion(self, db: AsyncSession, account_id: uuid.UUID, now: datetime) -> None:
        pending = await db.scalar(select(PendingDeletion).where(PendingDeletion.account_id == account_id))
        if pending is not None and pending.completed_at is None:
            pending.provider_cancel_state = "canceled"
            pending.completed_at = now
            await db.flush()

    # ----- lifecycle --------------------------------------------------------------------------------------------------
    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self.config.reconcile_interval_seconds)
            try:
                await self.reconcile()
            except Exception as exc:  # noqa: BLE001 - the next pass retries; never let the loop die
                log.warning("billing.reconcile_failed", error_class=type(exc).__name__)

    async def start(self) -> None:
        self._reconciler = asyncio.create_task(self._loop(), name="billing-reconciler")

    async def stop(self) -> None:
        if self._reconciler is not None:
            self._reconciler.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reconciler
            self._reconciler = None

    async def aclose(self) -> None:
        if self.provider is not None:
            await self.provider.aclose()
