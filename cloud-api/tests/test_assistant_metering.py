"""Allowance, cost cap, failed-turn limit and the operator ceiling against real PostgreSQL (docs/design/assistant.md
§3.7, threat T4). Reservations race from separate database sessions and never take more than any limit; settlement
records each cost case once; the sweeper repairs what a killed process left. Every test runs in its own far-future
month, so the real month's single ceiling row is never touched."""

from __future__ import annotations

import asyncio
import contextlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from dome_api.assistant import metering
from dome_api.assistant.metering import (
    AllowanceExhaustedError,
    Limits,
    Reservation,
    Settlement,
    SpendLimitError,
    TurnDuplicateError,
)
from tests.conftest import Env, sql

RESERVE = 250_000
BIG = Limits(interpretations=1_000, cost_cap_micro_usd=10**12, failed_turns=1_000)
_years = iter(range(2300, 2900))


def a_month() -> datetime:
    """A fresh calendar month for this test (its own usage period and ceiling row)."""
    return datetime(next(_years), 3, 14, 12, 0, tzinfo=UTC)


def new_account(env: Env) -> uuid.UUID:
    account_id = uuid.uuid4()
    sql(
        env.database_url,
        "INSERT INTO accounts (id, issuer, subject, email, display_name, plan) VALUES (%s, %s, %s, '', '', 'free')",
        (str(account_id), "https://metering.test", secrets.token_hex(8)),
    )
    return account_id


def period_row(env: Env, account_id: uuid.UUID, now: datetime) -> dict[str, Any]:
    start, _ = metering.period_for(now)
    cols = (
        "ai_interpretations_used, ai_interpretations_reserved, ai_cost_used_micro_usd, ai_cost_reserved_micro_usd, "
        "ai_failed_turns, ai_input_tokens, ai_output_tokens, ai_cache_read_tokens, ai_cache_write_tokens"
    )
    rows = sql(
        env.database_url,
        f"SELECT {cols} FROM usage_periods WHERE account_id = %s AND period_start = %s",
        (str(account_id), start),
    )
    names = [c.strip() for c in cols.split(",")]
    return dict(zip(names, rows[0], strict=True)) if rows else {}


def spend(env: Env, now: datetime) -> tuple[int, int]:
    rows = sql(
        env.database_url,
        "SELECT spent_micro_usd, reserved_micro_usd FROM ai_spend_months WHERE month_start = %s",
        (metering.period_for(now)[0],),
    )
    return (int(rows[0][0]), int(rows[0][1])) if rows else (0, 0)


async def reserve(
    env: Env,
    account_id: uuid.UUID,
    now: datetime,
    limits: Limits = BIG,
    ceiling: int = 10**12,
    turn: uuid.UUID | None = None,
) -> Reservation:
    return await metering.reserve(
        env.services.db,
        account_id=account_id,
        client_turn_id=turn or uuid.uuid4(),
        now=now,
        limits=limits,
        reserve_micro_usd=RESERVE,
        ceiling_micro_usd=ceiling,
    )


async def race(env: Env, accounts: list[uuid.UUID], now: datetime, limits: Limits, ceiling: int) -> list[Any]:
    """Ten reservations at once, each from its own database session."""
    return await asyncio.gather(
        *(reserve(env, accounts[i % len(accounts)], now, limits, ceiling) for i in range(10)), return_exceptions=True
    )


def test_period_is_the_calendar_month_in_utc() -> None:
    start, end = metering.period_for(datetime(2026, 12, 31, 23, 59, 59, tzinfo=UTC))
    assert (start, end) == (datetime(2026, 12, 1, tzinfo=UTC), datetime(2027, 1, 1, tzinfo=UTC))
    start, end = metering.period_for(datetime(2027, 1, 1, 0, 30, tzinfo=timezone_plus_one()))
    assert start == datetime(2026, 12, 1, tzinfo=UTC)  # 00:30 at UTC+1 is still December in UTC
    assert metering.period_for(datetime(2028, 2, 29, tzinfo=UTC))[1] == datetime(2028, 3, 1, tzinfo=UTC)


