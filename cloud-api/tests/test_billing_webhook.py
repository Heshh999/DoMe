"""The webhook route (docs/BILLING.md §6; docs/SECURITY.md T24): signature over the raw body with a tolerance, size cap
before parsing, no browser Origin, deduplication by event id, convergence under any delivery order, unknown and
unattributable events recorded, a processing failure marked and retried, nothing without a secret, and no email or raw
body in any log line or table."""

from __future__ import annotations

import dataclasses
import itertools
import json
import time
from collections.abc import AsyncIterator
from typing import Any

import pytest
from starlette.requests import Request

from dome_api.billing.fake_provider import FakeStripe, _Subscription, sign
from dome_api.billing.provider import ProviderError
from dome_api.billing.service import BillingService
from dome_api.errors import ApiError
from dome_api.routes import billing as billing_routes
from tests.conftest import AgentSim, Browser, Env, deliver, deliver_all, fake_stripe, sql


def events(env: Env, **where: Any) -> list[dict[str, Any]]:
    cols = "provider_event_id, event_type, kind, status, attempts, error, account_id::text, payload_digest"
    clauses = " AND ".join(f"{k} = %s" for k in where) or "TRUE"
    rows = sql(
        env.database_url, f"SELECT {cols} FROM billing_events WHERE {clauses} ORDER BY id", tuple(where.values())
    )
    return [dict(zip([c.strip().split("::")[0] for c in cols.split(",")], row, strict=True)) for row in rows]


def account_plan(env: Env, account_id: str) -> str:
    return str(sql(env.database_url, "SELECT plan FROM accounts WHERE id = %s", (account_id,))[0][0])


def sub_state(env: Env, account_id: str) -> tuple[str, str] | None:
    rows = sql(
        env.database_url,
        "SELECT state, plan FROM subscriptions WHERE account_id = %s ORDER BY updated_at DESC LIMIT 1",
        (account_id,),
    )
    return (rows[0][0], rows[0][1]) if rows else None


async def checkout(alice: Browser, plan: str = "pro", interval: str = "monthly") -> str:
    out = await alice.post(
        "/v1/billing/checkout", {"plan": plan, "interval": interval}, schema="billing_checkout_response"
    )
    return str(out["session_id"])


async def test_signature_missing_invalid_or_stale_is_400_and_oversized_413_and_origin_403(
    env: Env, billing: BillingService
) -> None:
    raw, header = fake_stripe(billing).event("ping", {"object": "x"})
    base = {"Content-Type": "application/json"}
    r = await env.http.post("/v1/billing/webhook", content=raw, headers=base)
    assert r.status_code == 400 and r.json()["error"]["code"] == "BILLING_WEBHOOK_INVALID"
    r = await env.http.post("/v1/billing/webhook", content=raw, headers={**base, "Stripe-Signature": "t=1,v1=00"})
    assert r.status_code == 400
    forged = sign(raw, secret="whsec_wrong")
    r = await env.http.post("/v1/billing/webhook", content=raw, headers={**base, "Stripe-Signature": forged})
    assert r.status_code == 400
    tampered = raw[:-1] + b" "
    r = await env.http.post("/v1/billing/webhook", content=tampered, headers={**base, "Stripe-Signature": header})
    assert r.status_code == 400
    stale = sign(raw, at=int(time.time()) - 301)
    r = await env.http.post("/v1/billing/webhook", content=raw, headers={**base, "Stripe-Signature": stale})
    assert r.status_code == 400
    fresh_enough = sign(raw, at=int(time.time()) - 200)
    r = await env.http.post("/v1/billing/webhook", content=raw, headers={**base, "Stripe-Signature": fresh_enough})
    assert r.status_code == 200 and r.json()["status"] == "ignored"
    big = (
        b'{"id":"evt_big","object":"event","type":"ping","created":1,"data":{"object":{"pad":"'
        + b"x" * 70_000
        + b'"}}}'
    )
    r = await env.http.post("/v1/billing/webhook", content=big, headers={**base, "Stripe-Signature": sign(big)})
    assert r.status_code == 413 and r.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"
    r = await env.http.post(
        "/v1/billing/webhook", content=raw, headers={**base, "Stripe-Signature": header, "Origin": env.origin}
    )
    assert r.status_code == 403 and r.json()["error"]["code"] == "FORBIDDEN"
    garbage = b"not json at all"
    r = await env.http.post("/v1/billing/webhook", content=garbage, headers={**base, "Stripe-Signature": sign(garbage)})
    assert r.status_code == 400 and r.json()["error"]["code"] == "BILLING_WEBHOOK_INVALID"
    assert events(env, provider_event_id="evt_big") == []  # refused deliveries leave no receipt row


