"""The Deploy button asks Stripe itself (tests/fake_stripe.py) whether the seven Stripe values fit together, before any
flyctl call: which account the key belongs to and whether it has a name (and, live, may take payments); whether each
price exists for that key with the type, interval, currency and amount DoMe shows (plans.json); whether this
environment's webhook endpoint listens to the events DoMe needs; whether the customer portal was saved. A mismatch stops
the run for the founder with the fix and changes nothing; a restricted key that may not read something, or a portal
setting that differs from the guide, is a warning; Stripe not answering never stops a deploy. The key never appears in
the log or the summary, not even when Stripe quotes it."""

from __future__ import annotations

import io
import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from conftest import STRIPE_LIVE_KEY, STRIPE_PRICES, STRIPE_TEST_KEY, Run, World, stripe_env
from fake_stripe import ACCOUNT_ID, API, API_VERSION
from test_deploy_billing import PRODUCTION_HOOK, STAGING_HOOK, assert_stripe_hygiene

import dome_deploy
from dome_deploy import HttpFailure, HttpResult, SecretBook, Stop

ROOT = Path(__file__).resolve().parents[3]
RESTRICTED_KEY = "rk_" + "test_" + "51FakeRestrictedKey0123456789abcdef"
SANDBOX = f"**domeremote sandbox** (`{ACCOUNT_ID}`)"
ALL_MATCH = f"| Stripe account | {SANDBOX}, Test mode: 5 prices, webhook endpoint and customer portal match |"
PRICE_PATHS = [f"/v1/prices/{p}?expand%5B%5D=product" for p in STRIPE_PRICES.values()]
READS = [
    "/v1/account",
    *PRICE_PATHS,
    "/v1/webhook_endpoints?limit=100",
    "/v1/billing_portal/configurations?is_default=true",
]
PRO_MONTHLY = "DOME_STRIPE_PRICE_PRO_MONTHLY"
PRO_ANNUAL = "DOME_STRIPE_PRICE_PRO_ANNUAL"
PLUS_ANNUAL = "DOME_STRIPE_PRICE_PRO_PLUS_ANNUAL"
TOPUP = "DOME_STRIPE_PRICE_TOPUP"


def staging(world: World, key: str = STRIPE_TEST_KEY) -> Run:
    world.staging_ready_web(billing="test")
    return world.run("deploy", **stripe_env(key=key))


def production(world: World, key: str = STRIPE_LIVE_KEY) -> Run:
    world.production_ready_web(billing="live" if key.startswith(("sk_live_", "rk_live_")) else "test")
    world.add_cluster("domeremote-production-db")
    return world.run("deploy", DEPLOY_ENVIRONMENT="production", **stripe_env(key=key))


def row(run: Run) -> str:
    [line] = [line for line in run.summary.splitlines() if line.startswith("| Stripe account |")]
    return line


def stopped(world: World, run: Run, *extra: str) -> None:
    """A Stripe stop: needs you, nothing changed, not one flyctl call, and no secret anywhere."""
    assert run.code == 1, run.summary
    assert "## Stopped: needs you" in run.summary
    assert "Stripe's own answers show that payments would not work" in run.summary
    # what the phone says for a refused setup since 2026-10-23 (mobile-app lib/labels.ts), not "didn't answer"
    assert "customers would see \"Payments aren't working on DoMe's side right now\"" in run.summary
    assert "Nothing was created or changed." in run.summary
    # the Billing row does not claim the values reached Fly: this run stopped before changing anything
    assert "stored in Fly" not in run.summary.replace("nothing was stored in Fly", "")
    assert "nothing was stored in Fly, as this run stopped before changing anything" in run.summary
    assert world.calls() == [], world.commands()
    assert run.summary.rstrip().count("Run Deploy again.") == 1
    assert_stripe_hygiene(world, run, *extra)


def deployed(world: World, run: Run, *extra: str) -> None:
    assert run.code == 0, run.summary
    assert "flyctl deploy" in world.commands()
    assert_stripe_hygiene(world, run, *extra)


# ======================================================================================================
# a matching setup
# ======================================================================================================


def test_a_matching_stripe_setup_is_named_in_the_summary_before_any_flyctl_call(world: World) -> None:
    run = staging(world)
    deployed(world, run)
    assert ALL_MATCH in run.summary
    assert "Stripe check:" not in run.summary
    # eight reads, in this order; GET only, the key only in the header, Stripe's API version, no redirect followed
    assert world.stripe.paths() == READS
    for url, headers in world.stripe.requests:
        assert headers["Authorization"] == f"Bearer {STRIPE_TEST_KEY}"
        assert headers["Stripe-Version"] == API_VERSION == dome_deploy.STRIPE_API_VERSION
        assert STRIPE_TEST_KEY not in url and url in world.web.no_redirect_requests
    assert run.stdout.index("==> Asking Stripe") < run.stdout.index("$ flyctl")
    rows = [line.split(" | ")[0] for line in run.summary.splitlines() if line.startswith("| ")]
    assert rows.index("| Stripe account") < rows.index("| Fly app domeremote-staging")