def timezone_plus_one() -> Any:
    from datetime import timezone

    return timezone(timedelta(hours=1))


async def test_concurrent_reservations_never_exceed_the_interpretation_limit(env: Env) -> None:
    account, now = new_account(env), a_month()
    limits = Limits(interpretations=3, cost_cap_micro_usd=10**12, failed_turns=1_000)
    results = await race(env, [account], now, limits, 10**12)
    won = [r for r in results if isinstance(r, Reservation)]
    lost = [r for r in results if isinstance(r, AllowanceExhaustedError)]
    assert len(won) == 3 and len(lost) == 7 and {e.reason for e in lost} == {"requests"}
    assert {e.resets_at for e in lost} == {metering.period_for(now)[1]}
    row = period_row(env, account, now)
    assert row["ai_interpretations_reserved"] == 3 and row["ai_cost_reserved_micro_usd"] == 3 * RESERVE
    assert spend(env, now) == (0, 3 * RESERVE)
    pending = sql(env.database_url, "SELECT count(*) FROM assistant_turns WHERE account_id = %s", (str(account),))
    assert pending[0][0] == 3  # the refused ones left no ledger row


async def test_concurrent_reservations_never_exceed_the_cost_cap(env: Env) -> None:
    account, now = new_account(env), a_month()
    limits = Limits(interpretations=100, cost_cap_micro_usd=3 * RESERVE, failed_turns=1_000)
    results = await race(env, [account], now, limits, 10**12)
    assert sum(isinstance(r, Reservation) for r in results) == 3
    assert {r.reason for r in results if isinstance(r, AllowanceExhaustedError)} == {"cost_cap"}
    assert period_row(env, account, now)["ai_cost_reserved_micro_usd"] == 3 * RESERVE


async def test_concurrent_reservations_never_exceed_the_ceiling_across_accounts(env: Env) -> None:
    alice, bob, now = new_account(env), new_account(env), a_month()
    results = await race(env, [alice, bob], now, BIG, 3 * RESERVE)
    assert sum(isinstance(r, Reservation) for r in results) == 3
    assert sum(isinstance(r, SpendLimitError) for r in results) == 7
    assert spend(env, now) == (0, 3 * RESERVE)
    # a refused turn keeps nothing: neither the account's reservation nor a ledger row
    reserved = period_row(env, alice, now).get("ai_interpretations_reserved", 0) + period_row(env, bob, now).get(
        "ai_interpretations_reserved", 0
    )
    assert reserved == 3


async def test_failed_turn_limit(env: Env) -> None:
    account, now = new_account(env), a_month()
    limits = Limits(interpretations=100, cost_cap_micro_usd=10**12, failed_turns=2)
    for status in ("timeout", "invalid_output"):
        r = await reserve(env, account, now, limits)
        assert await metering.settle(env.services.db, r, Settlement(status, False, 1_000), now)
    with pytest.raises(AllowanceExhaustedError) as ei:
        await reserve(env, account, now, limits)
    assert ei.value.reason == "failures"
    # cancellations and provider outages do not count as failed turns (their cost does)
    other = new_account(env)
    for status in ("cancelled", "provider_error", "cancelled"):
        r = await reserve(env, other, now, limits)
        await metering.settle(env.services.db, r, Settlement(status, False, 500), now)
    await reserve(env, other, now, limits)
    assert period_row(env, other, now)["ai_failed_turns"] == 0
    assert period_row(env, other, now)["ai_cost_used_micro_usd"] == 1_500


