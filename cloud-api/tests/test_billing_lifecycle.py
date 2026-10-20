"""The subscription lifecycle after Checkout with the in-memory fake Stripe and an injected clock (docs/BILLING.md
§4, §8, §11): renewal, failed payment with grace and its expiry, recovery inside grace, cancellation at period end
followed by deletion (the downgrade selection window, its defaults and its deadline), re-upgrade, an unknown price,
every ``entitlement_state`` value, account-deletion cancellation with its retry, and isolation between accounts."""

from __future__ import annotations

import asyncio
import dataclasses
import uuid
from datetime import datetime, timedelta
from typing import Any

import pytest

from dome_api.billing import state
from dome_api.billing.fake_provider import FakeStripe
from dome_api.billing.provider import SubscriptionSnapshot
from dome_api.billing.service import BillingService
from dome_api.db.engine import transaction
from dome_api.util import utcnow
from tests.conftest import AgentSim, Browser, ControllerSim, Env, deliver, deliver_all, fake_stripe, sql


class Clock:
    """The billing service's and the fake provider's notion of now; tests move it forward."""

    def __init__(self) -> None:
        self.now = utcnow()

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta: float) -> None:
        self.now += timedelta(**delta)


def use_clock(billing: BillingService) -> Clock:
    clock = Clock()
    billing.config = dataclasses.replace(billing.config, clock=clock)
    fake_stripe(billing).clock = clock
    return clock


def row(env: Env, account_id: str) -> dict[str, Any]:
    cols = "state, plan, interval, price_id, grace_until, current_period_end, cancel_at_period_end, provider_subscription_id"
    rows = sql(
        env.database_url,
        f"SELECT {cols} FROM subscriptions WHERE account_id = %s ORDER BY updated_at DESC LIMIT 1",
        (account_id,),
    )
    return dict(zip([c.strip() for c in cols.split(",")], rows[0], strict=True)) if rows else {}


def account_plan(env: Env, account_id: str) -> str:
    return str(sql(env.database_url, "SELECT plan FROM accounts WHERE id = %s", (account_id,))[0][0])


def plan_changes(env: Env, account_id: str) -> list[tuple[str, str, str]]:
    rows = sql(
        env.database_url,
        "SELECT detail FROM security_events WHERE account_id = %s AND kind = 'plan_changed' ORDER BY id",
        (account_id,),
    )
    return [(d["from"], d["to"], d["reason"]) for (d,) in rows]


async def subscribe(
    env: Env, billing: BillingService, browser: Browser, plan: str = "pro", interval: str = "monthly"
) -> str:
    """Checkout, pay on the hosted page, deliver Stripe's events; returns the provider subscription id."""
    out = await browser.post(
        "/v1/billing/checkout", {"plan": plan, "interval": interval}, schema="billing_checkout_response"
    )
    fake = fake_stripe(billing)
    await deliver_all(env, fake.complete_checkout(out["session_id"]))
    sub_id = fake.checkouts[out["session_id"]].subscription_id
    assert sub_id is not None and account_plan(env, browser.account_id) == plan
    return sub_id


async def session_state(browser: Browser) -> tuple[str, str]:
    s = await browser.get("/v1/session", schema="session_response")
    return s["plan"], s["entitlement_state"]


async def test_renewal_updates_period_without_plan_change(env: Env, billing: BillingService, alice: Browser) -> None:
    clock = use_clock(billing)
    sub_id = await subscribe(env, billing, alice)
    first_end = row(env, alice.account_id)["current_period_end"]
    clock.advance(days=30)
    await deliver(env, fake_stripe(billing).renew(sub_id))
    after = row(env, alice.account_id)
    assert after["state"] == "active" and after["current_period_end"] > first_end
    assert plan_changes(env, alice.account_id) == [("free", "pro", "webhook")]  # the renewal is not a plan change
    status = await alice.get("/v1/account/billing", schema="billing_status_response")
    shown = datetime.fromisoformat(status["subscription"]["current_period_end"].replace("Z", "+00:00"))
    assert abs(shown - after["current_period_end"]) < timedelta(seconds=1)
    assert (status["entitlement_state"], status["subscription"]["state"]) == ("active", "active")