def test_production_with_a_live_key_reads_live_mode_and_its_own_webhook(world: World) -> None:
    world.stripe.endpoint(STAGING_HOOK, "live")["status"] = "disabled"  # not production's: does not matter
    run = production(world)
    deployed(world, run, STRIPE_LIVE_KEY)
    assert (
        row(run) == f"| Stripe account | {SANDBOX}, Live mode: 5 prices, webhook endpoint and customer portal match |"
    )
    assert all(h["Authorization"] == f"Bearer {STRIPE_LIVE_KEY}" for _, h in world.stripe.requests)


def test_production_with_a_test_key_reads_test_mode(world: World) -> None:
    world.stripe.account["charges_enabled"] = False  # Live mode is not activated yet: Test mode does not need it
    run = production(world, STRIPE_TEST_KEY)
    deployed(world, run)
    assert f"{SANDBOX}, Test mode: 5 prices, webhook endpoint and customer portal match" in row(run)


def test_billing_off_asks_stripe_nothing(world: World) -> None:
    world.staging_ready_web()
    run = world.run("deploy")
    assert run.code == 0, run.summary
    assert world.stripe.requests == [] and "Stripe account" not in run.summary


@pytest.mark.parametrize(
    ("display", "business", "shown"),
    [
        (None, "DoMe Remote", "**DoMe Remote**"),
        ("", "DoMe Remote", "**DoMe Remote**"),
        ("domeremote | sandbox `x`", None, "**domeremote  sandbox x**"),
    ],
)
def test_the_account_is_shown_by_its_dashboard_name_else_its_business_name(
    world: World, display: str | None, business: str | None, shown: str
) -> None:
    world.stripe.account["settings"]["dashboard"]["display_name"] = display
    world.stripe.account["business_profile"]["name"] = business
    run = staging(world)
    deployed(world, run)
    assert row(run).startswith(f"| Stripe account | {shown} (`{ACCOUNT_ID}`), Test mode: 5 prices")


def test_an_account_id_that_is_not_stripe_shaped_is_not_shown(world: World) -> None:
    world.stripe.account["id"] = "acct_1Fake`|**x**"
    run = staging(world)
    deployed(world, run)
    assert (
        row(run)
        == "| Stripe account | **domeremote sandbox**, Test mode: 5 prices, webhook endpoint and customer portal match |"
    )
    assert "acct_1Fake" not in run.summary


# ======================================================================================================
# the account and the key
# ======================================================================================================


def test_an_account_without_any_name_stops_with_where_to_set_one(world: World) -> None:
    world.stripe.account["settings"]["dashboard"]["display_name"] = "   "
    world.stripe.account["business_profile"]["name"] = None
    run = staging(world)
    stopped(world, run)
    assert f"The Stripe account this key belongs to (`{ACCOUNT_ID}`) has neither an account name nor a" in run.summary
    assert "you must set an account or business name" in run.summary
    assert "**Settings** > **Business** and fill in the **Public business name**" in run.summary
    assert row(run) == (
        f"| Stripe account | an account without a name (`{ACCOUNT_ID}`), Test mode: **does not match** what DoMe "
        "needs (see the problem above) |"
    )
    assert world.stripe.paths() == READS  # everything else was still checked, so one run lists every fix


def test_a_live_account_that_may_not_take_payments_stops_production(world: World) -> None:
    world.stripe.account["charges_enabled"] = False
    run = production(world)
    stopped(world, run, STRIPE_LIVE_KEY)
    assert f"Stripe has not activated payments for {SANDBOX} yet" in run.summary
    assert "**Activate payments**" in run.summary and "keep production in Test mode" in run.summary


@pytest.mark.parametrize("echo", [False, True])
def test_a_key_stripe_refuses_stops_and_is_never_shown(world: World, echo: bool) -> None:
    world.stripe.revoked.add(STRIPE_TEST_KEY)
    world.stripe.echo_key = echo  # stripe-mock quotes the whole Authorization header in its 401
    run = staging(world)
    stopped(world, run)
    assert "Stripe refused the secret key in `DOME_STRIPE_SECRET_KEY` (HTTP 401)" in run.summary
    assert "(Stripe says: Invalid API Key provided: " in run.summary
    assert "**Developers** > **API keys**, click **Reveal** next to **Secret key**" in run.summary
    assert "check the account name at the top left" in run.summary
    assert row(run) == "| Stripe account | **key refused by Stripe** (HTTP 401) |"
    assert world.stripe.paths() == ["/v1/account"]
    tail = STRIPE_TEST_KEY[-12:]
    assert tail not in run.summary and tail not in run.stdout and "Bearer" not in run.summary


