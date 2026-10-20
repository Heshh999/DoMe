"""Billing (Stripe) through the Deploy button: the seven DOME_STRIPE_* values are all or none, checked before any
flyctl call; the two secrets reach Fly only on stdin, the five price ids with ``flyctl deploy -e``; a live key never
reaches staging; leftovers are removed when GitHub no longer has them; the summary and ``status`` say off / TEST MODE /
LIVE without a value; and the names, events and webhook path match cloud-api."""

from __future__ import annotations

import io
import re
from pathlib import Path

import pytest
from conftest import (
    STRIPE_LIVE_KEY,
    STRIPE_PRICES,
    STRIPE_TEST_KEY,
    STRIPE_WEBHOOK_SECRET,
    Run,
    World,
    stripe_env,
)
from fake_flyctl import V4, V6
from test_dome_deploy import assert_hygiene, deploy_argv, imports, mutating_calls

import dome_deploy
from dome_deploy import BILLING_MODE_TEXT, SecretBook

ROOT = Path(__file__).resolve().parents[3]
CLOUD_API = ROOT / "cloud-api" / "dome_api"
STAGING_HOOK = "https://staging.domeremote.com/v1/billing/webhook"
PRODUCTION_HOOK = "https://domeremote.com/v1/billing/webhook"
STRIPE_SECRETS = (STRIPE_TEST_KEY, STRIPE_LIVE_KEY, STRIPE_WEBHOOK_SECRET)
TEST_MODE = "Stripe: TEST MODE — no real charges"
LIVE_MODE = "Stripe: LIVE — real charges"
OFF = "Stripe: off — nothing can be bought, everyone stays on Free"


def assert_stripe_hygiene(world: World, run: Run, *extra: str) -> None:
    """No Stripe secret (or ``extra`` value) in any argv, any child's environment, the log or the summary."""
    assert_hygiene(world, run)
    values = [*STRIPE_SECRETS, *extra]
    for call in world.calls():
        assert not call["env_has_stripe_secret"], call["argv"][:2]
        for arg in call["argv"]:
            assert not any(v in arg for v in values), call["argv"][:2]
    output = "\n".join(line for line in run.stdout.splitlines() if not line.startswith("::add-mask::"))
    for value in values:
        assert value not in output and value not in run.summary


def staging_with_billing(world: World, mode: str = "test") -> Run:
    world.staging_ready_web(billing=mode)
    return world.run("deploy", **stripe_env())


# ======================================================================================================
# billing on
# ======================================================================================================


def test_staging_with_all_seven_test_values_turns_billing_on_in_test_mode(world: World) -> None:
    run = staging_with_billing(world)
    assert run.code == 0, run.summary
    # the two secrets: one staged import on stdin, with the others, before the deploy
    [staged] = imports(world)
    assert staged["DOME_STRIPE_SECRET_KEY"] == STRIPE_TEST_KEY
    assert staged["DOME_STRIPE_WEBHOOK_SECRET"] == STRIPE_WEBHOOK_SECRET
    assert not any(name in staged for name in STRIPE_PRICES)
    # the five price ids: not secret, passed like DOME_OIDC_CLIENT_ID
    argv = deploy_argv(world)
    for name, value in STRIPE_PRICES.items():
        assert argv[argv.index(f"{name}={value}") - 1] == "-e"
    app = world.state()["apps"]["domeremote-staging"]
    assert {n: app["deployed_env"][n] for n in STRIPE_PRICES} == STRIPE_PRICES
    assert app["secrets"]["DOME_STRIPE_SECRET_KEY"] == STRIPE_TEST_KEY
    # the summary: the mode in plain words, the webhook endpoint and its events, the test card
    assert f"| Billing (Stripe) | **{TEST_MODE}**" in run.summary
    assert "| DOME_STRIPE_SECRET_KEY, DOME_STRIPE_WEBHOOK_SECRET | updated from GitHub |" in run.summary
    assert "| domeremote-staging.fly.dev billing (/v1/plans) | reports test, as configured |" in run.summary
    assert f"must be exactly `{STAGING_HOOK}`" in run.summary and "In Stripe (Test mode)" in run.summary
    for event in (*dome_deploy.STRIPE_WEBHOOK_EVENTS, *dome_deploy.STRIPE_OPERATOR_EVENTS):
        assert f"`{event}`" in run.summary
    assert "`4242 4242 4242 4242`" in run.summary and "LIVE" not in run.summary
    assert world.web.requests.count("https://domeremote-staging.fly.dev/v1/plans") == 1
    assert_stripe_hygiene(world, run)


