"""The interface between the billing service and a payment provider (docs/BILLING.md §5–§6).

A provider creates customers, hosted Checkout and Portal pages, fetches the current subscription or Checkout Session
and verifies webhook signatures. It never sees a database, a session or a plan decision: the service attributes
objects to accounts and ``billing/state.py`` decides the plan from what the provider says *now*. Errors carry a
reason, a status and the provider's request id only: never a key, a body or a customer's email.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Protocol

ProviderErrorReason = Literal["timeout", "provider_error", "auth", "bad_request", "not_found"]
CheckoutMode = Literal["subscription", "payment"]
CheckoutStatus = Literal["open", "complete", "expired"]
PaymentStatus = Literal["paid", "unpaid", "no_payment_required"]


class ProviderError(Exception):
    """A provider call that did not produce its object. The text is the reason only."""

    def __init__(
        self, reason: ProviderErrorReason, *, status: int | None = None, request_id: str | None = None
    ) -> None:
        super().__init__(reason)
        self.reason: ProviderErrorReason = reason
        self.status = status
        self.request_id = request_id


@dataclass(frozen=True, slots=True)
class CustomerRef:
    id: str


@dataclass(frozen=True, slots=True)
class CheckoutSession:
    id: str
    url: str | None  # None once the session is complete or expired
    expires_at: datetime
    mode: CheckoutMode
    status: CheckoutStatus
    payment_status: PaymentStatus
    customer_id: str | None
    subscription_id: str | None
    payment_intent_id: str | None
    metadata: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SubscriptionSnapshot:
    """The provider's subscription as it is right now (never from an event payload)."""

    id: str
    customer_id: str
    status: str  # the provider's own status word (incomplete, trialing, active, past_due, unpaid, canceled, ...)
    price_id: str | None
    current_period_start: datetime | None
    current_period_end: datetime | None
    cancel_at_period_end: bool
    metadata: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class WebhookEvent:
    """A verified delivery: ids only. The handler fetches the objects it needs; the payload is never kept."""

    id: str
    type: str
    created: datetime
    customer_id: str | None
    subscription_id: str | None
    checkout_session_id: str | None
    invoice_id: str | None
    payment_intent_id: str | None
    payload_digest: str  # base64url(SHA-256(raw body)), for the billing_events row


class BillingProvider(Protocol):
    name: Literal["stripe", "fake"]

    async def ensure_customer(self, account_id: str, email: str) -> CustomerRef: ...

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
    ) -> CheckoutSession: ...

    async def retrieve_checkout(self, session_id: str) -> CheckoutSession: ...

    async def expire_checkout(self, session_id: str) -> None: ...

    async def create_portal(self, customer_id: str, return_url: str) -> str: ...

    async def retrieve_subscription(self, subscription_id: str) -> SubscriptionSnapshot: ...

    async def cancel_subscription_now(self, subscription_id: str) -> None: ...

    def parse_webhook(self, raw: bytes, signature_header: str | None) -> WebhookEvent: ...

    async def aclose(self) -> None: ...