@pytest.mark.parametrize(
    ("resource", "what", "matched"),
    [
        ("account", "the account", "5 prices, webhook endpoint and customer portal match"),
        ("prices", "the prices", "webhook endpoint and customer portal match"),
        ("webhook_endpoints", "the webhook endpoints", "5 prices and customer portal match"),
        ("billing_portal", "the customer portal settings", "5 prices and webhook endpoint match"),
    ],
)
def test_a_restricted_key_that_may_not_read_something_is_a_warning(
    world: World, resource: str, what: str, matched: str
) -> None:
    world.stripe.forbidden[RESTRICTED_KEY] = {resource}
    run = staging(world, RESTRICTED_KEY)
    deployed(world, run, RESTRICTED_KEY)
    lead = "Test mode" if resource == "account" else f"{SANDBOX}, Test mode"
    assert row(run) == (
        f"| Stripe account | {lead}: {matched}; not checked: {what} (the key may not read them); 1 warning in the "
        "Notes |"
    )
    assert f"- Stripe check: Stripe would not let this key read {what} (HTTP 403" in run.summary
    assert "the restricted key > **Edit**" in run.summary


# ======================================================================================================
# the five prices
# ======================================================================================================


def test_prices_from_another_account_or_sandbox_stop_with_the_same_account_fix(world: World) -> None:
    # the key of the main account's Test mode, the prices made in the sandbox: the key's account knows none of them
    world.stripe.account["settings"]["dashboard"]["display_name"] = "DoMe"
    for mode in ("test", "live"):  # another account: its ids exist in neither mode of this one
        world.stripe.modes[mode]["prices"].clear()
    run = staging(world)
    stopped(world, run)
    assert (
        f"Stripe does not know any of the five price ids in **DoMe** (`{ACCOUNT_ID}`), Test mode, where "
        "`DOME_STRIPE_SECRET_KEY` belongs."
    ) in run.summary
    assert "different Stripe accounts or sandboxes" in run.summary and '"domeremote sandbox"' in run.summary
    assert "or a price was deleted" not in run.summary
    assert f"check the account name at the top left (this key belongs to **DoMe** (`{ACCOUNT_ID}`))" in run.summary
    assert "copy from inside it both the secret key (**Developers** > **API keys**, `sk_test_...`)" in run.summary
    assert "No such price" not in run.summary  # explained, not quoted
    assert "exists in the other mode" not in run.summary
    assert "Signing secret** of its endpoint: copy it again from there if unsure" in run.summary


def test_a_key_from_another_account_is_not_asked_about_that_accounts_webhook_or_portal(world: World) -> None:
    """The key's account knows none of the prices, so it is the wrong account: its webhook endpoint and portal are not
    checked, and the founder is not told to add an endpoint there (whose signing secret would then be the wrong one,
    which no check can see) or to save its portal. One fix: the key and the prices from the same sandbox."""
    world.stripe.account["settings"]["dashboard"]["display_name"] = "DoMe"
    for mode in ("test", "live"):
        world.stripe.modes[mode]["prices"].clear()
        world.stripe.modes[mode]["endpoints"].clear()
        world.stripe.modes[mode]["portal"].clear()
    run = staging(world)
    stopped(world, run)
    assert world.stripe.paths() == READS[:6]
    assert "The webhook endpoint and the customer portal were not checked here" in run.summary
    steps = run.summary.split("### What to do next")[1].split("### What this run did")[0]
    assert "Add destination" not in run.summary and "**Customer portal**" not in steps
    assert "Stripe has no webhook endpoint" not in run.summary and "customer portal is not set up" not in run.summary
    assert steps.count("\n1. ") == 1 and "\n2. Run Deploy again." in steps


def test_one_unknown_price_is_named(world: World) -> None:
    for mode in ("test", "live"):
        del world.stripe.modes[mode]["prices"][STRIPE_PRICES[TOPUP]]
    run = staging(world)
    stopped(world, run)
    assert f"Stripe does not know the price id in `{TOPUP}` in {SANDBOX}, Test mode" in run.summary
    assert "or a price was deleted." in run.summary


def test_a_live_key_with_test_prices_hears_stripes_other_mode_hint(world: World) -> None:
    world.stripe.modes["live"]["prices"].clear()  # the ids are Test mode ones: Stripe says so
    run = production(world)
    stopped(world, run, STRIPE_LIVE_KEY)
    assert "a price with that id exists in the other mode: Test and Live never mix" in run.summary
    assert "`sk_live_...`" in run.summary


