"""Checkout and Portal pages end to end with the in-memory fake Stripe (docs/BILLING.md §5): session, CSRF and Origin;
one pending row and the same page twice; nothing unlocked by returning; activation only through a verified webhook, with
the snapshot and the assertion pushed to the online PC; Pro Plus keeps the assertion's plan claim at ``pro``; duplicate
subscriptions refused; the portal needs a customer; the per-account budget; a provider failure persists nothing."""

from __future__ import annotations

import asyncio
import dataclasses
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from joserfc import jwt
from joserfc.jwk import KeySet

from dome_api.assistant.service import AssistantService
from dome_api.billing.provider import ProviderError
from dome_api.billing.service import (
    CHECKOUT_CREATING_SECONDS,
    BillingService,
    checkout_expiry,
    checkout_expiry_replayable,
)
from dome_api.util import utcnow
from tests.conftest import SCHEMAS, AgentSim, Browser, Env, deliver, deliver_all, fake_stripe, sql


def subscription_row(env: Env, account_id: str) -> dict[str, Any]:
    cols = (
        "state, plan, interval, provider_customer_id, provider_subscription_id, provider_checkout_session_id, "
        "checkout_idempotency_key, price_id"
    )
    rows = sql(
        env.database_url,
        f"SELECT {cols} FROM subscriptions WHERE account_id = %s ORDER BY updated_at DESC LIMIT 1",
        (account_id,),
    )
    return dict(zip([c.strip() for c in cols.split(",")], rows[0], strict=True)) if rows else {}


def plan_events(env: Env, account_id: str) -> list[dict[str, Any]]:
    rows = sql(
        env.database_url,
        "SELECT detail FROM security_events WHERE account_id = %s AND kind = 'plan_changed' ORDER BY id",
        (account_id,),
    )
    return [row[0] for row in rows]


async def start_checkout(browser: Browser, plan: str = "pro", interval: str = "monthly") -> dict[str, Any]:
    out: dict[str, Any] = await browser.post(
        "/v1/billing/checkout", {"plan": plan, "interval": interval}, schema="billing_checkout_response"
    )
    return out


async def test_checkout_requires_session_csrf_origin_and_valid_body(
    env: Env, billing: BillingService, alice: Browser
) -> None:
    body = {"plan": "pro", "interval": "monthly"}
    anon = await env.http.post("/v1/billing/checkout", json=body)
    assert anon.status_code == 401 and anon.json()["error"]["code"] == "UNAUTHENTICATED"
    for csrf, origin in ((False, True), (True, False)):
        r = await alice.request("POST", "/v1/billing/checkout", body, expect=403, csrf=csrf, origin=origin)
        assert r.json()["error"]["code"] == "FORBIDDEN"
    r = await alice.request(
        "POST", "/v1/billing/checkout", body, expect=403, headers={"Origin": "https://evil.example"}
    )
    for bad in (
        {"plan": "free", "interval": "monthly"},
        {"plan": "pro", "interval": "weekly"},
        {"plan": "pro"},
        {"plan": "pro", "interval": "monthly", "price_id": "price_cheaper"},
        {"plan": "pro", "interval": "monthly", "amount": 1},
    ):
        r = await alice.request("POST", "/v1/billing/checkout", bad, expect=400)
        assert r.json()["error"]["code"] == "MALFORMED_MESSAGE"
    for path in ("/v1/billing/portal", "/v1/billing/topup"):
        r = await alice.request("POST", path, {"customer": "cus_x"}, expect=400)
        assert r.json()["error"]["code"] == "MALFORMED_MESSAGE"
    assert subscription_row(env, alice.account_id) == {}  # nothing was written by any refused request
    assert fake_stripe(billing).checkouts == {}


