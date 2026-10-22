"""Screen view start-up checks (docs/design/screen-view.md §12): the operator switch is off by default, the two
Cloudflare TURN values are both or neither, the analytics token only with them, the fake provider only in
development/test, the Free override never in production, the caps ordered, the STUN urls shaped; ``/healthz`` reports
the switch and the TURN figures without a secret; ``GET /v1/plans`` is unchanged (CONTRACT_ISSUES #19)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from dome_api.screen.service import ScreenService
from dome_api.settings import Settings
from tests.conftest import Env

TOKEN = "cf-turn-api-token-" + "SETTINGSFAKE0123456789abcdef"
ANALYTICS = "cf-analytics-token-" + "SETTINGSFAKE0123456789abcdef"
KEY_ID = "0123456789abcdef0123456789abcdef"


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
    assert TOKEN not in text and ANALYTICS not in text  # start-up errors never print values
    return text


def test_default_is_off_and_stun_only() -> None:
    s = make()
    assert s.screen_view_enabled is False
    assert s.effective_turn_provider == "none" and not s.turn_configured and not s.turn_reconciliation_configured
    assert s.stun_url_list == ["stun:stun.cloudflare.com:3478"]
    assert s.screen_turn_soft_cap_gb == 700 and s.screen_turn_hard_cap_gb == 900
    assert s.screen_turn_free_daily_gb == 2 and s.screen_turn_mints_per_account_month == 600
    assert s.screen_turn_ttl_unlimited_seconds == 14400 and s.screen_free_seconds_override == 0
    assert s.screen_charge_from == "forwarded"


def test_both_turn_values_turn_cloudflare_on_and_the_analytics_token_is_optional() -> None:
    s = make(turn_key_id=KEY_ID, turn_api_token=TOKEN)
    assert s.effective_turn_provider == "cloudflare" and s.turn_configured and not s.turn_reconciliation_configured
    s2 = make(turn_key_id=KEY_ID, turn_api_token=TOKEN, turn_analytics_token=ANALYTICS)
    assert s2.turn_reconciliation_configured
    s3 = make(turn_key_id=KEY_ID, turn_api_token=TOKEN, turn_provider="cloudflare", screen_view_enabled=True)
    assert s3.effective_turn_provider == "cloudflare" and s3.screen_view_enabled


def test_half_configured_turn_refuses_start_up_naming_the_variable() -> None:
    text = refused("half configured", turn_key_id=KEY_ID)
    assert "DOME_TURN_API_TOKEN" in text
    text = refused("half configured", turn_api_token=TOKEN)
    assert "DOME_TURN_KEY_ID" in text
    refused("DOME_TURN_ANALYTICS_TOKEN is set but", turn_analytics_token=ANALYTICS)
    refused("DOME_TURN_PROVIDER=cloudflare requires", turn_provider="cloudflare")
    refused("DOME_TURN_PROVIDER=none with DOME_TURN_", turn_provider="none", turn_key_id=KEY_ID, turn_api_token=TOKEN)
    refused("turn_key_id", turn_key_id="has spaces in it", turn_api_token=TOKEN)  # pydantic's own pattern check


def test_fake_provider_only_in_development_and_test() -> None:
    assert make(turn_provider="fake").effective_turn_provider == "fake"
    assert make(turn_provider="fake").turn_configured
    for env in ("staging", "production"):
        refused(
            "DOME_TURN_PROVIDER=fake is for development and tests only",
            env=env,
            turn_provider="fake",
            public_origin="https://x.example",
            oidc_issuer="https://idp.example",
            session_secret="a-real-looking-secret-0123456789",
            trusted_proxies="none",
        )


def test_free_override_is_refused_in_production_only() -> None:
    assert make(screen_free_seconds_override=120).screen_free_seconds_override == 120
    assert (
        make(env="staging", trusted_proxies="none", screen_free_seconds_override=120).screen_free_seconds_override
        == 120
    )
    refused(
        "DOME_SCREEN_FREE_SECONDS_OVERRIDE must be 0 in production",
        env="production",
        public_origin="https://x.example",
        oidc_issuer="https://idp.example",
        session_secret="a-real-looking-secret-0123456789",
        trusted_proxies="none",
        screen_free_seconds_override=120,
    )


def test_caps_and_stun_urls_are_checked() -> None:
    refused("SOFT_CAP_GB must be below", screen_turn_soft_cap_gb=900, screen_turn_hard_cap_gb=900)
    refused("stun: or stuns:", screen_stun_urls="turn:relay.example:3478")
    refused("stun: or stuns:", screen_stun_urls="stun:has space")
    refused("at least one", screen_stun_urls=" , ")
    s = make(screen_stun_urls="stun:a.example:3478, stuns:b.example:5349")
    assert s.stun_url_list == ["stun:a.example:3478", "stuns:b.example:5349"]


async def test_healthz_reports_the_switch_and_turn_without_secrets(env: Env, screen: ScreenService) -> None:
    r = await env.http.get("/healthz")
    body = r.json()
    assert r.status_code == 200
    assert body["screen_view"] == "on" and body["turn"] == "ok"
    assert isinstance(body["turn_month_gb"], float) and isinstance(body["turn_month_gb_provisional"], float)
    assert body["turn_reconciled_at"] is None
    assert "credential" not in r.text and "token" not in r.text.lower()
    # cached for a minute: the same answer, then a fresh one after the cache is dropped
    import dataclasses

    screen._health_cache = None
    screen.turn.config = dataclasses.replace(screen.turn.config, configured=False)
    body2 = (await env.http.get("/healthz")).json()
    assert body2["turn"] == "unconfigured"


async def test_healthz_says_off_by_default(env: Env) -> None:
    env.services.screen._health_cache = None
    body = (await env.http.get("/healthz")).json()
    assert body["screen_view"] == "off" and body["turn"] == "unconfigured"


async def test_plans_endpoint_is_unchanged_until_the_contract_carries_the_switch(env: Env) -> None:
    body = (await env.http.get("/v1/plans")).json()
    assert "screen_view_enabled" not in body  # CONTRACT_ISSUES #19: plans_response is closed
    assert body["plans"]["free"]["screen_view_seconds_per_window"] == 600
    assert body["plans"]["pro"]["screen_view_seconds_per_window"] is None


# ----- rules.screen_content: what the redaction keeps out of logs, tickets and diagnostics ------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("candidate from 203.0.113.7 failed", "candidate from [redacted] failed"),
        ("host 192.168.1.20:54321 reflexive", "host [redacted] reflexive"),
        ("loopback 127.0.0.1 and 0.0.0.0", "loopback [redacted] and [redacted]"),
        ("v6 2001:db8:85a3::8a2e:370:7334 seen", "v6 [redacted] seen"),
        ("full 2001:0db8:0000:0000:0000:ff00:0042:8329 seen", "full [redacted] seen"),
        ("bracketed [2001:db8::1]:3478 and [::1]:8080", "bracketed [redacted] and [redacted]"),
        ("bare ::1 and fe80::1", "bare [redacted] and [redacted]"),
        # the shape of an exception message: an address at the end of a sentence (review round 2026-10-22)
        ("could not reach 192.168.1.5.", "could not reach [redacted]."),
        ("peer 10.0.0.25:5000. retrying", "peer [redacted]. retrying"),
        ("no route to fe80::1.", "no route to [redacted]."),
    ],
)
def test_network_addresses_are_masked_in_free_text(text: str, expected: str) -> None:
    from dome_api.logging import redact_addresses, redact_text

    assert redact_addresses(text) == expected
    assert redact_text(text) == expected  # support messages and diagnostics go through the same mask


@pytest.mark.parametrize(
    "text",
    [
        "Windows 10.0.19045.3086 build",  # an octet above 255: a version string, not an address
        "the PC showed it at 10:00:03 after a Wi-Fi change",  # clock times have no ::
        "agent 0.3.0 on protocol 1.4",
        "version 1.2.3.4.5 of a library",  # a fifth dotted part: not an address
        "error codes PC_RECONNECTING and SCREEN_VIEW_DISABLED",
        "ratio 16:9 at 1920x1080",
    ],
)
def test_versions_times_and_prose_are_left_alone(text: str) -> None:
    from dome_api.logging import redact_addresses

    assert redact_addresses(text) == text


def test_sdp_candidates_and_credentials_are_redacted_by_key() -> None:
    from dome_api.logging import REDACTED, redact, redact_diagnostics

    record = {
        "offer_sdp": "v=0...",
        "answer_sdp": "v=0...",
        "sdp": "v=0...",
        "candidate": "candidate:1 1 udp 2122260223 192.0.2.1 54400 typ host",
        "candidates": ["a", "b"],
        "ice_servers": [{"urls": ["turn:turn.example:3478"], "username": "u", "credential": "c"}],
        "credential": "c",
        "username": "u",
        "turn_credential": "c",
        "screen_session_id": "AAAAAAAAAAAAAAAAAAAAAA",
        "reason": "stopped",
    }
    out = redact(record)
    for key in ("offer_sdp", "answer_sdp", "sdp", "candidate", "candidates", "ice_servers", "credential", "username"):
        assert out[key] == REDACTED, key
    assert out["turn_credential"] == REDACTED  # the `_credential` suffix
    assert out["screen_session_id"] == "AAAAAAAAAAAAAAAAAAAAAA" and out["reason"] == "stopped"
    # a diagnostics bundle with an address in a free-text field keeps the field and loses the address
    bundle = redact_diagnostics('{"note": "relay path via 198.51.100.4:3478", "ok": 1}')
    assert "198.51.100.4" not in bundle and '"ok":1' in bundle.replace(" ", "")