def _set(where: str, value: Any) -> Callable[[World], None]:
    """A change to the fake: "PRICE_VARIABLE.field[.field]" or "product:prod_id.field"."""

    def go(world: World) -> None:
        if where.startswith("product:"):
            product_id, _, name = where.removeprefix("product:").partition(".")
            if name == "deleted":
                del world.stripe.modes["test"]["products"][product_id]
                return
            world.stripe.product(product_id)[name] = value
            return
        variable, *path = where.split(".")
        target = world.stripe.price(variable)
        for name in path[:-1]:
            target = target[name]
        target[path[-1]] = value

    return go


@pytest.mark.parametrize(
    ("change", "expected", "fix"),
    [
        (_set(f"{PRO_MONTHLY}.active", False), f"`{PRO_MONTHLY}` (DoMe Pro, monthly): is archived in Stripe.", "**Unarchive**"),
        (_set("product:prod_FakeTopUp.active", False), f"`{TOPUP}` (DoMe AI top-up, one-time): its product is archived", "**Unarchive**"),
        (_set("product:prod_FakePro.deleted", True), f"`{PRO_ANNUAL}` (DoMe Pro, yearly): its product was deleted", "cannot be changed once made"),
        (_set(f"{PRO_MONTHLY}.livemode", True), "is a Live mode price, but the key is a Test mode key", "check the account name at the top left"),
        (_set(f"{PRO_MONTHLY}.currency", "eur"), f"`{PRO_MONTHLY}` (DoMe Pro, monthly): is priced in EUR, not USD.", "US dollars, tax-inclusive"),
        (_set(f"{PRO_MONTHLY}.type", "one_time"), "is a one-time price; it must be **recurring**, billed every month", "cannot be changed once made"),
        (_set(f"{PRO_ANNUAL}.recurring.interval", "month"), f"`{PRO_ANNUAL}` (DoMe Pro, yearly): bills every month; it must bill every year.", "cannot be changed once made"),
        (_set(f"{PRO_MONTHLY}.recurring.interval_count", 3), "bills every 3 months; it must bill every month", "cannot be changed once made"),
        (_set(f"{PRO_MONTHLY}.recurring.usage_type", "metered"), "is usage-based (metered); it must be one flat amount", "cannot be changed once made"),
        (_set(f"{TOPUP}.type", "recurring"), "the AI top-up must be a **one-time** price", "cannot be changed once made"),
        (_set(f"{PRO_MONTHLY}.billing_scheme", "tiered"), "uses tiered pricing; it must be one flat amount", "cannot be changed once made"),
        (_set(f"{PRO_MONTHLY}.unit_amount", None), "has no fixed amount (the customer chooses it)", "cannot be changed once made"),
        (_set(f"{PRO_MONTHLY}.unit_amount", 900), f"`{PRO_MONTHLY}` (DoMe Pro, monthly): Stripe would charge $9.00 but DoMe shows $9.99.", "change DoMe's display price (shared/protocol/plans.json)"),
        (_set(f"{PLUS_ANNUAL}.unit_amount", 2499900), "Stripe would charge $24,999.00 but DoMe shows $249.99.", "**Product catalog** > the product; copy the new"),
    ],
)  # fmt: skip
def test_a_price_that_is_not_what_dome_sells_stops_with_its_fix(
    world: World, change: Callable[[World], None], expected: str, fix: str
) -> None:
    change(world)
    run = staging(world)
    stopped(world, run)
    assert expected in run.summary
    assert fix in run.summary
    assert "Stripe would charge" in run.summary or "amount" not in fix


def test_a_price_in_another_currency_is_not_also_compared_by_amount(world: World) -> None:
    world.stripe.price(PRO_MONTHLY).update(currency="eur", unit_amount=900)
    run = staging(world)
    stopped(world, run)
    assert "is priced in EUR, not USD" in run.summary and "Stripe would charge" not in run.summary


def test_several_wrong_prices_and_settings_are_all_listed_in_one_stop(world: World) -> None:
    world.stripe.price(PRO_MONTHLY)["unit_amount"] = 900
    world.stripe.price(TOPUP)["active"] = False
    world.stripe.endpoints().clear()
    world.stripe.portal().clear()
    run = staging(world)
    stopped(world, run)
    for text in (
        "Stripe would charge $9.00 but DoMe shows $9.99",
        f"`{TOPUP}` (DoMe AI top-up, one-time): is archived in Stripe.",
        f"Stripe has no webhook endpoint for `{STAGING_HOOK}`",
        "The customer portal is not set up",
    ):
        assert text in run.summary
    steps = run.summary.split("### What to do next")[1].split("### What this run did")[0]
    assert steps.count("**Unarchive**") == 1 and steps.count("cannot be changed once made") == 2
    assert steps.rstrip().endswith("Run Deploy again.")