async def test_checkout_creates_pending_row_and_second_call_returns_same_session(
    env: Env, billing: BillingService, alice: Browser
) -> None:
    first = await start_checkout(alice)
    assert first["kind"] == "subscription" and first["checkout_url"].startswith("https://")
    row = subscription_row(env, alice.account_id)
    assert (row["state"], row["plan"], row["interval"]) == ("checkout_pending", "pro", "monthly")
    assert row["provider_checkout_session_id"] == first["session_id"] and row["checkout_idempotency_key"]
    assert row["price_id"] == "price_fake_pro_monthly" and row["provider_subscription_id"] is None
    second = await start_checkout(alice)
    assert second == first  # the open page is returned, not a second one
    assert len(fake_stripe(billing).checkouts) == 1
    # a different choice while the first page is still open gets its own page for that price
    other = await start_checkout(alice, "pro_plus", "annual")
    assert other["session_id"] != first["session_id"]
    # the replaced page is closed at the provider, so two pages can never both be paid
    assert fake_stripe(billing).checkouts[first["session_id"]].status == "expired"
    assert subscription_row(env, alice.account_id)["price_id"] == "price_fake_pro_plus_annual"
    # the fake gives every customer one id and the account only one customer
    assert len(fake_stripe(billing).customers) == 1
    status = await alice.get("/v1/account/billing", schema="billing_status_response")
    assert status["subscription"]["state"] == "checkout_pending" and status["entitlement_state"] == "pending"
    assert status["checkout_available"] is True and status["portal_available"] is True


async def test_return_url_unlocks_nothing(env: Env, billing: BillingService, alice: Browser) -> None:
    out = await start_checkout(alice)
    # the phone comes back from the hosted page with the session id in the query string: the server reads nothing
    s = await alice.get(f"/v1/session?checkout=return&session_id={out['session_id']}", schema="session_response")
    assert s["plan"] == "free" and s["entitlement_state"] == "pending" and s["limits"]["max_enabled_pcs"] == 1
    status = await alice.get("/v1/account/billing", schema="billing_status_response")
    assert status["plan"] == "free" and status["entitlement_state"] == "pending"
    assert sql(env.database_url, "SELECT plan FROM accounts WHERE id = %s", (alice.account_id,))[0][0] == "free"
    assert plan_events(env, alice.account_id) == []


async def test_checkout_completed_activates_pro_and_pushes_snapshot_with_assertion(
    env: Env, billing: BillingService, alice: Browser, online_agent: AgentSim
) -> None:
    out = await start_checkout(alice)
    deliveries = fake_stripe(billing).complete_checkout(out["session_id"])
    results = await deliver_all(env, deliveries)
    assert all(r["status"] == "processed" and r["duplicate"] is False for r in results)
    s = await alice.get("/v1/session", schema="session_response")
    assert (s["plan"], s["entitlement_state"]) == ("pro", "active")
    assert (s["limits"]["max_enabled_pcs"], s["limits"]["max_controllers"], s["limits"]["routines"]) == (5, 5, True)
    row = subscription_row(env, alice.account_id)
    assert row["state"] == "active" and row["provider_subscription_id"] and row["provider_checkout_session_id"] is None
    assert plan_events(env, alice.account_id) == [
        {"from": "free", "to": "pro", "reason": "webhook", "subscription_state": "active"}
    ]
    snap = await online_agent.recv_type("grants_snapshot")
    assert snap["pc_enabled"] is True and "entitlement_assertion" in snap
    jwks = (await env.http.get("/.well-known/dome-jwks.json")).json()
    claims = jwt.decode(snap["entitlement_assertion"], KeySet.import_key_set(jwks), algorithms=["EdDSA"]).claims
    SCHEMAS.validate_entitlement_claims(claims)
    assert claims["plan"] == "pro" and claims["limits"]["max_enabled_pcs"] == 5 and claims["pc"] == online_agent.pc_id
    status = await alice.get("/v1/account/billing", schema="billing_status_response")
    assert status["subscription"]["state"] == "active" and status["subscription"]["plan"] == "pro"
    assert status["subscription"]["interval"] == "monthly" and status["subscription"]["current_period_end"]
    assert status["checkout_available"] is False and status["portal_available"] is True
    assert status["topup"]["allowed"] is True and status["topup"]["balance_requests"] == 0
    assert status["selection"] is None