async def test_failed_payment_enters_grace_keeps_pro_then_unpaid_free_after_grace(
    env: Env, billing: BillingService, alice: Browser, online_agent: AgentSim
) -> None:
    clock = use_clock(billing)
    sub_id = await subscribe(env, billing, alice)
    await online_agent.recv_type("grants_snapshot")  # free -> pro
    failed_at = clock.now
    await deliver(env, fake_stripe(billing).fail_payment(sub_id))
    r = row(env, alice.account_id)
    assert r["state"] == "past_due" and r["grace_until"] == failed_at + timedelta(days=7)
    assert await session_state(alice) == ("pro", "past_due")
    status = await alice.get("/v1/account/billing", schema="billing_status_response")
    assert status["subscription"]["grace_until"] is not None and status["topup"]["allowed"] is True
    # the provider keeps retrying the card: a second failure notice does not move the deadline
    clock.advance(days=3)
    await deliver(env, fake_stripe(billing).fail_payment(sub_id))
    assert row(env, alice.account_id)["grace_until"] == failed_at + timedelta(days=7)
    clock.advance(days=5)  # eight days after the first failure
    counts = await billing.reconcile()
    assert counts["grace_ended"] >= 1  # other tests' rows share the database
    assert row(env, alice.account_id)["state"] == "unpaid" and account_plan(env, alice.account_id) == "free"
    assert await session_state(alice) == ("free", "expired")
    snap = await online_agent.recv_type("grants_snapshot")
    assert "entitlement_assertion" not in snap  # Free carries no assertion
    # a later pass that fetches the subscription (Stripe still says past_due) keeps the ended grace ended
    await billing.reconcile()
    assert row(env, alice.account_id)["state"] == "unpaid" and account_plan(env, alice.account_id) == "free"
    assert plan_changes(env, alice.account_id) == [("free", "pro", "webhook"), ("pro", "free", "grace_expired")]
    status = await alice.get("/v1/account/billing", schema="billing_status_response")
    assert status["topup"]["allowed"] is False and status["selection"]["open"] is True


async def test_payment_recovery_in_grace_returns_active_and_clears_grace(
    env: Env, billing: BillingService, alice: Browser
) -> None:
    clock = use_clock(billing)
    sub_id = await subscribe(env, billing, alice)
    await deliver(env, fake_stripe(billing).fail_payment(sub_id))
    clock.advance(days=2)
    await deliver(env, fake_stripe(billing).pay_open_invoice(sub_id))
    r = row(env, alice.account_id)
    assert r["state"] == "active" and r["grace_until"] is None
    assert await session_state(alice) == ("pro", "active")
    assert plan_changes(env, alice.account_id) == [("free", "pro", "webhook")]  # Pro never lapsed
    clock.advance(days=10)
    await billing.reconcile()
    assert account_plan(env, alice.account_id) == "pro" and row(env, alice.account_id)["state"] == "active"


async def controller_statuses(browser: Browser) -> dict[str, str]:
    return {c["id"]: c["status"] for c in (await browser.get("/v1/controllers"))["controllers"]}