async def test_a_chunked_body_over_the_cap_is_refused_while_it_streams(env: Env, billing: BillingService) -> None:
    """A chunked request declares no Content-Length: the cap is counted while the body streams, so an unauthenticated
    sender can never make the server buffer more than 64 KiB."""

    async def oversized() -> AsyncIterator[bytes]:
        for _ in range(70):  # 70 KiB in 1 KiB chunks
            yield b"x" * 1024

    r = await env.http.post(
        "/v1/billing/webhook",
        content=oversized(),
        headers={"Content-Type": "application/json", "Stripe-Signature": sign(b"x")},
    )
    assert r.status_code == 413 and r.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"
    assert "content-length" not in {k.lower() for k in r.request.headers}  # it really was sent chunked
    # at the ASGI level: reading stops at the chunk that passes the cap; the rest of the stream is never asked for
    asked = 0

    async def receive() -> dict[str, Any]:
        nonlocal asked
        asked += 1
        return {"type": "http.request", "body": b"x" * 1024, "more_body": asked < 1000}

    scope = {"type": "http", "method": "POST", "path": "/v1/billing/webhook", "headers": [], "query_string": b""}
    with pytest.raises(ApiError) as refused:
        await billing_routes.webhook(Request(scope, receive), env.services)
    assert (refused.value.status, refused.value.code) == (413, "PAYLOAD_TOO_LARGE")
    assert asked == billing_routes.WEBHOOK_MAX_BYTES // 1024 + 1

    raw, header = fake_stripe(billing).event("ping", {"object": "x"}, event_id="evt_chunked_small")

    async def small() -> AsyncIterator[bytes]:
        for i in range(0, len(raw), 16):
            yield raw[i : i + 16]

    r = await env.http.post(
        "/v1/billing/webhook", content=small(), headers={"Content-Type": "application/json", "Stripe-Signature": header}
    )
    assert r.status_code == 200 and r.json()["status"] == "ignored"  # a chunked body under the cap is read whole
    assert [e["status"] for e in events(env, provider_event_id="evt_chunked_small")] == ["ignored"]


async def test_duplicate_delivery_acknowledged_once(
    env: Env, billing: BillingService, alice: Browser, online_agent: AgentSim
) -> None:
    session_id = await checkout(alice)
    deliveries = fake_stripe(billing).complete_checkout(session_id)
    first = await deliver_all(env, deliveries)
    assert [r["status"] for r in first] == ["processed"] * 3
    again = await deliver_all(env, deliveries + deliveries)
    assert all(r == {"received": True, "duplicate": True, "status": "duplicate"} for r in again)
    ids = [json.loads(raw)["id"] for raw, _ in deliveries]
    for event_id in ids:
        assert len(events(env, provider_event_id=event_id)) == 1
    plan_changes = sql(
        env.database_url,
        "SELECT count(*) FROM security_events WHERE account_id = %s AND kind = 'plan_changed'",
        (alice.account_id,),
    )[0][0]
    assert plan_changes == 1 and account_plan(env, alice.account_id) == "pro"
    snap = await online_agent.recv_type("grants_snapshot")
    assert "entitlement_assertion" in snap
    with pytest.raises(TimeoutError):  # one transition, one push: the replays pushed nothing
        await online_agent.recv_type("grants_snapshot", timeout=0.7)


