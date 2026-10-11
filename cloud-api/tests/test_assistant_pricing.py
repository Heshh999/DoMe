"""The price table and per-turn cost (docs/design/assistant.md §3.7): µUSD per token, ceil of the exact sum, each
attempt at its own model's rates, unknown models deliberately dear, and the upper bound for an unfinished attempt."""

from __future__ import annotations

from decimal import Decimal

import pytest

from dome_api.assistant import pricing
from dome_api.assistant.pricing import (
    PRICES,
    UNKNOWN_MODEL_PRICE,
    cost_micro_usd,
    price_for,
    unknown_cost_bound,
    worst_case_turn_micro_usd,
)
from dome_api.assistant.provider import AttemptUsage


def usage(model: str = "claude-opus-5-5", i: int = 0, o: int = 0, r: int = 0, w: int = 0) -> AttemptUsage:
    return AttemptUsage(model=model, input_tokens=i, output_tokens=o, cache_read_tokens=r, cache_write_tokens=w)


def test_price_table() -> None:
    opus55 = PRICES["claude-opus-5-5"]
    assert (opus55.input, opus55.output, opus55.cache_write_5m, opus55.cache_write_1h, opus55.cache_read) == (
        Decimal(4),
        Decimal(20),
        Decimal(5),
        Decimal(8),
        Decimal("0.20"),
    )
    for fallback in ("claude-opus-5", "claude-opus-4-8"):
        p = PRICES[fallback]
        assert (p.input, p.output, p.cache_write_5m, p.cache_write_1h, p.cache_read) == (
            Decimal(5),
            Decimal(25),
            Decimal("6.25"),
            Decimal(10),
            Decimal("0.50"),
        )
    for p in PRICES.values():  # the unknown-model price is above every known one
        assert UNKNOWN_MODEL_PRICE.input > p.input and UNKNOWN_MODEL_PRICE.output > p.output


@pytest.mark.parametrize(
    ("attempt", "ttl", "expected"),
    [
        (usage(i=1_000), "5m", 4_000),
        (usage(o=100), "5m", 2_000),
        (usage(r=4_000), "5m", 800),
        (usage(w=4_000), "5m", 20_000),
        (usage(w=4_000), "1h", 32_000),
        (usage(i=180, o=60, r=3_600), "5m", 720 + 1_200 + 720),
        (usage(r=3), "5m", 1),  # 0.6 µUSD rounds up, never down
        (usage("claude-opus-4-8", i=1_000, o=100, r=1_000, w=1_000), "1h", 5_000 + 2_500 + 500 + 10_000),
    ],
)
def test_cost_per_model_and_ttl(attempt: AttemptUsage, ttl: pricing.CacheTtl, expected: int) -> None:
    assert cost_micro_usd([attempt], ttl) == expected


def test_each_attempt_priced_at_its_own_model_and_ceil_of_the_sum() -> None:
    declined = usage("claude-opus-5-5", i=10, w=3_000, o=5)
    served = usage("claude-opus-5", i=10, w=3_000, o=200)
    total = cost_micro_usd([declined, served], "5m")
    assert total == (40 + 15_000 + 100) + (50 + 18_750 + 5_000)
    assert cost_micro_usd([usage(r=1), usage(r=1), usage(r=1)], "5m") == 1  # ceil(0.6), not 3 × ceil(0.2)
    assert cost_micro_usd([], "5m") == 0


def test_unknown_model_is_priced_high_and_logged(capfd: pytest.CaptureFixture[str]) -> None:
    assert price_for("claude-someday-9") is UNKNOWN_MODEL_PRICE
    assert cost_micro_usd([usage("claude-someday-9", i=1_000, o=100)], "5m") == 10_000 + 5_000
    assert "assistant.unknown_model_price" in capfd.readouterr().out
    price_for("claude-opus-5-5")
    assert "assistant.unknown_model_price" not in capfd.readouterr().out


def test_unknown_cost_bound_from_message_start() -> None:
    start = usage(i=200, r=3_600, w=0)  # what message_start reported; output not yet known
    assert unknown_cost_bound(start, 1024, "5m") == 800 + 720 + 1024 * 20
    cold = usage(i=200, w=3_600)
    assert unknown_cost_bound(cold, 1024, "1h") == 800 + 28_800 + 20_480
    # a bound for one attempt stays far below the default reservation of $0.25
    assert unknown_cost_bound(usage(i=10_000), 1024, "1h") < 250_000


def test_worst_case_turn(capfd: pytest.CaptureFixture[str]) -> None:
    """10 000 input tokens at the 1-hour write rate plus the output limit, on the requested model and on the dearest
    fallback model: the default reservation of $0.25 covers the default 1024 output tokens, not 4096."""
    assert worst_case_turn_micro_usd("claude-opus-5-5", 1024) == (80_000 + 20_480) + (100_000 + 25_600)
    assert worst_case_turn_micro_usd("claude-opus-5-5", 4096) == (80_000 + 81_920) + (100_000 + 102_400)
    assert (
        worst_case_turn_micro_usd("claude-opus-5-5", 1024)
        <= 250_000
        < worst_case_turn_micro_usd("claude-opus-5-5", 4096)
    )
    # another model is priced like the metering prices it: the unknown-model rates, without a log line
    assert worst_case_turn_micro_usd("claude-someday-9", 1024) == (200_000 + 51_200) + (100_000 + 25_600)
    assert "unknown_model_price" not in capfd.readouterr().out