def test_an_answer_for_a_price_that_is_not_a_price_ends_the_check_with_a_warning(world: World) -> None:
    world.stripe.fail["/v1/prices/" + STRIPE_PRICES[PRO_MONTHLY]] = HttpResult(
        200, {}, json.dumps({"object": "product", "id": "prod_FakePro", "active": True}).encode()
    )
    run = staging(world)
    deployed(world, run)
    assert "could not run to the end (Stripe's API: an answer for a price that is not a price)" in run.summary
    assert "Stripe would charge" not in run.summary and "is archived" not in run.summary


def test_an_unexpected_answer_for_one_price_is_a_warning(world: World) -> None:
    world.stripe.fail["/v1/prices/" + STRIPE_PRICES[TOPUP]] = HttpResult(
        400, {}, json.dumps({"error": {"type": "invalid_request_error", "message": "Bad " + STRIPE_TEST_KEY}}).encode()
    )
    run = staging(world)
    deployed(world, run)
    assert f"Stripe would not show the price in `{TOPUP}` (HTTP 400: Bad ***)" in run.summary
    assert row(run).endswith("Test mode: webhook endpoint and customer portal match; 1 warning in the Notes |")


# ======================================================================================================
# the webhook endpoint
# ======================================================================================================


def test_no_webhook_endpoint_stops(world: World) -> None:
    world.stripe.endpoints().clear()
    run = staging(world)
    stopped(world, run)
    assert f"Stripe has no webhook endpoint for `{STAGING_HOOK}` in {SANDBOX}, Test mode" in run.summary
    assert '"Pending activation"' in run.summary and "Endpoints there now" not in run.summary
    assert "**Developers** > **Webhooks** > **Add destination**" in run.summary
    assert "`DOME_STRIPE_WEBHOOK_SECRET`" in run.summary


def test_dome_endpoints_with_other_addresses_are_listed_without_credentials_or_query(world: World) -> None:
    endpoints = world.stripe.endpoints()
    endpoints[0]["url"] = STAGING_HOOK + "/"
    endpoints[1]["url"] = "https://hookuser:hookpass99@domeremote-staging.fly.dev/v1/billing/webhook?token=abc123def"
    run = staging(world)
    stopped(world, run)
    assert (
        f"Endpoints for DoMe there now: `{STAGING_HOOK}/`, `https://domeremote-staging.fly.dev/v1/billing/webhook` "
        "(the address must match exactly)."
    ) in run.summary
    assert "hookpass99" not in run.summary and "abc123def" not in run.summary
    assert "other endpoint" not in run.summary


def test_other_services_endpoints_are_only_counted_never_shown(world: World) -> None:
    """The summary is public, and another service's webhook address often carries its secret in the path or the host
    (a capability URL), so only DoMe's own addresses are listed."""
    endpoints = world.stripe.endpoints()
    endpoints[0]["url"] = STAGING_HOOK.replace("https://", "http://")
    endpoints[1]["url"] = "https://hooks.zapier.com/hooks/catch/8840213/zq81fx7/"
    endpoints.append({**endpoints[1], "id": "we_FakeMake", "url": "https://hook.eu1.make.com/q7v9xk2m4n8p1r5t3w6y0z"})
    endpoints.append({**endpoints[1], "id": "we_FakePd", "url": "https://eo4f9k2m8x7q.m.pipedream.net/"})
    endpoints.append({**endpoints[1], "id": "we_FakeLook", "url": "https://domeremote.com.evil.example/x"})
    run = staging(world)
    stopped(world, run)
    assert f"Endpoints for DoMe there now: `{STAGING_HOOK.replace('https://', 'http://')}` (the address" in run.summary
    assert "4 other endpoints there send to other addresses (not shown)." in run.summary
    for secret in ("8840213", "zq81fx7", "q7v9xk2m4n8p1r5t3w6y0z", "eo4f9k2m8x7q", "zapier", "make.com", "evil"):
        assert secret not in run.summary and secret not in run.stdout


def test_only_other_services_endpoints_are_counted_alone(world: World) -> None:
    endpoints = world.stripe.endpoints()
    del endpoints[1:]
    endpoints[0]["url"] = "https://hooks.zapier.com/hooks/catch/8840213/zq81fx7/"
    run = staging(world)
    stopped(world, run)
    assert "Endpoints for DoMe" not in run.summary
    assert "1 other endpoint there sends to other addresses (not shown)." in run.summary


def test_production_needs_its_own_webhook_endpoint(world: World) -> None:
    world.stripe.endpoints("live").remove(world.stripe.endpoint(PRODUCTION_HOOK, "live"))
    run = production(world)
    stopped(world, run, STRIPE_LIVE_KEY)
    assert f"Stripe has no webhook endpoint for `{PRODUCTION_HOOK}` in {SANDBOX}, Live mode" in run.summary
    assert f"Endpoints for DoMe there now: `{STAGING_HOOK}`" in run.summary


