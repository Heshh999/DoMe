"""AI top-ups end to end with the fake Stripe and the scripted fake AI provider (docs/BILLING.md §10;
rules.ai_allowance): who may buy one, crediting exactly once from a verified paid Checkout, the balance cap (pending
pages included), spending after the 5-hour window or the week runs out with ``charged_from: "topup"``, and a balance
that is kept but not spendable on Free."""

from __future__ import annotations

import dataclasses
import json
import uuid
from typing import Any

import httpx

from dome_api.assistant.catalogue import CONSENT_VERSION
from dome_api.assistant.fake_provider import FakeProvider, FakeReply
from dome_api.assistant.service import AssistantService
from dome_api.billing.service import BillingService
from tests.conftest import Browser, Env, deliver, deliver_all, fake_stripe, sql


def answer() -> FakeReply:
    body = {"mode": "answer", "reply": "Done.", "choices": [], "handoff": "none", "steps": []}
    return FakeReply(json.dumps(body))


def turn_body() -> dict[str, Any]:
    context: dict[str, Any] = {
        "persona": {"name": "DoMe"},
        "pc_connection": "online",
        "playback": {"youtube": "none", "media": "none"},
        "pc_volume": 40,
    }
    return {
        "turn_id": str(uuid.uuid4()),
        "source": "typed",
        "text": "What can you do?",
        "recent": [],
        "context": context,
    }


async def turn(browser: Browser, *, expect: int = 200) -> httpx.Response:
    return await browser.request(
        "POST", "/v1/assistant/turn", turn_body(), schema="assistant_turn_response", expect=expect
    )


async def consent(browser: Browser) -> None:
    await browser.post(
        "/v1/assistant/consent", {"version": CONSENT_VERSION, "accept": True}, schema="assistant_status_response"
    )


async def subscribe(env: Env, billing: BillingService, browser: Browser, plan: str = "pro") -> str:
    out = await browser.post(
        "/v1/billing/checkout", {"plan": plan, "interval": "monthly"}, schema="billing_checkout_response"
    )
    fake = fake_stripe(billing)
    await deliver_all(env, fake.complete_checkout(out["session_id"]))
    sub_id = fake.checkouts[out["session_id"]].subscription_id
    assert sub_id is not None
    return sub_id


async def buy_topup(env: Env, billing: BillingService, browser: Browser) -> list[dict[str, Any]]:
    out = await browser.post("/v1/billing/topup", {}, schema="billing_checkout_response")
    assert out["kind"] == "topup" and out["checkout_url"].startswith("https://")
    return await deliver_all(env, fake_stripe(billing).topup_paid(out["session_id"]))


def balance(env: Env, account_id: str) -> int:
    rows = sql(env.database_url, "SELECT topup_remaining FROM ai_balances WHERE account_id = %s", (account_id,))
    return int(rows[0][0]) if rows else 0


def no_dev_grant(assistant: AssistantService) -> None:
    """The plan's own windows (the suite's settings grant 50 requests for development)."""
    assistant.config = dataclasses.replace(assistant.config, dev_grant_interpretations=0)
    assistant.provider = FakeProvider([answer() for _ in range(10)])


async def test_topup_refused_on_free_and_when_billing_off(
    env: Env, billing: BillingService, assistant: AssistantService, alice: Browser
) -> None:
    r = await alice.request("POST", "/v1/billing/topup", {}, expect=403)
    assert r.json()["error"]["code"] == "TOPUP_NOT_ALLOWED"
    status = await alice.get("/v1/account/billing", schema="billing_status_response")
    assert status["topup"] == {"allowed": False, "balance_requests": 0, "requests": 100, "price_cents": 299}
    await subscribe(env, billing, alice)
    assert (await alice.get("/v1/account/billing", schema="billing_status_response"))["topup"]["allowed"] is True
    # with the assistant switched off nobody is sold requests they cannot use
    saved = assistant.config
    assistant.config = dataclasses.replace(saved, enabled=False)
    assert (await alice.get("/v1/account/billing", schema="billing_status_response"))["topup"]["allowed"] is False
    r = await alice.request("POST", "/v1/billing/topup", {}, expect=403)
    assert r.json()["error"]["code"] == "TOPUP_NOT_ALLOWED"
    assistant.config = saved
    billing.config = dataclasses.replace(billing.config, enabled=False)
    r = await alice.request("POST", "/v1/billing/topup", {}, expect=503)
    assert r.json()["error"]["code"] == "BILLING_NOT_AVAILABLE"
    assert fake_stripe(billing).calls.count("create_checkout") == 1  # only the subscription's page


async def test_topup_checkout_completed_credits_exactly_once(
    env: Env, billing: BillingService, assistant: AssistantService, alice: Browser
) -> None:
    await subscribe(env, billing, alice)
    results = await buy_topup(env, billing, alice)
    assert [r["status"] for r in results] == ["processed", "processed"]  # completed + async_payment_succeeded
    assert balance(env, alice.account_id) == 100
    purchases = sql(
        env.database_url,
        "SELECT status, requests, amount_cents, currency, provider_payment_intent_id IS NOT NULL, credited_at IS NOT NULL "
        "FROM topup_purchases WHERE account_id = %s",
        (alice.account_id,),
    )
    assert purchases == [("credited", 100, 299, "USD", True, True)]
    credited = sql(
        env.database_url,
        "SELECT detail FROM security_events WHERE account_id = %s AND kind = 'topup_credited'",
        (alice.account_id,),
    )
    assert credited == [({"requests": 100, "balance": 100},)]
    # a replayed completion (a new event id for the same page) credits nothing more
    session_id = next(iter(c for c in fake_stripe(billing).checkouts.values() if c.mode == "payment")).id
    obj = {"id": session_id, "object": "checkout.session", "customer": None, "mode": "payment"}
    await deliver(env, fake_stripe(billing).event("checkout.session.completed", obj))
    assert balance(env, alice.account_id) == 100
    status = await alice.get("/v1/account/billing", schema="billing_status_response")
    assert status["topup"]["balance_requests"] == 100
    allowance = (await alice.get("/v1/assistant", schema="assistant_status_response"))["allowance"]
    assert allowance["topup"] == {"balance": 100, "allowed": True}


