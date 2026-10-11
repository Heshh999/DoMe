"""The hand-kept AI price table and per-turn cost (docs/design/assistant.md §3.7).

Prices are micro-US-dollars per token (equal to US dollars per million tokens), from the Claude API reference's
cached price table as of 2026-10-11. DoMe's spend is its own estimate; the operator reconciles it monthly against the
Anthropic Console's usage and cost report and re-checks this table against Anthropic's pricing page before any paid
launch (docs/OPERATIONS.md). Every attempt is priced at its own model's rates; a model missing here is priced
deliberately high and logged, so an unexpected fallback model can never make a turn look cheaper than it was.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from dome_api.assistant.provider import AttemptUsage
from dome_api.logging import get_logger

log = get_logger("dome_api.assistant")

CacheTtl = Literal["5m", "1h"]


@dataclass(frozen=True, slots=True)
class ModelPrice:
    input: Decimal
    output: Decimal
    cache_write_5m: Decimal
    cache_write_1h: Decimal
    cache_read: Decimal

    def cache_write(self, ttl: CacheTtl) -> Decimal:
        return self.cache_write_1h if ttl == "1h" else self.cache_write_5m


def _price(inp: str, out: str, w5: str, w1: str, read: str) -> ModelPrice:
    return ModelPrice(Decimal(inp), Decimal(out), Decimal(w5), Decimal(w1), Decimal(read))


PRICES: dict[str, ModelPrice] = {
    "claude-opus-5-5": _price("4", "20", "5", "8", "0.20"),
    # the models a server-side fallback is expected to run on (fallbacks: "default")
    "claude-opus-5": _price("5", "25", "6.25", "10", "0.50"),
    "claude-opus-4-8": _price("5", "25", "6.25", "10", "0.50"),
}
UNKNOWN_MODEL_PRICE = _price("10", "50", "12.5", "20", "1.00")
# What a server-side fallback is expected to run on (the skill's supported fallback targets)
FALLBACK_MODELS = ("claude-opus-5", "claude-opus-4-8")
# One attempt's input at most: the system block (about 4k tokens) plus a 16 KiB request body (§3.7)
MAX_INPUT_TOKENS = 10_000


def price_for(model: str) -> ModelPrice:
    price = PRICES.get(model)
    if price is None:
        log.warning("assistant.unknown_model_price", model=model[:64])
        return UNKNOWN_MODEL_PRICE
    return price


def _input_side(attempt: AttemptUsage, price: ModelPrice, ttl: CacheTtl) -> Decimal:
    return (
        attempt.input_tokens * price.input
        + attempt.cache_write_tokens * price.cache_write(ttl)
        + attempt.cache_read_tokens * price.cache_read
    )


def cost_micro_usd(attempts: Iterable[AttemptUsage], ttl: CacheTtl) -> int:
    """ceil(Σ input × in + output × out + cache_write × write(ttl) + cache_read × read) over the attempts."""
    total = Decimal(0)
    for attempt in attempts:
        price = price_for(attempt.model)
        total += _input_side(attempt, price, ttl) + attempt.output_tokens * price.output
    return math.ceil(total)


def worst_case_turn_micro_usd(model: str, max_output_tokens: int) -> int:
    """What one turn can cost at most, which the turn reservation must cover (§3.7): the requested model's attempt
    plus one fallback attempt at the dearest fallback model, each with ``MAX_INPUT_TOKENS`` all at the 1-hour
    cache-write rate (the dearest input rate) and ``max_output_tokens`` of output. Read without logging: start-up
    calls it before logging is configured."""

    def attempt(price: ModelPrice) -> Decimal:
        return MAX_INPUT_TOKENS * price.cache_write_1h + max_output_tokens * price.output

    fallback = max(attempt(PRICES[m]) for m in FALLBACK_MODELS)
    return math.ceil(attempt(PRICES.get(model, UNKNOWN_MODEL_PRICE)) + fallback)


def unknown_cost_bound(start: AttemptUsage, max_tokens: int, ttl: CacheTtl) -> int:
    """An upper bound for one attempt that opened but never finished: the input side its ``message_start`` reported,
    plus ``max_tokens`` at its model's output price (thinking counts toward ``max_tokens``)."""
    price = price_for(start.model)
    return math.ceil(_input_side(start, price, ttl) + max_tokens * price.output)
