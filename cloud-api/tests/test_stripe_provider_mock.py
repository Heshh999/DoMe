"""Every request DoMe's Stripe adapter sends, checked by stripe-mock (docs/BILLING.md §12): Stripe's official mock API,
which validates each request's parameter names, types, enums and required fields against Stripe's OpenAPI spec and
answers with fixture objects of the same API version. A 400 from it is a parameter Stripe's API would refuse.

Skipped unless ``DOME_TEST_STRIPE_MOCK_URL`` names a running stripe-mock. CI (.github/workflows/ci.yml) starts v0.206.0
with ``-strict-version-check``, so a request in any API version other than the one it embeds (the one the pinned SDK
sends, 2026-09-30.endive) is refused as well. To run it locally::

    go install github.com/stripe/stripe-mock@v0.206.0
    "$(go env GOPATH)/bin/stripe-mock" -http-addr 127.0.0.1:12111 -https-addr 127.0.0.1:12112 -strict-version-check &
    DOME_TEST_STRIPE_MOCK_URL=http://127.0.0.1:12111 uv run pytest tests/test_stripe_provider_mock.py

The arguments are the billing service's own: one test records every provider call the service makes through a whole
customer life with the fake provider (two Checkout pages, payment, webhooks, Portal, top-up, account deletion) and
replays each call against the adapter; another runs the Checkout, status and Portal routes with the adapter itself in
place of the fake. stripe-mock knows no account: a price's existence and type, the account name Checkout requires and
the expires_at window are not checked here (the fake provider checks the window, ``test_billing_checkout.py``)."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import uuid
from collections.abc import AsyncIterator, Awaitable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest
import stripe
from stripe import StripeClient

from dome_api.billing.fake_provider import FakeStripe
from dome_api.billing.provider import ProviderError
from dome_api.billing.service import BillingService, checkout_expiry
from dome_api.billing.stripe_provider import TIMEOUT_SECONDS, StripeProvider
from dome_api.settings import Settings
from dome_api.util import utcnow
from tests.conftest import Browser, Env, deliver_all, fake_stripe, sql

MOCK_URL = os.environ.get("DOME_TEST_STRIPE_MOCK_URL", "").rstrip("/")
pytestmark = pytest.mark.skipif(
    not MOCK_URL, reason="DOME_TEST_STRIPE_MOCK_URL is not set (a running stripe-mock; see the module docstring)"
)

KEY = "sk_test_" + "x" * 24  # stripe-mock accepts any test-mode key
SECRET = "whsec_" + "mocktest" * 3
PROVIDER_METHODS = (
    "ensure_customer",
    "create_checkout",
    "retrieve_checkout",
    "expire_checkout",
    "create_portal",
    "retrieve_subscription",
    "cancel_subscription_now",
)
Call = tuple[str, tuple[Any, ...], dict[str, Any]]


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
        stripe_price_pro_monthly="price_mock_pm",
        stripe_price_pro_annual="price_mock_pa",
        stripe_price_pro_plus_monthly="price_mock_ppm",
        stripe_price_pro_plus_annual="price_mock_ppa",
        stripe_price_topup="price_mock_top",
    )


@pytest.fixture
async def mock_client() -> AsyncIterator[StripeClient]:
    """A client built like StripeProvider's own (HTTPX, SDK retries off, the same timeout), aimed at stripe-mock."""
    http = stripe.HTTPXClient(timeout=TIMEOUT_SECONDS)
    yield StripeClient(KEY, max_network_retries=0, http_client=http, base_addresses={"api": MOCK_URL})
    await http.close_async()  # type: ignore[no-untyped-call]


@pytest.fixture
def adapter(mock_client: StripeClient) -> StripeProvider:
    return StripeProvider(settings(), client=mock_client)


async def accepted[T](what: str, call: Awaitable[T]) -> T:
    """One adapter call; a refusal fails the test with stripe-mock's own explanation (which parameter, what is wrong),
    kept as the context of the ProviderError the adapter raised."""
    try:
        return await call
    except ProviderError as exc:
        pytest.fail(f"stripe-mock refused {what}: {exc.reason} (HTTP {exc.status}): {exc.__context__}")


def record(fake: FakeStripe, monkeypatch: pytest.MonkeyPatch) -> list[Call]:
    """Every provider call the billing service makes from now on, with its exact arguments (the fake still answers)."""
    calls: list[Call] = []
    for name in PROVIDER_METHODS:
        original = getattr(fake, name)

        async def recorded(*args: Any, _name: str = name, _original: Any = original, **kwargs: Any) -> Any:
            calls.append((_name, args, kwargs))
            return await _original(*args, **kwargs)

        monkeypatch.setattr(fake, name, recorded)
    return calls


def wire(obj: Any) -> dict[str, Any]:
    """An object exactly as stripe-mock sent it (the raw JSON, as it would sit in an event's ``data.object``)."""
    assert obj.last_response is not None
    body: dict[str, Any] = json.loads(obj.last_response.body)
    return body