async def test_cancel_at_period_end_then_deleted_downgrades_opens_selection_and_defaults_apply(
    env: Env, billing: BillingService, alice: Browser, online_agent: AgentSim
) -> None:
    clock = use_clock(billing)
    sub_id = await subscribe(env, billing, alice)
    others = [AgentSim(env), AgentSim(env)]
    for i, agent in enumerate(others):
        await agent.link(alice, name=f"Spare PC {i}")
    phones = [ControllerSim(env, alice, name=f"Phone {i}") for i in range(4)]
    for phone in phones:
        await phone.pair(online_agent)
    # last seen: the online PC newest, then spare 0, then spare 1; phones 2 and 3 newer than 0 and 1
    base = utcnow()
    for pc_id, age in ((online_agent.pc_id, 1), (others[0].pc_id, 2), (others[1].pc_id, 3)):
        sql(env.database_url, "UPDATE pcs SET last_seen_at = %s WHERE id = %s", (base - timedelta(hours=age), pc_id))
    for phone, age in zip(phones, (40, 30, 2, 1), strict=True):
        sql(
            env.database_url,
            "UPDATE controllers SET last_seen_at = %s WHERE id = %s",
            (base - timedelta(minutes=age), phone.controller_id),
        )
    # the customer cancels in the portal: Pro stays until the period ends
    await deliver(env, fake_stripe(billing).set_cancel_at_period_end(sub_id, True))
    assert await session_state(alice) == ("pro", "canceling")
    status = await alice.get("/v1/account/billing", schema="billing_status_response")
    assert status["subscription"]["cancel_at_period_end"] is True and status["selection"] is None
    clock.advance(days=30)
    await deliver(env, fake_stripe(billing).delete_subscription(sub_id))
    assert account_plan(env, alice.account_id) == "free"
    enabled = dict(
        sql(env.database_url, "SELECT id::text, enabled FROM pcs WHERE account_id = %s", (alice.account_id,))
    )
    assert enabled == {online_agent.pc_id: True, others[0].pc_id: False, others[1].pc_id: False}
    disabled_at = sql(
        env.database_url,
        "SELECT count(*) FROM pcs WHERE plan_disabled_at IS NOT NULL AND account_id = %s",
        (alice.account_id,),
    )
    assert disabled_at[0][0] == 2
    snap = await online_agent.recv_type("grants_snapshot", pc_enabled=True)
    by_id = {c["controller_id"]: c["status"] for c in snap["controllers"]}
    assert [by_id[p.controller_id] for p in phones] == ["plan_disabled", "plan_disabled", "active", "active"]
    assert (
        sql(env.database_url, "SELECT plan_changed_at FROM accounts WHERE id = %s", (alice.account_id,))[0][0]
        == clock.now
    )
    selection = await alice.get("/v1/account/plan-selection", schema="plan_selection_response")
    assert selection["open"] is True and selection["plan"] == "free"
    assert selection["limits"] == {"max_enabled_pcs": 1, "max_controllers": 2}
    assert {p["id"]: p["selected"] for p in selection["pcs"]} == enabled
    status = await alice.get("/v1/account/billing", schema="billing_status_response")
    assert status["selection"]["open"] is True and status["entitlement_state"] == "expired"
    # the owner moves the enabled PC and phones within the window
    too_many = {"pc_ids": [others[0].pc_id, others[1].pc_id], "controller_ids": []}
    r = await alice.request("PUT", "/v1/account/plan-selection", too_many, expect=400)
    assert r.json()["error"]["code"] == "PLAN_SELECTION_INVALID"
    choice = {"pc_ids": [others[0].pc_id], "controller_ids": [phones[0].controller_id, phones[1].controller_id]}
    out = await alice.request("PUT", "/v1/account/plan-selection", choice, expect=200)
    body = out.json()
    assert {p["id"]: p["selected"] for p in body["pcs"]} == {
        online_agent.pc_id: False,
        others[0].pc_id: True,
        others[1].pc_id: False,
    }
    snap = await online_agent.recv_type("grants_snapshot", pc_enabled=False)
    statuses = await controller_statuses(alice)
    assert [statuses[p.controller_id] for p in phones] == ["active", "active", "plan_disabled", "plan_disabled"]  # type: ignore[index]
    kinds = sql(
        env.database_url,
        "SELECT count(*) FROM security_events WHERE account_id = %s AND kind = 'plan_selection_applied'",
        (alice.account_id,),
    )
    assert kinds[0][0] == 1
    # after the window the choice is made under Devices; the last selection stays as it is
    clock.advance(days=15)
    r = await alice.request("PUT", "/v1/account/plan-selection", choice, expect=409)
    assert r.json()["error"]["code"] == "PLAN_SELECTION_CLOSED"
    selection = await alice.get("/v1/account/plan-selection", schema="plan_selection_response")
    assert selection["open"] is False and selection["deadline"] is not None
    status = await alice.get("/v1/account/billing", schema="billing_status_response")
    assert status["selection"]["open"] is False
    for phone in phones:
        await phone.close()


