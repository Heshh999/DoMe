"""Protocol 1.3 billing bodies (docs/PROTOCOL.md §20; shared/protocol/README.md "Protocol 1.3"): every new definition
accepts its example and refuses unknown fields and out-of-enum values; the new error codes exist; the shared
``fixtures/ai-window-cases.json`` loads and every case that carries limits/used evaluates the way cloud-api's metering
does (``exhausted_reason`` below is the reference arithmetic of rules.ai_allowance)."""

import copy
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from dome_protocol import ProtocolError, load_registry, load_schemas

FIXTURE = json.loads((Path(__file__).resolve().parents[2] / "protocol" / "fixtures" / "ai-window-cases.json").read_text(encoding="utf-8"))
TS = "2026-10-24T13:00:00.000Z"
UUID = "44444444-4444-4444-8444-444444444444"
UUID2 = "55555555-5555-4555-8555-555555555555"
PRICING = {"currency": "USD", "plans": {"pro": {"monthly_cents": 999, "annual_cents": 9999}, "pro_plus": {"monthly_cents": 2499, "annual_cents": 24999}}, "topup": {"requests": 100, "price_cents": 299, "max_balance_requests": 1000}}
PLANS_RESPONSE = {"plans": {"free": {}, "pro": {}, "pro_plus": {}}, "pricing": PRICING, "billing_enabled": False, "billing_mode": "off", "ai_enabled": False}
CHECKOUT_REQUEST = {"plan": "pro", "interval": "monthly"}
CHECKOUT_RESPONSE = {"kind": "subscription", "checkout_url": "https://checkout.stripe.com/c/pay/cs_test_a1", "session_id": "cs_test_a1", "expires_at": TS}
PORTAL = {"portal_url": "https://billing.stripe.com/p/session/x"}
WEBHOOK = {"received": True, "duplicate": False, "status": "processed"}
SUBSCRIPTION = {"state": "active", "plan": "pro", "interval": "monthly", "current_period_end": TS, "cancel_at_period_end": False, "grace_until": None, "checkout_expires_at": None}
STATUS = {
    "billing_enabled": True,
    "billing_mode": "test",
    "plan": "pro",
    "entitlement_state": "active",
    "subscription": SUBSCRIPTION,
    "checkout_available": False,
    "checkout_plans": [],
    "portal_available": True,
    "pricing": PRICING,
    "topup": {"allowed": True, "balance_requests": 0, "requests": 100, "price_cents": 299},
    "selection": None,
}
SELECTION_REQUEST = {"pc_ids": [UUID], "controller_ids": [UUID, UUID2]}
SELECTION_RESPONSE = {
    "open": True,
    "deadline": TS,
    "plan": "free",
    "limits": {"max_enabled_pcs": 1, "max_controllers": 2},
    "pcs": [{"id": UUID, "name": "Desk PC", "selected": True, "last_seen": TS}, {"id": UUID2, "name": "Laptop", "selected": False, "last_seen": None}],
    "controllers": [{"id": UUID, "display_name": "Phone", "selected": True, "last_seen": None}],
}


def ok(name, value):
    load_schemas().validate_rest(name, value)


def bad(name, value):
    with pytest.raises(ProtocolError) as ei:
        load_schemas().validate_rest(name, value)
    assert ei.value.code == "MALFORMED_MESSAGE"


def with_(base, path, value):
    out = copy.deepcopy(base)
    node = out
    for key in path[:-1]:
        node = node[key]
    if value is ...:
        del node[path[-1]]
    else:
        node[path[-1]] = value
    return out


EXAMPLES = [
    ("plans_response", PLANS_RESPONSE),
    ("pricing", PRICING),
    ("billing_checkout_request", CHECKOUT_REQUEST),
    ("billing_checkout_response", CHECKOUT_RESPONSE),
    ("billing_portal_response", PORTAL),
    ("billing_webhook_response", WEBHOOK),
    ("billing_status_response", STATUS),
    ("plan_selection_request", SELECTION_REQUEST),
    ("plan_selection_response", SELECTION_RESPONSE),
]