def signed(body: dict[str, Any]) -> tuple[bytes, str]:
    """A delivery signed by Stripe's documented scheme, computed here rather than by the SDK that verifies it."""
    raw = json.dumps(body).encode()
    t = int(time.time())
    mac = hmac.new(SECRET.encode(), f"{t}.".encode() + raw, hashlib.sha256).hexdigest()
    return raw, f"t={t},v1={mac}"


async def test_every_call_the_billing_service_makes_is_valid_for_stripe(
    env: Env, billing: BillingService, alice: Browser, adapter: StripeProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = fake_stripe(billing)
    calls = record(fake, monkeypatch)
    await alice.post("/v1/billing/checkout", {"plan": "pro", "interval": "monthly"}, schema="billing_checkout_response")
    # another choice: the open page is fetched and closed at the provider before the new one is created
    second = await alice.post(
        "/v1/billing/checkout", {"plan": "pro_plus", "interval": "annual"}, schema="billing_checkout_response"
    )
    await deliver_all(env, fake.complete_checkout(second["session_id"]))  # fetches the page and the subscription
    await alice.post("/v1/billing/portal", {}, schema="billing_portal_response")
    await alice.post("/v1/billing/topup", {}, schema="billing_checkout_response")
    async with env.services.db() as db:
        assert await billing.cancel_for_account_deletion(db, uuid.UUID(alice.account_id)) == "canceled"
        await db.commit()
    assert {name for name, _, _ in calls} == set(PROVIDER_METHODS)
    modes = sorted(kwargs["mode"] for name, _, kwargs in calls if name == "create_checkout")
    assert modes == ["payment", "subscription", "subscription"]
    for name, args, kwargs in calls:
        await accepted(name, getattr(adapter, name)(*args, **kwargs))


async def test_checkout_status_and_portal_routes_with_the_stripe_adapter(
    env: Env, billing: BillingService, alice: Browser, adapter: StripeProvider
) -> None:
    """The routes with the real adapter in place of the fake: the service's request, the SDK's form encoding and the
    response handling (customer and session ids, the hosted URL, the expiry Stripe echoes back) end to end."""
    billing.provider = adapter
    before = utcnow()
    out = await alice.post(
        "/v1/billing/checkout", {"plan": "pro", "interval": "monthly"}, schema="billing_checkout_response"
    )
    assert out["kind"] == "subscription" and out["session_id"].startswith("cs_")
    assert out["checkout_url"].startswith("https://")
    [(customer_id, session_id, expires_at)] = sql(
        env.database_url,
        "SELECT provider_customer_id, provider_checkout_session_id, checkout_expires_at FROM subscriptions "
        "WHERE account_id = %s",
        (alice.account_id,),
    )
    assert customer_id.startswith("cus_") and session_id == out["session_id"]
    # the stored expiry is the one sent and echoed: whole seconds, the configured 23 hours, inside Stripe's window
    assert expires_at.microsecond == 0
    assert before.replace(microsecond=0) + timedelta(hours=23) <= expires_at <= utcnow() + timedelta(hours=23)
    status = await alice.get("/v1/account/billing", schema="billing_status_response")  # asks about the open page
    assert status["entitlement_state"] == "pending" and status["portal_available"] is True
    portal = await alice.post("/v1/billing/portal", {}, schema="billing_portal_response")
    assert portal["portal_url"].startswith("https://")


async def test_checkout_parameters_for_both_modes_and_the_echoed_page(adapter: StripeProvider) -> None:
    customer = await accepted("customers.create", adapter.ensure_customer(str(uuid.uuid4()), "buyer@example.test"))
    assert customer.id.startswith("cus_")
    expires_at = checkout_expiry(utcnow(), 23)
    for mode, price in (("subscription", "price_mock_pm"), ("payment", "price_mock_top")):
        page = await accepted(
            f"checkout.sessions.create ({mode})",
            adapter.create_checkout(
                customer_id=customer.id,
                price_id=price,
                mode=cast(Any, mode),
                success_url="https://staging.domeremote.test/app/billing?checkout=return&session_id={CHECKOUT_SESSION_ID}",
                cancel_url="https://staging.domeremote.test/app/billing?checkout=cancel",
                idempotency_key=f"checkout:{uuid.uuid4()}",
                metadata={"dome_account_id": str(uuid.uuid4()), "dome_kind": "subscription", "dome_price": price},
                automatic_tax=False,
                expires_at=expires_at,
            ),
        )
        assert page.id.startswith("cs_") and page.url and page.url.startswith("https://")
        assert (page.mode, page.status, page.payment_status) == (mode, "open", "unpaid")
        assert page.expires_at == expires_at  # Stripe answers with the expiry it was sent


async def test_a_parameter_stripe_would_refuse_is_refused_here(adapter: StripeProvider) -> None:
    """The sentinel: stripe-mock really validates what the adapter sends, and its 400 reaches the service as
    ``bad_request``. If this passes against a server, the other tests' passes mean something."""
    with pytest.raises(ProviderError) as ei:
        await adapter.create_checkout(
            customer_id="cus_mock",
            price_id="price_mock_pm",
            mode=cast(Any, "subscriptions"),  # not one of Stripe's modes
            success_url="https://staging.domeremote.test/app/billing?checkout=return",
            cancel_url="https://staging.domeremote.test/app/billing?checkout=cancel",
            idempotency_key=f"checkout:{uuid.uuid4()}",
            metadata={"dome_account_id": "acct"},
            automatic_tax=False,
            expires_at=checkout_expiry(utcnow(), 23),
        )
    assert (ei.value.reason, ei.value.status) == ("bad_request", 400)
    assert "mode" in str(ei.value.__context__)  # stripe-mock names the parameter in its message


async def test_retrievals_read_the_fields_this_api_version_has(
    adapter: StripeProvider, mock_client: StripeClient
) -> None:
    """Ids, status words and the subscription item's period, read from the fixture objects of the pinned API version."""
    raw_sub = wire(await mock_client.v1.subscriptions.retrieve_async("sub_mock"))
    item = raw_sub["items"]["data"][0]
    # this API version keeps the billing period on the subscription item only
    assert "current_period_end" not in raw_sub and "current_period_end" in item
    sub = await accepted("subscriptions.retrieve", adapter.retrieve_subscription("sub_mock"))
    assert (sub.id, sub.customer_id, sub.status, sub.price_id) == (
        "sub_mock",
        raw_sub["customer"],
        raw_sub["status"],
        item["price"]["id"],
    )
    assert sub.current_period_start == datetime.fromtimestamp(item["current_period_start"], UTC)
    assert sub.current_period_end == datetime.fromtimestamp(item["current_period_end"], UTC)
    assert sub.cancel_at_period_end is raw_sub["cancel_at_period_end"] and dict(sub.metadata) == raw_sub["metadata"]
    raw_page = wire(await mock_client.v1.checkout.sessions.retrieve_async("cs_test_mock"))
    page = await accepted("checkout.sessions.retrieve", adapter.retrieve_checkout("cs_test_mock"))
    assert (page.id, page.status, page.payment_status, page.mode, page.url) == (
        "cs_test_mock",
        raw_page["status"],
        raw_page["payment_status"],
        raw_page["mode"],
        raw_page["url"],
    )
    assert page.payment_intent_id == raw_page["payment_intent"]
    assert int(page.expires_at.timestamp()) == raw_page["expires_at"]
    await accepted("checkout.sessions.expire", adapter.expire_checkout("cs_test_mock"))
    url = await accepted(
        "billing_portal.sessions.create", adapter.create_portal("cus_mock", "https://e.test/app/billing")
    )
    assert url.startswith("https://")
    await accepted("subscriptions.cancel", adapter.cancel_subscription_now("sub_mock"))


async def test_webhooks_carrying_stripe_mock_objects_parse_to_the_right_ids(
    adapter: StripeProvider, mock_client: StripeClient
) -> None:
    """Deliveries whose ``data.object`` is stripe-mock's own object for each event type DoMe handles, verified and
    parsed by the SDK: the ids the service then fetches come from where this API version puts them."""
    v1 = mock_client.v1
    sub = wire(await v1.subscriptions.retrieve_async("sub_mock"))
    invoice = wire(await v1.invoices.retrieve_async("in_mock"))
    page = wire(await v1.checkout.sessions.retrieve_async("cs_test_mock"))
    charge = wire(await v1.charges.retrieve_async("ch_mock"))
    dispute = wire(await v1.disputes.retrieve_async("dp_mock"))
    # an invoice names its subscription only under parent.subscription_details in this API version
    invoice_sub = invoice["parent"]["subscription_details"]["subscription"]
    assert "subscription" not in invoice and invoice_sub
    cases: list[tuple[str, dict[str, Any], dict[str, Any]]] = [
        ("customer.subscription.updated", sub, {"subscription_id": sub["id"], "customer_id": sub["customer"]}),
        ("invoice.paid", invoice, {"invoice_id": invoice["id"], "subscription_id": invoice_sub}),
        ("checkout.session.completed", page, {"checkout_session_id": page["id"], "subscription_id": None}),
        ("charge.refunded", charge, {"payment_intent_id": charge["payment_intent"]}),
        ("charge.dispute.created", dispute, {"payment_intent_id": dispute["payment_intent"]}),
    ]
    for i, (type_, obj, expected) in enumerate(cases):
        body = {
            "id": f"evt_mock_{i}",
            "object": "event",
            "api_version": stripe.api_version,
            "type": type_,
            "created": int(time.time()),
            "data": {"object": obj},
        }
        event = adapter.parse_webhook(*signed(body))
        assert (event.id, event.type, event.customer_id) == (body["id"], type_, obj.get("customer")), type_
        for field, value in expected.items():
            assert getattr(event, field) == value, (type_, field)