def test_a_disabled_webhook_endpoint_stops(world: World) -> None:
    world.stripe.endpoint(STAGING_HOOK)["status"] = "disabled"
    run = staging(world)
    stopped(world, run)
    assert f"The webhook endpoint `{STAGING_HOOK}` is **disabled** in Stripe (Test mode)" in run.summary
    assert "(its menu > **Enable**)" in run.summary


@pytest.mark.parametrize("event", dome_deploy.STRIPE_WEBHOOK_EVENTS)
def test_a_webhook_endpoint_missing_a_required_event_stops(world: World, event: str) -> None:
    world.stripe.endpoint(STAGING_HOOK)["enabled_events"].remove(event)
    run = staging(world)
    stopped(world, run)
    assert f"The webhook endpoint `{STAGING_HOOK}` does not listen to `{event}`, so DoMe would not hear" in run.summary
    assert "edit it and tick the events named above" in run.summary


def test_an_endpoint_for_every_event_counts_as_having_them_all(world: World) -> None:
    world.stripe.endpoint(STAGING_HOOK)["enabled_events"] = ["*"]
    run = staging(world)
    deployed(world, run)
    assert ALL_MATCH in run.summary


def test_missing_refund_and_dispute_events_are_a_warning(world: World) -> None:
    world.stripe.endpoint(STAGING_HOOK)["enabled_events"] = list(dome_deploy.STRIPE_WEBHOOK_EVENTS)
    run = staging(world)
    deployed(world, run)
    recorded = ", ".join(f"`{e}`" for e in dome_deploy.STRIPE_OPERATOR_EVENTS)
    assert f"- Stripe check: The webhook endpoint `{STAGING_HOOK}` does not listen to {recorded}." in run.summary
    assert row(run).endswith("webhook endpoint and customer portal match; 1 warning in the Notes |")


def test_two_endpoints_for_the_same_address_are_a_warning_and_the_working_one_counts(world: World) -> None:
    extra = {**world.stripe.endpoint(STAGING_HOOK), "id": "we_FakeOld", "status": "disabled", "enabled_events": []}
    world.stripe.endpoints().insert(0, extra)
    run = staging(world)
    deployed(world, run)
    assert f"- Stripe check: 2 webhook endpoints in Stripe (Test mode) send to `{STAGING_HOOK}`." in run.summary


# ======================================================================================================
# the customer portal
# ======================================================================================================


@pytest.mark.parametrize("state", ["never saved", "switched off"])
def test_a_customer_portal_that_was_never_saved_stops(world: World, state: str) -> None:
    if state == "never saved":
        world.stripe.portal().clear()
    else:
        world.stripe.portal()[0]["active"] = False
    run = staging(world)
    stopped(world, run)
    assert f"The customer portal is not set up in {SANDBOX}, Test mode" in run.summary
    assert "**Manage subscription**" in run.summary and "customers could not cancel" in run.summary
    assert "**Settings** > **Billing** > **Customer portal**, set it up" in run.summary


def test_a_portal_configuration_that_is_not_the_default_does_not_count(world: World) -> None:
    other = {**world.stripe.portal()[0], "id": "bpc_FakeOther", "is_default": False}
    world.stripe.fail["/v1/billing_portal/configurations"] = HttpResult(
        200, {}, json.dumps({"object": "list", "data": [other], "has_more": False}).encode()
    )
    run = staging(world)
    stopped(world, run)
    assert f"The customer portal is not set up in {SANDBOX}, Test mode" in run.summary


def _feature(path: str, value: Any) -> Callable[[World], None]:
    def go(world: World) -> None:
        *parents, name = path.split(".")
        target = world.stripe.portal_features()
        for parent in parents:
            target = target[parent]
        target[name] = value

    return go


def _drop_price(variable: str) -> Callable[[World], None]:
    def go(world: World) -> None:
        for product in world.stripe.portal_features()["subscription_update"]["products"]:
            if STRIPE_PRICES[variable] in product["prices"]:
                product["prices"].remove(STRIPE_PRICES[variable])

    return go


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        (_feature("subscription_cancel.enabled", False), "The customer portal does not let customers cancel."),
        (_feature("subscription_cancel.mode", "immediately"), "The customer portal cancels subscriptions **immediately**"),
        (_feature("payment_method_update.enabled", False), "does not let customers update their card"),
        (_feature("subscription_update.enabled", False), "does not let customers switch between DoMe Pro and DoMe Pro Plus"),
        (_feature("subscription_update.default_allowed_updates", ["quantity"]), "does not let customers switch between"),
        (_feature("subscription_update.products", []), f"does not offer the prices in `{PRO_MONTHLY}`, `{PRO_ANNUAL}`"),
        (_drop_price(PLUS_ANNUAL), f"Switching plans in the customer portal does not offer the price in `{PLUS_ANNUAL}`."),
    ],
)  # fmt: skip
def test_customer_portal_settings_that_differ_from_the_guide_are_warnings(
    world: World, change: Callable[[World], None], expected: str
) -> None:
    change(world)
    run = staging(world)
    deployed(world, run)
    assert expected in run.summary and "- Stripe check: " in run.summary
    assert row(run).endswith("5 prices, webhook endpoint and customer portal match; 1 warning in the Notes |")


