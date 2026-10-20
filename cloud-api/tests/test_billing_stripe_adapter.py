"""The Stripe adapter without the network (docs/BILLING.md §12): webhook verification through the SDK's own
``construct_event`` with headers made by the SDK's signature helper and by the fake provider (so the fake signs
exactly as Stripe does), the id and period normalisation from SDK objects, the Checkout parameters sent, and errors
that carry a reason only. Not exercised against Stripe test mode from here."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest
import stripe

from dome_api.billing.fake_provider import sign, verify
from dome_api.billing.provider import ProviderError
from dome_api.billing.stripe_provider import (
    StripeProvider,
    _translate,
    checkout_from_stripe,
    event_from_stripe,
    invoice_subscription_id,
    subscription_from_stripe,
)
from dome_api.settings import Settings

SECRET = "whsec_" + "adaptertest" * 3
KEY = "sk_test_" + "adapterkey" * 3


def settings() -> Settings:
    return Settings(
        env="test",
        public_origin="http://127.0.0.1:1",
        database_url="postgresql+psycopg://dome@/unused",
        session_secret="test-session-secret-0123456789",
        oidc_issuer="http://idp.invalid",
        oidc_client_id="c",
        oidc_client_secret="s",
        entitlement_signing_key_pem_path=Path("/nonexistent.pem"),
        stripe_secret_key=KEY,
        stripe_webhook_secret=SECRET,
        stripe_price_pro_monthly="price_adapter_pm",
        stripe_price_pro_annual="price_adapter_pa",
        stripe_price_pro_plus_monthly="price_adapter_ppm",
        stripe_price_pro_plus_annual="price_adapter_ppa",
        stripe_price_topup="price_adapter_top",
    )


def event_body(obj: dict[str, Any], type_: str = "invoice.paid") -> bytes:
    body = {
        "id": "evt_adapter_1",
        "object": "event",
        "type": type_,
        "created": int(time.time()),
        "data": {"object": obj},
    }
    return json.dumps(body).encode()


INVOICE = {
    "id": "in_1",
    "object": "invoice",
    "customer": "cus_1",
    "parent": {"type": "subscription_details", "subscription_details": {"subscription": "sub_1"}},
}


def test_sdk_signed_delivery_parses_to_ids_only() -> None:
    provider = StripeProvider(settings())
    raw = event_body(INVOICE)
    header = stripe.WebhookSignature.generate_signature_header(raw.decode(), SECRET)
    event = provider.parse_webhook(raw, header)
    assert (event.id, event.type, event.customer_id, event.subscription_id, event.invoice_id) == (
        "evt_adapter_1",
        "invoice.paid",
        "cus_1",
        "sub_1",
        "in_1",
    )
    assert event.checkout_session_id is None and len(event.payload_digest) == 43


def test_the_fake_signs_exactly_like_stripe() -> None:
    """The fake's header verifies with the SDK, and the SDK's header verifies with the fake: tests that use the fake
    exercise the same signature scheme production does."""
    provider = StripeProvider(settings())
    raw = event_body(INVOICE)
    assert provider.parse_webhook(raw, sign(raw, secret=SECRET)).subscription_id == "sub_1"
    sdk_header = stripe.WebhookSignature.generate_signature_header(raw.decode(), SECRET)
    verify(raw, sdk_header, secret=SECRET, tolerance=300)


@pytest.mark.parametrize(
    "header",
    [
        None,
        "",
        "t=1,v1=00",
        sign(event_body(INVOICE), secret="whsec_" + "another"),
        sign(event_body(INVOICE), secret=SECRET, at=int(time.time()) - 301),  # outside the 300 s tolerance
    ],
)
def test_bad_or_stale_signatures_are_bad_request(header: str | None) -> None:
    provider = StripeProvider(settings())
    with pytest.raises(ProviderError) as ei:
        provider.parse_webhook(event_body(INVOICE), header)
    assert ei.value.reason == "bad_request" and str(ei.value) == "bad_request"


def test_a_tampered_body_is_refused() -> None:
    provider = StripeProvider(settings())
    header = sign(event_body(INVOICE), secret=SECRET)
    tampered = event_body({**INVOICE, "customer": "cus_attacker"})
    with pytest.raises(ProviderError):
        provider.parse_webhook(tampered, header)
    with pytest.raises(ProviderError):  # signed garbage is still refused
        provider.parse_webhook(b"not json", sign(b"not json", secret=SECRET))


def test_invoice_subscription_id_in_both_api_shapes() -> None:
    new = stripe.StripeObject.construct_from(INVOICE, KEY)
    old = stripe.StripeObject.construct_from({"id": "in_2", "object": "invoice", "subscription": "sub_2"}, KEY)
    none = stripe.StripeObject.construct_from({"id": "in_3", "object": "invoice"}, KEY)
    assert (invoice_subscription_id(new), invoice_subscription_id(old), invoice_subscription_id(none)) == (
        "sub_1",
        "sub_2",
        None,
    )


def test_subscription_snapshot_reads_the_item_period_and_price() -> None:
    sub = stripe.Subscription.construct_from(
        {
            "id": "sub_1",
            "object": "subscription",
            "customer": "cus_1",
            "status": "past_due",
            "cancel_at_period_end": True,
            "metadata": {"dome_account_id": "acct"},
            "items": {
                "object": "list",
                "data": [
                    {
                        "id": "si_1",
                        "object": "subscription_item",
                        "price": {"id": "price_adapter_pm", "object": "price"},
                        "current_period_start": 1_800_000_000,
                        "current_period_end": 1_802_592_000,
                    }
                ],
            },
        },
        KEY,
    )
    snap = subscription_from_stripe(sub)
    assert (snap.id, snap.customer_id, snap.status, snap.price_id, snap.cancel_at_period_end) == (
        "sub_1",
        "cus_1",
        "past_due",
        "price_adapter_pm",
        True,
    )
    assert snap.current_period_end is not None and int(snap.current_period_end.timestamp()) == 1_802_592_000
    assert dict(snap.metadata) == {"dome_account_id": "acct"}


def test_checkout_session_normalisation() -> None:
    s = stripe.checkout.Session.construct_from(
        {
            "id": "cs_1",
            "object": "checkout.session",
            "url": "https://checkout.stripe.com/c/pay/cs_1",
            "expires_at": 1_800_000_000,
            "mode": "payment",
            "status": "complete",
            "payment_status": "paid",
            "customer": {"id": "cus_1", "object": "customer"},
            "subscription": None,
            "payment_intent": "pi_1",
            "metadata": {"dome_kind": "topup"},
        },
        KEY,
    )
    out = checkout_from_stripe(s)
    assert (out.id, out.mode, out.status, out.payment_status, out.customer_id, out.payment_intent_id) == (
        "cs_1",
        "payment",
        "complete",
        "paid",
        "cus_1",
        "pi_1",
    )
    assert out.metadata == {"dome_kind": "topup"}


def test_checkout_event_ids() -> None:
    raw = event_body(
        {"id": "cs_9", "object": "checkout.session", "customer": "cus_9", "subscription": "sub_9"},
        "checkout.session.completed",
    )
    event = event_from_stripe(stripe.Event.construct_from(json.loads(raw), KEY), raw)
    assert (event.checkout_session_id, event.subscription_id, event.customer_id) == ("cs_9", "sub_9", "cus_9")


async def test_checkout_parameters_sent_to_stripe(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = StripeProvider(settings())
    sent: dict[str, Any] = {}

    async def create_async(params: Any = None, options: Any = None) -> Any:
        sent.update(params=params, options=options)
        return stripe.checkout.Session.construct_from(
            {
                "id": "cs_new",
                "object": "checkout.session",
                "url": "https://checkout.stripe.com/c/pay/cs_new",
                "expires_at": 1_800_000_000,
                "mode": "subscription",
                "status": "open",
                "payment_status": "unpaid",
                "customer": "cus_1",
            },
            KEY,
        )

    monkeypatch.setattr(provider._client.v1.checkout.sessions, "create_async", create_async)
    from datetime import UTC, datetime

    out = await provider.create_checkout(
        customer_id="cus_1",
        price_id="price_adapter_pm",
        mode="subscription",
        success_url="https://domeremote.com/app/billing?checkout=return&session_id={CHECKOUT_SESSION_ID}",
        cancel_url="https://domeremote.com/app/billing?checkout=cancel",
        idempotency_key="checkout:acct:1",
        metadata={"dome_account_id": "acct", "dome_kind": "subscription"},
        automatic_tax=False,
        expires_at=datetime.fromtimestamp(1_800_000_000, UTC),
    )
    assert out.id == "cs_new" and out.url and out.status == "open"
    params = sent["params"]
    assert params["mode"] == "subscription" and params["line_items"] == [{"price": "price_adapter_pm", "quantity": 1}]
    assert params["customer"] == "cus_1" and params["client_reference_id"] == "acct"
    assert params["allow_promotion_codes"] is False and params["automatic_tax"] == {"enabled": False}
    assert params["subscription_data"] == {"metadata": {"dome_account_id": "acct"}}
    assert params["expires_at"] == 1_800_000_000
    assert sent["options"] == {"idempotency_key": "checkout:acct:1"}


def test_errors_carry_a_reason_never_the_key() -> None:
    auth = stripe.AuthenticationError(f"Invalid API Key provided: {KEY}", http_status=401)
    missing = stripe.InvalidRequestError("No such subscription", param="id", http_status=404)  # type: ignore[no-untyped-call]
    down = stripe.APIConnectionError("connection reset")  # type: ignore[no-untyped-call]
    out = [_translate(e) for e in (auth, missing, down, stripe.APIError("boom", http_status=500))]
    assert [e.reason for e in out] == ["auth", "not_found", "timeout", "provider_error"]
    assert all(KEY not in str(e) and KEY not in repr(e) for e in out)
    assert out[0].status == 401 and out[3].status == 500


async def test_expire_checkout_tolerates_a_page_that_is_already_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = StripeProvider(settings())
    calls: list[str] = []

    async def closed(session_id: str) -> Any:
        calls.append(session_id)
        raise stripe.InvalidRequestError("Only open sessions can be expired", param=None, http_status=400)  # type: ignore[no-untyped-call]

    monkeypatch.setattr(provider._client.v1.checkout.sessions, "expire_async", closed)
    await provider.expire_checkout("cs_done")
    assert calls == ["cs_done"]

    async def down(session_id: str) -> Any:
        raise stripe.APIError("boom", http_status=500)

    monkeypatch.setattr(provider._client.v1.checkout.sessions, "expire_async", down)
    with pytest.raises(ProviderError) as ei:
        await provider.expire_checkout("cs_open")
    assert ei.value.reason == "provider_error"