@pytest.mark.parametrize(
    ("key", "mode", "text"),
    [(STRIPE_TEST_KEY, "test", TEST_MODE), (STRIPE_LIVE_KEY, "live", LIVE_MODE)],
)
def test_production_accepts_a_test_key_and_says_which_mode_runs(world: World, key: str, mode: str, text: str) -> None:
    world.production_ready_web(billing=mode)
    world.add_cluster("domeremote-production-db")
    run = world.run("deploy", DEPLOY_ENVIRONMENT="production", **stripe_env(key=key))
    assert run.code == 0, run.summary
    [staged] = imports(world)
    assert staged["DOME_STRIPE_SECRET_KEY"] == key
    assert f"| Billing (Stripe) | **{text}**" in run.summary
    assert f"must be exactly `{PRODUCTION_HOOK}`" in run.summary
    assert ("`4242 4242 4242 4242`" in run.summary) == (mode == "test")
    assert ("Real cards are charged from now on" in run.summary) == (mode == "live")
    assert ("**Production is in Stripe Test mode**" in run.summary) == (mode == "test")
    assert (TEST_MODE in run.summary) == (mode == "test") and (LIVE_MODE in run.summary) == (mode == "live")
    assert_stripe_hygiene(world, run)


def test_a_rerun_with_billing_restages_the_secrets_and_removes_nothing(world: World) -> None:
    assert staging_with_billing(world).code == 0
    world.reset_log()
    run = world.run("deploy", **stripe_env())
    assert run.code == 0, run.summary
    assert mutating_calls(world) == ["flyctl secrets import", "flyctl deploy"]
    [staged] = imports(world)
    assert {"DOME_STRIPE_SECRET_KEY", "DOME_STRIPE_WEBHOOK_SECRET"} <= set(staged)
    assert_stripe_hygiene(world, run)


def test_a_restricted_test_key_is_accepted(world: World) -> None:
    world.staging_ready_web(billing="test")
    restricted = "rk_" + "test_" + "51FakeRestrictedKey0123456789abcdef"
    run = world.run("deploy", **stripe_env(key=restricted))
    assert run.code == 0, run.summary
    assert imports(world)[0]["DOME_STRIPE_SECRET_KEY"] == restricted
    assert TEST_MODE in run.summary
    assert_stripe_hygiene(world, run, restricted)


# ======================================================================================================
# billing off, and switching it off
# ======================================================================================================


def test_no_stripe_values_keep_billing_off_honestly(world: World) -> None:
    world.staging_ready_web()
    run = world.run("deploy")
    assert run.code == 0, run.summary
    [staged] = imports(world)
    assert not any(name.startswith("DOME_STRIPE_") for name in staged)
    assert not any("DOME_STRIPE_" in arg for arg in deploy_argv(world))
    assert "secrets unset" not in " ".join(world.commands())
    assert f"| Billing (Stripe) | **{OFF}** (this GitHub Environment has no `DOME_STRIPE_` values) |" in run.summary
    assert "| domeremote-staging.fly.dev billing (/v1/plans) | reports off, as configured |" in run.summary
    assert "/v1/billing/webhook" not in run.summary and "4242" not in run.summary