async def test_reupgrade_restores_plan_disabled_pcs_and_clears_window(
    env: Env, billing: BillingService, alice: Browser, linked_agent: AgentSim
) -> None:
    use_clock(billing)
    sub_id = await subscribe(env, billing, alice)
    spare = AgentSim(env)
    await spare.link(alice, name="Spare PC")
    sql(env.database_url, "UPDATE pcs SET last_seen_at = now() WHERE id = %s", (linked_agent.pc_id,))
    await deliver(env, fake_stripe(billing).delete_subscription(sub_id))
    assert dict(
        sql(env.database_url, "SELECT id::text, enabled FROM pcs WHERE account_id = %s", (alice.account_id,))
    ) == {
        linked_agent.pc_id: True,
        spare.pc_id: False,
    }
    # a PC the owner disabled by hand is never re-enabled by an upgrade
    sql(
        env.database_url, "UPDATE pcs SET enabled = false, plan_disabled_at = NULL WHERE id = %s", (linked_agent.pc_id,)
    )
    await subscribe(env, billing, alice, "pro_plus", "monthly")
    pcs = dict(sql(env.database_url, "SELECT id::text, enabled FROM pcs WHERE account_id = %s", (alice.account_id,)))
    assert pcs == {linked_agent.pc_id: False, spare.pc_id: True}
    disabled = sql(
        env.database_url,
        "SELECT count(*) FROM pcs WHERE plan_disabled_at IS NOT NULL AND account_id = %s",
        (alice.account_id,),
    )
    assert disabled[0][0] == 0
    assert (
        sql(env.database_url, "SELECT plan_changed_at FROM accounts WHERE id = %s", (alice.account_id,))[0][0] is None
    )
    status = await alice.get("/v1/account/billing", schema="billing_status_response")
    assert status["selection"] is None and status["plan"] == "pro_plus"
    assert (
        sql(env.database_url, "SELECT count(*) FROM subscriptions WHERE account_id = %s", (alice.account_id,))[0][0]
        == 2
    )


async def test_unknown_price_grants_no_paid_plan_and_logs_the_account_only(
    env: Env, billing: BillingService, alice: Browser, capfd: pytest.CaptureFixture[str]
) -> None:
    """A price DOME_STRIPE_PRICE_* does not name (made in the dashboard, another product on the same Stripe account)
    is never an entitlement: the row follows the subscription, the account stays Free, the operator sees
    billing.unknown_price with the account id only (no Stripe object id), and the customer can still reach the portal."""
    out = await alice.post(
        "/v1/billing/checkout", {"plan": "pro_plus", "interval": "monthly"}, schema="billing_checkout_response"
    )
    fake = fake_stripe(billing)
    fake.checkouts[out["session_id"]].price_id = "price_made_in_the_dashboard"
    capfd.readouterr()
    await deliver_all(env, fake.complete_checkout(out["session_id"]))
    logs = capfd.readouterr()
    lines = [line for line in (logs.out + logs.err).splitlines() if "billing.unknown_price" in line]
    assert lines and all(alice.account_id in line for line in lines)
    assert not any(sub_id in line for line in lines for sub_id in fake.subscriptions)
    assert not any("cus_" in line or "cs_test" in line for line in lines)
    r = row(env, alice.account_id)
    assert (r["state"], r["plan"], r["price_id"], r["interval"]) == (
        "active",
        "free",
        "price_made_in_the_dashboard",
        None,
    )
    assert account_plan(env, alice.account_id) == "free"
    assert await session_state(alice) == ("free", "free")
    status = await alice.get("/v1/account/billing", schema="billing_status_response")
    assert status["subscription"] is None and status["portal_available"] is True
    assert status["checkout_available"] is False  # a live subscription exists; the operator fixes the price first
    assert (await billing.reconcile())["mismatches"] == 0 and account_plan(env, alice.account_id) == "free"