@pytest.mark.parametrize("absent", ["null", "missing"])
def test_a_product_list_stripe_does_not_show_is_not_a_warning(world: World, absent: str) -> None:
    # Deploy #9: a portal the founder had set up drew "does not offer the price in" all four prices
    update = world.stripe.portal_features()["subscription_update"]
    if absent == "null":
        update["products"] = None
    else:
        del update["products"]
    run = staging(world)
    deployed(world, run)
    assert "does not offer" not in run.summary and "- Stripe check: " not in run.summary
    assert row(run).endswith("5 prices, webhook endpoint and customer portal match |")


# ======================================================================================================
# Stripe not answering never stops a deploy
# ======================================================================================================


@pytest.mark.parametrize(
    ("answer", "reason"),
    [
        (HttpFailure("timeout", "timed out"), "no answer: timeout"),
        (HttpFailure("dns", "no such host"), "no answer: dns"),
        (HttpFailure("tls", "certificate verify failed"), "no answer: tls"),
        (HttpResult(500, {}, b'{"error": {"type": "api_error"}}'), "HTTP 500"),
        (HttpResult(503, {}, b"<html>down</html>"), "HTTP 503"),
        (HttpResult(429, {}, b'{"error": {"type": "invalid_request_error", "code": "rate_limit"}}'), "HTTP 429"),
        (HttpResult(302, {"location": "https://elsewhere.example/"}, b""), "HTTP 302"),
        (HttpResult(200, {}, b"not json"), "HTTP 200 with an answer that is not Stripe's"),
        (HttpResult(200, {}, b"[]"), "HTTP 200 with an answer that is not Stripe's"),
        (HttpResult(400, {}, b'{"error": {"message": "odd"}}'), "HTTP 400: odd"),
    ],
)
def test_stripe_not_answering_is_a_warning_and_the_deploy_goes_on(world: World, answer: Any, reason: str) -> None:
    world.stripe.fail["/v1/account"] = answer
    run = staging(world)
    deployed(world, run)
    assert row(run) == (
        f"| Stripe account | Test mode: **not checked**: Stripe's API did not answer ({reason}); the deploy went on; "
        "1 warning in the Notes |"
    )
    assert f"- Stripe check: The Stripe check could not run (Stripe's API: {reason})." in run.summary
    assert "never stops a deploy" in run.summary
    assert world.stripe.paths() == ["/v1/account"]


@pytest.mark.parametrize("path", ["/v1/prices", "/v1/webhook_endpoints", "/v1/billing_portal"])
def test_stripe_failing_halfway_is_a_warning_with_the_account_shown(world: World, path: str) -> None:
    world.stripe.fail[path] = HttpResult(502, {}, b"")
    run = staging(world)
    deployed(world, run)
    assert row(run).startswith(f"| Stripe account | {SANDBOX}, Test mode: **not checked**: Stripe's API did not answer")
    assert "The Stripe check could not run to the end (Stripe's API: HTTP 502)" in run.summary


def test_a_real_problem_found_before_stripe_failed_still_stops(world: World) -> None:
    world.stripe.price(PRO_MONTHLY)["unit_amount"] = 900
    world.stripe.fail["/v1/webhook_endpoints"] = HttpFailure("timeout", "timed out")
    run = staging(world)
    stopped(world, run)
    assert "Stripe would charge $9.00 but DoMe shows $9.99" in run.summary
    assert "could not run to the end (Stripe's API: no answer: timeout)" in run.summary


# ======================================================================================================
# pieces: redaction, plans.json, agreement with cloud-api
# ======================================================================================================


@pytest.mark.parametrize(
    "text",
    [
        # assembled at run time, like conftest's fakes, so no line looks like a Stripe secret to a scanner
        "Invalid API Key provided: " + "sk_" + "test_" + "*" * 29 + "ghij",
        "The provided key '" + "rk_" + "live_" + "********1234' does not have the required permissions",
        "Authorization was 'Bearer something-secret-0123'",
        "authorization was 'bearer " + "sk_" + "test_" + "abc'",
        "signing secret " + "whsec_" + "abcdefgh1234 is wrong",
        "publishable " + "pk_" + "live_" + "abcdef0123 key",
    ],
)
def test_stripes_own_words_never_carry_a_key(text: str) -> None:
    clean = dome_deploy.stripe_text(SecretBook(io.StringIO()), text + "\n | more")
    assert "[key hidden]" in clean or "[stripe secret hidden]" in clean
    assert not re.search(r"(sk|rk|pk)_(test|live)_|whsec_|bearer \S", clean, re.IGNORECASE)
    assert "|" not in clean and "\n" not in clean
    assert len(dome_deploy.stripe_text(SecretBook(io.StringIO()), "x" * 999)) == 300


