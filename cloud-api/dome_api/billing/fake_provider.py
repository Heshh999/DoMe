"""An in-memory stand-in for Stripe (docs/BILLING.md §12). Development and tests only: settings refuse
``DOME_BILLING_PROVIDER=fake`` in staging and production and ``build_provider`` raises there.

It keeps customers, Checkout Sessions, subscriptions and invoices, signs its deliveries exactly the way Stripe does
(``t=<unix>,v1=<hex HMAC-SHA256(secret, f"{t}.{raw}")>``, tolerance checked), and its test helpers mutate the state
**and return the signed delivery** ``(raw, header)`` for the test to post to ``/v1/billing/webhook`` in any order or
any number of times. ``fail_next`` names methods that raise ``ProviderError("provider_error")`` once. Like Stripe, it
refuses a Checkout ``expires_at`` outside 30 minutes to 24 hours from its own clock (``bad_request``, param
``expires_at``), and a customer id it does not know (``bad_request``, ``resource_missing``, param ``customer``: what
Stripe answers for a customer of another account, sandbox or mode).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from dome_protocol.keys import b64url_encode

from dome_api.billing.provider import (
    CheckoutMode,
    CheckoutSession,
    CheckoutStatus,
    CustomerRef,
    PaymentStatus,
    ProviderError,
    SubscriptionSnapshot,
    WebhookEvent,
)
from dome_api.util import utcnow

FAKE_WEBHOOK_SECRET = "whsec_fake"  # noqa: S105 - a fixture, not a credential
Delivery = tuple[bytes, str]


@dataclass(slots=True)
class _Customer:
    id: str
    account_id: str
    email: str  # kept only here, so tests can prove it never leaves the fake


@dataclass(slots=True)
class _Checkout:
    id: str
    customer_id: str
    price_id: str
    mode: CheckoutMode
    status: CheckoutStatus
    payment_status: PaymentStatus
    expires_at: datetime
    metadata: dict[str, str]
    subscription_id: str | None = None
    payment_intent_id: str | None = None
    idempotency_key: str = ""


@dataclass(slots=True)
class _Subscription:
    id: str
    customer_id: str
    status: str
    price_id: str
    period_start: datetime
    period_end: datetime
    cancel_at_period_end: bool = False
    metadata: dict[str, str] = field(default_factory=dict)
    open_invoice_id: str | None = None


def sign(raw: bytes, *, secret: str = FAKE_WEBHOOK_SECRET, at: int | None = None) -> str:
    t = int(time.time()) if at is None else at
    mac = hmac.new(secret.encode(), f"{t}.".encode() + raw, hashlib.sha256).hexdigest()
    return f"t={t},v1={mac}"


def verify(raw: bytes, header: str | None, *, secret: str, tolerance: int, now: int | None = None) -> None:
    """Stripe's scheme: ``t`` within the tolerance and one ``v1`` matching HMAC-SHA256 over ``t.raw``."""
    if not header:
        raise ProviderError("bad_request")
    parts = dict(p.split("=", 1) for p in header.split(",") if "=" in p)
    try:
        t = int(parts["t"])
    except (KeyError, ValueError):
        raise ProviderError("bad_request") from None
    if abs((int(time.time()) if now is None else now) - t) > tolerance:
        raise ProviderError("bad_request")
    expected = hmac.new(secret.encode(), f"{t}.".encode() + raw, hashlib.sha256).hexdigest()
    candidates = [v for k, v in (p.split("=", 1) for p in header.split(",") if "=" in p) if k == "v1"]
    if not any(hmac.compare_digest(c, expected) for c in candidates):
        raise ProviderError("bad_request")


def payload_digest(raw: bytes) -> str:
    digest: str = b64url_encode(hashlib.sha256(raw).digest())
    return digest


