"""Allowance windows, taste, top-ups, cost cap, failed-turn limit and the operator ceiling against real PostgreSQL
(docs/design/assistant.md §3.7; rules.ai_allowance; threat T4). Reservations race from separate database sessions and
never take more than any limit; settlement records each cost case once in the turn's own windows; the sweeper repairs
what a killed process left; the shared fixture ``fixtures/ai-window-cases.json`` evaluates identically through
``exhausted_reason``. Every test runs in its own far-future month, so the real month's ceiling row is never touched."""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import json
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from dome_protocol import load_schemas
from sqlalchemy.ext.asyncio import AsyncSession

from dome_api.assistant import metering
from dome_api.assistant.metering import (
    AllowanceExhaustedError,
    Limits,
    Reservation,
    Settlement,
    SpendLimitError,
    TurnDuplicateError,
    _Counts,
)
from tests.conftest import Env, sql

RESERVE = 250_000
FIXTURE = json.loads(
    (Path(__file__).resolve().parents[2] / "shared" / "protocol" / "fixtures" / "ai-window-cases.json").read_text()
)
_years = iter(range(2300, 2900))


def windows(
    window: int = 1_000,
    week: int = 1_000,
    *,
    cost_cap: int = 10**12,
    failed: int = 1_000,
    topup_allowed: bool = False,
    taste: int = 0,
) -> Limits:
    return Limits(
        taste=taste,
        window_requests=window,
        week_requests=week,
        window_hours=5,
        week_hours=24 * 7,
        cost_cap_micro_usd=cost_cap,
        failed_turns=failed,
        topup_allowed=topup_allowed,
    )


def taste(limit: int = 10, *, cost_cap: int = 10**12, failed: int = 1_000) -> Limits:
    return Limits(
        taste=limit,
        window_requests=0,
        week_requests=0,
        window_hours=5,
        week_hours=24 * 7,
        cost_cap_micro_usd=cost_cap,
        failed_turns=failed,
        topup_allowed=False,
    )


BIG = windows()


def a_month() -> datetime:
    """A fresh calendar month for this test (its own ceiling row; windows are per account anyway)."""
    return datetime(next(_years), 3, 14, 12, 0, tzinfo=UTC)


def new_account(env: Env, plan: str = "free") -> uuid.UUID:
    account_id = uuid.uuid4()
    sql(
        env.database_url,
        "INSERT INTO accounts (id, issuer, subject, email, display_name, plan) VALUES (%s, %s, %s, '', '', %s)",
        (str(account_id), "https://metering.test", secrets.token_hex(8), plan),
    )
    return account_id


def window_rows(env: Env, account_id: uuid.UUID, kind: str) -> list[dict[str, Any]]:
    cols = (
        "window_start, window_end, requests_used, requests_reserved, cost_used_micro_usd, cost_reserved_micro_usd, "
        "failed_turns"
    )
    rows = sql(
        env.database_url,
        f"SELECT {cols} FROM usage_windows WHERE account_id = %s AND kind = %s ORDER BY window_start",
        (str(account_id), kind),
    )
    names = [c.strip() for c in cols.split(",")]
    return [dict(zip(names, row, strict=True)) for row in rows]


def week_row(env: Env, account_id: uuid.UUID) -> dict[str, Any]:
    rows = window_rows(env, account_id, "week")
    return rows[-1] if rows else {}


def balance(env: Env, account_id: uuid.UUID) -> dict[str, int]:
    rows = sql(
        env.database_url,
        "SELECT taste_used, taste_reserved, topup_remaining, topup_reserved FROM ai_balances WHERE account_id = %s",
        (str(account_id),),
    )
    if not rows:
        return {}
    return dict(zip(("taste_used", "taste_reserved", "topup_remaining", "topup_reserved"), rows[0], strict=True))


def credit_topup(env: Env, account_id: uuid.UUID, requests: int) -> None:
    sql(
        env.database_url,
        "INSERT INTO ai_balances (account_id, topup_remaining) VALUES (%s, %s) "
        "ON CONFLICT (account_id) DO UPDATE SET topup_remaining = ai_balances.topup_remaining + EXCLUDED.topup_remaining",
        (str(account_id), requests),
    )