async def test_pro_plus_checkout_sets_plan_pro_plus_with_assertion_plan_pro(
    env: Env, billing: BillingService, alice: Browser, linked_agent: AgentSim
) -> None:
    out = await start_checkout(alice, "pro_plus", "annual")
    await deliver_all(env, fake_stripe(billing).complete_checkout(out["session_id"]))
    s = await alice.get("/v1/session", schema="session_response")
    assert (s["plan"], s["entitlement_state"]) == ("pro_plus", "active")
    status = await alice.get("/v1/account/billing", schema="billing_status_response")
    assert (status["plan"], status["subscription"]["plan"], status["subscription"]["interval"]) == (
        "pro_plus",
        "pro_plus",
        "annual",
    )
    r = await linked_agent.rest("POST", "/v1/agent/entitlement", bearer=True, schema="agent_entitlement_response")
    body = r.json()
    assert body["plan"] == "pro"  # the agent knows free and pro; Pro Plus shares every PC-facing limit
    jwks = (await env.http.get("/.well-known/dome-jwks.json")).json()
    claims = jwt.decode(body["assertion"], KeySet.import_key_set(jwks), algorithms=["EdDSA"]).claims
    SCHEMAS.validate_entitlement_claims(claims)
    assert claims["plan"] == "pro" and claims["limits"]["max_controllers"] == 5
    # the assistant sees the Pro Plus windows
    assistant = await alice.get("/v1/assistant", schema="assistant_status_response")
    assert assistant["allowance"]["window"]["limit"] == 90 and assistant["allowance"]["week"]["limit"] == 300


async def test_already_subscribed_409_and_portal_works(env: Env, billing: BillingService, alice: Browser) -> None:
    out = await start_checkout(alice)
    await deliver_all(env, fake_stripe(billing).complete_checkout(out["session_id"]))
    r = await alice.request("POST", "/v1/billing/checkout", {"plan": "pro_plus", "interval": "monthly"}, expect=409)
    err = r.json()["error"]
    assert err["code"] == "ALREADY_SUBSCRIBED" and err["detail"] == {"portal_available": True}
    portal = await alice.post("/v1/billing/portal", {}, schema="billing_portal_response")
    assert portal["portal_url"].startswith("https://")
    customer = fake_stripe(billing).customer_for_account(alice.account_id)
    assert customer is not None and fake_stripe(billing).portal_sessions == [(customer.id, portal["portal_url"])]
    # a second subscription at the provider is impossible: one live row per account
    assert (
        sql(env.database_url, "SELECT count(*) FROM subscriptions WHERE account_id = %s", (alice.account_id,))[0][0]
        == 1
    )


async def test_portal_without_customer_404(env: Env, billing: BillingService, alice: Browser) -> None:
    r = await alice.request("POST", "/v1/billing/portal", {}, expect=404)
    assert r.json()["error"]["code"] == "BILLING_NO_CUSTOMER"
    assert fake_stripe(billing).portal_sessions == []


async def test_rate_limit_per_account(env: Env, billing: BillingService, alice: Browser, bob: Browser) -> None:
    billing.config = dataclasses.replace(billing.config, requests_per_hour=2)
    billing._limiter = type(billing._limiter)(2, 3600)
    await start_checkout(alice, "pro", "monthly")
    await start_checkout(alice, "pro", "annual")  # a different price: a second page creation
    r = await alice.request("POST", "/v1/billing/checkout", {"plan": "pro_plus", "interval": "monthly"}, expect=429)
    assert r.json()["error"]["code"] == "RATE_LIMITED" and r.headers.get("retry-after")
    assert len(fake_stripe(billing).checkouts) == 2
    await start_checkout(bob)  # another account has its own budget