def test_known_secrets_are_hidden_in_stripes_words_too() -> None:
    book = SecretBook(io.StringIO())
    book.add("some-known-secret-value", mask=False)
    assert dome_deploy.stripe_text(book, "it was some-known-secret-value") == "it was ***"


def test_the_amounts_come_from_plans_json() -> None:
    amounts, currency = dome_deploy.display_prices()
    pricing = json.loads((ROOT / "shared" / "protocol" / "plans.json").read_text(encoding="utf-8"))["pricing_defaults"]
    assert currency == pricing["currency"].lower() == "usd"
    assert amounts == {
        PRO_MONTHLY: pricing["plans"]["pro"]["monthly_cents"],
        PRO_ANNUAL: pricing["plans"]["pro"]["annual_cents"],
        "DOME_STRIPE_PRICE_PRO_PLUS_MONTHLY": pricing["plans"]["pro_plus"]["monthly_cents"],
        PLUS_ANNUAL: pricing["plans"]["pro_plus"]["annual_cents"],
        TOPUP: pricing["topup"]["price_cents"],
    }


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        "{}",
        '{"pricing_defaults": {"currency": "USD", "plans": {}, "topup": {"price_cents": 299}}}',
        '{"pricing_defaults": {"currency": "dollars", "plans": {"pro": {"monthly_cents": 999, "annual_cents": 9999}, '
        '"pro_plus": {"monthly_cents": 2499, "annual_cents": 24999}}, "topup": {"price_cents": 299}}}',
        '{"pricing_defaults": {"currency": "USD", "plans": {"pro": {"monthly_cents": "9.99", "annual_cents": 9999}, '
        '"pro_plus": {"monthly_cents": 2499, "annual_cents": 24999}}, "topup": {"price_cents": 299}}}',
        '{"pricing_defaults": {"currency": "USD", "plans": {"pro": {"monthly_cents": 0, "annual_cents": 9999}, '
        '"pro_plus": {"monthly_cents": 2499, "annual_cents": 24999}}, "topup": {"price_cents": 299}}}',
        '{"pricing_defaults": {"currency": "USD", "plans": {"pro": {"monthly_cents": 999, "annual_cents": 9999}, '
        '"pro_plus": {"monthly_cents": 2499, "annual_cents": 24999}}, "topup": {"price_cents": -299}}}',
    ],
)
def test_an_unreadable_plans_json_needs_an_engineer_before_any_flyctl_call(
    world: World, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, content: str
) -> None:
    broken = tmp_path / "plans.json"
    broken.write_text(content, encoding="utf-8")
    monkeypatch.setattr(dome_deploy, "PLANS_FILE", broken)
    with pytest.raises(Stop):
        dome_deploy.display_prices()
    run = staging(world)
    assert run.code == 1 and "## Failed: needs an engineer" in run.summary
    assert "shared/protocol/plans.json does not hold DoMe's prices" in run.summary
    assert world.calls() == [] and world.stripe.requests == []


def test_the_price_terms_match_cloud_apis_price_map() -> None:
    settings = (ROOT / "cloud-api" / "dome_api" / "settings.py").read_text(encoding="utf-8")
    found = re.findall(r'self\.stripe_price_(\w+): \("(\w+)", "(monthly|annual)"\)', settings)
    assert len(found) == 4
    for suffix, plan, interval in found:
        variable = "DOME_STRIPE_PRICE_" + suffix.upper()
        every = {"monthly": "month", "annual": "year"}[interval]
        assert dome_deploy.STRIPE_PRICE_TERMS[variable] == (every, ("plans", plan, f"{interval}_cents"))
    assert dome_deploy.STRIPE_PRICE_TERMS[TOPUP] == ("", ("topup", "price_cents"))
    assert list(dome_deploy.STRIPE_PRICE_TERMS) == list(dome_deploy.STRIPE_PRICES)


def test_the_api_version_is_the_one_cloud_apis_stripe_sdk_sends() -> None:
    """dome_deploy reads the shapes the app gets only while both use one API version: the SDK pins it per release
    (stripe/_api_version.py). When cloud-api moves to another stripe release, look up its version and update both."""
    lock = (ROOT / "cloud-api" / "uv.lock").read_text(encoding="utf-8")
    match = re.search(r'\[\[package\]\]\nname = "stripe"\nversion = "([^"]+)"', lock)
    assert match is not None and match.group(1) == dome_deploy.STRIPE_SDK_VERSION
    assert dome_deploy.STRIPE_API_VERSION == "2026-09-30.endive" and dome_deploy.STRIPE_API == API