def spend(env: Env, now: datetime) -> tuple[int, int]:
    rows = sql(
        env.database_url,
        "SELECT spent_micro_usd, reserved_micro_usd FROM ai_spend_months WHERE month_start = %s",
        (metering.month_for(now),),
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


async def view(env: Env, account_id: uuid.UUID, now: datetime, limits: Limits) -> metering.AllowanceView:
    async with env.services.db() as db:
        return await metering.allowance(db, account_id, now, limits, RESERVE)


# ----- windows ---------------------------------------------------------------------------------------------------------


def test_month_for_the_operator_ceiling_is_the_calendar_month_in_utc() -> None:
    assert metering.month_for(datetime(2026, 12, 31, 23, 59, 59, tzinfo=UTC)) == datetime(2026, 12, 1, tzinfo=UTC)
    from datetime import timezone

    plus_one = timezone(timedelta(hours=1))
    assert metering.month_for(datetime(2027, 1, 1, 0, 30, tzinfo=plus_one)) == datetime(2026, 12, 1, tzinfo=UTC)


async def test_first_reservation_opens_both_windows_and_looking_does_not(env: Env) -> None:
    account, now = new_account(env), a_month()
    before = await view(env, account, now, windows(30, 120))
    assert before.kind == "windows" and before.window is not None and before.window.starts_at is None
    assert before.body()["window"] == {"limit": 30, "used": 0, "starts_at": None, "ends_at": None}
    assert window_rows(env, account, "window") == [] and window_rows(env, account, "week") == []
    r = await reserve(env, account, now, windows(30, 120))
    assert r.source == "window" and r.window_start == now and r.week_start == now
    [window] = window_rows(env, account, "window")
    [week] = window_rows(env, account, "week")
    assert (window["window_start"], window["window_end"]) == (now, now + timedelta(hours=5))
    assert (week["window_start"], week["window_end"]) == (now, now + timedelta(days=7))
    assert window["requests_reserved"] == 1 and week["requests_reserved"] == 1
    assert week["cost_reserved_micro_usd"] == RESERVE and window["cost_reserved_micro_usd"] == 0
    after = await view(env, account, now + timedelta(hours=1), windows(30, 120))
    assert after.window is not None and after.window.ends_at == now + timedelta(hours=5)
    assert after.week is not None and after.week.ends_at == now + timedelta(days=7)
    # the next reservation after the window ended opens a new one; the week is still open
    later = now + timedelta(hours=5, seconds=1)
    r2 = await reserve(env, account, later, windows(30, 120))
    assert r2.window_start == later and r2.week_start == now
    assert [w["window_start"] for w in window_rows(env, account, "window")] == [now, later]
    assert len(window_rows(env, account, "week")) == 1


async def test_window_exhaustion_names_the_window_and_its_end(env: Env) -> None:
    account, now = new_account(env), a_month()
    limits = windows(2, 120)
    await reserve(env, account, now, limits)
    await reserve(env, account, now + timedelta(minutes=1), limits)
    with pytest.raises(AllowanceExhaustedError) as ei:
        await reserve(env, account, now + timedelta(minutes=2), limits)
    assert (ei.value.reason, ei.value.resets_at) == ("window", now + timedelta(hours=5))
    v = await view(env, account, now + timedelta(minutes=2), limits)
    assert (v.exhausted, v.resets_at) == ("window", now + timedelta(hours=5))
    assert v.body()["resets_at"] == v.body()["window"]["ends_at"]
    # a refused reservation keeps nothing: no ledger row, no extra reservation
    assert (
        sql(env.database_url, "SELECT count(*) FROM assistant_turns WHERE account_id = %s", (str(account),))[0][0] == 2
    )
    assert window_rows(env, account, "window")[0]["requests_reserved"] == 2


async def test_week_exhaustion_with_room_in_the_window(env: Env) -> None:
    account, now = new_account(env), a_month()
    limits = windows(30, 3)
    for i in range(3):
        await reserve(env, account, now + timedelta(hours=6 * i), limits)  # three windows, one week
    with pytest.raises(AllowanceExhaustedError) as ei:
        await reserve(env, account, now + timedelta(hours=18), limits)
    assert (ei.value.reason, ei.value.resets_at) == ("week", now + timedelta(days=7))
    assert len(window_rows(env, account, "window")) == 3 and week_row(env, account)["requests_reserved"] == 3
    # after the week ended a new one opens and the next turn is taken
    r = await reserve(env, account, now + timedelta(days=7, seconds=1), limits)
    assert r.week_start == now + timedelta(days=7, seconds=1)


async def test_free_taste_is_ten_then_never_renews(env: Env) -> None:
    account, now = new_account(env), a_month()
    limits = taste(10)
    for i in range(10):
        r = await reserve(env, account, now + timedelta(days=3 * i), limits)  # across weeks and months alike
        assert r.source == "taste" and r.window_start is None
        await metering.settle(env.services.db, r, Settlement("answered", True, 1_000), now + timedelta(days=3 * i))
    with pytest.raises(AllowanceExhaustedError) as ei:
        await reserve(env, account, now + timedelta(days=400), limits)
    assert (ei.value.reason, ei.value.resets_at) == ("taste", None)
    assert balance(env, account) == {"taste_used": 10, "taste_reserved": 0, "topup_remaining": 0, "topup_reserved": 0}
    assert window_rows(env, account, "window") == []  # a taste plan opens no 5-hour windows
    v = await view(env, account, now + timedelta(days=400), limits)
    body = v.body()
    assert body["kind"] == "taste" and body["taste"] == {"limit": 10, "used": 10}
    assert (
        body["window"] is None and body["week"] is None and body["exhausted"] == "taste" and body["resets_at"] is None
    )
    load_schemas().validate_rest("assistant_allowance", body)


async def test_topup_pays_when_a_window_is_full_and_is_kept_but_not_spent_on_free(env: Env) -> None:
    account, now = new_account(env), a_month()
    credit_topup(env, account, 2)
    limits = windows(1, 120, topup_allowed=True)
    first = await reserve(env, account, now, limits)
    assert first.source == "window"
    second = await reserve(env, account, now, limits)
    assert second.source == "topup" and second.window_start is None and second.week_start == now
    assert balance(env, account)["topup_reserved"] == 1
    # a top-up turn still reserves the weekly cost cap but never the window's requests
    assert week_row(env, account)["cost_reserved_micro_usd"] == 2 * RESERVE
    assert week_row(env, account)["requests_reserved"] == 1
    await metering.settle(env.services.db, second, Settlement("answered", True, 900), now)
    await metering.settle(env.services.db, first, Settlement("answered", True, 100), now)
    b = balance(env, account)
    assert (b["topup_remaining"], b["topup_reserved"]) == (1, 0)
    assert week_row(env, account)["requests_used"] == 1 and week_row(env, account)["cost_used_micro_usd"] == 1_000
    # an uncharged top-up turn gives the request back
    third = await reserve(env, account, now, limits)
    await metering.settle(env.services.db, third, Settlement("refused", False, 100), now)
    assert balance(env, account)["topup_remaining"] == 1
    # the plan stops allowing top-ups (a downgrade): the balance is kept, the window refuses
    v = await view(env, account, now, taste(10))
    assert v.kind == "taste" and v.topup_balance == 1 and v.topup_allowed is False
    with pytest.raises(AllowanceExhaustedError) as ei:
        await reserve(env, account, now, windows(1, 120, topup_allowed=False))
    assert ei.value.reason == "window"
    assert balance(env, account)["topup_remaining"] == 1


async def test_exhausted_reasons_in_order(env: Env) -> None:
    """window before week before cost_cap before failures (the status body names one reason)."""
    account, now = new_account(env), a_month()
    await reserve(env, account, now, windows(5, 5))
    full = _Counts(window_used=5, week_used=5, cost_used=10**9, failed=10**6, window_end=now, week_end=now)
    assert metering.exhausted_reason(full, windows(5, 50, cost_cap=1, failed=1), RESERVE) == (None, "window")
    # both full: the bucket that clears later is named, so the reset time given is when a request works again
    later_week = dataclasses.replace(full, window_end=now + timedelta(hours=5), week_end=now + timedelta(days=7))
    assert metering.exhausted_reason(later_week, windows(5, 5, cost_cap=1, failed=1), RESERVE) == (None, "week")
    later_window = dataclasses.replace(full, window_end=now + timedelta(hours=5), week_end=now + timedelta(hours=1))
    assert metering.exhausted_reason(later_window, windows(5, 5, cost_cap=1, failed=1), RESERVE) == (None, "window")
    assert metering.exhausted_reason(full, windows(50, 5, cost_cap=1, failed=1), RESERVE) == (None, "week")
    assert metering.exhausted_reason(full, windows(50, 50, cost_cap=1, failed=1), RESERVE) == (None, "cost_cap")
    assert metering.exhausted_reason(full, windows(50, 50, failed=1), RESERVE) == (None, "failures")
    assert metering.exhausted_reason(full, windows(50, 50, failed=10**9), RESERVE) == ("window", None)
    # a top-up pays for a full window or week, never for the cost cap or the failed turns
    paid = _Counts(window_used=5, topup_remaining=3)
    assert metering.exhausted_reason(paid, windows(5, 50, topup_allowed=True), RESERVE) == ("topup", None)
    assert metering.exhausted_reason(paid, windows(5, 50, topup_allowed=False), RESERVE) == (None, "window")
    capped = _Counts(window_used=5, topup_remaining=3, cost_used=10**9)
    assert metering.exhausted_reason(capped, windows(5, 50, cost_cap=1, topup_allowed=True), RESERVE) == (
        None,
        "cost_cap",
    )
    assert metering.exhausted_reason(_Counts(taste_used=10), taste(10), RESERVE) == (None, "taste")
    assert metering.exhausted_reason(_Counts(taste_used=9), taste(10), RESERVE) == ("taste", None)
    assert metering.exhausted_reason(_Counts(), Limits(0, 0, 0, 5, 168, 10**9, 10, False), RESERVE) == (None, "window")
    assert Limits(0, 0, 0, 5, 168, 10**9, 10, False).kind == "none"


@pytest.mark.parametrize(
    "case", [c for c in FIXTURE["cases"] if "limits" in c and "used" in c], ids=lambda c: c["name"]
)
def test_shared_fixture_cases_evaluate_identically(case: dict[str, Any]) -> None:
    """The phone evaluates the same cases (fixtures/ai-window-cases.json): source and exhausted reason agree."""
    expect = case["expect"]
    if "warn" in expect:
        used, limit = case["used"]["window"], case["limits"]["window"]
        assert (used * 100 >= limit * metering.WARN_AT_PERCENT) is expect["warn"]
        return
    counts = _Counts(
        window_used=case["used"].get("window", 0),
        week_used=case["used"].get("week", 0),
        topup_remaining=case.get("topup_balance", 0),
        window_end=datetime.fromisoformat(case["window_ends_at"]) if "window_ends_at" in case else None,
        week_end=datetime.fromisoformat(case["week_ends_at"]) if "week_ends_at" in case else None,
    )
    limits = windows(case["limits"]["window"], case["limits"]["week"], topup_allowed=case.get("topup_allowed", False))
    source, reason = metering.exhausted_reason(counts, limits, RESERVE)
    assert (source, reason) == (expect["source"], expect["exhausted"])
    if reason is not None:
        assert metering._reset_for(reason, counts) == datetime.fromisoformat(expect["resets_at"])


def test_shared_fixture_taste_and_window_opening_cases() -> None:
    first = next(c for c in FIXTURE["cases"] if c["name"] == "first request opens both windows")
    now = datetime.fromisoformat(first["now"])
    assert now + timedelta(hours=FIXTURE["window_hours"]) == datetime.fromisoformat(first["expect"]["window_ends_at"])
    assert now + timedelta(days=FIXTURE["week_days"]) == datetime.fromisoformat(first["expect"]["week_ends_at"])
    taste_case = next(c for c in FIXTURE["cases"] if "taste" in c)
    counts = _Counts(taste_used=taste_case["taste"]["used"])
    assert metering.exhausted_reason(counts, taste(taste_case["taste"]["limit"]), RESERVE) == (None, "taste")
    assert metering._reset_for("taste", counts) is None
    assert FIXTURE["warn_at_percent"] == metering.WARN_AT_PERCENT == 80


# ----- concurrency ------------------------------------------------------------------------------------------------------


async def test_concurrent_reservations_never_exceed_the_window(env: Env) -> None:
    account, now = new_account(env), a_month()
    results = await race(env, [account], now, windows(3, 120), 10**12)
    won = [r for r in results if isinstance(r, Reservation)]
    lost = [r for r in results if isinstance(r, AllowanceExhaustedError)]
    assert len(won) == 3 and len(lost) == 7 and {e.reason for e in lost} == {"window"}
    assert {e.resets_at for e in lost} == {now + timedelta(hours=5)}
    [window] = window_rows(env, account, "window")  # ten racing reservations opened exactly one window
    assert window["requests_reserved"] == 3 and week_row(env, account)["cost_reserved_micro_usd"] == 3 * RESERVE
    assert spend(env, now) == (0, 3 * RESERVE)
    pending = sql(env.database_url, "SELECT count(*) FROM assistant_turns WHERE account_id = %s", (str(account),))
    assert pending[0][0] == 3  # the refused ones left no ledger row


async def test_concurrent_reservations_never_exceed_the_week_the_taste_or_the_topup(env: Env) -> None:
    week_account, now = new_account(env), a_month()
    results = await race(env, [week_account], now, windows(100, 4), 10**12)
    assert sum(isinstance(r, Reservation) for r in results) == 4
    assert {r.reason for r in results if isinstance(r, AllowanceExhaustedError)} == {"week"}
    taste_account = new_account(env)
    results = await race(env, [taste_account], now, taste(3), 10**12)
    assert sum(isinstance(r, Reservation) for r in results) == 3 and balance(env, taste_account)["taste_reserved"] == 3
    assert {r.reason for r in results if isinstance(r, AllowanceExhaustedError)} == {"taste"}
    topup_account = new_account(env)
    credit_topup(env, topup_account, 2)
    results = await race(env, [topup_account], now, windows(1, 100, topup_allowed=True), 10**12)
    sources = sorted(r.source for r in results if isinstance(r, Reservation))
    assert sources == ["topup", "topup", "window"]
    assert balance(env, topup_account)["topup_reserved"] == 2
    assert {r.reason for r in results if isinstance(r, AllowanceExhaustedError)} == {"window"}


async def test_concurrent_reservations_never_exceed_the_cost_cap(env: Env) -> None:
    account, now = new_account(env), a_month()
    results = await race(env, [account], now, windows(100, 100, cost_cap=3 * RESERVE), 10**12)
    assert sum(isinstance(r, Reservation) for r in results) == 3
    assert {r.reason for r in results if isinstance(r, AllowanceExhaustedError)} == {"cost_cap"}
    assert week_row(env, account)["cost_reserved_micro_usd"] == 3 * RESERVE


async def test_concurrent_reservations_never_exceed_the_ceiling_across_accounts(env: Env) -> None:
    alice, bob, now = new_account(env), new_account(env), a_month()
    results = await race(env, [alice, bob], now, BIG, 3 * RESERVE)
    assert sum(isinstance(r, Reservation) for r in results) == 3
    assert sum(isinstance(r, SpendLimitError) for r in results) == 7
    assert spend(env, now) == (0, 3 * RESERVE)
    # a refused turn keeps nothing: neither the account's reservation nor a ledger row
    reserved = week_row(env, alice).get("requests_reserved", 0) + week_row(env, bob).get("requests_reserved", 0)
    assert reserved == 3


# ----- settlement -------------------------------------------------------------------------------------------------------


async def test_failed_turn_limit_is_per_week(env: Env) -> None:
    account, now = new_account(env), a_month()
    limits = windows(100, 100, failed=2)
    for status in ("timeout", "invalid_output"):
        r = await reserve(env, account, now, limits)
        assert await metering.settle(env.services.db, r, Settlement(status, False, 1_000), now)
    with pytest.raises(AllowanceExhaustedError) as ei:
        await reserve(env, account, now, limits)
    assert (ei.value.reason, ei.value.resets_at) == ("failures", now + timedelta(days=7))
    await reserve(env, account, now + timedelta(days=7, seconds=1), limits)  # a new week forgets them
    # cancellations and provider outages do not count as failed turns (their cost does)
    other = new_account(env)
    for status in ("cancelled", "provider_error", "cancelled"):
        r = await reserve(env, other, now, limits)
        await metering.settle(env.services.db, r, Settlement(status, False, 500), now)
    await reserve(env, other, now, limits)
    assert week_row(env, other)["failed_turns"] == 0 and week_row(env, other)["cost_used_micro_usd"] == 1_500


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
    [window] = window_rows(env, account, "window")
    week = week_row(env, account)
    assert (window["requests_used"], window["requests_reserved"]) == (1, 0)
    assert (week["requests_used"], week["requests_reserved"], week["failed_turns"]) == (1, 0, 0)
    assert (week["cost_used_micro_usd"], week["cost_reserved_micro_usd"]) == (4_321, 0)
    assert spend(env, now) == (4_321, 0)
    ledger = sql(
        env.database_url,
        "SELECT status, charged, cost_micro_usd, model_served, fallback_used, provider_request_id, latency_ms, "
        "finished_at IS NOT NULL, source, window_start, period_start FROM assistant_turns WHERE id = %s",
        (str(r.ledger_id),),
    )[0]
    assert ledger == ("answered", True, 4_321, "claude-opus-5-5", True, "req_x", 1234, True, "window", now, now)
    # refused: metered, not charged, a failed turn; provider error before the stream: nothing billed
    for settlement, failed in ((Settlement("refused", False, 900), 1), (Settlement("provider_error", False, 0), 0)):
        before = week_row(env, account)
        r = await reserve(env, account, now)
        await metering.settle(env.services.db, r, settlement, now)
        after = week_row(env, account)
        assert after["requests_used"] == before["requests_used"]
        assert after["failed_turns"] == before["failed_turns"] + failed
        assert after["cost_used_micro_usd"] == before["cost_used_micro_usd"] + settlement.cost_micro_usd
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
    assert window_rows(env, alice, "window")[0]["requests_reserved"] == 1


async def test_sweeper_settles_stale_pending_turns_into_their_own_windows(env: Env) -> None:
    account, now = new_account(env), a_month()
    stale = await reserve(env, account, now)
    later = now + timedelta(hours=6)  # the stale turn's 5-hour window has ended by then
    fresh = await reserve(env, account, later - timedelta(seconds=10))  # opens a second window
    assert await metering.sweep(env.services.db, later, 80) >= 1  # global: earlier tests' leftovers go too
    assert await metering.sweep(env.services.db, later, 80) == 0
    statuses = dict(
        sql(env.database_url, "SELECT id::text, status FROM assistant_turns WHERE account_id = %s", (str(account),))
    )
    assert statuses == {str(stale.ledger_id): "abandoned", str(fresh.ledger_id): "pending"}
    ended, current = window_rows(env, account, "window")  # the stale turn was released from the ended window
    week = week_row(env, account)
    assert (ended["requests_reserved"], ended["requests_used"]) == (0, 0)
    assert (current["requests_reserved"], current["requests_used"]) == (1, 0)
    assert (week["requests_reserved"], week["cost_used_micro_usd"], week["failed_turns"]) == (1, RESERVE, 1)
    assert week["requests_used"] == 0  # abandoned turns are never charged
    # the service's own settle after the sweeper got there first changes nothing
    assert not await metering.settle(env.services.db, stale, Settlement("answered", True, 1), later)


async def test_sweeper_and_a_live_settle_never_deadlock(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    """Stale rows of two accounts and a live turn of the second: the sweeper pauses between the rows while the live
    turn settles. Settling both rows in one transaction would hold the month row while waiting for bob's balance row,
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
    week = week_row(env, bob)
    assert (week["requests_reserved"], week["requests_used"], week["failed_turns"]) == (0, 1, 1)
    assert week["cost_used_micro_usd"] == RESERVE + 9_000
    assert spend(env, now) == (2 * RESERVE + 9_000, 0)


async def test_window_rollover_settles_into_the_turns_own_window(env: Env) -> None:
    account = new_account(env)
    now = a_month()
    r = await reserve(env, account, now)
    after = now + timedelta(hours=5, seconds=5)
    await metering.settle(env.services.db, r, Settlement("answered", True, 100), after)
    [first] = window_rows(env, account, "window")
    assert first["requests_used"] == 1 and first["requests_reserved"] == 0
    v = await view(env, account, after, BIG)
    assert v.window is not None and (v.window.used, v.window.starts_at, v.exhausted) == (0, None, None)
    assert v.week is not None and (v.week.used, v.week.starts_at) == (1, now)


async def test_allowance_bodies_validate_against_the_contract(env: Env) -> None:
    account, now = new_account(env), a_month()
    bodies = [(await view(env, account, now, windows(30, 120, topup_allowed=True))).body()]
    r = await reserve(env, account, now, windows(30, 120))
    await metering.settle(env.services.db, r, Settlement("answered", True, 100), now)
    bodies.append((await view(env, account, now, windows(30, 120, topup_allowed=True))).body())
    bodies.append((await view(env, account, now, windows(1, 120))).body())  # exhausted: window
    bodies.append((await view(env, account, now, Limits(0, 0, 0, 5, 168, 10**9, 10, False))).body())  # kind none
    for body in bodies:
        load_schemas().validate_rest("assistant_allowance", body)
    assert bodies[1]["window"]["used"] == 1 and bodies[1]["topup"] == {"balance": 0, "allowed": True}
    assert bodies[2]["exhausted"] == "window" and bodies[2]["resets_at"] == bodies[2]["window"]["ends_at"]
    assert bodies[3]["kind"] == "none" and bodies[3]["window"] is None and bodies[3]["taste"] is None
