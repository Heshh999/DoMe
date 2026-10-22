"""The Screen view ledger (rules.screen_metering; docs/design/screen-view.md §10): Free minutes per rolling window,
reserved in chunks and **settled on the relay's own clock**.

The shape copies ``assistant/metering.py``: every operation runs in the caller's transaction, locks the account's
``screen_accounts`` row ``FOR UPDATE`` first (created on first use), so one account's reservations are serialised while
other accounts are untouched, and writes seconds only. Nothing the agent reports (``live_seconds``, ``relayed_bytes``)
ever lowers a charge; the agent's word buys at most a bounded pause discount for a ``paused -> resumed`` pair the relay
saw itself, during a session that had started, at most ``screen_secure_desktop_pause_max_seconds`` per WINDOW (a pause
still open when the session ends is charged: an unpaired pause is the PC's word alone).

Every locking read flushes this session's pending changes first and re-reads the row (``populate_existing``): the
sessions do not autoflush, and without it a ``FOR UPDATE`` select returns the identity-mapped object as an earlier
unlocked read saw it, so a sweep and a late ``ended`` could both settle one reservation.

How a session is charged:

* ``reserve_start`` takes the first chunk (``screen_allowance_chunk_seconds``) from the open window, or opens one;
  nothing left refuses the start (``AllowanceExhaustedError`` with the window's end). The reservation remembers where
  the relay's clock started (``segment_started_at`` = the forward; or the first ``started`` frame when the operator
  chose so) and how far the tickets reach (``reserved_until``).
* ``renew`` (every ``screen_usage``) takes the next chunk and moves ``reserved_until`` one chunk on; when the window has
  rolled over it first settles the old window with the seconds measured until its end and continues in the new one.
* ``settle`` (the ``ended`` frame) charges the wall seconds from the segment start to the end, rounded up to 10 s, less
  the bounded pause discount, capped at what was reserved; the rest of the last chunk is refunded by that cap. A start
  that failed before any ``started`` frame within ``screen_connect_timeout_seconds`` is charged nothing, three times
  per window; the fourth is charged its measured seconds. A start refused at the PC (Deny, or no answer) with no
  ``started`` frame within the consent timeout plus 5 s is likewise charged nothing, three times per window, on a
  counter of its own (quick Denies never use up the connection failures' forgiveness, and the two cannot be stacked).
* A plan change under a live session (Free ↔ unlimited) closes the current segment the way a window rollover does:
  the Free segment is settled against its window, an unlimited one charges no window; the session continues under
  the new plan from the next ticket.
* ``sweep`` settles reservations whose last ticket passed without an ``ended`` frame **in full**: a dead session never
  consumes more than it reserved, and never less than it was ticketed.

Plans with ``screen_view_seconds_per_window: null`` get the same reservations (so the dead-man tickets and the TURN
bookkeeping are identical) with ``unlimited: true`` and no window arithmetic.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from dome_api.db.models import ScreenAccount, ScreenReservation, ScreenViewWindow
from dome_api.logging import get_logger
from dome_api.util import ts

log = get_logger("dome_api.screen.metering")

ROUND_SECONDS = 10
MAX_ZERO_CHARGED_ATTEMPTS = 3
# a reservation is swept this long after its last ticket ran out (the agent's own `ended` has had time to arrive)
SWEEP_GRACE_SECONDS = 10
SWEEP_BATCH = 100
# end reasons of a start that never became a session; with no `started` frame seen within the connect timeout they
# are forgiven (charged 0) at most MAX_ZERO_CHARGED_ATTEMPTS times per window (rules.screen_metering (3))
FAILED_START_REASONS = frozenset({"connection_failed", "start_failed"})
# a start the PC's owner refused (SCREEN_CONSENT_DECLINED / _TIMEOUT): nothing was shown; forgiven on its own counter
CONSENT_REFUSED = "consent_refused"
MAX_CONSENT_FORGIVEN = 3
CONSENT_GRACE_SECONDS = 5


class AllowanceExhaustedError(Exception):
    """Nothing left in the account's window (SCREEN_ALLOWANCE_EXHAUSTED); ``resets_at`` is the window's end."""

    def __init__(self, resets_at: datetime | None) -> None:
        super().__init__("allowance exhausted")
        self.resets_at = resets_at


@dataclass(frozen=True, slots=True)
class Limits:
    seconds_per_window: int | None  # None = unlimited (plans.json screen_view_seconds_per_window null)
    window_hours: int
    chunk_seconds: int
    connect_timeout_seconds: int
    pause_max_seconds: int
    charge_from: str = "forwarded"  # or "started" (rules.screen_metering (3))
    consent_timeout_seconds: int = 30

    @property
    def unlimited(self) -> bool:
        return self.seconds_per_window is None


@dataclass(frozen=True, slots=True)
class Ticket:
    """What the relay tells both devices: the next ``granted_until`` and the allowance as it stands."""

    granted_until: datetime | None
    unlimited: bool
    remaining_seconds: int | None
    resets_at: datetime | None

    def allowance_state(self) -> dict[str, Any]:
        """relay-frames.schema.json#/$defs/screen_allowance_state"""
        return {
            "unlimited": self.unlimited,
            "remaining_seconds": None if self.unlimited else self.remaining_seconds,
            "resets_at": None if self.unlimited else ts(self.resets_at),
        }