class FakeStripe:
    name: Literal["stripe", "fake"] = "fake"
    MONTHLY = timedelta(days=30)

    def __init__(
        self, *, clock: Callable[[], datetime] = utcnow, secret: str = FAKE_WEBHOOK_SECRET, tolerance: int = 300
    ) -> None:
        self.clock = clock
        self.secret = secret
        self.tolerance = tolerance
        self.customers: dict[str, _Customer] = {}
        self.checkouts: dict[str, _Checkout] = {}
        self.subscriptions: dict[str, _Subscription] = {}
        self.portal_sessions: list[tuple[str, str]] = []
        self.fail_next: list[str] = []
        self.calls: list[str] = []
        self.closed = False
        self._events = 0

    # ----- helpers --------------------------------------------------------------------------------------------------
    def _maybe_fail(self, method: str) -> None:
        self.calls.append(method)
        if method in self.fail_next:
            self.fail_next.remove(method)
            raise ProviderError("provider_error", status=500, request_id="req_fake_fail")

    def _id(self, prefix: str) -> str:
        return f"{prefix}_{secrets.token_hex(8)}"

    def _snapshot(self, sub: _Subscription) -> SubscriptionSnapshot:
        return SubscriptionSnapshot(
            id=sub.id,
            customer_id=sub.customer_id,
            status=sub.status,
            price_id=sub.price_id,
            current_period_start=sub.period_start,
            current_period_end=sub.period_end,
            cancel_at_period_end=sub.cancel_at_period_end,
            metadata=dict(sub.metadata),
        )

    def _checkout(self, c: _Checkout) -> CheckoutSession:
        return CheckoutSession(
            id=c.id,
            url=f"https://checkout.fake.test/c/pay/{c.id}" if c.status == "open" else None,
            expires_at=c.expires_at,
            mode=c.mode,
            status=c.status,
            payment_status=c.payment_status,
            customer_id=c.customer_id,
            subscription_id=c.subscription_id,
            payment_intent_id=c.payment_intent_id,
            metadata=dict(c.metadata),
        )

    # ----- BillingProvider -------------------------------------------------------------------------------------------
    async def ensure_customer(self, account_id: str, email: str) -> CustomerRef:
        self._maybe_fail("ensure_customer")
        for c in self.customers.values():
            if c.account_id == account_id:
                return CustomerRef(c.id)
        c = _Customer(self._id("cus"), account_id, email)
        self.customers[c.id] = c
        return CustomerRef(c.id)

    async def create_checkout(
        self,
        *,
        customer_id: str,
        price_id: str,
        mode: CheckoutMode,
        success_url: str,
        cancel_url: str,
        idempotency_key: str,
        metadata: Mapping[str, str],
        automatic_tax: bool,
        expires_at: datetime,
    ) -> CheckoutSession:
        self._maybe_fail("create_checkout")
        if customer_id not in self.customers:
            raise ProviderError("bad_request", status=400, code="resource_missing", param="customer")
        for existing in self.checkouts.values():
            if existing.idempotency_key == idempotency_key:  # Stripe replays the first response for the same key
                return self._checkout(existing)
        # Stripe's rule, by its own clock: expires_at 30 minutes to 24 hours after the session is created
        if not timedelta(minutes=30) <= expires_at - self.clock() <= timedelta(hours=24):
            raise ProviderError("bad_request", status=400, param="expires_at")
        c = _Checkout(
            id=self._id("cs_test"),
            customer_id=customer_id,
            price_id=price_id,
            mode=mode,
            status="open",
            payment_status="unpaid",
            expires_at=expires_at,
            metadata=dict(metadata),
            idempotency_key=idempotency_key,
        )
        self.checkouts[c.id] = c
        return self._checkout(c)

    async def retrieve_checkout(self, session_id: str) -> CheckoutSession:
        self._maybe_fail("retrieve_checkout")
        c = self.checkouts.get(session_id)
        if c is None:
            raise ProviderError("not_found", status=404)
        return self._checkout(c)

    async def expire_checkout(self, session_id: str) -> None:
        self._maybe_fail("expire_checkout")
        c = self.checkouts.get(session_id)
        if c is None:
            raise ProviderError("not_found", status=404)
        if c.status == "open":
            c.status = "expired"

    async def create_portal(self, customer_id: str, return_url: str) -> str:
        self._maybe_fail("create_portal")
        if customer_id not in self.customers:
            raise ProviderError("bad_request", status=400, code="resource_missing", param="customer")
        url = f"https://billing.fake.test/p/session/{self._id('bps')}"
        self.portal_sessions.append((customer_id, url))
        return url

    async def retrieve_subscription(self, subscription_id: str) -> SubscriptionSnapshot:
        self._maybe_fail("retrieve_subscription")
        sub = self.subscriptions.get(subscription_id)
        if sub is None:
            raise ProviderError("not_found", status=404)
        return self._snapshot(sub)

    async def cancel_subscription_now(self, subscription_id: str) -> None:
        self._maybe_fail("cancel_subscription_now")
        sub = self.subscriptions.get(subscription_id)
        if sub is None:
            raise ProviderError("not_found", status=404)
        sub.status = "canceled"

    def parse_webhook(self, raw: bytes, signature_header: str | None) -> WebhookEvent:
        verify(raw, signature_header, secret=self.secret, tolerance=self.tolerance)
        try:
            body = json.loads(raw)
            obj = body["data"]["object"]
            return WebhookEvent(
                id=str(body["id"]),
                type=str(body["type"]),
                created=datetime.fromtimestamp(int(body["created"]), UTC),
                customer_id=obj.get("customer"),
                subscription_id=obj.get("subscription") if obj.get("object") != "subscription" else obj.get("id"),
                checkout_session_id=obj.get("id") if obj.get("object") == "checkout.session" else None,
                invoice_id=obj.get("id") if obj.get("object") == "invoice" else None,
                payment_intent_id=obj.get("payment_intent"),
                payload_digest=payload_digest(raw),
            )
        except (ValueError, KeyError, TypeError, AttributeError):
            raise ProviderError("bad_request") from None

    async def aclose(self) -> None:
        self.closed = True

    # ----- test helpers: mutate state and return the signed delivery -----------------------------------------------
    def event(self, type_: str, obj: dict[str, Any], *, event_id: str | None = None, at: int | None = None) -> Delivery:
        """An arbitrary signed delivery (unknown types, unattributable ids, replays with a fixed ``event_id``)."""
        self._events += 1
        now = self.clock()
        body = {
            "id": event_id or f"evt_fake_{self._events:06d}_{secrets.token_hex(3)}",
            "object": "event",
            "type": type_,
            "created": int(now.timestamp()),
            "data": {"object": obj},
        }
        raw = json.dumps(body, separators=(",", ":")).encode()
        return raw, sign(raw, secret=self.secret, at=at)

    def _sub_obj(self, sub: _Subscription) -> dict[str, Any]:
        return {"id": sub.id, "object": "subscription", "customer": sub.customer_id, "status": sub.status}

    def _invoice_obj(self, sub: _Subscription, invoice_id: str | None = None) -> dict[str, Any]:
        return {
            "id": invoice_id or self._id("in"),
            "object": "invoice",
            "customer": sub.customer_id,
            "subscription": sub.id,
        }

    def _checkout_obj(self, c: _Checkout) -> dict[str, Any]:
        return {
            "id": c.id,
            "object": "checkout.session",
            "customer": c.customer_id,
            "subscription": c.subscription_id,
            "payment_intent": c.payment_intent_id,
            "mode": c.mode,
        }

    def complete_checkout(self, session_id: str, *, status: str = "active") -> list[Delivery]:
        """The customer paid on the hosted page: a subscription appears (``status`` active or incomplete) and Stripe
        sends ``customer.subscription.created``, ``checkout.session.completed`` and, when paid, ``invoice.paid``."""
        c = self.checkouts[session_id]
        assert c.mode == "subscription" and c.status == "open"
        now = self.clock()
        sub = _Subscription(
            id=self._id("sub"),
            customer_id=c.customer_id,
            status=status,
            price_id=c.price_id,
            period_start=now,
            period_end=now + self.MONTHLY,
            metadata={k: v for k, v in c.metadata.items() if k == "dome_account_id"},
        )
        self.subscriptions[sub.id] = sub
        c.status = "complete"
        c.subscription_id = sub.id
        c.payment_status = "paid" if status == "active" else "unpaid"
        out = [self.event("customer.subscription.created", self._sub_obj(sub))]
        out.append(self.event("checkout.session.completed", self._checkout_obj(c)))
        if status == "active":
            out.append(self.event("invoice.paid", self._invoice_obj(sub)))
        return out

    def renew(self, sub_id: str) -> Delivery:
        sub = self.subscriptions[sub_id]
        sub.period_start, sub.period_end = sub.period_end, sub.period_end + self.MONTHLY
        sub.status = "active"
        sub.open_invoice_id = None
        return self.event("invoice.paid", self._invoice_obj(sub))

    def fail_payment(self, sub_id: str) -> Delivery:
        sub = self.subscriptions[sub_id]
        sub.status = "past_due"
        sub.open_invoice_id = self._id("in")
        return self.event("invoice.payment_failed", self._invoice_obj(sub, sub.open_invoice_id))

    def pay_open_invoice(self, sub_id: str) -> Delivery:
        sub = self.subscriptions[sub_id]
        sub.status = "active"
        sub.period_start, sub.period_end = sub.period_end, sub.period_end + self.MONTHLY
        invoice_id, sub.open_invoice_id = sub.open_invoice_id, None
        return self.event("invoice.paid", self._invoice_obj(sub, invoice_id))

    def set_cancel_at_period_end(self, sub_id: str, value: bool) -> Delivery:
        sub = self.subscriptions[sub_id]
        sub.cancel_at_period_end = value
        return self.event("customer.subscription.updated", self._sub_obj(sub))

    def mark_unpaid(self, sub_id: str) -> Delivery:
        sub = self.subscriptions[sub_id]
        sub.status = "unpaid"
        return self.event("customer.subscription.updated", self._sub_obj(sub))

    def delete_subscription(self, sub_id: str) -> Delivery:
        sub = self.subscriptions[sub_id]
        sub.status = "canceled"
        return self.event("customer.subscription.deleted", self._sub_obj(sub))

    def checkout_expired(self, session_id: str) -> Delivery:
        """The hosted page timed out unpaid: Stripe sends ``checkout.session.expired``."""
        c = self.checkouts[session_id]
        c.status = "expired"
        return self.event("checkout.session.expired", self._checkout_obj(c))

    def topup_paid(self, session_id: str) -> list[Delivery]:
        """A paid one-time Checkout: Stripe sends both ``completed`` and ``async_payment_succeeded``."""
        c = self.checkouts[session_id]
        assert c.mode == "payment" and c.status == "open"
        c.status = "complete"
        c.payment_status = "paid"
        c.payment_intent_id = self._id("pi")
        return [
            self.event("checkout.session.completed", self._checkout_obj(c)),
            self.event("checkout.session.async_payment_succeeded", self._checkout_obj(c)),
        ]

    def customer_for_account(self, account_id: str) -> _Customer | None:
        return next((c for c in self.customers.values() if c.account_id == account_id), None)

    @staticmethod
    def deliveries_in_order(deliveries: list[Delivery], order: list[int]) -> list[Delivery]:
        return [deliveries[i] for i in order]