@pytest.mark.parametrize(("name", "value"), EXAMPLES)
def test_examples_accepted(name, value):
    ok(name, value)


@pytest.mark.parametrize(("name", "value"), EXAMPLES)
def test_unknown_top_level_field_refused(name, value):
    bad(name, {**value, "extra": "x"})


def test_plan_ids():
    for plan in ("free", "pro", "pro_plus"):
        ok("plan_id", plan)
    bad("plan_id", "enterprise")
    for plan in ("pro", "pro_plus"):
        ok("paid_plan_id", plan)
    bad("paid_plan_id", "free")
    for mode in ("off", "test", "live"):
        ok("billing_mode", mode)
    bad("billing_mode", "sandbox")
    bad("billing_interval", "weekly")


def test_plans_response_and_pricing():
    ok("plans_response", {**PLANS_RESPONSE, "billing_enabled": True, "billing_mode": "live"})
    bad("plans_response", with_(PLANS_RESPONSE, ["billing_mode"], ...))
    ok("plans_response", {**PLANS_RESPONSE, "ai_enabled": True})
    bad("plans_response", with_(PLANS_RESPONSE, ["ai_enabled"], ...))  # the website must know whether AI can be used
    bad("plans_response", {**PLANS_RESPONSE, "ai_enabled": "off"})
    bad("plans_response", {**PLANS_RESPONSE, "pricing": {"currency": "USD", "monthly_cents": 999, "annual_cents": 9999}})  # the 1.2 shape
    bad("pricing", with_(PRICING, ["currency"], "usd"))
    bad("pricing", with_(PRICING, ["plans", "free"], {"monthly_cents": 0, "annual_cents": 0}))
    bad("pricing", with_(PRICING, ["plans", "pro", "monthly_cents"], -1))
    bad("pricing", with_(PRICING, ["plans", "pro", "weekly_cents"], 1))
    bad("pricing", with_(PRICING, ["topup", "requests"], 0))
    bad("pricing", with_(PRICING, ["topup", "max_balance_requests"], ...))
    # the display prices in plans.json are what the contract's pricing object carries
    assert load_registry().plans["pricing_defaults"]["plans"] == PRICING["plans"]
    assert load_registry().plans["pricing_defaults"]["topup"] == PRICING["topup"]
    assert load_registry().plans["pricing_defaults"]["currency"] == "USD"


def test_checkout_bodies():
    ok("billing_checkout_request", {"plan": "pro_plus", "interval": "annual"})
    bad("billing_checkout_request", {"plan": "free", "interval": "monthly"})
    bad("billing_checkout_request", {"plan": "pro", "interval": "weekly"})
    bad("billing_checkout_request", {"plan": "pro"})
    bad("billing_checkout_request", {"plan": "pro", "interval": "monthly", "price_id": "price_x"})  # the price is never the client's
    ok("billing_checkout_response", {**CHECKOUT_RESPONSE, "kind": "topup"})
    bad("billing_checkout_response", {**CHECKOUT_RESPONSE, "kind": "payment"})
    bad("billing_checkout_response", {**CHECKOUT_RESPONSE, "checkout_url": "http://checkout.stripe.com/x"})
    bad("billing_checkout_response", {**CHECKOUT_RESPONSE, "checkout_url": "https://" + "x" * 2048})
    bad("billing_checkout_response", {**CHECKOUT_RESPONSE, "session_id": ""})
    bad("billing_portal_response", {"portal_url": "javascript:alert(1)"})
    for status in ("processed", "ignored", "failed", "duplicate"):
        ok("billing_webhook_response", {**WEBHOOK, "status": status})
    bad("billing_webhook_response", {**WEBHOOK, "status": "queued"})


