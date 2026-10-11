"""Allowance, cost cap, failed-turn limit and the operator's spending ceiling (docs/design/assistant.md §3.7).

Every turn is reserved before the provider call and settled after it, each in its own short transaction, so no
connection or row lock is held while the model thinks. Reservation and settlement are conditional ``UPDATE``s, which
PostgreSQL serialises per row (READ COMMITTED re-evaluates the ``WHERE`` after waiting for the row lock): concurrent
turns can never take more than the interpretation limit, the per-account cost cap, the failed-turn limit or the
ceiling. Every transaction touches one ledger row and locks in one order (ledger, usage period, spend month); the
sweeper settles each stale row in a transaction of its own, so reserve, settle and the sweeper cannot deadlock. Only
counts and micro-dollars are written; nothing here ever sees the words.
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
from dome_api.db.models import AiSpendMonth, AssistantTurn, UsagePeriod
from dome_api.logging import get_logger

log = get_logger("dome_api.assistant")

ExhaustedReason = Literal["requests", "cost_cap", "failures"]
# Outcomes that may cost money without giving the owner anything count against the failed-turn limit (§3.7);
# the owner's own Cancel and the provider's outages do not, but their cost does.
FAILED_STATUSES = frozenset({"timeout", "abandoned", "refused", "invalid_output"})
SWEEP_BATCH = 100


class TurnDuplicateError(Exception):
    """The phone sent a turn_id it already used (AI_TURN_DUPLICATE)."""


class AllowanceExhaustedError(Exception):
    def __init__(self, reason: ExhaustedReason, resets_at: datetime) -> None:
        super().__init__(reason)
        self.reason: ExhaustedReason = reason
        self.resets_at = resets_at


class SpendLimitError(Exception):
    """The operator's monthly ceiling cannot take one more reservation (AI_SPEND_LIMIT_REACHED)."""


@dataclass(frozen=True, slots=True)
class Limits:
    interpretations: int  # charged turns per usage period
    cost_cap_micro_usd: int  # metered cost per usage period, unknown-cost turns included
    failed_turns: int  # failed turns per usage period


@dataclass(frozen=True, slots=True)
class Reservation:
    ledger_id: uuid.UUID
    account_id: uuid.UUID
    period_start: datetime
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
class AllowanceView:
    limit: int
    used: int
    period_start: datetime
    period_end: datetime
    exhausted: ExhaustedReason | None

    def body(self) -> dict[str, Any]:
        """rest.schema.json#/$defs/assistant_allowance"""
        return {
            "limit": self.limit,
            "used": self.used,
            "period_start": format_rfc3339(self.period_start),
            "period_end": format_rfc3339(self.period_end),
            "exhausted": self.exhausted,
        }


