"""The Stripe adapter (official ``stripe`` SDK, pinned in pyproject.toml; API version per the SDK's default).

Every call goes through the SDK's ``*_async`` methods with the SDK's own retries off (DoMe decides what to retry: the
reconciler re-fetches, a page creation is simply reported to the customer) and a 10-second timeout. Only ids, states
and timestamps leave this module; the key never appears in an error, a log line or an exception text (``ProviderError``
carries the reason, HTTP status and Stripe request id only). The customer's email goes to Stripe once, for receipts,
in ``ensure_customer``, and is never logged.

Not yet exercised against Stripe test mode (docs/BILLING.md §12): the request shapes and the id normalisation (an
invoice's subscription lives under ``parent.subscription_details.subscription`` in the pinned API version, the
period under the subscription item) follow the pinned SDK's models and must be confirmed by the founder's first
test-mode run.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Literal, cast

import stripe
from dome_protocol.keys import b64url_encode
from stripe import StripeClient

from dome_api.billing.provider import (
    CheckoutMode,
    CheckoutSession,
    CheckoutStatus,
    CustomerRef,
    PaymentStatus,
    ProviderError,
    ProviderErrorReason,
    SubscriptionSnapshot,
    WebhookEvent,
)
from dome_api.settings import Settings

TIMEOUT_SECONDS = 10.0


def _reason(exc: Exception) -> ProviderErrorReason:
    if isinstance(exc, stripe.AuthenticationError | stripe.PermissionError):
        return "auth"
    if isinstance(exc, stripe.InvalidRequestError):
        return "not_found" if getattr(exc, "http_status", None) == 404 else "bad_request"
    if isinstance(exc, stripe.APIConnectionError):
        return "timeout"
    return "provider_error"


def _translate(exc: Exception) -> ProviderError:
    status = getattr(exc, "http_status", None)
    request_id = getattr(exc, "request_id", None)
    return ProviderError(_reason(exc), status=int(status) if status else None, request_id=request_id)


def _ts(value: Any) -> datetime | None:
    return datetime.fromtimestamp(int(value), UTC) if value else None


def _id_of(value: Any) -> str | None:
    """Stripe returns related objects as an id string or an expanded object; the id is what the ledger keeps."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    got = getattr(value, "id", None) or (value.get("id") if isinstance(value, Mapping) else None)
    return str(got) if got else None


def _metadata(obj: Any) -> dict[str, str]:
    raw = getattr(obj, "metadata", None) or {}
    # SDK objects are no longer mappings (stripe 15+): to_dict() is the supported way to read them as one
    plain = raw.to_dict() if hasattr(raw, "to_dict") else dict(raw)
    return {str(k): str(v) for k, v in plain.items()}


def checkout_from_stripe(s: Any) -> CheckoutSession:
    status: CheckoutStatus = s.status if s.status in ("open", "complete", "expired") else "expired"
    payment_status: PaymentStatus = (
        s.payment_status if s.payment_status in ("paid", "unpaid", "no_payment_required") else "unpaid"
    )
    mode: CheckoutMode = "subscription" if s.mode == "subscription" else "payment"
    return CheckoutSession(
        id=str(s.id),
        url=getattr(s, "url", None),
        expires_at=_ts(getattr(s, "expires_at", None)) or datetime.now(UTC),
        mode=mode,
        status=status,
        payment_status=payment_status,
        customer_id=_id_of(getattr(s, "customer", None)),
        subscription_id=_id_of(getattr(s, "subscription", None)),
        payment_intent_id=_id_of(getattr(s, "payment_intent", None)),
        metadata=_metadata(s),
    )


def subscription_from_stripe(sub: Any) -> SubscriptionSnapshot:
    items = list(getattr(getattr(sub, "items", None), "data", None) or [])
    first = items[0] if items else None
    price_id = _id_of(getattr(first, "price", None)) if first is not None else None
    # the pinned API version keeps the period on the subscription item; older ones kept it on the subscription
    period_start = getattr(first, "current_period_start", None) if first is not None else None
    period_end = getattr(first, "current_period_end", None) if first is not None else None
    if period_start is None:
        period_start = getattr(sub, "current_period_start", None)
    if period_end is None:
        period_end = getattr(sub, "current_period_end", None)
    customer_id = _id_of(getattr(sub, "customer", None)) or ""
    return SubscriptionSnapshot(
        id=str(sub.id),
        customer_id=customer_id,
        status=str(sub.status),
        price_id=price_id,
        current_period_start=_ts(period_start),
        current_period_end=_ts(period_end),
        cancel_at_period_end=bool(getattr(sub, "cancel_at_period_end", False)),
        metadata=_metadata(sub),
    )


def invoice_subscription_id(obj: Any) -> str | None:
    """An invoice's subscription id: ``parent.subscription_details.subscription`` (pinned API) or the legacy
    top-level ``subscription``."""
    parent = getattr(obj, "parent", None)
    details = getattr(parent, "subscription_details", None) if parent is not None else None
    from_parent = _id_of(getattr(details, "subscription", None)) if details is not None else None
    return from_parent or _id_of(getattr(obj, "subscription", None))


