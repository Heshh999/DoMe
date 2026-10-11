"""The assistant's start-up checks (docs/design/assistant.md §3.11, threats T5, T17): every validator, the renamed key,
the ANTHROPIC_* refusal (naming the variable, never its value) and a plan whose cost cap cannot hold one turn."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from dome_api.settings import Settings

KEY = "sk-ant-api03-SETTINGSKEY-" + "x" * 30


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


ANTHROPIC_ON = {
    "ai_enabled": True,
    "ai_provider": "anthropic",
    "ai_anthropic_api_key": KEY,
    "ai_monthly_ceiling_cents": 500,
    "ai_dev_grant_interpretations": 300,
    "ai_dev_grant_cost_cap_cents": 500,
}


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    import os

    for name in list(os.environ):
        if name.upper().startswith("ANTHROPIC_") or name == "DOME_AI_API_KEY":
            monkeypatch.delenv(name)
    return monkeypatch


def refused(match: str, **over: Any) -> str:
    with pytest.raises(ValidationError, match=match) as ei:
        make(**over)
    text = str(ei.value)
    assert KEY not in text and "SETTINGSKEY" not in text  # start-up errors never print values
    return text


def test_defaults_are_off_and_match_the_design(clean_env: pytest.MonkeyPatch) -> None:
    s = make()
    assert (s.ai_enabled, s.ai_provider, s.ai_model, s.ai_effort) == (False, "", "claude-opus-5-5", "low")
    assert (s.ai_max_output_tokens, s.ai_timeout_seconds, s.ai_max_retries, s.ai_turn_deadline_seconds) == (
        1024,
        15,
        1,
        20,
    )
    assert (s.ai_cache_ttl, s.ai_monthly_ceiling_cents, s.ai_turn_reserve_micro_usd, s.ai_failed_turns_per_period) == (
        "5m",
        0,
        250_000,
        20,
    )
    assert (s.ai_dev_grant_interpretations, s.ai_dev_grant_cost_cap_cents) == (0, 0)
    on = make(**ANTHROPIC_ON)
    assert on.ai_anthropic_api_key.get_secret_value() == KEY
    assert KEY not in repr(on) and KEY not in str(on) and KEY not in str(on.model_dump())


def test_enabled_needs_a_provider_key_ceiling_and_a_sane_deadline(clean_env: pytest.MonkeyPatch) -> None:
    refused("DOME_AI_PROVIDER", **{**ANTHROPIC_ON, "ai_provider": ""})
    refused("DOME_AI_ANTHROPIC_API_KEY", **{**ANTHROPIC_ON, "ai_anthropic_api_key": ""})
    refused("DOME_AI_ANTHROPIC_API_KEY", **{**ANTHROPIC_ON, "ai_anthropic_api_key": "   "})
    refused("DOME_AI_MONTHLY_CEILING_CENTS", **{**ANTHROPIC_ON, "ai_monthly_ceiling_cents": 0})
    refused(
        "DOME_AI_TURN_DEADLINE_SECONDS", **{**ANTHROPIC_ON, "ai_timeout_seconds": 16, "ai_turn_deadline_seconds": 20}
    )
    make(**{**ANTHROPIC_ON, "ai_timeout_seconds": 15, "ai_turn_deadline_seconds": 20})
    refused("ai_provider", **{**ANTHROPIC_ON, "ai_provider": "openai"})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("ai_model", "gpt-5"),
        ("ai_model", "claude-Opus"),
        ("ai_model", "claude-" + "a" * 49),
        ("ai_effort", "max"),
        ("ai_max_output_tokens", 511),
        ("ai_max_output_tokens", 4097),
        ("ai_timeout_seconds", 4),
        ("ai_timeout_seconds", 31),
        ("ai_max_retries", 2),
        ("ai_turn_deadline_seconds", 9),
        ("ai_turn_deadline_seconds", 61),
        ("ai_cache_ttl", "24h"),
        ("ai_turn_reserve_micro_usd", 9_999),
        ("ai_failed_turns_per_period", 0),
        ("ai_failed_turns_per_period", 1001),
        ("ai_monthly_ceiling_cents", -1),
    ],
)
def test_field_bounds(clean_env: pytest.MonkeyPatch, field: str, value: Any) -> None:
    refused(field, **{**ANTHROPIC_ON, field: value})


@pytest.mark.parametrize(
    "variable", ["ANTHROPIC_BASE_URL", "ANTHROPIC_LOG", "ANTHROPIC_CUSTOM_HEADERS", "ANTHROPIC_API_KEY"]
)
def test_anthropic_variables_are_refused_with_the_anthropic_provider(
    clean_env: pytest.MonkeyPatch, variable: str
) -> None:
    secret_value = "http://VALUE-NEVER-PRINTED.invalid"
    clean_env.setenv(variable, secret_value)
    text = refused(variable, **ANTHROPIC_ON)
    assert secret_value not in text
    make(**{**ANTHROPIC_ON, "ai_enabled": False})  # allowed while the assistant is off
    make(**{**ANTHROPIC_ON, "ai_provider": "fake"})  # and with the fake provider (it never reads them)


def test_fake_provider_and_dev_grants_only_in_development_and_test(clean_env: pytest.MonkeyPatch) -> None:
    fake = {**ANTHROPIC_ON, "ai_provider": "fake"}
    make(**fake)
    make(**{**fake, "env": "development"})
    staging = {"env": "staging", "trusted_proxies": "none"}
    refused("fake is for development and tests only", **{**fake, **staging})
    refused("DEV_GRANT", **{**ANTHROPIC_ON, **staging})
    refused("DEV_GRANT", **{**staging, "ai_dev_grant_cost_cap_cents": 1})
    make(**{**ANTHROPIC_ON, **staging, "ai_dev_grant_interpretations": 0, "ai_dev_grant_cost_cap_cents": 0})


def test_a_cost_cap_below_one_reservation_refuses_start_up(clean_env: pytest.MonkeyPatch) -> None:
    text = refused("cost cap", **{**ANTHROPIC_ON, "ai_dev_grant_cost_cap_cents": 24})  # 240 000 µUSD < 250 000
    assert "plan 'free'" in text
    make(**{**ANTHROPIC_ON, "ai_dev_grant_cost_cap_cents": 25})
    make(**{**ANTHROPIC_ON, "ai_dev_grant_cost_cap_cents": 0, "ai_dev_grant_interpretations": 0})  # no allowance: fine
    refused("cost cap", **{**ANTHROPIC_ON, "ai_dev_grant_cost_cap_cents": 50, "ai_turn_reserve_micro_usd": 600_000})


def test_a_reservation_below_the_worst_case_turn_refuses_start_up(clean_env: pytest.MonkeyPatch) -> None:
    """The default $0.25 covers the default 1024 output tokens (one attempt plus one fallback attempt) up to 1555;
    more output needs a larger reservation, or concurrent turns could pass the cost cap and the ceiling."""
    make(**ANTHROPIC_ON)
    make(**{**ANTHROPIC_ON, "ai_max_output_tokens": 1555})  # 249 975 µUSD at most
    text = refused("DOME_AI_TURN_RESERVE_MICRO_USD is below", **{**ANTHROPIC_ON, "ai_max_output_tokens": 1556})
    assert "DOME_AI_MAX_OUTPUT_TOKENS" in text and "1556" not in text and "250000" not in text  # never values
    refused("DOME_AI_TURN_RESERVE_MICRO_USD is below", **{**ANTHROPIC_ON, "ai_max_output_tokens": 4096})
    make(**{**ANTHROPIC_ON, "ai_max_output_tokens": 4096, "ai_turn_reserve_micro_usd": 364_320})
    refused("DOME_AI_MODEL", **{**ANTHROPIC_ON, "ai_model": "claude-someday-9"})  # priced at the unknown-model rates
    make(**{**ANTHROPIC_ON, "ai_model": "claude-someday-9", "ai_turn_reserve_micro_usd": 376_800})
    make(**{**ANTHROPIC_ON, "ai_enabled": False, "ai_max_output_tokens": 4096})  # checked only with the assistant on


def test_renamed_key_variable(clean_env: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]) -> None:
    clean_env.setenv("DOME_AI_API_KEY", "sk-ant-old-value-NEVER-PRINTED")
    text = refused("renamed to DOME_AI_ANTHROPIC_API_KEY")
    assert "NEVER-PRINTED" not in text
    clean_env.setenv("DOME_AI_API_KEY", "")  # every older .env copied from .env.example has it empty
    make()
    assert "settings.renamed_variable" in capfd.readouterr().out