def period_for(now: datetime) -> tuple[datetime, datetime]:
    """The usage period: the calendar month in UTC, for every plan and billing interval (spec §14). When billing
    anchors periods to the subscription, only this function changes."""
    start = now.astimezone(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end = (start + timedelta(days=32)).replace(day=1)
    return start, end


def exhausted_reason(
    *, used: int, reserved: int, cost_used: int, cost_reserved: int, failed: int, limits: Limits, reserve: int
) -> ExhaustedReason | None:
    """Which limit stops a new turn, checked in this order: interpretations, cost cap, failed turns."""
    if used + reserved >= limits.interpretations:
        return "requests"
    if cost_used + cost_reserved + reserve > limits.cost_cap_micro_usd:
        return "cost_cap"
    if failed >= limits.failed_turns:
        return "failures"
    return None


async def _period_row(db: AsyncSession, account_id: uuid.UUID, period_start: datetime) -> UsagePeriod | None:
    row: UsagePeriod | None = await db.scalar(
        select(UsagePeriod).where(UsagePeriod.account_id == account_id, UsagePeriod.period_start == period_start)
    )
    return row


async def allowance(
    db: AsyncSession, account_id: uuid.UUID, now: datetime, limits: Limits, reserve: int
) -> AllowanceView:
    start, end = period_for(now)
    row = await _period_row(db, account_id, start)
    used = row.ai_interpretations_used if row else 0
    reason = exhausted_reason(
        used=used,
        reserved=row.ai_interpretations_reserved if row else 0,
        cost_used=row.ai_cost_used_micro_usd if row else 0,
        cost_reserved=row.ai_cost_reserved_micro_usd if row else 0,
        failed=row.ai_failed_turns if row else 0,
        limits=limits,
        reserve=reserve,
    )
    return AllowanceView(limit=limits.interpretations, used=used, period_start=start, period_end=end, exhausted=reason)


async def ceiling_reached(db: AsyncSession, now: datetime, reserve: int, ceiling: int) -> bool:
    month = await db.get(AiSpendMonth, period_for(now)[0])
    spent = month.spent_micro_usd + month.reserved_micro_usd if month else 0
    return spent + reserve > ceiling


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
    """Write the pending ledger row and take one interpretation, one reservation of cost cap and one reservation of
    the ceiling, all or nothing. Raises :class:`TurnDuplicateError`, :class:`AllowanceExhaustedError` or
    :class:`SpendLimitError` (and nothing is kept)."""
    period_start, period_end = period_for(now)
    month_start = period_start
    ledger_id = uuid.uuid4()
    try:
        async with transaction(factory) as db:
            inserted = await db.scalar(
                pg_insert(AssistantTurn)
                .values(
                    id=ledger_id,
                    account_id=account_id,
                    client_turn_id=client_turn_id,
                    status="pending",
                    period_start=period_start,
                    month_start=month_start,
                    reserved_micro_usd=reserve_micro_usd,
                    created_at=now,
                )
                .on_conflict_do_nothing(constraint="uq_assistant_turns_account_turn")
                .returning(AssistantTurn.id)
            )
            if inserted is None:
                raise TurnDuplicateError
            await db.execute(
                pg_insert(UsagePeriod)
                .values(
                    id=uuid.uuid4(),
                    account_id=account_id,
                    period_start=period_start,
                    period_end=period_end,
                    ai_interpretations_used=0,
                    ai_transcription_seconds_used=0,
                    ai_interpretations_reserved=0,
                    ai_transcription_seconds_reserved=0,
                )
                .on_conflict_do_nothing(constraint="uq_usage_periods_account_start")
            )
            u = UsagePeriod
            taken = await db.scalar(
                update(u)
                .where(
                    u.account_id == account_id,
                    u.period_start == period_start,
                    u.ai_interpretations_used + u.ai_interpretations_reserved < limits.interpretations,
                    u.ai_cost_used_micro_usd + u.ai_cost_reserved_micro_usd + reserve_micro_usd
                    <= limits.cost_cap_micro_usd,
                    u.ai_failed_turns < limits.failed_turns,
                )
                .values(
                    ai_interpretations_reserved=u.ai_interpretations_reserved + 1,
                    ai_cost_reserved_micro_usd=u.ai_cost_reserved_micro_usd + reserve_micro_usd,
                )
                .returning(u.id)
                .execution_options(synchronize_session=False)
            )
            if taken is None:
                raise AllowanceExhaustedError("requests", period_end)  # the reason is re-read below, after the rollback
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
    except AllowanceExhaustedError:
        async with factory() as db:
            row = await _period_row(db, account_id, period_start)
        reason: ExhaustedReason | None = None
        if row is not None:
            reason = exhausted_reason(
                used=row.ai_interpretations_used,
                reserved=row.ai_interpretations_reserved,
                cost_used=row.ai_cost_used_micro_usd,
                cost_reserved=row.ai_cost_reserved_micro_usd,
                failed=row.ai_failed_turns,
                limits=limits,
                reserve=reserve_micro_usd,
            )
        raise AllowanceExhaustedError(reason or "requests", period_end) from None
    return Reservation(
        ledger_id=ledger_id,
        account_id=account_id,
        period_start=period_start,
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
    u = UsagePeriod
    await db.execute(
        update(u)
        .where(u.account_id == r.account_id, u.period_start == r.period_start)  # the turn's own period
        .values(
            ai_interpretations_reserved=u.ai_interpretations_reserved - 1,
            ai_interpretations_used=u.ai_interpretations_used + (1 if s.charged else 0),
            ai_cost_reserved_micro_usd=u.ai_cost_reserved_micro_usd - r.reserve_micro_usd,
            ai_cost_used_micro_usd=u.ai_cost_used_micro_usd + s.cost_micro_usd,
            ai_failed_turns=u.ai_failed_turns + (1 if s.failed else 0),
            ai_input_tokens=u.ai_input_tokens + s.input_tokens,
            ai_output_tokens=u.ai_output_tokens + s.output_tokens,
            ai_cache_read_tokens=u.ai_cache_read_tokens + s.cache_read_tokens,
            ai_cache_write_tokens=u.ai_cache_write_tokens + s.cache_write_tokens,
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
    uncharged, a failed turn, at the full reservation. Repairs the reservations a crash left behind.

    Each row is settled in its own transaction, which locks it again first: one transaction for several rows would
    hold a month row while it waits for another account's period row, the opposite of a live turn's order."""
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
                    select(t.account_id, t.period_start, t.month_start, t.reserved_micro_usd)
                    .where(t.id == candidate, t.status == "pending")
                    .with_for_update(skip_locked=True)  # a turn settling it right now wins
                )
            ).first()
            if row is None:
                continue
            account_id, period_start, month_start, reserved = row
            r = Reservation(candidate, account_id, period_start, month_start, int(reserved))
            if await _settle_in(
                db, r, Settlement(status="abandoned", charged=False, cost_micro_usd=int(reserved)), now
            ):
                swept += 1
    return swept