async def test_entitlement_state_mapping_in_session_document(env: Env, billing: BillingService, alice: Browser) -> None:
    clock = use_clock(billing)
    seen = [await session_state(alice)]
    out = await alice.post(
        "/v1/billing/checkout", {"plan": "pro", "interval": "annual"}, schema="billing_checkout_response"
    )
    seen.append(await session_state(alice))
    fake = fake_stripe(billing)
    await deliver_all(env, fake.complete_checkout(out["session_id"]))
    sub_id = fake.checkouts[out["session_id"]].subscription_id
    assert sub_id is not None
    seen.append(await session_state(alice))
    await deliver(env, fake.set_cancel_at_period_end(sub_id, True))
    seen.append(await session_state(alice))
    await deliver(env, fake.set_cancel_at_period_end(sub_id, False))
    await deliver(env, fake.fail_payment(sub_id))
    seen.append(await session_state(alice))
    await deliver(env, fake.delete_subscription(sub_id))
    seen.append(await session_state(alice))
    clock.advance(days=31)
    seen.append(await session_state(alice))
    assert seen == [
        ("free", "free"),
        ("free", "pending"),
        ("pro", "active"),
        ("pro", "canceling"),
        ("pro", "past_due"),
        ("free", "expired"),
        ("free", "free"),
    ]
    # an incomplete first payment (3-D Secure pending) is `pending` and unlocks nothing
    status = await alice.get("/v1/account/billing", schema="billing_status_response")
    assert status["checkout_available"] is True
    out = await alice.post(
        "/v1/billing/checkout", {"plan": "pro", "interval": "monthly"}, schema="billing_checkout_response"
    )
    await deliver_all(env, fake.complete_checkout(out["session_id"], status="incomplete"))
    assert await session_state(alice) == ("free", "pending")
    r = await alice.request("POST", "/v1/billing/checkout", {"plan": "pro", "interval": "monthly"}, expect=409)
    assert r.json()["error"]["code"] == "ALREADY_SUBSCRIBED"


async def test_checkout_pending_expires_with_the_page_and_the_reconciler(
    env: Env, billing: BillingService, alice: Browser
) -> None:
    clock = use_clock(billing)
    out = await alice.post(
        "/v1/billing/checkout", {"plan": "pro", "interval": "monthly"}, schema="billing_checkout_response"
    )
    assert await session_state(alice) == ("free", "pending")
    clock.advance(hours=25)
    assert await session_state(alice) == ("free", "free")  # the page cannot be paid any more
    assert (await billing.reconcile())["checkouts_expired"] >= 1
    assert row(env, alice.account_id) == {}
    # Stripe's own expiry notice for that page changes nothing further
    r = await deliver(env, fake_stripe(billing).checkout_expired(out["session_id"]))
    assert r["status"] in ("processed", "ignored") and account_plan(env, alice.account_id) == "free"