def test_removing_every_stripe_value_from_github_removes_them_from_fly(world: World) -> None:
    assert staging_with_billing(world).code == 0
    world.reset_log()
    world.web.plans("https://domeremote-staging.fly.dev", "off")
    run = world.run("deploy")
    # without the removal the app would refuse to start (two secrets, no price ids: "half configured")
    assert run.code == 0, run.summary
    assert mutating_calls(world) == ["flyctl secrets import", "flyctl secrets unset", "flyctl deploy"]
    [unset] = [c["argv"] for c in world.calls("flyctl") if c["argv"][:2] == ["secrets", "unset"]]
    assert unset == [
        "secrets", "unset", "--stage", "-a", "domeremote-staging", "DOME_STRIPE_SECRET_KEY", "DOME_STRIPE_WEBHOOK_SECRET"
    ]  # fmt: skip
    secrets = world.state()["apps"]["domeremote-staging"]["secrets"]
    assert not any(name.startswith("DOME_STRIPE_") for name in secrets)
    assert "| DOME_STRIPE_SECRET_KEY, DOME_STRIPE_WEBHOOK_SECRET | removed from Fly with this deploy" in run.summary
    assert "Billing was switched **off** by this deploy" in run.summary
    assert "Subscriptions that already exist keep running and charging at Stripe" in run.summary
    assert OFF in run.summary
    assert_stripe_hygiene(world, run)


def test_a_price_id_kept_as_a_fly_secret_is_removed_so_github_decides(world: World) -> None:
    assert staging_with_billing(world).code == 0
    world.update(
        lambda s: s["apps"]["domeremote-staging"]["secrets"].update(DOME_STRIPE_PRICE_TOPUP="price_byhand0001")
    )
    world.reset_log()
    run = world.run("deploy", **stripe_env())
    assert run.code == 0, run.summary
    [unset] = [c["argv"] for c in world.calls("flyctl") if c["argv"][:2] == ["secrets", "unset"]]
    assert unset[5:] == ["DOME_STRIPE_PRICE_TOPUP"]
    assert "Billing was switched" not in run.summary


def test_a_refused_removal_stops_before_the_deploy(world: World) -> None:
    assert staging_with_billing(world).code == 0
    world.reset_log()
    world.update(lambda s: s["fail"].update({"secrets unset": {"exit": 1}}))
    run = world.run("deploy")
    assert run.code == 1 and "## Failed\n" in run.summary
    assert "Fly did not remove the secrets (DOME_STRIPE_SECRET_KEY, DOME_STRIPE_WEBHOOK_SECRET)" in run.summary
    assert "flyctl deploy" not in world.commands()


# ======================================================================================================
# stops before any command
# ======================================================================================================


@pytest.mark.parametrize("missing", list(dome_deploy.STRIPE_NAMES))
def test_half_configured_billing_stops_before_any_command(world: World, missing: str) -> None:
    world.staging_ready_web()
    run = world.run("deploy", **{**stripe_env(), missing: ""})
    assert run.code == 1 and world.calls() == []
    assert "Stopped: needs you" in run.summary and "Nothing was created or changed" in run.summary
    assert f"Payments are half set up in the staging environment: `{missing}` is missing." in run.summary
    assert "all seven Stripe values or none" in run.summary
    tab = "secrets" if missing in dome_deploy.STRIPE_SECRET_NAMES else "variables"
    assert f"**Environment {tab}** add: `{missing}`" in run.summary
    assert "`DOME_STRIPE_PRICE_PRO_ANNUAL` = DoMe Pro, yearly" in run.summary
    assert "delete every `DOME_STRIPE_` secret and variable" in run.summary
    assert_stripe_hygiene(world, run)


def test_one_value_alone_names_the_other_six(world: World) -> None:
    run = world.run("deploy", DOME_STRIPE_PRICE_TOPUP=STRIPE_PRICES["DOME_STRIPE_PRICE_TOPUP"])
    assert run.code == 1 and world.calls() == []
    assert "**Environment secrets** add: `DOME_STRIPE_SECRET_KEY`, `DOME_STRIPE_WEBHOOK_SECRET`." in run.summary
    assert "`DOME_STRIPE_PRICE_PRO_PLUS_ANNUAL` are missing" in run.summary