def round_up(seconds: float) -> int:
    """Charged seconds are rounded up to ROUND_SECONDS (a 1 s view costs 10 s, never 0)."""
    if seconds <= 0:
        return 0
    return int(math.ceil(seconds / ROUND_SECONDS) * ROUND_SECONDS)


async def _locked(db: AsyncSession, stmt: Any) -> Any:
    """``stmt`` with ``FOR UPDATE``, read afresh: this session's pending changes are flushed first (the sessions do not
    autoflush) and the identity-mapped object is overwritten with the locked row, so the state guards below never read
    what an earlier, unlocked read saw."""
    await db.flush()
    return await db.scalar(stmt.with_for_update().execution_options(populate_existing=True))


async def lock_account(db: AsyncSession, account_id: uuid.UUID, now: datetime) -> ScreenAccount:
    """The account's ``screen_accounts`` row, created when missing and locked FOR UPDATE: every reservation, renewal,
    settlement and TURN mint of an account goes through here first, in this lock position."""
    await db.execute(
        pg_insert(ScreenAccount)
        .values(account_id=account_id, turn_mints_this_month=0, updated_at=now)
        .on_conflict_do_nothing(index_elements=[ScreenAccount.account_id])
    )
    row: ScreenAccount | None = await _locked(db, select(ScreenAccount).where(ScreenAccount.account_id == account_id))
    assert row is not None
    return row


async def open_window(db: AsyncSession, account_id: uuid.UUID, now: datetime) -> ScreenViewWindow | None:
    w = ScreenViewWindow
    row: ScreenViewWindow | None = await db.scalar(
        select(w)
        .where(w.account_id == account_id, w.window_start <= now, w.window_end > now)
        .order_by(w.window_start.desc())
        .limit(1)
    )
    return row


async def _ensure_window(db: AsyncSession, account_id: uuid.UUID, now: datetime, limits: Limits) -> ScreenViewWindow:
    """The open window, or a new one starting now (the account row is locked by the caller)."""
    row = await open_window(db, account_id, now)
    if row is None:
        row = ScreenViewWindow(
            id=uuid.uuid4(),
            account_id=account_id,
            window_start=now,
            window_end=now + timedelta(hours=limits.window_hours),
            used_seconds=0,
            reserved_seconds=0,
            zero_charged_attempts=0,
            consent_forgiven=0,
            pause_discount_seconds=0,
            created_at=now,
        )
        db.add(row)
        await db.flush()
    return row


def _room(window: ScreenViewWindow | None, limits: Limits) -> int:
    assert limits.seconds_per_window is not None
    if window is None:
        return limits.seconds_per_window
    return max(0, limits.seconds_per_window - window.used_seconds - window.reserved_seconds)


async def allowance(db: AsyncSession, account_id: uuid.UUID, now: datetime, limits: Limits) -> Ticket:
    """The allowance as the phone may see it (read only; looking opens no window and reserves nothing)."""
    if limits.unlimited:
        return Ticket(granted_until=None, unlimited=True, remaining_seconds=None, resets_at=None)
    window = await open_window(db, account_id, now)
    return Ticket(
        granted_until=None,
        unlimited=False,
        remaining_seconds=_room(window, limits),
        resets_at=window.window_end if window is not None else None,
    )