async def test_provider_error_is_502_and_nothing_persisted_as_active(
    env: Env, billing: BillingService, alice: Browser
) -> None:
    fake_stripe(billing).fail_next.append("create_checkout")
    r = await alice.request("POST", "/v1/billing/checkout", {"plan": "pro", "interval": "monthly"}, expect=502)
    err = r.json()["error"]
    assert err["code"] == "BILLING_PROVIDER_ERROR" and err["retryable"] is True
    row = subscription_row(env, alice.account_id)
    assert row["state"] == "checkout_pending" and row["provider_checkout_session_id"] is None
    assert sql(env.database_url, "SELECT plan FROM accounts WHERE id = %s", (alice.account_id,))[0][0] == "free"
    status = await alice.get("/v1/account/billing", schema="billing_status_response")
    assert status["plan"] == "free" and status["checkout_available"] is True
    # no hosted page exists, so nothing is shown as pending (an honest Billing page)
    assert status["entitlement_state"] == "free" and status["subscription"] is None
    s = await alice.get("/v1/session", schema="session_response")
    assert s["entitlement_state"] == "free"
    # a retry right away replays the same idempotency key and expiry: had the provider created the page after all
    # (a timeout on our side), it answers with that same page instead of a second one
    key_before = row["checkout_idempotency_key"]
    out = await start_checkout(alice)
    after = subscription_row(env, alice.account_id)
    assert (
        after["provider_checkout_session_id"] == out["session_id"] and after["checkout_idempotency_key"] == key_before
    )
    # a failed attempt older than the creation window gets a fresh key
    fake_stripe(billing).fail_next.append("create_checkout")
    await alice.request("POST", "/v1/billing/checkout", {"plan": "pro", "interval": "annual"}, expect=502)
    sql(
        env.database_url,
        "UPDATE subscriptions SET updated_at = updated_at - interval '1 minute' WHERE account_id = %s",
        (alice.account_id,),
    )
    stale_key = subscription_row(env, alice.account_id)["checkout_idempotency_key"]
    await start_checkout(alice, "pro", "annual")
    assert subscription_row(env, alice.account_id)["checkout_idempotency_key"] != stale_key
    fake_stripe(billing).fail_next.append("ensure_customer")
    r = await alice.request("POST", "/v1/billing/portal", {}, expect=200)  # the customer already exists in our row
    fake_stripe(billing).fail_next.clear()
    fake_stripe(billing).fail_next.append("create_portal")
    r = await alice.request("POST", "/v1/billing/portal", {}, expect=502)
    assert r.json()["error"]["code"] == "BILLING_PROVIDER_ERROR"


async def test_billing_off_answers_503_everywhere_and_status_still_works(
    env: Env, billing: BillingService, alice: Browser
) -> None:
    billing.config = dataclasses.replace(billing.config, enabled=False)
    for path, body in (
        ("/v1/billing/checkout", {"plan": "pro", "interval": "monthly"}),
        ("/v1/billing/portal", {}),
        ("/v1/billing/topup", {}),
    ):
        r = await alice.request("POST", path, body, expect=503)
        assert r.json()["error"]["code"] == "BILLING_NOT_AVAILABLE" and r.json()["error"]["retryable"] is False
    assert (await deliver(env, fake_stripe(billing).event("ping", {"object": "x"}), expect=503))["error"][
        "code"
    ] == "BILLING_NOT_AVAILABLE"
    status = await alice.get("/v1/account/billing", schema="billing_status_response")
    assert status == {
        **status,
        "billing_enabled": False,
        "billing_mode": "off",
        "plan": "free",
        "entitlement_state": "free",
        "subscription": None,
        "checkout_available": False,
        "portal_available": False,
        "selection": None,
    }
    assert status["topup"]["allowed"] is False and status["topup"]["price_cents"] == 299
    selection = await alice.get("/v1/account/plan-selection", schema="plan_selection_response")
    assert selection["open"] is False and selection["deadline"] is None and selection["plan"] == "free"
    plans = (await env.http.get("/v1/plans")).json()
    assert (plans["billing_enabled"], plans["billing_mode"]) == (False, "off")