@pytest.mark.parametrize("order", list(itertools.permutations(range(4))))
async def test_out_of_order_deliveries_converge(
    env: Env, billing: BillingService, alice: Browser, order: tuple[int, ...]
) -> None:
    """created / completed / paid / deleted in every order: the final row and plan are the same because every
    delivery fetches the subscription as it is now (canceled)."""
    session_id = await checkout(alice)
    fake = fake_stripe(billing)
    created, completed, paid = fake.complete_checkout(session_id)
    deleted = fake.delete_subscription(next(iter(fake.subscriptions)))
    await deliver_all(env, FakeStripe.deliveries_in_order([created, completed, paid, deleted], list(order)))
    assert sub_state(env, alice.account_id) == ("canceled", "pro") and account_plan(env, alice.account_id) == "free"
    s = await alice.get("/v1/session", schema="session_response")
    assert s["plan"] == "free" and s["entitlement_state"] == "expired"
    statuses = {e["status"] for e in events(env, account_id=alice.account_id)}
    assert statuses == {"processed"}


async def test_unknown_type_ignored_and_unattributed_recorded(env: Env, billing: BillingService) -> None:
    fake = fake_stripe(billing)
    r = await deliver(env, fake.event("product.created", {"id": "prod_x", "object": "product"}))
    assert r["status"] == "ignored"
    [row] = events(env, event_type="product.created")
    assert (row["status"], row["error"], row["kind"]) == ("ignored", "unhandled_type", "other")
    # a subscription DoMe never created (another Stripe account, a dashboard experiment) is recorded, not applied
    fake.subscriptions["sub_stranger"] = _Subscription(
        id="sub_stranger",
        customer_id="cus_stranger",
        status="active",
        price_id="price_fake_pro_monthly",
        period_start=fake.clock(),
        period_end=fake.clock(),
    )
    r = await deliver(
        env,
        fake.event(
            "customer.subscription.updated",
            {"id": "sub_stranger", "object": "subscription", "customer": "cus_stranger", "status": "active"},
        ),
    )
    assert r["status"] == "ignored"
    [row] = [e for e in events(env, event_type="customer.subscription.updated") if e["error"] == "unattributed"]
    assert row["account_id"] is None and row["kind"] == "subscription"
    # a dispute is for the operator: recorded, logged, nothing changes
    r = await deliver(
        env, fake.event("charge.dispute.created", {"id": "dp_1", "object": "dispute", "customer": "cus_stranger"})
    )
    assert r["status"] == "ignored"
    assert events(env, event_type="charge.dispute.created")[0]["error"] == "operator_action"