async def test_account_deletion_cancels_or_records_pending_then_retries(
    env: Env, billing: BillingService, alice: Browser
) -> None:
    sub_id = await subscribe(env, billing, alice)
    fake = fake_stripe(billing)
    account_id = uuid.UUID(alice.account_id)
    # the future DELETE /v1/account route soft-deletes the account and then asks billing to cancel
    sql(env.database_url, "UPDATE accounts SET deleted_at = now() WHERE id = %s", (alice.account_id,))
    fake.fail_next.append("cancel_subscription_now")
    async with env.services.db() as db:
        assert await billing.cancel_for_account_deletion(db, account_id) == "pending"
        await db.commit()
    pending = sql(
        env.database_url,
        "SELECT attempts, last_error, completed_at FROM pending_deletions WHERE account_id = %s",
        (alice.account_id,),
    )
    assert pending == [(1, "provider_error", None)]
    assert fake.subscriptions[sub_id].status == "active"  # nothing claimed that did not happen
    counts = await billing.reconcile()
    assert counts["deletions_retried"] >= 1 and fake.subscriptions[sub_id].status == "canceled"
    done = sql(
        env.database_url,
        "SELECT provider_cancel_state, completed_at IS NOT NULL FROM pending_deletions WHERE account_id = %s",
        (alice.account_id,),
    )
    assert done == [("canceled", True)]
    assert row(env, alice.account_id)["state"] == "canceled" and account_plan(env, alice.account_id) == "free"
    # webhooks about the deleted account's subscription change nothing
    fake.subscriptions[sub_id].status = "active"
    r = await deliver(env, fake.renew(sub_id))
    assert r["status"] == "ignored" and account_plan(env, alice.account_id) == "free"
    async with env.services.db() as db:
        assert await billing.cancel_for_account_deletion(db, account_id) == "none"
        await db.commit()


async def test_account_deletion_without_subscription_is_none(env: Env, billing: BillingService, alice: Browser) -> None:
    async with env.services.db() as db:
        assert await billing.cancel_for_account_deletion(db, uuid.UUID(alice.account_id)) == "none"
        await db.commit()
    assert fake_stripe(billing).calls == []


async def test_cross_account_isolation(env: Env, billing: BillingService, alice: Browser, bob: Browser) -> None:
    alice_sub = await subscribe(env, billing, alice)
    bob_sub = await subscribe(env, billing, bob, "pro_plus", "annual")
    fake = fake_stripe(billing)
    await deliver(env, fake.delete_subscription(bob_sub))
    assert account_plan(env, bob.account_id) == "free" and account_plan(env, alice.account_id) == "pro"
    assert row(env, alice.account_id)["provider_subscription_id"] == alice_sub
    # each account reads only its own billing; no route takes another account's id
    a = await alice.get("/v1/account/billing", schema="billing_status_response")
    b = await bob.get("/v1/account/billing", schema="billing_status_response")
    assert (a["plan"], a["entitlement_state"]) == ("pro", "active")
    assert (b["plan"], b["entitlement_state"]) == ("free", "expired")
    # bob's selection cannot name alice's PC
    alice_pc = AgentSim(env)
    await alice_pc.link(alice, name="Alice PC")
    r = await bob.request(
        "PUT", "/v1/account/plan-selection", {"pc_ids": [alice_pc.pc_id], "controller_ids": []}, expect=400
    )
    assert r.json()["error"]["code"] == "PLAN_SELECTION_INVALID"
    assert sql(env.database_url, "SELECT enabled FROM pcs WHERE id = %s", (alice_pc.pc_id,))[0][0] is True
    # a delivery naming alice's subscription with bob's customer id still follows alice's own row
    customer_bob = fake.customer_for_account(bob.account_id)
    assert customer_bob is not None
    raw = fake.event(
        "customer.subscription.updated",
        {"id": alice_sub, "object": "subscription", "customer": customer_bob.id, "status": "canceled"},
    )
    await deliver(env, raw)
    assert account_plan(env, alice.account_id) == "pro" and account_plan(env, bob.account_id) == "free"


def test_fake_is_never_built_outside_development_and_test() -> None:
    assert FakeStripe().name == "fake"