async def reserve_start(
    db: AsyncSession,
    *,
    account_id: uuid.UUID,
    pc_id: uuid.UUID,
    controller_id: uuid.UUID,
    command_id: uuid.UUID,
    now: datetime,
    limits: Limits,
) -> tuple[ScreenReservation, Ticket]:
    """Take the first chunk for a ``screen.session_start`` the relay is about to forward, in the caller's
    transaction (it commits together with the command row). Raises :class:`AllowanceExhaustedError` when nothing is
    left; the caller answers the controller with SCREEN_ALLOWANCE_EXHAUSTED and the PC never sees the command."""
    await lock_account(db, account_id, now)
    reservation = ScreenReservation(
        id=uuid.uuid4(),
        account_id=account_id,
        pc_id=pc_id,
        controller_id=controller_id,
        command_id=command_id,
        unlimited=limits.unlimited,
        forwarded_at=now,
        segment_started_at=now,
        reserved_until=now + timedelta(seconds=limits.chunk_seconds),
        outstanding_seconds=limits.chunk_seconds,
        charged_seconds=0,
        pause_discount_seconds=0,
        state="reserved",
        created_at=now,
    )
    if limits.unlimited:
        db.add(reservation)
        await db.flush()
        return reservation, Ticket(reservation.reserved_until, True, None, None)
    window = await _ensure_window(db, account_id, now, limits)
    room = _room(window, limits)
    if room <= 0:
        raise AllowanceExhaustedError(window.window_end)
    grant = min(limits.chunk_seconds, room)
    reservation.window_start = window.window_start
    reservation.reserved_until = now + timedelta(seconds=grant)
    reservation.outstanding_seconds = grant
    window.reserved_seconds += grant
    db.add(reservation)
    await db.flush()
    return reservation, Ticket(reservation.reserved_until, False, room - grant, window.window_end)


async def _window_of(db: AsyncSession, reservation: ScreenReservation) -> ScreenViewWindow | None:
    if reservation.window_start is None:
        return None
    w = ScreenViewWindow
    row: ScreenViewWindow | None = await _locked(
        db, select(w).where(w.account_id == reservation.account_id, w.window_start == reservation.window_start)
    )
    return row


def _measured(reservation: ScreenReservation, end: datetime) -> float:
    return max(0.0, (end - reservation.segment_started_at).total_seconds())


def _discount(reservation: ScreenReservation, window: ScreenViewWindow | None, limits: Limits) -> int:
    """Secure-desktop pauses the relay saw as a ``paused -> resumed`` pair, at most ``pause_max_seconds`` per window
    (what this window already discounted for other sessions counts). A pause still open at the end is not counted."""
    room = limits.pause_max_seconds - (window.pause_discount_seconds if window is not None else 0)
    return max(0, min(reservation.pause_discount_seconds, room, limits.pause_max_seconds))


async def _settle_segment(
    db: AsyncSession,
    reservation: ScreenReservation,
    window: ScreenViewWindow | None,
    end: datetime,
    limits: Limits,
    *,
    forgive: str | None,
) -> int:
    """Charge the current segment against its window (relay clock) and release the rest of the reservation.
    ``forgive`` (``"connection"`` or ``"consent"``) applies that zero-charge rule when the window still has attempts
    of that kind left. Returns the seconds charged."""
    measured = _measured(reservation, end)
    discount = min(_discount(reservation, window, limits), int(measured))
    charge = min(round_up(measured - discount), reservation.outstanding_seconds)
    if forgive == "connection" and window is not None and window.zero_charged_attempts < MAX_ZERO_CHARGED_ATTEMPTS:
        window.zero_charged_attempts += 1
        charge, discount = 0, 0
    elif forgive == "consent" and window is not None and window.consent_forgiven < MAX_CONSENT_FORGIVEN:
        window.consent_forgiven += 1
        charge, discount = 0, 0
    if window is not None:
        window.reserved_seconds = max(0, window.reserved_seconds - reservation.outstanding_seconds)
        window.used_seconds += charge
        window.pause_discount_seconds += discount  # the per-window cap: what this window has given away so far
    reservation.charged_seconds += charge
    reservation.outstanding_seconds = 0
    # the discount was spent on this segment; a pause still open continues into the next one from `end` (only its
    # part after `end` can ever be discounted, and only once the relay sees it resumed)
    reservation.pause_discount_seconds = 0
    if reservation.paused_at is not None and reservation.paused_at < end:
        reservation.paused_at = end
    return charge