async def test_settlement_records_each_case_once(env: Env) -> None:
    account, now = new_account(env), a_month()
    answered = Settlement(
        "answered",
        True,
        4_321,
        input_tokens=180,
        output_tokens=60,
        cache_read_tokens=3600,
        cache_write_tokens=10,
        model_served="claude-opus-5-5",
        fallback_used=True,
        provider_request_id="req_x",
        latency_ms=1234,
    )
    r = await reserve(env, account, now)
    assert await metering.settle(env.services.db, r, answered, now + timedelta(seconds=2))
    assert not await metering.settle(env.services.db, r, answered, now)  # a second settle changes nothing
    row = period_row(env, account, now)
    assert (row["ai_interpretations_used"], row["ai_interpretations_reserved"], row["ai_failed_turns"]) == (1, 0, 0)
    assert (row["ai_cost_used_micro_usd"], row["ai_cost_reserved_micro_usd"]) == (4_321, 0)
    assert (
        row["ai_input_tokens"],
        row["ai_output_tokens"],
        row["ai_cache_read_tokens"],
        row["ai_cache_write_tokens"],
    ) == (
        180,
        60,
        3600,
        10,
    )
    assert spend(env, now) == (4_321, 0)
    ledger = sql(
        env.database_url,
        "SELECT status, charged, cost_micro_usd, model_served, fallback_used, provider_request_id, latency_ms, "
        "finished_at IS NOT NULL FROM assistant_turns WHERE id = %s",
        (str(r.ledger_id),),
    )[0]
    assert ledger == ("answered", True, 4_321, "claude-opus-5-5", True, "req_x", 1234, True)
    # refused: metered, not charged, a failed turn; provider error before the stream: nothing billed
    for settlement, failed in ((Settlement("refused", False, 900), 1), (Settlement("provider_error", False, 0), 0)):
        before = period_row(env, account, now)
        r = await reserve(env, account, now)
        await metering.settle(env.services.db, r, settlement, now)
        after = period_row(env, account, now)
        assert after["ai_interpretations_used"] == before["ai_interpretations_used"]
        assert after["ai_failed_turns"] == before["ai_failed_turns"] + failed
        assert after["ai_cost_used_micro_usd"] == before["ai_cost_used_micro_usd"] + settlement.cost_micro_usd
    assert spend(env, now) == (4_321 + 900, 0)


async def test_cost_over_the_reservation_is_recorded_and_logged(env: Env, capfd: pytest.CaptureFixture[str]) -> None:
    account, now = new_account(env), a_month()
    r = await reserve(env, account, now)
    await metering.settle(env.services.db, r, Settlement("answered", True, RESERVE + 1), now)
    assert spend(env, now) == (RESERVE + 1, 0)
    assert "assistant.cost_over_reserve" in capfd.readouterr().out


async def test_duplicate_turn_ids(env: Env) -> None:
    alice, bob, now = new_account(env), new_account(env), a_month()
    turn = uuid.uuid4()
    await reserve(env, alice, now, turn=turn)
    with pytest.raises(TurnDuplicateError):
        await reserve(env, alice, now, turn=turn)
    await reserve(env, bob, now, turn=turn)  # the same id from another account is a different turn
    assert period_row(env, alice, now)["ai_interpretations_reserved"] == 1


async def test_sweeper_settles_stale_pending_turns(env: Env) -> None:
    account, now = new_account(env), a_month()
    stale = await reserve(env, account, now)
    fresh = await reserve(env, account, now + timedelta(seconds=100))
    later = now + timedelta(seconds=81)  # deadline 20 s + 60 s grace
    assert await metering.sweep(env.services.db, later, 80) >= 1  # global: earlier tests' leftovers go too
    assert await metering.sweep(env.services.db, later, 80) == 0
    statuses = dict(
        sql(env.database_url, "SELECT id::text, status FROM assistant_turns WHERE account_id = %s", (str(account),))
    )
    assert statuses == {str(stale.ledger_id): "abandoned", str(fresh.ledger_id): "pending"}
    row = period_row(env, account, now)
    assert (row["ai_interpretations_reserved"], row["ai_cost_used_micro_usd"], row["ai_failed_turns"]) == (
        1,
        RESERVE,
        1,
    )
    assert row["ai_interpretations_used"] == 0  # abandoned turns are never charged
    # the service's own settle after the sweeper got there first changes nothing
    assert not await metering.settle(env.services.db, stale, Settlement("answered", True, 1), later)


