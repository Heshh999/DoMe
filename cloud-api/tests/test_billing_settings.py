"""Billing start-up checks (docs/BILLING.md §5): a half-configured Stripe set, a live key outside production, the
renamed price variables and the fake provider outside development/test all refuse start-up naming variables (never
values); ``billing_enabled`` / ``billing_mode`` derive from the set; ``GET /v1/plans`` reports them."""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from dome_api.billing.service import BillingService
from dome_api.settings import Settings
from tests.conftest import Env

LIVE_KEY = "sk_live_" + "SETTINGSFAKELIVE" + "x" * 24  # split so secret scanners see no key-shaped literal
TEST_KEY = "sk_test_" + "SETTINGSFAKETEST" + "x" * 24
WHSEC = "whsec_" + "SETTINGSFAKEHOOK" + "x" * 24
PRICES = {
    "stripe_price_pro_monthly": "price_1Atest",
    "stripe_price_pro_annual": "price_1Btest",
    "stripe_price_pro_plus_monthly": "price_1Ctest",
    "stripe_price_pro_plus_annual": "price_1Dtest",
    "stripe_price_topup": "price_1Etest",
}
FULL = {"stripe_secret_key": TEST_KEY, "stripe_webhook_secret": WHSEC, **PRICES}
SECRETS = (LIVE_KEY, TEST_KEY, WHSEC, "SETTINGS")


def make(**over: Any) -> Settings:
    values: dict[str, Any] = {
        "env": "test",
        "public_origin": "http://127.0.0.1:1",
        "database_url": "postgresql+psycopg://dome@/unused",
        "session_secret": "test-session-secret-0123456789",
        "oidc_issuer": "http://idp.invalid",
        "oidc_client_id": "c",
        "oidc_client_secret": "s",
        "entitlement_signing_key_pem_path": Path("/nonexistent.pem"),
        **over,
    }
    return Settings(**values)


def refused(match: str, **over: Any) -> str:
    with pytest.raises(ValidationError, match=match) as ei:
        make(**over)
    text = str(ei.value)
    assert not any(secret in text for secret in SECRETS)  # start-up errors never print values
    return text


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for name in ("DOME_STRIPE_PRICE_MONTHLY", "DOME_STRIPE_PRICE_ANNUAL"):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_blank_values_mean_billing_off(clean_env: pytest.MonkeyPatch) -> None:
    s = make()
    assert (s.billing_enabled, s.billing_mode, s.effective_billing_provider) == (False, "off", "")
    assert s.price_map() == {"": ("pro", "monthly")} or all(not k for k in s.price_map())
    assert s.billing_webhook_tolerance_seconds == 300 and s.billing_checkout_pending_hours == 24
    assert s.rate_billing_requests_per_hour == 10 and s.stripe_automatic_tax is False


def test_full_test_mode_set_enables_billing(clean_env: pytest.MonkeyPatch) -> None:
    s = make(**FULL)
    assert (s.billing_enabled, s.billing_mode, s.effective_billing_provider) == (True, "test", "stripe")
    assert s.price_map() == {
        "price_1Atest": ("pro", "monthly"),
        "price_1Btest": ("pro", "annual"),
        "price_1Ctest": ("pro_plus", "monthly"),
        "price_1Dtest": ("pro_plus", "annual"),
    }
    assert s.topup_price_id == "price_1Etest"
    assert TEST_KEY not in repr(s) and WHSEC not in str(s.model_dump())
    rk = make(**{**FULL, "stripe_secret_key": "rk_test_RESTRICTED" + "x" * 24})
    assert rk.billing_mode == "test"


@pytest.mark.parametrize("missing", sorted(FULL))
def test_a_partial_set_refuses_start_up_naming_the_missing_variables(
    clean_env: pytest.MonkeyPatch, missing: str
) -> None:
    text = refused("half configured", **{**FULL, missing: ""})
    assert f"DOME_{missing.upper()}" in text
    present = [f"DOME_{name.upper()}" for name in FULL if name != missing]
    assert not any(name in text.split("must be set")[0].split(":")[-1] for name in present)


