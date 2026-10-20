"""Allowance windows, top-up balance, cost cap, failed-turn limit and the operator's spending ceiling
(docs/design/assistant.md §3.7; rules.ai_allowance).

Every turn is reserved before the provider call and settled after it, each in its own short transaction, so no
connection or row lock is held while the model thinks. A reservation locks the account's ``ai_balances`` row
(``FOR UPDATE``, created on first use), which serialises one account's AI accounting while leaving other accounts
untouched; the ceiling row is a conditional ``UPDATE`` that PostgreSQL serialises across accounts. Every transaction
locks in one order (ledger, ai_balances, usage windows, spend month); the sweeper settles each stale row in a
transaction of its own, so reserve, settle and the sweeper cannot deadlock. Only counts and micro-dollars are written;
nothing here ever sees the words.

Which bucket pays (rules.ai_allowance): a plan with windows takes a turn from the 5-hour window **and** the weekly
window (both need room), else from the top-up balance when the plan may spend it, else the turn is refused; a plan with
only a taste takes it from the taste, which never renews. A window opens at the reservation that finds none open and
ends after its length; nothing resets on a calendar. The cost cap and the failed-turn limit are checked on the weekly
window for every source, so a top-up never bypasses the backstops. The operator ceiling stays per calendar month.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from dome_protocol import format_rfc3339
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dome_api.db.engine import transaction
from dome_api.db.models import AiBalance, AiSpendMonth, AssistantTurn, UsageWindow
from dome_api.logging import get_logger

log = get_logger("dome_api.assistant")

ExhaustedReason = Literal["taste", "window", "week", "cost_cap", "failures"]
Source = Literal["taste", "window", "topup"]
AllowanceKind = Literal["none", "taste", "windows"]
# Outcomes that may cost money without giving the owner anything count against the failed-turn limit (§3.7);
# the owner's own Cancel and the provider's outages do not, but their cost does.
FAILED_STATUSES = frozenset({"timeout", "abandoned", "refused", "invalid_output"})
SWEEP_BATCH = 100
WARN_AT_PERCENT = 80  # rest.schema.json#/$defs/assistant_allowance pins it; the phone warns from here


class TurnDuplicateError(Exception):
    """The phone sent a turn_id it already used (AI_TURN_DUPLICATE)."""


class AllowanceExhaustedError(Exception):
    def __init__(self, reason: ExhaustedReason, resets_at: datetime | None) -> None:
        super().__init__(reason)
        self.reason: ExhaustedReason = reason
        self.resets_at = resets_at  # the end of the bucket that refused; None for the taste, which never renews


class SpendLimitError(Exception):
    """The operator's monthly ceiling cannot take one more reservation (AI_SPEND_LIMIT_REACHED)."""


@dataclass(frozen=True, slots=True)
class Limits:
    taste: int  # one-time charged turns (Free); 0 on plans with windows
    window_requests: int  # charged turns per 5-hour window; 0 = no windows
    week_requests: int  # charged turns per weekly window
    window_hours: int
    week_hours: int
    cost_cap_micro_usd: int  # metered cost per weekly window, unknown-cost turns included
    failed_turns: int  # failed turns per weekly window
    topup_allowed: bool  # the balance may be spent (plan allows, billing on, assistant configured)

    @property
    def kind(self) -> AllowanceKind:
        if self.window_requests > 0:
            return "windows"
        return "taste" if self.taste > 0 else "none"

    @property
    def has_allowance(self) -> bool:
        return self.kind != "none"


@dataclass(frozen=True, slots=True)
class Reservation:
    ledger_id: uuid.UUID
    account_id: uuid.UUID
    source: Source
    window_start: datetime | None  # the 5-hour window the turn was taken from (source window only)
    week_start: datetime  # the weekly window carrying the cost reservation
    month_start: datetime
    reserve_micro_usd: int


@dataclass(frozen=True, slots=True)
class Settlement:
    status: str
    charged: bool
    cost_micro_usd: int
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    model_served: str | None = None
    fallback_used: bool = False
    provider_request_id: str | None = None
    latency_ms: int | None = None

    @property
    def failed(self) -> bool:
        return self.status in FAILED_STATUSES


@dataclass(frozen=True, slots=True)
class Bucket:
    limit: int
    used: int
    starts_at: datetime | None
    ends_at: datetime | None

    def body(self) -> dict[str, Any]:
        return {
            "limit": self.limit,
            "used": self.used,
            "starts_at": format_rfc3339(self.starts_at) if self.starts_at else None,
            "ends_at": format_rfc3339(self.ends_at) if self.ends_at else None,
        }