def event_from_stripe(event: Any, raw: bytes) -> WebhookEvent:
    obj = event.data.object
    kind = str(getattr(obj, "object", "") or "")
    subscription_id: str | None = None
    invoice_id: str | None = None
    checkout_id: str | None = None
    payment_intent_id = _id_of(getattr(obj, "payment_intent", None))
    if kind == "subscription":
        subscription_id = str(obj.id)
    elif kind == "invoice":
        invoice_id = str(obj.id)
        subscription_id = invoice_subscription_id(obj)
    elif kind == "checkout.session":
        checkout_id = str(obj.id)
        subscription_id = _id_of(getattr(obj, "subscription", None))
    elif kind in ("charge", "dispute"):
        payment_intent_id = payment_intent_id or _id_of(getattr(obj, "payment_intent", None))
    return WebhookEvent(
        id=str(event.id),
        type=str(event.type),
        created=_ts(getattr(event, "created", None)) or datetime.now(UTC),
        customer_id=_id_of(getattr(obj, "customer", None)),
        subscription_id=subscription_id,
        checkout_session_id=checkout_id,
        invoice_id=invoice_id,
        payment_intent_id=payment_intent_id,
        payload_digest=str(b64url_encode(hashlib.sha256(raw).digest())),
    )


class StripeProvider:
    name: Literal["stripe", "fake"] = "stripe"

    def __init__(self, settings: Settings) -> None:
        self._webhook_secret = settings.stripe_webhook_secret.get_secret_value()
        self._tolerance = settings.billing_webhook_tolerance_seconds
        http_client = stripe.HTTPXClient(timeout=TIMEOUT_SECONDS)
        self._client = StripeClient(
            settings.stripe_secret_key.get_secret_value(), max_network_retries=0, http_client=http_client
        )

    async def ensure_customer(self, account_id: str, email: str) -> CustomerRef:
        try:
            customer = await self._client.v1.customers.create_async(
                params={"email": email, "metadata": {"dome_account_id": account_id}},
                options={"idempotency_key": f"customer:{account_id}"},
            )
        except stripe.StripeError as exc:
            raise _translate(exc) from None
        return CustomerRef(str(customer.id))

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
        params: dict[str, Any] = {
            "mode": mode,
            "line_items": [{"price": price_id, "quantity": 1}],
            "customer": customer_id,
            "client_reference_id": metadata.get("dome_account_id"),
            "success_url": success_url,
            "cancel_url": cancel_url,
            "metadata": dict(metadata),
            "allow_promotion_codes": False,
            "automatic_tax": {"enabled": automatic_tax},
            "expires_at": int(expires_at.timestamp()),
        }
        if mode == "subscription":
            params["subscription_data"] = {"metadata": {"dome_account_id": metadata.get("dome_account_id", "")}}
        try:
            session = await self._client.v1.checkout.sessions.create_async(
                params=cast(Any, params), options={"idempotency_key": idempotency_key}
            )
        except stripe.StripeError as exc:
            raise _translate(exc) from None
        return checkout_from_stripe(session)

    async def retrieve_checkout(self, session_id: str) -> CheckoutSession:
        try:
            session = await self._client.v1.checkout.sessions.retrieve_async(session_id)
        except stripe.StripeError as exc:
            raise _translate(exc) from None
        return checkout_from_stripe(session)

    async def expire_checkout(self, session_id: str) -> None:
        try:
            await self._client.v1.checkout.sessions.expire_async(session_id)
        except stripe.StripeError as exc:
            if _reason(exc) in ("not_found", "bad_request"):
                return  # gone, or no longer open (completed or already expired): nothing left to close
            raise _translate(exc) from None

    async def create_portal(self, customer_id: str, return_url: str) -> str:
        try:
            session = await self._client.v1.billing_portal.sessions.create_async(
                params={"customer": customer_id, "return_url": return_url}
            )
        except stripe.StripeError as exc:
            raise _translate(exc) from None
        return str(session.url)

    async def retrieve_subscription(self, subscription_id: str) -> SubscriptionSnapshot:
        try:
            sub = await self._client.v1.subscriptions.retrieve_async(subscription_id)
        except stripe.StripeError as exc:
            raise _translate(exc) from None
        return subscription_from_stripe(sub)

    async def cancel_subscription_now(self, subscription_id: str) -> None:
        try:
            await self._client.v1.subscriptions.cancel_async(subscription_id)
        except stripe.StripeError as exc:
            if _reason(exc) == "not_found":
                return  # already gone at the provider: the outcome we wanted
            raise _translate(exc) from None

    def parse_webhook(self, raw: bytes, signature_header: str | None) -> WebhookEvent:
        try:
            event = stripe.Webhook.construct_event(raw, signature_header or "", self._webhook_secret, self._tolerance)
        except stripe.SignatureVerificationError:
            raise ProviderError("bad_request") from None
        except (ValueError, stripe.StripeError):
            raise ProviderError("bad_request") from None
        return event_from_stripe(event, raw)

    async def aclose(self) -> None:
        return None