@pytest.mark.parametrize("key", [STRIPE_LIVE_KEY, "rk_" + "live_" + "51FakeRestrictedLive0123456789abc"])
def test_staging_never_accepts_a_live_key(world: World, key: str) -> None:
    world.staging_ready_web()
    run = world.run("deploy", **stripe_env(key=key))
    assert run.code == 1 and world.calls() == []
    assert "in the staging environment is a **live** key" in run.summary and "charges real cards" in run.summary
    assert "**Test mode**" in run.summary and LIVE_MODE not in run.summary
    assert_stripe_hygiene(world, run, key)


PUBLISHABLE = "pk_" + "test_" + "51FakePublishableKey0123456789abcdef"


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({"DOME_STRIPE_SECRET_KEY": PUBLISHABLE}, "is Stripe's **publishable** key"),
        ({"DOME_STRIPE_SECRET_KEY": "pk_" + "live_" + "51FakePublishableLive0123456789"}, "**publishable** key"),
        ({"DOME_STRIPE_SECRET_KEY": STRIPE_WEBHOOK_SECRET}, "holds the webhook's signing secret"),
        ({"DOME_STRIPE_SECRET_KEY": "stripe-secret-0123456789abcdefghij"}, "is not a Stripe secret key"),
        ({"DOME_STRIPE_SECRET_KEY": "sk_" + "test_" + "abc"}, "too short to be a whole Stripe key"),
        ({"DOME_STRIPE_SECRET_KEY": STRIPE_TEST_KEY + " x"}, "`DOME_STRIPE_SECRET_KEY` contains a space"),
        ({"DOME_STRIPE_WEBHOOK_SECRET": STRIPE_TEST_KEY}, "holds a Stripe API key, not the webhook's signing secret"),
        ({"DOME_STRIPE_WEBHOOK_SECRET": "signing-secret-0123456789"}, "starts with `whsec_`"),
        ({"DOME_STRIPE_WEBHOOK_SECRET": "whsec_" + "short"}, "starts with `whsec_`"),
        ({"DOME_STRIPE_WEBHOOK_SECRET": STRIPE_WEBHOOK_SECRET + "#x"}, "`DOME_STRIPE_WEBHOOK_SECRET` contains a space"),
        (
            {"DOME_STRIPE_PRICE_PRO_MONTHLY": "prod_FakeProduct0001"},
            "`DOME_STRIPE_PRICE_PRO_MONTHLY` holds a product id",
        ),
        ({"DOME_STRIPE_PRICE_TOPUP": "price_1 Fake"}, "`DOME_STRIPE_PRICE_TOPUP` (DoMe AI top-up, one-time) is not"),
        ({"DOME_STRIPE_PRICE_TOPUP": "price_"}, "is not a Stripe price id"),
        (
            {"DOME_STRIPE_PRICE_PRO_ANNUAL": STRIPE_PRICES["DOME_STRIPE_PRICE_PRO_MONTHLY"]},
            "`DOME_STRIPE_PRICE_PRO_MONTHLY`, `DOME_STRIPE_PRICE_PRO_ANNUAL` hold the same price id",
        ),
    ],
)
def test_unusable_stripe_values_stop_before_any_command(world: World, env: dict[str, str], expected: str) -> None:
    world.staging_ready_web()
    run = world.run("deploy", **{**stripe_env(), **env})
    assert run.code == 1 and world.calls() == [], run.summary
    assert "Stopped: needs you" in run.summary and expected in run.summary
    assert "Nothing was created or changed" in run.summary
    assert "https://github.com/Heshh999/DoMe/settings/environments > **staging**" in run.summary
    assert_stripe_hygiene(world, run, *(v for v in env.values() if len(v) >= 12 and not v.startswith("price_")))


@pytest.mark.parametrize(("secret", "steps"), [(STRIPE_TEST_KEY, "**Roll key**"), (STRIPE_WEBHOOK_SECRET, "roll its")])
def test_a_secret_pasted_into_a_price_variable_is_treated_as_exposed(world: World, secret: str, steps: str) -> None:
    world.staging_ready_web()
    run = world.run("deploy", **stripe_env(DOME_STRIPE_PRICE_PRO_PLUS_MONTHLY=secret))
    assert run.code == 1 and world.calls() == []
    assert (
        "The variable `DOME_STRIPE_PRICE_PRO_PLUS_MONTHLY` holds a Stripe secret instead of a price id" in run.summary
    )
    assert "treat that secret as exposed" in run.summary and steps in run.summary
    assert "DoMe Pro Plus, monthly" in run.summary
    assert_stripe_hygiene(world, run)