@dataclass(frozen=True, slots=True)
class AllowanceView:
    kind: AllowanceKind
    taste: tuple[int, int] | None  # (limit, used)
    window: Bucket | None
    week: Bucket | None
    topup_balance: int
    topup_allowed: bool
    exhausted: ExhaustedReason | None
    resets_at: datetime | None

    def body(self) -> dict[str, Any]:
        """rest.schema.json#/$defs/assistant_allowance"""
        return {
            "kind": self.kind,
            "taste": {"limit": self.taste[0], "used": self.taste[1]} if self.taste else None,
            "window": self.window.body() if self.window else None,
            "week": self.week.body() if self.week else None,
            "topup": {"balance": self.topup_balance, "allowed": self.topup_allowed},
            "exhausted": self.exhausted,
            "resets_at": format_rfc3339(self.resets_at) if self.resets_at else None,
            "warn_at_percent": WARN_AT_PERCENT,
        }


@dataclass(frozen=True, slots=True)
class _Counts:
    """What the account's rows say right now (zeros where no row exists)."""

    taste_used: int = 0
    taste_reserved: int = 0
    topup_remaining: int = 0
    topup_reserved: int = 0
    window_used: int = 0
    window_reserved: int = 0
    window_start: datetime | None = None
    window_end: datetime | None = None
    week_used: int = 0
    week_reserved: int = 0
    week_start: datetime | None = None
    week_end: datetime | None = None
    cost_used: int = 0
    cost_reserved: int = 0
    failed: int = 0