async def _switch_plan(db: AsyncSession, reservation: ScreenReservation, now: datetime, limits: Limits) -> None:
    """The account's plan changed between unlimited and Free under a live session: close the current segment as a
    window rollover would (a Free segment against its window; an unlimited one charges no window), then continue
    under the new plan from ``now`` with nothing outstanding."""
    window = await _window_of(db, reservation)
    await _settle_segment(db, reservation, window, now, limits, forgive=None)
    reservation.segment_started_at = now
    reservation.unlimited = limits.unlimited
    reservation.window_start = None
    reservation.reserved_until = max(reservation.reserved_until, now)


async def renew(
    db: AsyncSession, reservation_id: uuid.UUID, now: datetime, limits: Limits
) -> tuple[ScreenReservation | None, Ticket | None]:
    """The next chunk for a live session (``screen_usage`` -> ``screen_allowance``). Returns the locked reservation and
    the ticket, or ``(reservation, None)`` when nothing is left (the caller sends ``final: true``), or ``(None, None)``
    when the reservation is gone or already settled."""
    r = ScreenReservation
    reservation = await _locked(db, select(r).where(r.id == reservation_id))
    if reservation is None or reservation.state not in ("reserved", "live"):
        return None, None
    await lock_account(db, reservation.account_id, now)
    if reservation.unlimited != limits.unlimited:
        await _switch_plan(db, reservation, now, limits)
    base = max(reservation.reserved_until, now)
    if limits.unlimited:
        reservation.reserved_until = base + timedelta(seconds=limits.chunk_seconds)
        reservation.outstanding_seconds += limits.chunk_seconds
        return reservation, Ticket(reservation.reserved_until, True, None, None)
    window = await _window_of(db, reservation)
    if window is not None and window.window_end <= now:
        # the window rolled over under a live session: the old window keeps the seconds measured until its end (capped
        # at what it reserved), the new one starts now and the session continues there
        await _settle_segment(db, reservation, window, window.window_end, limits, forgive=None)
        reservation.segment_started_at = now
        window = None
    if window is None:
        window = await _ensure_window(db, reservation.account_id, now, limits)
        reservation.window_start = window.window_start
    room = _room(window, limits)
    if room <= 0:
        return reservation, None
    grant = min(limits.chunk_seconds, room)
    reservation.reserved_until = base + timedelta(seconds=grant)
    reservation.outstanding_seconds += grant
    window.reserved_seconds += grant
    return reservation, Ticket(reservation.reserved_until, False, room - grant, window.window_end)


async def mark_started(db: AsyncSession, reservation_id: uuid.UUID, now: datetime, limits: Limits) -> bool:
    """The relay saw ``screen_session{started}``: remember it (and, when the operator charges from the first frame,
    start the clock here). Returns False when the reservation is not live any more."""
    r = ScreenReservation
    reservation = await _locked(db, select(r).where(r.id == reservation_id))
    if reservation is None or reservation.state not in ("reserved", "live"):
        return False
    if reservation.started_at is None:
        reservation.started_at = now
        if limits.charge_from == "started" and reservation.charged_seconds == 0:
            reservation.segment_started_at = now
    reservation.state = "live"
    return True


async def bind_session(db: AsyncSession, reservation_id: uuid.UUID, screen_session_id: str) -> bool:
    r = ScreenReservation
    done = await db.scalar(
        update(r)
        .where(r.id == reservation_id, r.state.in_(("reserved", "live")))
        .values(screen_session_id=screen_session_id, state="live")
        .returning(r.id)
        .execution_options(synchronize_session=False)
    )
    return done is not None


async def mark_paused(db: AsyncSession, reservation_id: uuid.UUID, now: datetime) -> None:
    """A secure-desktop pause begins, as the relay saw it, during a session that has started (a pause before the
    `started` frame can never be discounted)."""
    r = ScreenReservation
    reservation = await _locked(db, select(r).where(r.id == reservation_id))
    if (
        reservation is not None
        and reservation.state in ("reserved", "live")
        and reservation.started_at is not None
        and reservation.paused_at is None
    ):
        reservation.paused_at = now