async def test_late_events_of_an_ended_subscription_never_touch_the_new_one(
    env: Env, billing: BillingService, alice: Browser, capfd: pytest.CaptureFixture[str]
) -> None:
    fake = fake_stripe(billing)
    old_sub = await subscribe(env, billing, alice)
    deleted = fake.delete_subscription(old_sub)
    await deliver(env, deleted)
    new_sub = await subscribe(env, billing, alice, "pro_plus", "annual")
    # Stripe redelivers the old subscription's events late (a new event id each time)
    await deliver(env, fake.event("customer.subscription.deleted", fake._sub_obj(fake.subscriptions[old_sub])))
    await deliver(env, fake.event("invoice.payment_failed", fake._invoice_obj(fake.subscriptions[old_sub])))
    assert account_plan(env, alice.account_id) == "pro_plus"
    assert row(env, alice.account_id)["provider_subscription_id"] == new_sub
    # a second live subscription made outside DoMe's pages never replaces the one DoMe follows
    customer = fake.customer_for_account(alice.account_id)
    assert customer is not None
    stray_id = "sub_outside_" + uuid.uuid4().hex[:8]
    from dome_api.billing.fake_provider import _Subscription

    fake.subscriptions[stray_id] = _Subscription(
        id=stray_id,
        customer_id=customer.id,
        status="active",
        price_id="price_fake_pro_monthly",
        period_start=fake.clock(),
        period_end=fake.clock() + timedelta(days=30),
        metadata={"dome_account_id": alice.account_id},
    )
    capfd.readouterr()
    await deliver(env, fake.event("customer.subscription.created", fake._sub_obj(fake.subscriptions[stray_id])))
    logs = capfd.readouterr()
    assert "billing.second_subscription" in logs.out + logs.err
    r = row(env, alice.account_id)
    assert (r["provider_subscription_id"], r["plan"], r["state"]) == (new_sub, "pro_plus", "active")


async def test_reconciler_recovers_a_paid_checkout_whose_webhooks_never_arrived(
    env: Env, billing: BillingService, alice: Browser, capfd: pytest.CaptureFixture[str]
) -> None:
    """Every delivery failed (a wrong DOME_STRIPE_WEBHOOK_SECRET answers each 400, so no receipt row exists to retry):
    the reconciler asks the provider about the stale page before dropping it, and applies the paid subscription."""
    clock = use_clock(billing)
    out = await alice.post(
        "/v1/billing/checkout", {"plan": "pro", "interval": "monthly"}, schema="billing_checkout_response"
    )
    fake = fake_stripe(billing)
    fake.complete_checkout(out["session_id"])  # paid; nothing is ever delivered
    clock.advance(hours=25)
    capfd.readouterr()
    counts = await billing.reconcile()
    assert (counts["checkouts_recovered"], counts["checkouts_expired"]) == (1, 0)
    assert "billing.webhook_missed" in "".join(capfd.readouterr())
    assert account_plan(env, alice.account_id) == "pro"
    assert row(env, alice.account_id)["state"] == "active" and len(fake.subscriptions) == 1
    assert plan_changes(env, alice.account_id) == [("free", "pro", "webhook")]


async def test_reconciler_keeps_a_page_it_cannot_ask_about_and_one_still_payable(
    env: Env, billing: BillingService, alice: Browser, bob: Browser
) -> None:
    clock = use_clock(billing)
    fake = fake_stripe(billing)
    paid = await alice.post(
        "/v1/billing/checkout", {"plan": "pro", "interval": "monthly"}, schema="billing_checkout_response"
    )
    fake.complete_checkout(paid["session_id"])
    await bob.post("/v1/billing/checkout", {"plan": "pro", "interval": "monthly"}, schema="billing_checkout_response")
    clock.advance(hours=25)
    for page in fake.checkouts.values():  # Stripe's page lifetime is what counts, not the row's age
        page.expires_at = clock.now + timedelta(hours=1)
    fake.fail_next.append("retrieve_checkout")  # the first page asked about cannot be fetched: its row stays
    counts = await billing.reconcile()
    assert counts["checkouts_expired"] == 0
    assert row(env, bob.account_id)["state"] == "checkout_pending"  # still payable: kept
    second = await billing.reconcile()  # the provider answers now: the paid page is applied
    assert second["checkouts_expired"] == 0 and account_plan(env, alice.account_id) == "pro"
    clock.advance(hours=2)
    assert (await billing.reconcile())["checkouts_expired"] == 1 and row(env, bob.account_id) == {}