async def test_a_paid_page_whose_webhook_is_late_is_never_sold_twice(
    env: Env, billing: BillingService, alice: Browser, bob: Browser
) -> None:
    """The customer paid on the hosted page and the webhook has not been processed yet: the Billing page stops
    offering plans (the paid subscription is applied as soon as anyone asks), and a second checkout never creates a
    second page, so the provider never holds two subscriptions."""
    fake = fake_stripe(billing)
    out = await start_checkout(alice)
    deliveries = fake.complete_checkout(out["session_id"])  # paid; nothing delivered yet
    status = await alice.get("/v1/account/billing", schema="billing_status_response")
    assert status["checkout_available"] is False and status["checkout_plans"] == []
    assert (status["plan"], status["entitlement_state"]) == ("pro", "active")  # fetched from the provider, not the URL
    r = await alice.request("POST", "/v1/billing/checkout", {"plan": "pro", "interval": "monthly"}, expect=409)
    assert r.json()["error"]["code"] == "ALREADY_SUBSCRIBED"
    # the same without the status read first: the checkout itself asks the provider about the stored page
    other = await start_checkout(bob, "pro_plus", "annual")
    late = fake.complete_checkout(other["session_id"])
    for plan, interval in (("pro", "monthly"), ("pro_plus", "annual")):
        r = await bob.request("POST", "/v1/billing/checkout", {"plan": plan, "interval": interval}, expect=409)
        assert r.json()["error"]["code"] == "ALREADY_SUBSCRIBED"
    assert sql(env.database_url, "SELECT plan FROM accounts WHERE id = %s", (bob.account_id,))[0][0] == "pro_plus"
    assert len(fake.subscriptions) == 2 and len(fake.checkouts) == 2  # one each: nothing was sold twice
    # the late deliveries change nothing further
    assert all(r["status"] == "processed" for r in await deliver_all(env, deliveries + late))
    assert len(fake.subscriptions) == 2
    assert subscription_row(env, alice.account_id)["state"] == "active"


async def test_a_completed_page_without_its_subscription_yet_answers_checkout_processing(
    env: Env, billing: BillingService, alice: Browser
) -> None:
    fake = fake_stripe(billing)
    out = await start_checkout(alice)
    fake.checkouts[out["session_id"]].status = "complete"  # paid, the subscription not created yet
    r = await alice.request("POST", "/v1/billing/checkout", {"plan": "pro", "interval": "monthly"}, expect=409)
    err = r.json()["error"]
    assert err["code"] == "CHECKOUT_PROCESSING" and err["retryable"] is True
    assert len(fake.checkouts) == 1 and fake.subscriptions == {}
    status = await alice.get("/v1/account/billing", schema="billing_status_response")
    assert status["checkout_available"] is False and status["plan"] == "free"


async def test_concurrent_checkouts_create_one_page(env: Env, billing: BillingService, alice: Browser) -> None:
    """A double tap, or two tabs: both requests answer with the same page and the provider holds one open page."""
    fake = fake_stripe(billing)
    first, second = await asyncio.gather(start_checkout(alice), start_checkout(alice))
    assert first["session_id"] == second["session_id"]
    assert [c.status for c in fake.checkouts.values()] == ["open"]
    # a page still being created for one choice (no session id yet, within the creation window): the same choice
    # replays it, another choice waits instead of opening a second, untracked page
    sql(
        env.database_url,
        "UPDATE subscriptions SET provider_checkout_session_id = NULL, updated_at = now() WHERE account_id = %s",
        (alice.account_id,),
    )
    r = await alice.request("POST", "/v1/billing/checkout", {"plan": "pro_plus", "interval": "monthly"}, expect=409)
    assert r.json()["error"]["code"] == "CHECKOUT_PROCESSING"
    again = await start_checkout(alice)
    assert again["session_id"] == first["session_id"] and len(fake.checkouts) == 1
    assert subscription_row(env, alice.account_id)["provider_checkout_session_id"] == first["session_id"]