async def test_processing_failure_marks_failed_500_and_redelivery_or_reconciler_recovers(
    env: Env, billing: BillingService, alice: Browser, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_id = await checkout(alice)
    fake = fake_stripe(billing)
    created, completed, paid = fake.complete_checkout(session_id)
    fake.fail_next.append("retrieve_subscription")
    r = await deliver(env, created, expect=500)
    assert r["error"]["code"] == "INTERNAL"
    event_id = json.loads(created[0])["id"]
    [row] = events(env, provider_event_id=event_id)
    assert (row["status"], row["attempts"], row["error"]) == ("failed", 1, "ProviderError")
    assert account_plan(env, alice.account_id) == "free"
    # Stripe redelivers the same event after the 500: the failed row is processed again, not taken for a duplicate
    r = await deliver(env, created)
    assert r == {"received": True, "duplicate": False, "status": "processed"}
    [row] = events(env, provider_event_id=event_id)
    assert (row["status"], row["attempts"]) == ("processed", 2) and account_plan(env, alice.account_id) == "pro"
    assert (await deliver(env, created))["status"] == "duplicate"  # a processed event is never processed twice
    # without a redelivery the reconciler retries a failed row
    fake.fail_next.append("retrieve_subscription")
    await deliver(env, completed, expect=500)
    completed_id = json.loads(completed[0])["id"]
    counts = await billing.reconcile()
    assert counts["events_retried"] >= 1  # other tests' failed rows share the database
    [row] = events(env, provider_event_id=completed_id)
    assert row["status"] == "processed"
    # a row that keeps failing stops being retried after five attempts (this subscription's fetch always fails;
    # the reconciler's other fetches, for rows of other tests, are unaffected)
    sub_id = fake.checkouts[session_id].subscription_id
    original = fake.retrieve_subscription

    async def always_fails(subscription_id: str) -> Any:
        if subscription_id == sub_id:
            raise ProviderError("provider_error", status=500)
        return await original(subscription_id)

    monkeypatch.setattr(fake, "retrieve_subscription", always_fails)
    await deliver(env, paid, expect=500)
    paid_id = json.loads(paid[0])["id"]
    for _ in range(6):
        await billing.reconcile()
    [row] = events(env, provider_event_id=paid_id)
    assert row["status"] == "failed" and row["attempts"] == 5
    assert (await deliver(env, paid))["status"] == "duplicate"  # past the limit only an operator replays it


async def test_webhook_disabled_without_secret_503(env: Env, billing: BillingService) -> None:
    raw, header = fake_stripe(billing).event("ping", {"object": "x"})
    billing.config = dataclasses.replace(billing.config, enabled=False)
    r = await env.http.post(
        "/v1/billing/webhook", content=raw, headers={"Content-Type": "application/json", "Stripe-Signature": header}
    )
    assert r.status_code == 503 and r.json()["error"]["code"] == "BILLING_NOT_AVAILABLE"
    billing.config = dataclasses.replace(billing.config, enabled=True)
    billing.provider = None  # a provider that could not be built
    r = await env.http.post(
        "/v1/billing/webhook", content=raw, headers={"Content-Type": "application/json", "Stripe-Signature": header}
    )
    assert r.status_code == 503
    assert events(env, event_type="ping") == [] or all(e["status"] == "ignored" for e in events(env, event_type="ping"))


async def test_webhook_logs_carry_no_email_or_body(
    env: Env, billing: BillingService, alice: Browser, capfd: pytest.CaptureFixture[str]
) -> None:
    """The customer's email reaches the provider once (receipts) and nothing else: no log line, no table but the fake's
    own store. Raw bodies and signatures never appear either."""
    capfd.readouterr()
    sentinel = alice.email  # user-...@example.test, the only place the email is: the OIDC identity
    session_id = await checkout(alice)
    fake = fake_stripe(billing)
    customer = fake.customer_for_account(alice.account_id)
    assert customer is not None and customer.email == sentinel
    deliveries = fake.complete_checkout(session_id)
    await deliver_all(env, deliveries)
    await deliver(env, fake.event("charge.refunded", {"id": "ch_1", "object": "charge", "customer": customer.id}))
    out = capfd.readouterr()
    logs = out.out + out.err
    assert "billing.webhook" in logs and "billing.transition" in logs
    assert sentinel not in logs and customer.email not in logs
    for raw, header in deliveries:
        assert raw.decode() not in logs and header.split(",")[1] not in logs
        assert json.loads(raw)["data"]["object"]["id"] not in logs  # object ids stay in tables, not in logs
    tables = sql(env.database_url, "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
    for (table,) in tables:
        if table == "accounts":
            continue  # the account row itself carries the OIDC email, as before billing
        stored = sql(env.database_url, f'SELECT t::text FROM "{table}" t')
        assert not any(sentinel in row[0] for row in stored), table
        for raw, _ in deliveries:
            assert not any(raw.decode() in row[0] for row in stored), table


def test_billing_fields_are_redacted_in_any_log_line() -> None:
    """Defence in depth: billing never passes these to a logger, and the redaction would drop them if it did."""
    from dome_api.logging import redact

    line = {
        "payload": "{...}",
        "raw_body": "{...}",
        "signature": "t=1,v1=00",
        "stripe_signature": "t=1,v1=00",
        "email": "someone@example.test",
        "customer_email": "someone@example.test",
        "name": "Card Holder",
        "address": {"line1": "1 Main St"},
        "stripe_secret_key": "sk_test_x",
        "webhook_secret": "whsec_x",
        "event_type": "invoice.paid",
    }
    out = redact(line)
    assert out["event_type"] == "invoice.paid"
    assert all(v == "[redacted]" for k, v in out.items() if k != "event_type")