async def test_unpaid_or_expired_topup_page_credits_nothing(
    env: Env, billing: BillingService, assistant: AssistantService, alice: Browser
) -> None:
    await subscribe(env, billing, alice)
    out = await alice.post("/v1/billing/topup", {}, schema="billing_checkout_response")
    fake = fake_stripe(billing)
    obj = {"id": out["session_id"], "object": "checkout.session", "customer": None, "mode": "payment"}
    r = await deliver(env, fake.event("checkout.session.completed", obj))  # a forged-looking "completed": still unpaid
    assert r["status"] == "ignored" and balance(env, alice.account_id) == 0
    await deliver(env, fake.checkout_expired(out["session_id"]))
    rows = sql(env.database_url, "SELECT status FROM topup_purchases WHERE account_id = %s", (alice.account_id,))
    assert rows == [("expired",)] and balance(env, alice.account_id) == 0


async def test_topup_balance_limit_409(
    env: Env, billing: BillingService, assistant: AssistantService, alice: Browser
) -> None:
    await subscribe(env, billing, alice)
    await buy_topup(env, billing, alice)
    sql(env.database_url, "UPDATE ai_balances SET topup_remaining = 901 WHERE account_id = %s", (alice.account_id,))
    r = await alice.request("POST", "/v1/billing/topup", {}, expect=409)
    assert r.json()["error"]["code"] == "TOPUP_BALANCE_LIMIT"
    sql(env.database_url, "UPDATE ai_balances SET topup_remaining = 800 WHERE account_id = %s", (alice.account_id,))
    await alice.post("/v1/billing/topup", {}, schema="billing_checkout_response")
    await alice.post("/v1/billing/topup", {}, schema="billing_checkout_response")
    # two pages are open: 800 + 200 held, a third would pass the cap even before either is paid
    r = await alice.request("POST", "/v1/billing/topup", {}, expect=409)
    assert r.json()["error"]["code"] == "TOPUP_BALANCE_LIMIT"


async def test_turns_use_window_then_topup_and_report_charged_from(
    env: Env, billing: BillingService, assistant: AssistantService, alice: Browser
) -> None:
    no_dev_grant(assistant)
    await subscribe(env, billing, alice)
    await buy_topup(env, billing, alice)
    await consent(alice)
    first = (await turn(alice)).json()
    assert first["charged"] is True and first["charged_from"] == "window"
    assert first["allowance"]["window"]["limit"] == 30 and first["allowance"]["window"]["used"] == 1
    # the rest of the 5-hour window is used up
    sql(
        env.database_url,
        "UPDATE usage_windows SET requests_used = 30 WHERE account_id = %s AND kind = 'window'",
        (alice.account_id,),
    )
    nxt = (await turn(alice)).json()
    assert nxt["charged_from"] == "topup" and nxt["allowance"]["topup"] == {"balance": 99, "allowed": True}
    assert nxt["allowance"]["exhausted"] is None  # the top-up still pays
    assert balance(env, alice.account_id) == 99
    sources = sql(
        env.database_url,
        "SELECT source FROM assistant_turns WHERE account_id = %s AND status = 'answered' ORDER BY created_at",
        (alice.account_id,),
    )
    assert sources == [("window",), ("topup",)]
    # the top-up keeps paying until it is empty, then the full window refuses and names its end
    sql(env.database_url, "UPDATE ai_balances SET topup_remaining = 1 WHERE account_id = %s", (alice.account_id,))
    assert (await turn(alice)).json()["charged_from"] == "topup"
    r = await turn(alice, expect=429)
    err = r.json()["error"]
    assert err["code"] == "AI_ALLOWANCE_EXHAUSTED" and err["detail"]["reason"] == "window"
    assert err["detail"]["resets_at"] == nxt["allowance"]["window"]["ends_at"]


async def test_topup_not_spendable_after_downgrade_but_kept(
    env: Env, billing: BillingService, assistant: AssistantService, alice: Browser
) -> None:
    no_dev_grant(assistant)
    sub_id = await subscribe(env, billing, alice)
    await buy_topup(env, billing, alice)
    await deliver(env, fake_stripe(billing).delete_subscription(sub_id))
    status = await alice.get("/v1/account/billing", schema="billing_status_response")
    assert status["plan"] == "free" and status["topup"]["allowed"] is False
    assert status["topup"]["balance_requests"] == 100  # kept across the downgrade
    await consent(alice)
    out = (await turn(alice)).json()
    assert out["charged_from"] == "taste" and out["allowance"]["kind"] == "taste"
    assert out["allowance"]["topup"] == {"balance": 100, "allowed": False}
    sql(env.database_url, "UPDATE ai_balances SET taste_used = 10 WHERE account_id = %s", (alice.account_id,))
    err = (await turn(alice, expect=429)).json()["error"]
    assert (err["detail"]["reason"], err["detail"]["resets_at"]) == ("taste", None)
    assert balance(env, alice.account_id) == 100
    # back on a paid plan the balance is spendable again
    await subscribe(env, billing, alice, "pro_plus")
    allowance = (await alice.get("/v1/assistant", schema="assistant_status_response"))["allowance"]
    assert allowance["topup"] == {"balance": 100, "allowed": True} and allowance["kind"] == "windows"