async def test_reconciler_credits_a_paid_topup_whose_webhooks_never_arrived(
    env: Env, billing: BillingService, alice: Browser
) -> None:
    clock = use_clock(billing)
    await subscribe(env, billing, alice)
    out = await alice.post("/v1/billing/topup", {}, schema="billing_checkout_response")
    fake = fake_stripe(billing)
    fake.topup_paid(out["session_id"])  # paid; nothing is ever delivered
    unpaid = await alice.post("/v1/billing/topup", {}, schema="billing_checkout_response")
    clock.advance(hours=25)
    counts = await billing.reconcile()
    assert counts["checkouts_recovered"] == 1
    balance = sql(
        env.database_url, "SELECT topup_remaining FROM ai_balances WHERE account_id = %s", (alice.account_id,)
    )
    assert balance[0][0] == 100
    statuses = dict(
        sql(
            env.database_url,
            "SELECT provider_checkout_session_id, status FROM topup_purchases WHERE account_id = %s",
            (alice.account_id,),
        )
    )
    assert statuses == {out["session_id"]: "credited", unpaid["session_id"]: "expired"}
    assert (await billing.reconcile())["checkouts_recovered"] == 0  # credited exactly once


async def test_a_stale_active_snapshot_never_reactivates_a_canceled_subscription(
    env: Env, billing: BillingService, alice: Browser
) -> None:
    """A snapshot fetched before the cancellation and applied after it: canceled is final at the provider."""
    sub_id = await subscribe(env, billing, alice)
    fake = fake_stripe(billing)
    stale = await fake.retrieve_subscription(sub_id)  # 'active', fetched now
    await deliver(env, fake.delete_subscription(sub_id))
    assert account_plan(env, alice.account_id) == "free"
    async with transaction(billing.db) as db:
        touched = await state.apply(
            db, uuid.UUID(alice.account_id), stale, reason="webhook", now=utcnow(), price_map=billing.config.price_map
        )
    assert touched == [] and account_plan(env, alice.account_id) == "free"
    assert row(env, alice.account_id)["state"] == "canceled"


async def test_fetch_and_apply_are_serialised_per_account(
    env: Env, billing: BillingService, alice: Browser, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Handler A fetches 'incomplete' and is slow; the payment then succeeds and handler B starts. B cannot fetch until
    A has applied, so the newer snapshot ('active') is the one that lands last."""
    out = await alice.post(
        "/v1/billing/checkout", {"plan": "pro", "interval": "monthly"}, schema="billing_checkout_response"
    )
    fake = fake_stripe(billing)
    await deliver_all(env, fake.complete_checkout(out["session_id"], status="incomplete"))
    sub_id = fake.checkouts[out["session_id"]].subscription_id
    assert sub_id is not None and account_plan(env, alice.account_id) == "free"
    original = fake.retrieve_subscription
    fetched, release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def slow_first(subscription_id: str) -> SubscriptionSnapshot:
        nonlocal calls
        calls += 1
        snapshot = await original(subscription_id)
        if calls == 1:
            fetched.set()
            await release.wait()
        return snapshot

    monkeypatch.setattr(fake, "retrieve_subscription", slow_first)
    account_id = uuid.UUID(alice.account_id)
    a = asyncio.create_task(billing._refresh(sub_id, account_id))
    await asyncio.wait_for(fetched.wait(), 5)
    fake.subscriptions[sub_id].status = "active"  # the payment went through after A's fetch
    b = asyncio.create_task(billing._refresh(sub_id, account_id))
    await asyncio.sleep(0.3)
    assert calls == 1 and not b.done()  # B waits for A's lock and has not fetched yet
    release.set()
    await asyncio.wait_for(asyncio.gather(a, b), 10)
    assert account_plan(env, alice.account_id) == "pro" and row(env, alice.account_id)["state"] == "active"