async def test_the_page_expires_after_the_configured_pending_hours(
    env: Env, billing: BillingService, alice: Browser
) -> None:
    billing.config = dataclasses.replace(billing.config, checkout_pending_hours=2)
    before = utcnow().replace(microsecond=0)
    out = await start_checkout(alice)
    page = fake_stripe(billing).checkouts[out["session_id"]]
    assert before + timedelta(hours=2) <= page.expires_at <= utcnow() + timedelta(hours=2)
    # whole seconds, as the request carries it: the row stores exactly the expiry the provider was sent
    stored = sql(
        env.database_url, "SELECT checkout_expires_at FROM subscriptions WHERE account_id = %s", (alice.account_id,)
    )
    assert page.expires_at.microsecond == 0 and stored[0][0] == page.expires_at


def test_the_expiry_sent_is_always_inside_stripes_window() -> None:
    """Stripe accepts expires_at from 30 minutes to 24 hours after it creates the session. Whatever hours a config
    holds, the value sent is 1 to 23 hours ahead in whole seconds (half an hour inside both ends), and a replay within
    the creation window may send it again; a stored value outside the margin (the older build's 24 hours) may not."""
    now = datetime(2026, 10, 23, 12, 0, 0, 987_654, tzinfo=UTC)
    for hours, lifetime in ((0, 1), (1, 1), (2, 2), (23, 23), (24, 23), (48, 23)):
        sent = checkout_expiry(now, hours)
        assert sent == now.replace(microsecond=0) + timedelta(hours=lifetime), hours
        for later in (timedelta(0), timedelta(seconds=CHECKOUT_CREATING_SECONDS)):
            assert checkout_expiry_replayable(sent, now + later), (hours, later)
    assert not checkout_expiry_replayable(now + timedelta(hours=24), now)
    assert not checkout_expiry_replayable(now + timedelta(minutes=40), now)
    assert checkout_expiry_replayable(now + timedelta(minutes=45), now)
    assert checkout_expiry_replayable(now + timedelta(hours=23, minutes=45), now)


@pytest.mark.parametrize("drift", [timedelta(minutes=20), timedelta(minutes=-20)])
async def test_a_server_clock_off_from_stripes_still_gets_a_page(
    env: Env, billing: BillingService, alice: Browser, drift: timedelta
) -> None:
    """The fake refuses an expires_at outside 30 minutes to 24 hours by its own clock, as Stripe does. With the server
    20 minutes ahead (with the default page lifetime) or 20 minutes behind (with the shortest, 1 hour), the page is
    still created. Before, the 24-hour default was past Stripe's limit as soon as the server's clock ran ahead."""
    billing.config = dataclasses.replace(billing.config, clock=lambda: utcnow() + drift)
    if drift < timedelta(0):
        billing.config = dataclasses.replace(billing.config, checkout_pending_hours=1)
    out = await start_checkout(alice)
    page = fake_stripe(billing).checkouts[out["session_id"]]
    assert timedelta(minutes=30) <= page.expires_at - utcnow() <= timedelta(hours=24)
    with pytest.raises(ProviderError) as ei:  # what the old 24 hours amounted to, 20 minutes ahead
        await fake_stripe(billing).create_checkout(
            customer_id=page.customer_id,
            price_id=page.price_id,
            mode="subscription",
            success_url="https://x.test/ok",
            cancel_url="https://x.test/cancel",
            idempotency_key="checkout:old-default",
            metadata={},
            automatic_tax=False,
            expires_at=utcnow() + timedelta(minutes=20) + timedelta(hours=24),
        )
    assert (ei.value.reason, ei.value.status, ei.value.param) == ("bad_request", 400, "expires_at")