async def test_sweeper_and_a_live_settle_never_deadlock(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    """Stale rows of two accounts and a live turn of the second: the sweeper pauses between the rows while the live
    turn settles. Settling both rows in one transaction would hold the month row while waiting for bob's period row,
    which the live settle holds while it waits for the month row: PostgreSQL would abort one of them."""
    alice, bob, now = new_account(env), new_account(env), a_month()
    await metering.sweep(env.services.db, now, 0)  # earlier tests' leftovers, so this test's rows share one batch
    stale_alice = await reserve(env, alice, now)
    stale_bob = await reserve(env, bob, now + timedelta(seconds=1))
    later = now + timedelta(seconds=100)
    live = await reserve(env, bob, later)
    paused, live_done = asyncio.Event(), asyncio.Event()
    original = metering._settle_in

    async def settle_in(db: AsyncSession, r: Reservation, s: Settlement, when: datetime) -> bool:
        if r.ledger_id == stale_bob.ledger_id:  # alice's row is settled; the live turn goes now
            paused.set()
            with contextlib.suppress(TimeoutError):
                async with asyncio.timeout(1.0):
                    await live_done.wait()
        return await original(db, r, s, when)

    async def live_settle() -> bool:
        await paused.wait()
        try:
            return await metering.settle(env.services.db, live, Settlement("answered", True, 9_000), later)
        finally:
            live_done.set()

    monkeypatch.setattr(metering, "_settle_in", settle_in)
    swept, settled = await asyncio.gather(metering.sweep(env.services.db, later, 80), live_settle())
    assert (swept, settled) == (2, True)
    statuses = dict(
        sql(
            env.database_url,
            "SELECT id::text, status FROM assistant_turns WHERE account_id IN (%s, %s)",
            (str(alice), str(bob)),
        )
    )
    assert statuses == {
        str(stale_alice.ledger_id): "abandoned",
        str(stale_bob.ledger_id): "abandoned",
        str(live.ledger_id): "answered",
    }
    row = period_row(env, bob, now)
    assert (row["ai_interpretations_reserved"], row["ai_interpretations_used"], row["ai_failed_turns"]) == (0, 1, 1)
    assert row["ai_cost_used_micro_usd"] == RESERVE + 9_000
    assert spend(env, now) == (2 * RESERVE + 9_000, 0)


async def test_period_rollover_settles_into_the_turns_own_period(env: Env) -> None:
    account = new_account(env)
    year = next(_years)
    before_midnight = datetime(year, 1, 31, 23, 59, 59, tzinfo=UTC)
    after_midnight = datetime(year, 2, 1, 0, 0, 5, tzinfo=UTC)
    r = await reserve(env, account, before_midnight)
    await metering.settle(env.services.db, r, Settlement("answered", True, 100), after_midnight)
    january, february = period_row(env, account, before_midnight), period_row(env, account, after_midnight)
    assert january["ai_interpretations_used"] == 1 and january["ai_interpretations_reserved"] == 0
    assert february == {}
    async with env.services.db() as db:
        view = await metering.allowance(db, account, after_midnight, BIG, RESERVE)
    assert (view.used, view.exhausted, view.period_start) == (0, None, datetime(year, 2, 1, tzinfo=UTC))


async def test_allowance_view_reasons_in_order(env: Env) -> None:
    account, now = new_account(env), a_month()
    async with env.services.db() as db:
        empty = await metering.allowance(db, account, now, Limits(2, 10 * RESERVE, 5), RESERVE)
        assert (empty.limit, empty.used, empty.exhausted) == (2, 0, None)
        assert (await metering.allowance(db, account, now, Limits(0, 10 * RESERVE, 5), RESERVE)).exhausted == "requests"
        assert (await metering.allowance(db, account, now, Limits(2, RESERVE - 1, 5), RESERVE)).exhausted == "cost_cap"
    for _ in range(2):
        r = await reserve(env, account, now)
        await metering.settle(env.services.db, r, Settlement("answered", True, RESERVE), now)
    async with env.services.db() as db:
        view = await metering.allowance(db, account, now, Limits(2, 2 * RESERVE, 1), RESERVE)
    assert view.exhausted == "requests" and view.used == 2  # requests first, though the cap is also reached
    body = view.body()
    assert body["period_start"].endswith("T00:00:00.000Z") and set(body) == {
        "limit",
        "used",
        "period_start",
        "period_end",
        "exhausted",
    }