def month_for(now: datetime) -> datetime:
    """The operator ceiling's period: the calendar month in UTC (DOME_AI_MONTHLY_CEILING_CENTS)."""
    return now.astimezone(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def exhausted_reason(counts: _Counts, limits: Limits, reserve: int) -> tuple[Source | None, ExhaustedReason | None]:
    """Where the next turn would be taken from, or why it is refused. Checked in this order: the bucket (taste, or
    window then week, with the top-up balance paying for a full window or week when allowed; when both are full the
    reason is the one whose end is later), then the cost cap,
    then the failed turns; both backstops live on the weekly window. The shared fixture
    ``fixtures/ai-window-cases.json`` pins the same arithmetic for the phone."""
    source: Source | None
    if limits.kind == "none":
        return None, "window"
    if limits.kind == "taste":
        if counts.taste_used + counts.taste_reserved >= limits.taste:
            return None, "taste"
        source = "taste"
    else:
        full: ExhaustedReason | None = None
        window_full = counts.window_used + counts.window_reserved >= limits.window_requests
        week_full = counts.week_used + counts.week_reserved >= limits.week_requests
        if window_full and week_full:
            # both full (4 full windows of Pro use up its week): the reason is the one that clears later, so the
            # reported reset time is when a request can really be made again
            later_week = counts.week_end is None or counts.window_end is None or counts.week_end >= counts.window_end
            full = "week" if later_week else "window"
        elif window_full:
            full = "window"
        elif week_full:
            full = "week"
        if full is None:
            source = "window"
        elif limits.topup_allowed and counts.topup_remaining - counts.topup_reserved > 0:
            source = "topup"
        else:
            return None, full
    if counts.cost_used + counts.cost_reserved + reserve > limits.cost_cap_micro_usd:
        return None, "cost_cap"
    if counts.failed >= limits.failed_turns:
        return None, "failures"
    return source, None


def _reset_for(reason: ExhaustedReason | None, counts: _Counts) -> datetime | None:
    if reason == "window":
        return counts.window_end
    if reason in ("week", "cost_cap", "failures"):
        return counts.week_end
    return None


async def _open_window(db: AsyncSession, account_id: uuid.UUID, kind: str, now: datetime) -> UsageWindow | None:
    w = UsageWindow
    row: UsageWindow | None = await db.scalar(
        select(w)
        .where(w.account_id == account_id, w.kind == kind, w.window_start <= now, w.window_end > now)
        .order_by(w.window_start.desc())
        .limit(1)
    )
    return row


async def _counts(db: AsyncSession, account_id: uuid.UUID, now: datetime, *, lock: bool) -> _Counts:
    stmt = select(AiBalance).where(AiBalance.account_id == account_id)
    if lock:
        stmt = stmt.with_for_update()
    balance = await db.scalar(stmt)
    window = await _open_window(db, account_id, "window", now)
    week = await _open_window(db, account_id, "week", now)
    return _Counts(
        taste_used=balance.taste_used if balance else 0,
        taste_reserved=balance.taste_reserved if balance else 0,
        topup_remaining=balance.topup_remaining if balance else 0,
        topup_reserved=balance.topup_reserved if balance else 0,
        window_used=window.requests_used if window else 0,
        window_reserved=window.requests_reserved if window else 0,
        window_start=window.window_start if window else None,
        window_end=window.window_end if window else None,
        week_used=week.requests_used if week else 0,
        week_reserved=week.requests_reserved if week else 0,
        week_start=week.window_start if week else None,
        week_end=week.window_end if week else None,
        cost_used=week.cost_used_micro_usd if week else 0,
        cost_reserved=week.cost_reserved_micro_usd if week else 0,
        failed=week.failed_turns if week else 0,
    )


async def allowance(
    db: AsyncSession, account_id: uuid.UUID, now: datetime, limits: Limits, reserve: int
) -> AllowanceView:
    """The account's allowance as the phone sees it (read only; no window is opened by looking)."""
    c = await _counts(db, account_id, now, lock=False)
    _, reason = exhausted_reason(c, limits, reserve)
    kind = limits.kind
    window = week = None
    if kind == "windows":
        window = Bucket(limits.window_requests, c.window_used, c.window_start, c.window_end)
        week = Bucket(limits.week_requests, c.week_used, c.week_start, c.week_end)
    return AllowanceView(
        kind=kind,
        taste=(limits.taste, c.taste_used) if kind == "taste" else None,
        window=window,
        week=week,
        topup_balance=max(0, c.topup_remaining - c.topup_reserved),
        topup_allowed=limits.topup_allowed,
        exhausted=reason,
        resets_at=_reset_for(reason, c),
    )


async def topup_balance(db: AsyncSession, account_id: uuid.UUID) -> int:
    """Purchased requests not yet used or reserved (kept across plan changes, docs/BILLING.md §10)."""
    balance = await db.scalar(select(AiBalance).where(AiBalance.account_id == account_id))
    return max(0, balance.topup_remaining - balance.topup_reserved) if balance else 0


async def ceiling_reached(db: AsyncSession, now: datetime, reserve: int, ceiling: int) -> bool:
    month = await db.get(AiSpendMonth, month_for(now))
    spent = month.spent_micro_usd + month.reserved_micro_usd if month else 0
    return spent + reserve > ceiling


async def lock_balance(db: AsyncSession, account_id: uuid.UUID, now: datetime) -> AiBalance:
    """The account's ``ai_balances`` row, created when missing and locked FOR UPDATE (every reservation, settlement
    and credit of an account goes through here, in this lock position)."""
    await db.execute(
        pg_insert(AiBalance)
        .values(
            account_id=account_id, taste_used=0, taste_reserved=0, topup_remaining=0, topup_reserved=0, updated_at=now
        )
        .on_conflict_do_nothing(index_elements=[AiBalance.account_id])
    )
    row = await db.scalar(select(AiBalance).where(AiBalance.account_id == account_id).with_for_update())
    assert row is not None
    return row


async def _ensure_window(
    db: AsyncSession, account_id: uuid.UUID, kind: str, now: datetime, length_hours: int
) -> UsageWindow:
    """The open window of ``kind``, or a new one starting now (the account's balance row is locked by the caller,
    so two reservations of one account cannot open two windows)."""
    row = await _open_window(db, account_id, kind, now)
    if row is None:
        row = UsageWindow(
            id=uuid.uuid4(),
            account_id=account_id,
            kind=kind,
            window_start=now,
            window_end=now + timedelta(hours=length_hours),
            requests_used=0,
            requests_reserved=0,
            cost_used_micro_usd=0,
            cost_reserved_micro_usd=0,
            failed_turns=0,
            created_at=now,
        )
        db.add(row)
        await db.flush()
    return row


async def reserve(
    factory: async_sessionmaker[AsyncSession],
    *,
    account_id: uuid.UUID,
    client_turn_id: uuid.UUID,
    now: datetime,
    limits: Limits,
    reserve_micro_usd: int,
    ceiling_micro_usd: int,
) -> Reservation:
    """Write the pending ledger row and take one request from the bucket that pays, one reservation of the weekly
    cost cap and one reservation of the ceiling, all or nothing. Raises :class:`TurnDuplicateError`,
    :class:`AllowanceExhaustedError` or :class:`SpendLimitError` (and nothing is kept, not even a new window)."""
    month_start = month_for(now)
    ledger_id = uuid.uuid4()
    async with transaction(factory) as db:
        inserted = await db.scalar(
            pg_insert(AssistantTurn)
            .values(
                id=ledger_id,
                account_id=account_id,
                client_turn_id=client_turn_id,
                status="pending",
                period_start=now,  # replaced below by the weekly window's start
                month_start=month_start,
                reserved_micro_usd=reserve_micro_usd,
                created_at=now,
            )
            .on_conflict_do_nothing(constraint="uq_assistant_turns_account_turn")
            .returning(AssistantTurn.id)
        )
        if inserted is None:
            raise TurnDuplicateError
        balance = await lock_balance(db, account_id, now)
        week = await _ensure_window(db, account_id, "week", now, limits.week_hours)
        window = None
        if limits.kind == "windows":
            window = await _ensure_window(db, account_id, "window", now, limits.window_hours)
        counts = _Counts(
            taste_used=balance.taste_used,
            taste_reserved=balance.taste_reserved,
            topup_remaining=balance.topup_remaining,
            topup_reserved=balance.topup_reserved,
            window_used=window.requests_used if window else 0,
            window_reserved=window.requests_reserved if window else 0,
            window_start=window.window_start if window else None,
            window_end=window.window_end if window else None,
            week_used=week.requests_used,
            week_reserved=week.requests_reserved,
            week_start=week.window_start,
            week_end=week.window_end,
            cost_used=week.cost_used_micro_usd,
            cost_reserved=week.cost_reserved_micro_usd,
            failed=week.failed_turns,
        )
        source, reason = exhausted_reason(counts, limits, reserve_micro_usd)
        if source is None:
            assert reason is not None
            raise AllowanceExhaustedError(reason, _reset_for(reason, counts))  # the transaction rolls back
        if source == "taste":
            balance.taste_reserved += 1
        elif source == "topup":
            balance.topup_reserved += 1
        else:
            assert window is not None
            window.requests_reserved += 1
            week.requests_reserved += 1
        balance.updated_at = now
        week.cost_reserved_micro_usd += reserve_micro_usd
        await db.flush()
        await db.execute(
            pg_insert(AiSpendMonth)
            .values(month_start=month_start, spent_micro_usd=0, reserved_micro_usd=0, updated_at=now)
            .on_conflict_do_nothing(index_elements=[AiSpendMonth.month_start])
        )
        m = AiSpendMonth
        within = await db.scalar(
            update(m)
            .where(
                m.month_start == month_start,
                m.spent_micro_usd + m.reserved_micro_usd + reserve_micro_usd <= ceiling_micro_usd,
            )
            .values(reserved_micro_usd=m.reserved_micro_usd + reserve_micro_usd, updated_at=now)
            .returning(m.month_start)
            .execution_options(synchronize_session=False)
        )
        if within is None:
            raise SpendLimitError
        window_start = window.window_start if source == "window" and window is not None else None
        await db.execute(
            update(AssistantTurn)
            .where(AssistantTurn.id == ledger_id)
            .values(source=source, window_start=window_start, period_start=week.window_start)
            .execution_options(synchronize_session=False)
        )
        return Reservation(
            ledger_id=ledger_id,
            account_id=account_id,
            source=source,
            window_start=window_start,
            week_start=week.window_start,
            month_start=month_start,
            reserve_micro_usd=reserve_micro_usd,
        )


async def _settle_in(db: AsyncSession, r: Reservation, s: Settlement, now: datetime) -> bool:
    t = AssistantTurn
    done = await db.scalar(
        update(t)
        .where(t.id == r.ledger_id, t.status == "pending")  # guards against a double settle with the sweeper
        .values(
            status=s.status,
            charged=s.charged,
            finished_at=now,
            latency_ms=s.latency_ms,
            input_tokens=s.input_tokens,
            output_tokens=s.output_tokens,
            cache_read_tokens=s.cache_read_tokens,
            cache_write_tokens=s.cache_write_tokens,
            cost_micro_usd=s.cost_micro_usd,
            model_served=s.model_served[:64] if s.model_served else None,
            fallback_used=s.fallback_used,
            provider_request_id=s.provider_request_id[:128] if s.provider_request_id else None,
        )
        .returning(t.id)
        .execution_options(synchronize_session=False)
    )
    if done is None:
        return False
    charged = 1 if s.charged else 0
    b = AiBalance
    await db.execute(select(b).where(b.account_id == r.account_id).with_for_update())  # the account's lock position
    if r.source == "taste":
        await db.execute(
            update(b)
            .where(b.account_id == r.account_id)
            .values(taste_reserved=b.taste_reserved - 1, taste_used=b.taste_used + charged, updated_at=now)
            .execution_options(synchronize_session=False)
        )
    elif r.source == "topup":
        await db.execute(
            update(b)
            .where(b.account_id == r.account_id)
            .values(topup_reserved=b.topup_reserved - 1, topup_remaining=b.topup_remaining - charged, updated_at=now)
            .execution_options(synchronize_session=False)
        )
    w = UsageWindow
    if r.source == "window":
        await db.execute(
            update(w)
            .where(w.account_id == r.account_id, w.kind == "window", w.window_start == r.window_start)
            .values(requests_reserved=w.requests_reserved - 1, requests_used=w.requests_used + charged)
            .execution_options(synchronize_session=False)
        )
    await db.execute(
        update(w)
        .where(w.account_id == r.account_id, w.kind == "week", w.window_start == r.week_start)  # the turn's own week
        .values(
            requests_reserved=w.requests_reserved - (1 if r.source == "window" else 0),
            requests_used=w.requests_used + (charged if r.source == "window" else 0),
            cost_reserved_micro_usd=w.cost_reserved_micro_usd - r.reserve_micro_usd,
            cost_used_micro_usd=w.cost_used_micro_usd + s.cost_micro_usd,
            failed_turns=w.failed_turns + (1 if s.failed else 0),
        )
        .execution_options(synchronize_session=False)
    )
    m = AiSpendMonth
    await db.execute(
        update(m)
        .where(m.month_start == r.month_start)
        .values(
            reserved_micro_usd=m.reserved_micro_usd - r.reserve_micro_usd,
            spent_micro_usd=m.spent_micro_usd + s.cost_micro_usd,
            updated_at=now,
        )
        .execution_options(synchronize_session=False)
    )
    if s.cost_micro_usd > r.reserve_micro_usd:
        log.warning(
            "assistant.cost_over_reserve",
            ledger_id=str(r.ledger_id),
            cost_micro_usd=s.cost_micro_usd,
            reserved_micro_usd=r.reserve_micro_usd,
        )
    return True


async def settle(factory: async_sessionmaker[AsyncSession], r: Reservation, s: Settlement, now: datetime) -> bool:
    """Release the reservation and record what the turn really cost. ``False`` when the row was already settled."""
    async with transaction(factory) as db:
        return await _settle_in(db, r, s, now)


async def sweep(factory: async_sessionmaker[AsyncSession], now: datetime, stale_after_seconds: float) -> int:
    """Settle ledger rows still pending long after their deadline (a killed process, a lost task) as ``abandoned``:
    uncharged, a failed turn, at the full reservation. Repairs the reservations a crash left behind, in the turn's own
    windows even when they have ended since.

    Each row is settled in its own transaction, which locks it again first: one transaction for several rows would
    hold a month row while it waits for another account's rows, the opposite of a live turn's order."""
    cutoff = now - timedelta(seconds=stale_after_seconds)
    t = AssistantTurn
    async with factory() as db:
        candidates = (
            await db.scalars(
                select(t.id)
                .where(t.status == "pending", t.created_at < cutoff)
                .order_by(t.created_at)
                .limit(SWEEP_BATCH)
            )
        ).all()
    swept = 0
    for candidate in candidates:
        async with transaction(factory) as db:
            row = (
                await db.execute(
                    select(t.account_id, t.source, t.window_start, t.period_start, t.month_start, t.reserved_micro_usd)
                    .where(t.id == candidate, t.status == "pending")
                    .with_for_update(skip_locked=True)  # a turn settling it right now wins
                )
            ).first()
            if row is None:
                continue
            account_id, source, window_start, week_start, month_start, reserved = row
            if source is None:
                # the reservation never completed (crash between the insert and the bucket update): nothing to release
                await db.execute(
                    update(t)
                    .where(t.id == candidate)
                    .values(status="abandoned", finished_at=now)
                    .execution_options(synchronize_session=False)
                )
                swept += 1
                continue
            r = Reservation(candidate, account_id, source, window_start, week_start, month_start, int(reserved))
            if await _settle_in(
                db, r, Settlement(status="abandoned", charged=False, cost_micro_usd=int(reserved)), now
            ):
                swept += 1
    return swept