@pytest.mark.parametrize("names", ["DOME_STRIPE_SECRET_KEY ", "DOME_STRIPE_SECRET_KEY DOME_STRIPE_WEBHOOK_SECRET"])
def test_a_stripe_secret_saved_as_a_variable_must_be_replaced(world: World, names: str) -> None:
    world.staging_ready_web()
    env = stripe_env()
    for name in names.split():
        env[name] = ""  # a variable never reaches the program: only its NAME, through the probe
    run = world.run("deploy", STRIPE_SECRETS_AS_VARIABLES=names, **env)
    assert run.code == 1 and world.calls() == []
    for name in names.split():
        assert f"`{name}` is saved as an Environment **variable**" in run.summary
        assert f"Delete the **variable** `{name}`" in run.summary
    assert "Payments are half set up" not in run.summary  # not reported twice
    assert ("**Roll key**" in run.summary) and (("Signing secret" in run.summary) == ("WEBHOOK" in names))
    # even with nothing else set, the misplaced secret is reported
    run = world.run("deploy", STRIPE_SECRETS_AS_VARIABLES="DOME_STRIPE_SECRET_KEY")
    assert run.code == 1 and "is saved as an Environment **variable**" in run.summary
    assert "Payments are half set up" in run.summary  # the other six are missing


# ======================================================================================================
# checks after the deploy, status, gate
# ======================================================================================================


@pytest.mark.parametrize(
    ("plans", "expected"),
    [
        ("off", "/v1/plans reports billing off, but this deploy configured test"),
        (None, "/v1/plans did not answer with a billing mode (no answer: dns)"),
        ("garbled", "/v1/plans did not answer with a billing mode (an unexpected answer)"),
    ],
)
def test_the_running_app_must_report_the_configured_billing(world: World, plans: str | None, expected: str) -> None:
    world.staging_ready_web()
    if plans is None:
        del world.web.routes["https://domeremote-staging.fly.dev/v1/plans"]
    elif plans == "garbled":
        world.web.json(
            "https://domeremote-staging.fly.dev/v1/plans", {"billing_enabled": False, "billing_mode": "test"}
        )
    run = world.run("deploy", **stripe_env())
    assert run.code == 1 and "Failed: needs an engineer" in run.summary
    assert expected in run.summary
    assert_stripe_hygiene(world, run)


def test_billing_reported_on_although_github_has_no_values_fails(world: World) -> None:
    world.staging_ready_web(billing="live")
    run = world.run("deploy")
    assert run.code == 1 and "/v1/plans reports billing live, but this deploy configured off" in run.summary


@pytest.mark.parametrize(
    ("plans", "expected"),
    [
        ("off", f"| Billing (Stripe) | **{OFF}** |"),
        ("test", f"| Billing (Stripe) | **{TEST_MODE}** |"),
        ("live", f"| Billing (Stripe) | **{LIVE_MODE}** |"),
        (None, "| Billing (Stripe) | unknown (/v1/plans: no answer: dns) |"),
    ],
)
def test_status_reports_billing_without_secrets_or_flyctl(world: World, plans: str | None, expected: str) -> None:
    world.staging_ready_web(billing=plans or "off")
    if plans is None:
        del world.web.routes["https://domeremote-staging.fly.dev/v1/plans"]
    world.dns["domeremote-staging.fly.dev"] = [V4, V6]
    no_secrets = {k: "<unset>" for k in ("FLY_API_TOKEN", "DOME_OIDC_CLIENT_SECRET", *dome_deploy.STRIPE_NAMES)}
    run = world.run("status", **no_secrets)
    assert run.code == 0 and world.calls() == []
    assert expected in run.summary