@pytest.mark.parametrize("stored", ["24 hours 5 minutes", "10 minutes"])
async def test_a_stored_expiry_stripe_would_refuse_is_not_replayed(
    env: Env, billing: BillingService, alice: Browser, stored: str
) -> None:
    """A retry within the creation window replays the stored key and expiry, unless that expiry is no longer one
    Stripe accepts (a clock that moved, or a row from a build that sent 24 hours): sending it again would only be
    refused again, so the page creation starts over with a new key and an expiry inside the window."""
    fake = fake_stripe(billing)
    fake.fail_next.append("create_checkout")
    await alice.request("POST", "/v1/billing/checkout", {"plan": "pro", "interval": "monthly"}, expect=502)
    key_before = subscription_row(env, alice.account_id)["checkout_idempotency_key"]
    sql(
        env.database_url,
        f"UPDATE subscriptions SET checkout_expires_at = now() + interval '{stored}', updated_at = now() "
        "WHERE account_id = %s",
        (alice.account_id,),
    )
    out = await start_checkout(alice)
    row = subscription_row(env, alice.account_id)
    assert row["provider_checkout_session_id"] == out["session_id"] and row["checkout_idempotency_key"] != key_before
    page = fake.checkouts[out["session_id"]]
    assert timedelta(hours=22) < page.expires_at - utcnow() <= timedelta(hours=23)


async def test_a_customer_id_from_a_replaced_secret_key_is_replaced_too(
    env: Env, billing: BillingService, alice: Browser, capfd: pytest.CaptureFixture[str]
) -> None:
    """Staging, 2026-10-23: a first attempt under one Stripe account stored the customer id, then Checkout refused
    (prices from another sandbox). After the founder pastes the key of the sandbox that holds the prices, that
    customer id is unknown there (Stripe: 400 resource_missing, param customer; the fake answers the same). The
    checkout forgets it on every row, creates the customer under the current key and makes the page; before, every
    later attempt sent the old id again and failed, while the Deploy button's Stripe check reported a match."""
    fake = fake_stripe(billing)
    fake.fail_next.append("create_checkout")
    await alice.request("POST", "/v1/billing/checkout", {"plan": "pro", "interval": "monthly"}, expect=502)
    old = subscription_row(env, alice.account_id)["provider_customer_id"]
    assert old in fake.customers
    fake.customers.clear()  # the new key's account knows none of the old account's customers
    # creating the customer under the new key fails once: the old id stays forgotten, the next attempt goes through
    fake.fail_next.append("ensure_customer")
    r = await alice.request("POST", "/v1/billing/checkout", {"plan": "pro", "interval": "monthly"}, expect=502)
    assert r.json()["error"]["detail"] == {"reason": "provider_error"}
    assert subscription_row(env, alice.account_id)["provider_customer_id"] is None
    capfd.readouterr()
    out = await start_checkout(alice)
    row = subscription_row(env, alice.account_id)
    new = row["provider_customer_id"]
    assert new and new != old and new in fake.customers
    assert row["state"] == "checkout_pending" and row["provider_checkout_session_id"] == out["session_id"]
    assert fake.checkouts[out["session_id"]].customer_id == new
    assert sql(env.database_url, "SELECT count(*) FROM subscriptions WHERE provider_customer_id = %s", (old,)) == [(0,)]
    logged = capfd.readouterr()
    assert "billing.provider_error" not in logged.out + logged.err