def test_subscription_states_and_status():
    for state in ("checkout_pending", "incomplete", "trialing", "active", "cancel_scheduled", "past_due", "unpaid", "canceled"):
        ok("subscription_state", state)
        ok("billing_status_response", with_(STATUS, ["subscription", "state"], state))
    bad("subscription_state", "disputed")
    for ent in ("free", "active", "pending", "past_due", "canceling", "expired"):
        ok("billing_status_response", {**STATUS, "entitlement_state": ent})
    bad("billing_status_response", {**STATUS, "entitlement_state": "paused"})
    ok("billing_status_response", {**STATUS, "plan": "free", "subscription": None, "checkout_available": True, "portal_available": False, "billing_enabled": False, "billing_mode": "off"})
    ok("billing_status_response", {**STATUS, "plan": "free", "subscription": None, "checkout_available": True, "checkout_plans": ["pro", "pro_plus"]})
    ok("billing_status_response", {**STATUS, "plan": "free", "subscription": None, "checkout_available": True, "checkout_plans": ["pro"]})  # AI off: no Pro Plus
    bad("billing_status_response", {**STATUS, "checkout_plans": ["free"]})
    bad("billing_status_response", {**STATUS, "checkout_plans": ["pro", "pro"]})
    bad("billing_status_response", with_(STATUS, ["checkout_plans"], ...))
    ok("billing_status_response", {**STATUS, "subscription": {**SUBSCRIPTION, "state": "checkout_pending", "interval": None, "current_period_end": None, "checkout_expires_at": TS}})
    ok("billing_status_response", {**STATUS, "subscription": {**SUBSCRIPTION, "state": "past_due", "grace_until": TS}})
    ok("billing_status_response", {**STATUS, "selection": {"open": True, "deadline": TS}})
    bad("billing_status_response", {**STATUS, "selection": {"open": True}})
    bad("billing_status_response", with_(STATUS, ["subscription", "plan"], "free"))
    bad("billing_status_response", with_(STATUS, ["subscription", "customer_id"], "cus_x"))  # provider ids never reach the phone
    bad("billing_status_response", with_(STATUS, ["topup", "balance_requests"], -1))
    bad("billing_status_response", with_(STATUS, ["topup", "requests"], 0))
    bad("billing_status_response", with_(STATUS, ["topup", "price_cents"], ...))


def test_plan_selection_bodies():
    ok("plan_selection_request", {"pc_ids": [], "controller_ids": []})
    bad("plan_selection_request", {"pc_ids": [UUID, UUID], "controller_ids": []})  # uniqueItems
    bad("plan_selection_request", {"pc_ids": [UUID] * 17, "controller_ids": []})
    bad("plan_selection_request", {"pc_ids": ["not-a-uuid"], "controller_ids": []})
    bad("plan_selection_request", {"pc_ids": []})
    ok("plan_selection_response", {**SELECTION_RESPONSE, "open": False, "deadline": None, "plan": "pro"})
    bad("plan_selection_response", with_(SELECTION_RESPONSE, ["pcs", 0, "enabled"], True))
    bad("plan_selection_response", with_(SELECTION_RESPONSE, ["controllers", 0, "kid"], "x"))
    bad("plan_selection_response", with_(SELECTION_RESPONSE, ["pcs", 0, "name"], "x" * 65))
    bad("plan_selection_response", with_(SELECTION_RESPONSE, ["limits", "routines"], True))


def test_error_codes_and_rule():
    reg = load_registry()
    expected = {
        "BILLING_NOT_AVAILABLE": False,
        "BILLING_PROVIDER_ERROR": True,
        "BILLING_WEBHOOK_INVALID": False,
        "ALREADY_SUBSCRIBED": False,
        "CHECKOUT_PROCESSING": True,
        "PLAN_NOT_AVAILABLE": False,
        "BILLING_NO_CUSTOMER": False,
        "TOPUP_NOT_ALLOWED": False,
        "TOPUP_BALANCE_LIMIT": False,
        "PLAN_SELECTION_CLOSED": False,
        "PLAN_SELECTION_INVALID": False,
    }
    for code, retryable in expected.items():
        assert reg.errors[code]["retryable"] is retryable, code
        assert reg.errors[code]["user_message"].endswith(".")
    exhausted = reg.errors["AI_ALLOWANCE_EXHAUSTED"]["user_message"]
    assert "month" not in exhausted and "refreshes" in exhausted
    from dome_protocol.registry import _load_json

    version = _load_json("version.json")
    assert version["protocol_version"] == "1.3"
    rule = version["rules"]["ai_allowance"]
    assert "AI_ALLOWANCE_EXHAUSTED{reason, resets_at}" in rule and "never renews" in rule and "'pro' for every paid plan" in rule