@pytest.mark.parametrize(
    ("probe", "steps"),
    [
        ("DOME_STRIPE_SECRET_KEY ", ["**Roll key**"]),
        ("DOME_STRIPE_WEBHOOK_SECRET", ["roll its **Signing secret**"]),
        ("DOME_STRIPE_SECRET_KEY DOME_STRIPE_WEBHOOK_SECRET", ["**Roll key**", "roll its **Signing secret**"]),
    ],
)
def test_gate_refuses_stripe_repository_secrets(world: World, probe: str, steps: list[str]) -> None:
    run = world.run("gate", REPO_LEVEL_SECRETS=probe)
    assert run.code == 1 and "Stopped: needs you" in run.summary
    for name in probe.split():
        assert f"`{name}`" in run.summary
    for step in steps:
        assert step in run.summary
    assert world.web.requests == [] and world.calls() == []


# ======================================================================================================
# redaction and agreement with cloud-api
# ======================================================================================================


def test_redaction_hides_stripe_secrets_it_was_never_told_about() -> None:
    book = SecretBook(io.StringIO())
    text = book.redact(f"key {STRIPE_LIVE_KEY} hook {STRIPE_WEBHOOK_SECRET} price price_1FakeTopUp000000005")
    assert STRIPE_LIVE_KEY not in text and STRIPE_WEBHOOK_SECRET not in text
    assert "price_1FakeTopUp000000005" in text  # a price id is not secret


def test_names_and_rules_match_cloud_api_settings() -> None:
    text = (CLOUD_API / "settings.py").read_text(encoding="utf-8")
    fields = re.search(r"_STRIPE_FIELDS = \(([^)]*)\)", text)
    assert fields is not None
    assert {"DOME_" + f.upper() for f in re.findall(r'"([a-z_]+)"', fields.group(1))} == set(dome_deploy.STRIPE_NAMES)
    price = re.search(r'_PRICE_ID = r"\^\((.*)\)\?\$"', text)
    assert price is not None and "^" + price.group(1) + "$" == dome_deploy._STRIPE_PRICE_ID.pattern
    for prefix in (*dome_deploy.STRIPE_TEST_KEY_PREFIXES, *dome_deploy.STRIPE_LIVE_KEY_PREFIXES, "whsec_"):
        assert f'"{prefix}"' in text
    # the app refuses a live key outside production; the Deploy button refuses it on staging, before Fly
    assert 'self.env != "production" and not key.startswith(("sk_test_", "rk_test_"))' in text


def test_webhook_path_and_events_match_cloud_api() -> None:
    routes = (CLOUD_API / "routes" / "billing.py").read_text(encoding="utf-8")
    assert '@router.post("/billing/webhook")' in routes
    main = (CLOUD_API / "main.py").read_text(encoding="utf-8")
    assert 'app.include_router(router, prefix="/v1")' in main
    assert dome_deploy.STRIPE_WEBHOOK_PATH == "/v1/billing/webhook"
    service = (CLOUD_API / "billing" / "service.py").read_text(encoding="utf-8")

    def frozenset_of(name: str) -> set[str]:
        match = re.search(name + r" = frozenset\(\s*\{([^}]*)\}", service)
        assert match is not None, name
        return set(re.findall(r'"([a-z_.]+)"', match.group(1)))

    acted_on = frozenset_of("SUBSCRIPTION_EVENTS") | frozenset_of("CHECKOUT_DONE_EVENTS") | {"checkout.session.expired"}
    assert '"checkout.session.expired"' in service
    assert set(dome_deploy.STRIPE_WEBHOOK_EVENTS) == acted_on
    assert set(dome_deploy.STRIPE_OPERATOR_EVENTS) == frozenset_of("OPERATOR_EVENTS")
    assert dome_deploy.load_environments()["production"].webhook_url == PRODUCTION_HOOK
    assert dome_deploy.load_environments()["staging"].webhook_url == STAGING_HOOK


def test_billing_mode_words_are_the_ones_the_founder_reads() -> None:
    assert BILLING_MODE_TEXT["test"] == TEST_MODE and BILLING_MODE_TEXT["live"] == LIVE_MODE
    assert set(BILLING_MODE_TEXT) == {"off", "test", "live"}