def test_live_key_refused_outside_production(clean_env: pytest.MonkeyPatch) -> None:
    for env in ("test", "development"):
        text = refused("never charge real cards", **{**FULL, "env": env, "stripe_secret_key": LIVE_KEY})
        assert "DOME_STRIPE_SECRET_KEY" in text
    staging = {"env": "staging", "trusted_proxies": "none"}
    refused("never charge real cards", **{**FULL, **staging, "stripe_secret_key": LIVE_KEY})
    production = {
        "env": "production",
        "trusted_proxies": "none",
        "public_origin": "https://dome.example",
        "oidc_issuer": "https://idp.example",
        "session_secret": "a-real-secret-value-0123456789",
    }
    live = make(**{**FULL, **production, "stripe_secret_key": LIVE_KEY})
    assert (live.billing_enabled, live.billing_mode) == (True, "live")
    test_in_prod = make(**{**FULL, **production})
    assert test_in_prod.billing_mode == "test"  # a test key in production is allowed (the founder's dry run)


def test_key_and_secret_shapes(clean_env: pytest.MonkeyPatch) -> None:
    refused("sk_/rk_ prefix", **{**FULL, "stripe_secret_key": "pk_test_PUBLISHABLE" + "x" * 24})
    refused("whsec_ prefix", **{**FULL, "stripe_webhook_secret": "not-a-signing-secret"})
    refused("five different", **{**FULL, "stripe_price_topup": "price_1Atest"})
    refused("stripe_price_topup", **{**FULL, "stripe_price_topup": "prod_1E"})  # a product id is not a price id
    refused("DOME_BILLING_PROVIDER=stripe requires", billing_provider="stripe")


def test_fake_provider_only_in_development_and_test(clean_env: pytest.MonkeyPatch) -> None:
    fake = {
        **FULL,
        "stripe_secret_key": "sk_test_fake",
        "stripe_webhook_secret": "whsec_fake",
        "billing_provider": "fake",
    }
    assert make(**fake).billing_mode == "test" and make(**fake).effective_billing_provider == "fake"
    make(**{**fake, "env": "development"})
    refused("fake is for development and tests only", **{**fake, "env": "staging", "trusted_proxies": "none"})


def test_renamed_price_variables(clean_env: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]) -> None:
    clean_env.setenv("DOME_STRIPE_PRICE_MONTHLY", "price_OLDVALUE_NEVER_PRINTED")
    text = refused("renamed to DOME_STRIPE_PRICE_PRO_MONTHLY")
    assert "NEVER_PRINTED" not in text
    clean_env.setenv("DOME_STRIPE_PRICE_MONTHLY", "")  # an older .env copied from .env.example has it empty
    clean_env.setenv("DOME_STRIPE_PRICE_ANNUAL", "")
    make()
    assert capfd.readouterr().out.count("settings.renamed_variable") >= 2


def test_checkout_pending_hours_stay_within_the_page_lifetime_stripe_allows(clean_env: pytest.MonkeyPatch) -> None:
    """The setting is the hosted page's expires_at as well as the reconciler's wait: Stripe accepts at most 24 hours,
    and a shorter wait than the page's lifetime would drop a row whose page can still be paid."""
    assert make(billing_checkout_pending_hours=1).billing_checkout_pending_hours == 1
    assert make(billing_checkout_pending_hours=24).billing_checkout_pending_hours == 24
    for hours in (0, 25, 72):
        refused("billing_checkout_pending_hours", billing_checkout_pending_hours=hours)


async def test_plans_endpoint_reports_billing_mode(env: Env, billing: BillingService) -> None:
    r = await env.http.get("/v1/plans")
    assert r.status_code == 200 and (r.json()["billing_enabled"], r.json()["billing_mode"]) == (True, "test")
    assert r.json()["ai_enabled"] is True  # the suite runs the assistant with its fake provider
    billing.config = dataclasses.replace(billing.config, enabled=False)
    try:
        r = await env.http.get("/v1/plans")
        assert (r.json()["billing_enabled"], r.json()["billing_mode"]) == (False, "off")
    finally:
        billing.config = dataclasses.replace(billing.config, enabled=True)
    billing.config = dataclasses.replace(billing.config, mode="live")
    r = await env.http.get("/v1/plans")
    assert (r.json()["billing_enabled"], r.json()["billing_mode"]) == (True, "live")


def test_cli_billing_reconcile_runs_one_pass(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``dome-api --billing-reconcile`` runs one reconciliation pass against the database and exits (operators)."""
    from dome_api import cli

    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(cli, "configure_logging", lambda *a, **k: None)  # keep the suite's logging as it is
    assert cli.main(["--skip-migrations", "--billing-reconcile"]) == 0
    out = capsys.readouterr().out
    assert "checkouts_expired=" in out and "grace_ended=" in out and "events_retried=" in out