async def test_a_customer_id_stripe_does_not_know_is_replaced_once_within_one_request(
    env: Env,
    billing: BillingService,
    alice: Browser,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same in one request: the stored id is refused, the customer is created under the current key, and the page
    is made with a new idempotency key (the request differs). Tried once only."""
    fake = fake_stripe(billing)
    fake.fail_next.append("create_checkout")
    await alice.request("POST", "/v1/billing/checkout", {"plan": "pro", "interval": "monthly"}, expect=502)
    before = subscription_row(env, alice.account_id)
    fake.customers.clear()
    capfd.readouterr()
    out = await start_checkout(alice)
    row = subscription_row(env, alice.account_id)
    assert row["provider_customer_id"] not in (None, before["provider_customer_id"])
    assert row["checkout_idempotency_key"] != before["checkout_idempotency_key"]
    assert fake.calls[-3:] == ["create_checkout", "ensure_customer", "create_checkout"]
    page = fake.checkouts[out["session_id"]]
    assert timedelta(hours=22) < page.expires_at - utcnow() <= timedelta(hours=23)
    logged = capfd.readouterr()
    assert "billing.customer_unknown" in logged.out + logged.err
    # a customer refused again is not retried a second time in the same request
    calls = len(fake.calls)

    async def refused(**kwargs: Any) -> Any:
        fake.calls.append("create_checkout")
        raise ProviderError("bad_request", status=400, code="resource_missing", param="customer")

    sql(env.database_url, "UPDATE subscriptions SET state = 'canceled' WHERE account_id = %s", (alice.account_id,))
    monkeypatch.setattr(fake, "create_checkout", refused)
    r = await alice.request("POST", "/v1/billing/checkout", {"plan": "pro", "interval": "monthly"}, expect=502)
    assert r.json()["error"]["detail"] == {"reason": "bad_request"}
    assert fake.calls[calls:] == ["create_checkout", "ensure_customer", "create_checkout"]


async def test_a_provider_refusal_logs_stripes_code_and_parameter_and_answers_the_reason_only(
    env: Env,
    billing: BillingService,
    alice: Browser,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """``billing.provider_error`` carries Stripe's error code and the parameter it names (the short machine strings
    that tell a wrong price id from a missing account name; never Stripe's message, which can quote customer data). The
    phone still gets ``{"reason"}`` only."""

    async def refused(**kwargs: Any) -> Any:
        raise ProviderError(
            "bad_request", status=400, request_id="req_diag1", code="resource_missing", param="line_items[0][price]"
        )

    monkeypatch.setattr(fake_stripe(billing), "create_checkout", refused)
    capfd.readouterr()
    r = await alice.request("POST", "/v1/billing/checkout", {"plan": "pro", "interval": "monthly"}, expect=502)
    err = r.json()["error"]
    assert err["code"] == "BILLING_PROVIDER_ERROR" and err["detail"] == {"reason": "bad_request"}
    out = capfd.readouterr()
    [line] = [x for x in (out.out + out.err).splitlines() if "billing.provider_error" in x]
    assert "create_checkout" in line and "req_diag1" in line
    assert "resource_missing" in line and "line_items[0][price]" in line
    assert "[redacted]" not in line  # the fields are not named "code" / "*_code", which the redactor blanks


async def test_pro_plus_is_not_sold_while_the_assistant_is_off(
    env: Env, billing: BillingService, assistant: AssistantService, alice: Browser
) -> None:
    """Pro Plus differs from Pro only in AI requests: while the assistant is off it cannot be bought (as top-ups
    cannot), the Billing status lists only Pro, and GET /v1/plans says AI is off. Pro is still sold."""
    assistant.config = dataclasses.replace(assistant.config, enabled=False)
    status = await alice.get("/v1/account/billing", schema="billing_status_response")
    assert status["checkout_available"] is True and status["checkout_plans"] == ["pro"]
    assert (await env.http.get("/v1/plans")).json()["ai_enabled"] is False
    for interval in ("monthly", "annual"):
        r = await alice.request("POST", "/v1/billing/checkout", {"plan": "pro_plus", "interval": interval}, expect=403)
        err = r.json()["error"]
        assert err["code"] == "PLAN_NOT_AVAILABLE" and err["detail"] == {"plan": "pro_plus", "reason": "ai_off"}
    assert fake_stripe(billing).checkouts == {} and subscription_row(env, alice.account_id) == {}
    out = await start_checkout(alice, "pro", "monthly")
    assert out["kind"] == "subscription"
    assistant.config = dataclasses.replace(assistant.config, enabled=True)
    status = await alice.get("/v1/account/billing", schema="billing_status_response")
    assert status["checkout_plans"] == ["pro", "pro_plus"]