async def mark_resumed(db: AsyncSession, reservation_id: uuid.UUID, now: datetime) -> None:
    """Only a pause the relay saw begin counts (rules.screen_metering (3)); the per-window cap is applied at
    settlement."""
    r = ScreenReservation
    reservation = await _locked(db, select(r).where(r.id == reservation_id))
    if reservation is None or reservation.paused_at is None:
        return
    reservation.pause_discount_seconds += max(0, int((now - reservation.paused_at).total_seconds()))
    reservation.paused_at = None


async def settle(
    db: AsyncSession,
    reservation_id: uuid.UUID,
    now: datetime,
    limits: Limits,
    *,
    reason: str,
    swept: bool = False,
) -> tuple[ScreenReservation | None, int]:
    """Close the reservation at ``now`` (the ``ended`` frame, a failed start result, or the sweeper) and charge the
    relay-measured seconds. Idempotent: a reservation already settled returns ``(row, 0)`` without a second charge."""
    r = ScreenReservation
    reservation = await _locked(db, select(r).where(r.id == reservation_id))
    if reservation is None:
        return None, 0
    if reservation.state not in ("reserved", "live"):
        return reservation, 0
    await lock_account(db, reservation.account_id, now)
    window = await _window_of(db, reservation)
    end = now
    if swept:
        # the agent never reported the end: the whole ticketed time is charged (the dead-man rule, §10)
        end = max(now, reservation.reserved_until)
    elapsed = (end - reservation.forwarded_at).total_seconds()
    forgive: str | None = None
    if reservation.started_at is None and not swept:
        if reason in FAILED_START_REASONS and elapsed <= limits.connect_timeout_seconds:
            forgive = "connection"
        elif reason == CONSENT_REFUSED and elapsed <= limits.consent_timeout_seconds + CONSENT_GRACE_SECONDS:
            forgive = "consent"
    if window is not None and window.window_end <= end and reservation.segment_started_at < window.window_end:
        # ended after its window rolled over: the old window takes its share first, the rest is never charged (no
        # new window was opened by a dead or ending session)
        charged = await _settle_segment(db, reservation, window, window.window_end, limits, forgive=forgive)
    else:
        charged = await _settle_segment(db, reservation, window, end, limits, forgive=forgive)
    reservation.state = "swept" if swept else "settled"
    reservation.end_reason = reason[:32]
    reservation.ended_at = now
    return reservation, charged


async def sweep(db: AsyncSession, now: datetime, limits_for: Any) -> list[ScreenReservation]:
    """Settle, in full, every reservation whose last ticket passed SWEEP_GRACE_SECONDS ago without an ``ended`` frame
    (an agent that vanished, a relay restart). ``limits_for(account_id)`` is awaited per row. Each row is locked in its
    own statement; the caller runs this in one transaction per sweep pass."""
    r = ScreenReservation
    cutoff = now - timedelta(seconds=SWEEP_GRACE_SECONDS)
    ids = (
        await db.scalars(
            select(r.id)
            .where(r.state.in_(("reserved", "live")), r.reserved_until < cutoff)
            .order_by(r.reserved_until)
            .limit(SWEEP_BATCH)
        )
    ).all()
    swept: list[ScreenReservation] = []
    for rid in ids:
        row = await db.scalar(select(r).where(r.id == rid))
        if row is None:
            continue
        limits = await limits_for(row.account_id)
        settled, charged = await settle(db, rid, now, limits, reason="swept", swept=True)
        if settled is not None and settled.state == "swept":
            swept.append(settled)
            log.info("screen.reservation_swept", account_id=str(settled.account_id), charged_seconds=charged)
    return swept


__all__ = [
    "CONSENT_REFUSED",
    "FAILED_START_REASONS",
    "MAX_CONSENT_FORGIVEN",
    "MAX_ZERO_CHARGED_ATTEMPTS",
    "ROUND_SECONDS",
    "SWEEP_GRACE_SECONDS",
    "AllowanceExhaustedError",
    "Limits",
    "Ticket",
    "allowance",
    "bind_session",
    "lock_account",
    "mark_paused",
    "mark_resumed",
    "mark_started",
    "open_window",
    "renew",
    "reserve_start",
    "round_up",
    "settle",
    "sweep",
]