# ----- the shared allowance fixture ------------------------------------------------------------------------------------


def parse(ts):
    return datetime.fromisoformat(ts).astimezone(UTC)


def exhausted_reason(*, limits, used, topup_balance=0, topup_allowed=False, ends=None):
    """Reference arithmetic of rules.ai_allowance (cloud-api's metering.exhausted_reason agrees, see
    cloud-api/tests/test_assistant_metering.py): the window is checked before the week; when both are full the reason
    is the one whose end (``ends``: bucket -> ISO time) is later; a full bucket is paid by the top-up balance when the
    plan allows it. Returns (source, exhausted)."""
    if "taste" in limits:
        if used["taste"] >= limits["taste"]:
            return (None, "taste")
        return ("taste", None)
    full = [b for b in ("window", "week") if b in limits and used.get(b, 0) >= limits[b]]
    if not full:
        return ("window", None)
    if topup_allowed and topup_balance > 0:
        return ("topup", None)
    if len(full) == 2 and ends and parse(ends["window"]) > parse(ends["week"]):
        return (None, "window")
    return (None, full[-1])


def test_fixture_loads():
    assert FIXTURE["window_hours"] == 5 and FIXTURE["week_days"] == 7 and FIXTURE["warn_at_percent"] == 80
    plans = load_registry().plans["plans"]
    assert all(p["ai_window"]["hours"] == FIXTURE["window_hours"] and p["ai_week"]["days"] == FIXTURE["week_days"] for p in plans.values())
    assert len(FIXTURE["cases"]) >= 10 and len({c["name"] for c in FIXTURE["cases"]}) == len(FIXTURE["cases"])


@pytest.mark.parametrize("case", FIXTURE["cases"], ids=lambda c: c["name"])
def test_fixture_case(case):
    expect = case["expect"]
    if case.get("open_window", "x") is None and case.get("open_week", "x") is None:
        now = parse(case["now"])
        assert parse(expect["window_ends_at"]) == now + timedelta(hours=FIXTURE["window_hours"])
        assert parse(expect["week_ends_at"]) == now + timedelta(days=FIXTURE["week_days"])
    if "taste" in case:
        source, reason = exhausted_reason(limits={"taste": case["taste"]["limit"]}, used={"taste": case["taste"]["used"]})
        assert (source, reason) == (None, expect["exhausted"]) and expect["resets_at"] is None
    if "limits" in case and "used" in case and "warn" not in expect:
        ends = {b: case[f"{b}_ends_at"] for b in ("window", "week") if f"{b}_ends_at" in case}
        source, reason = exhausted_reason(limits=case["limits"], used=case["used"], topup_balance=case.get("topup_balance", 0), topup_allowed=case.get("topup_allowed", False), ends=ends)
        assert (source, reason) == (expect["source"], expect["exhausted"]), case["name"]
        if reason is not None:
            assert expect["resets_at"] == case[f"{reason}_ends_at"]
            assert parse(expect["resets_at"]) > parse(case["now"])
    if "warn" in expect:
        used, limit = case["used"]["window"], case["limits"]["window"]
        assert (used * 100 >= limit * FIXTURE["warn_at_percent"]) is expect["warn"]
    if "display_new_york" in expect and "resets_at" in expect and expect["resets_at"] is not None:
        # the wording is the phone's (lib/billing.ts); the Python side only pins that the time named is the reset time
        assert parse(expect["resets_at"]) == parse(case.get("resets_at") or expect["resets_at"])
