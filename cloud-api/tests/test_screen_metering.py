"""The Screen view ledger against real PostgreSQL (docs/design/screen-view.md §10; rules.screen_metering; threat T30):
chunks are reserved per rolling window and settled on the relay's own clock. Ten concurrent starts never exceed the
window; a lying agent that reports zero seconds and connection_failed on every end still exhausts it; connection_failed
is free only without a `started` frame, within 20 s and three times per window; pauses are discounted only as pairs the
relay saw and at most 120 s; the unused rest of the last chunk is refunded by the cap; the sweeper charges an unreported
reservation in full; the window rolls over under a live session; unlimited plans get chunks without arithmetic."""

from __future__ import annotations

import asyncio
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from dome_protocol.keys import b64url_encode
from sqlalchemy import select

from dome_api.db.engine import transaction
from dome_api.db.models import ScreenReservation
from dome_api.screen import metering
from dome_api.screen.metering import AllowanceExhaustedError, Limits, Ticket
from dome_api.util import ts
from tests.conftest import Env, new_screen_session_id, sql

FREE = Limits(
    seconds_per_window=600, window_hours=5, chunk_seconds=60, connect_timeout_seconds=20, pause_max_seconds=120
)
UNLIMITED = Limits(
    seconds_per_window=None, window_hours=5, chunk_seconds=60, connect_timeout_seconds=20, pause_max_seconds=120
)
_years = iter(range(2400, 2900))


def far_future() -> datetime:
    """Each test gets its own point in time, so its windows never touch another test's rows."""
    return datetime(next(_years), 3, 15, 12, 0, tzinfo=UTC)


def new_account(env: Env, plan: str = "free") -> uuid.UUID:
    account_id = uuid.uuid4()
    sql(
        env.database_url,
        "INSERT INTO accounts (id, issuer, subject, email, display_name, plan) VALUES (%s, %s, %s, '', '', %s)",
        (str(account_id), "https://screen-metering.test", secrets.token_hex(8), plan),
    )
    return account_id


def _kid() -> str:
    kid: str = b64url_encode(secrets.token_bytes(32))
    return kid


def new_pc(env: Env, account_id: uuid.UUID) -> uuid.UUID:
    pc_id = uuid.uuid4()
    sql(
        env.database_url,
        "INSERT INTO pcs (id, account_id, name, public_jwk, kid, enabled, remote_enabled_reported, platform, agent_version)"
        " VALUES (%s, %s, 'Desk', '{}', %s, true, true, 'development', '')",
        (str(pc_id), str(account_id), _kid()),
    )
    return pc_id


def new_controller(env: Env, account_id: uuid.UUID) -> uuid.UUID:
    cid = uuid.uuid4()
    sql(
        env.database_url,
        "INSERT INTO controllers (id, account_id, kid, public_jwk, display_name) VALUES (%s, %s, %s, '{}', 'Phone')",
        (str(cid), str(account_id), _kid()),
    )
    return cid


class Party:
    def __init__(self, env: Env, plan: str = "free") -> None:
        self.env = env
        self.account = new_account(env, plan)
        self.pc = new_pc(env, self.account)
        self.controller = new_controller(env, self.account)


def window_rows(env: Env, account_id: uuid.UUID) -> list[dict[str, Any]]:
    cols = "window_start, window_end, used_seconds, reserved_seconds, zero_charged_attempts, consent_forgiven, pause_discount_seconds"
    rows = sql(
        env.database_url,
        f"SELECT {cols} FROM screen_view_windows WHERE account_id = %s ORDER BY window_start",
        (str(account_id),),
    )
    names = [c.strip() for c in cols.split(",")]
    return [dict(zip(names, row, strict=True)) for row in rows]


def reservation_row(env: Env, reservation_id: uuid.UUID) -> dict[str, Any]:
    cols = (
        "state, outstanding_seconds, charged_seconds, reserved_until, segment_started_at, started_at, end_reason, "
        "window_start, pause_discount_seconds, paused_at, final_reason, screen_session_id"
    )
    [row] = sql(env.database_url, f"SELECT {cols} FROM screen_reservations WHERE id = %s", (str(reservation_id),))
    return dict(zip([c.strip() for c in cols.split(",")], row, strict=True))


async def start(env: Env, p: Party, now: datetime, limits: Limits = FREE) -> tuple[uuid.UUID, Ticket]:
    async with transaction(env.services.db) as db:
        reservation, ticket = await metering.reserve_start(
            db,
            account_id=p.account,
            pc_id=p.pc,
            controller_id=p.controller,
            command_id=uuid.uuid4(),
            now=now,
            limits=limits,
        )
        return reservation.id, ticket


async def renew(env: Env, rid: uuid.UUID, now: datetime, limits: Limits = FREE) -> Ticket | None:
    async with transaction(env.services.db) as db:
        _, ticket = await metering.renew(db, rid, now, limits)
        return ticket


async def settle(env: Env, rid: uuid.UUID, now: datetime, reason: str, limits: Limits = FREE) -> int:
    async with transaction(env.services.db) as db:
        _, charged = await metering.settle(db, rid, now, limits, reason=reason)
        return charged


async def started(env: Env, rid: uuid.UUID, now: datetime, limits: Limits = FREE) -> None:
    async with transaction(env.services.db) as db:
        assert await metering.mark_started(db, rid, now, limits)


async def view(env: Env, account_id: uuid.UUID, now: datetime, limits: Limits = FREE) -> Ticket:
    async with env.services.db() as db:
        return await metering.allowance(db, account_id, now, limits)


# ----- reserve, renew, settle ------------------------------------------------------------------------------------------


async def test_first_start_opens_the_window_and_takes_one_chunk_looking_does_not(env: Env) -> None:
    p, now = Party(env), far_future()
    assert window_rows(env, p.account) == []
    before = await view(env, p.account, now)
    assert before == Ticket(None, False, 600, None) and window_rows(env, p.account) == []
    rid, ticket = await start(env, p, now)
    assert ticket.granted_until == now + timedelta(seconds=60) and ticket.unlimited is False
    assert ticket.remaining_seconds == 540 and ticket.resets_at == now + timedelta(hours=5)
    assert ticket.allowance_state() == {
        "unlimited": False,
        "remaining_seconds": 540,
        "resets_at": ts(now + timedelta(hours=5)),
    }
    [window] = window_rows(env, p.account)
    assert (window["used_seconds"], window["reserved_seconds"]) == (0, 60)
    assert window["window_end"] == now + timedelta(hours=5)
    row = reservation_row(env, rid)
    assert row["state"] == "reserved" and row["outstanding_seconds"] == 60 and row["segment_started_at"] == now


async def test_renewals_extend_the_ticket_from_its_end_until_nothing_is_left(env: Env) -> None:
    p, now = Party(env), far_future()
    limits = Limits(150, 5, 60, 20, 120)
    rid, t0 = await start(env, p, now, limits)
    t1 = await renew(env, rid, now + timedelta(seconds=40), limits)
    assert t1 is not None and t1.granted_until == now + timedelta(seconds=120) and t1.remaining_seconds == 30
    t2 = await renew(env, rid, now + timedelta(seconds=100), limits)
    assert t2 is not None and t2.granted_until == now + timedelta(seconds=150) and t2.remaining_seconds == 0
    assert await renew(env, rid, now + timedelta(seconds=130), limits) is None  # final: true
    [window] = window_rows(env, p.account)
    assert window["reserved_seconds"] == 150 and window["used_seconds"] == 0
    # the whole 150 s were watched: charged in full, nothing refunded, nothing over-charged
    assert await settle(env, rid, now + timedelta(seconds=150), "allowance_exhausted", limits) == 150
    [window] = window_rows(env, p.account)
    assert (window["used_seconds"], window["reserved_seconds"]) == (150, 0)
    assert await view(env, p.account, now + timedelta(seconds=151), limits) == Ticket(
        None, False, 0, now + timedelta(hours=5)
    )


async def test_charge_is_the_relay_clock_rounded_up_and_capped_never_the_agents_figure(env: Env) -> None:
    p, now = Party(env), far_future()
    rid, _ = await start(env, p, now)
    # a 23 s view costs 30 s; the rest of the chunk is refunded by the cap
    assert await settle(env, rid, now + timedelta(seconds=23), "stopped") == 30
    [window] = window_rows(env, p.account)
    assert (window["used_seconds"], window["reserved_seconds"]) == (30, 0)
    row = reservation_row(env, rid)
    assert row["state"] == "settled" and row["charged_seconds"] == 30 and row["end_reason"] == "stopped"
    # settling again charges nothing (idempotent against the sweeper)
    assert await settle(env, rid, now + timedelta(seconds=200), "stopped") == 0
    # a session that ran past its last ticket without renewing is capped at what was reserved
    rid2, _ = await start(env, p, now)
    assert await settle(env, rid2, now + timedelta(seconds=95), "stopped") == 60
    assert window_rows(env, p.account)[0]["used_seconds"] == 90
    # 1 s costs 10 s: never 0 for a session that happened
    rid3, _ = await start(env, p, now)
    await started(env, rid3, now + timedelta(seconds=1))
    assert await settle(env, rid3, now + timedelta(seconds=1), "stopped") == 10


async def test_a_lying_agent_still_exhausts_its_window(env: Env) -> None:
    """Streams 59 s each time, then reports connection_failed with live_seconds 0: charged 60 s a time."""
    p, now = Party(env), far_future()
    for i in range(10):
        t = now + timedelta(seconds=120 * i)
        rid, ticket = await start(env, p, t)
        assert ticket.remaining_seconds == 600 - 60 * (i + 1)
        # no `started` frame ever: the relay cannot tell a lie from a failure, so only the first three quick ones
        # could have been forgiven, and 59 s is past the 20 s connect timeout anyway
        assert await settle(env, rid, t + timedelta(seconds=59), "connection_failed") == 60
    with pytest.raises(AllowanceExhaustedError) as ei:
        await start(env, p, now + timedelta(seconds=1300))
    assert ei.value.resets_at == now + timedelta(hours=5)
    [window] = window_rows(env, p.account)
    assert (window["used_seconds"], window["reserved_seconds"], window["zero_charged_attempts"]) == (600, 0, 0)


async def test_connection_failed_is_free_only_without_started_within_20s_and_three_times(env: Env) -> None:
    p, now = Party(env), far_future()
    for i in range(3):
        rid, _ = await start(env, p, now + timedelta(seconds=30 * i))
        assert await settle(env, rid, now + timedelta(seconds=30 * i + 15), "connection_failed") == 0
    [window] = window_rows(env, p.account)
    assert (window["used_seconds"], window["zero_charged_attempts"]) == (0, 3)
    # the fourth quick failure is charged its measured seconds
    rid, _ = await start(env, p, now + timedelta(seconds=100))
    assert await settle(env, rid, now + timedelta(seconds=115), "connection_failed") == 20
    # a failure after `started` is never free, however quick
    p2 = Party(env)
    rid, _ = await start(env, p2, now)
    await started(env, rid, now + timedelta(seconds=2))
    assert await settle(env, rid, now + timedelta(seconds=5), "connection_failed") == 10
    # a failure after the connect timeout is never free either
    rid, _ = await start(env, p2, now)
    assert await settle(env, rid, now + timedelta(seconds=21), "connection_failed") == 30
    # an ordinary end is never forgiven
    rid, _ = await start(env, p2, now)
    assert await settle(env, rid, now + timedelta(seconds=3), "stopped") == 10
    assert window_rows(env, p2.account)[0]["zero_charged_attempts"] == 0


async def test_pauses_are_discounted_only_as_pairs_the_relay_saw_and_at_most_120s(env: Env) -> None:
    """rules.screen_metering (3), design §7/§10: the PC's word alone never buys minutes. Only a `paused -> resumed`
    pair the relay saw during a started session is discounted, at most 120 s per WINDOW (a patched agent that pauses
    and ends every session would otherwise turn 600 s into hours); an unpaired pause is charged in full."""
    p, now = Party(env), far_future()
    rid, _ = await start(env, p, now)
    for _ in range(4):
        assert await renew(env, rid, now, FREE) is not None  # 300 s reserved
    async with transaction(env.services.db) as db:
        await metering.mark_paused(db, rid, now + timedelta(seconds=1))  # before `started`: never discounted
        await metering.mark_resumed(db, rid, now + timedelta(seconds=5))
    assert reservation_row(env, rid)["pause_discount_seconds"] == 0
    await started(env, rid, now + timedelta(seconds=6))
    async with transaction(env.services.db) as db:
        await metering.mark_paused(db, rid, now + timedelta(seconds=10))
        await metering.mark_resumed(db, rid, now + timedelta(seconds=50))  # 40 s pair
        await metering.mark_resumed(db, rid, now + timedelta(seconds=60))  # a resume without a pause: nothing
        await metering.mark_paused(db, rid, now + timedelta(seconds=100))
        await metering.mark_resumed(db, rid, now + timedelta(seconds=250))  # 150 s pair: the cap is 120 in total
    assert reservation_row(env, rid)["pause_discount_seconds"] == 190
    # 290 s measured, 120 s discounted at most: 170
    assert await settle(env, rid, now + timedelta(seconds=290), "stopped") == 170
    assert window_rows(env, p.account)[0]["pause_discount_seconds"] == 120
    # an unpaired pause (paused, then ended while paused, the honest UAC-unanswered case too): charged in full
    t = now + timedelta(seconds=300)
    rid2, _ = await start(env, p, t)
    await started(env, rid2, t)
    async with transaction(env.services.db) as db:
        await metering.mark_paused(db, rid2, t + timedelta(seconds=1))
    assert await settle(env, rid2, t + timedelta(seconds=40), "secure_desktop") == 40
    # the window's 120 s are spent: a second session's genuine pair in the same window buys nothing more
    t = now + timedelta(seconds=400)
    rid3, _ = await start(env, p, t)
    await started(env, rid3, t)
    async with transaction(env.services.db) as db:
        await metering.mark_paused(db, rid3, t + timedelta(seconds=10))
        await metering.mark_resumed(db, rid3, t + timedelta(seconds=50))
    assert await settle(env, rid3, t + timedelta(seconds=60), "stopped") == 60
    assert window_rows(env, p.account)[0]["pause_discount_seconds"] == 120


async def test_ten_concurrent_starts_never_exceed_the_window(env: Env) -> None:
    p, now = Party(env), far_future()
    limits = Limits(300, 5, 60, 20, 120)

    async def one() -> Any:
        try:
            return await start(env, p, now, limits)
        except AllowanceExhaustedError as exc:
            return exc

    results = await asyncio.gather(*(one() for _ in range(10)))
    won = [r for r in results if isinstance(r, tuple)]
    lost = [r for r in results if isinstance(r, AllowanceExhaustedError)]
    assert len(won) == 5 and len(lost) == 5
    assert {e.resets_at for e in lost} == {now + timedelta(hours=5)}
    [window] = window_rows(env, p.account)  # ten racing starts opened exactly one window
    assert window["reserved_seconds"] == 300 and window["used_seconds"] == 0
    rows = sql(env.database_url, "SELECT count(*) FROM screen_reservations WHERE account_id = %s", (str(p.account),))
    assert rows[0][0] == 5  # the refused ones left no row


async def test_concurrent_renewals_of_many_sessions_never_exceed_the_window(env: Env) -> None:
    p, now = Party(env), far_future()
    limits = Limits(600, 5, 60, 20, 120)
    rids = [(await start(env, p, now, limits))[0] for _ in range(5)]  # 300 reserved
    results = await asyncio.gather(*(renew(env, rid, now + timedelta(seconds=40), limits) for rid in rids * 2))
    granted = sum(1 for t in results if t is not None)
    assert granted == 5  # 300 left = five chunks, whichever renewals won
    [window] = window_rows(env, p.account)
    assert window["reserved_seconds"] == 600
    assert all(
        t is None for t in await asyncio.gather(*(renew(env, rid, now + timedelta(seconds=90), limits) for rid in rids))
    )


async def test_sweeper_charges_an_unreported_reservation_in_full(env: Env) -> None:
    p, now = Party(env), far_future()
    rid, _ = await start(env, p, now)
    live, _ = await start(env, p, now + timedelta(seconds=30))  # its ticket has not run out yet
    async with transaction(env.services.db) as db:
        swept = await metering.sweep(db, now + timedelta(seconds=75), lambda _account: _limits(FREE))
    assert [r.id for r in swept if r.account_id == p.account] == [rid]  # the sweep is global; other tests' rows too
    row = reservation_row(env, rid)
    assert row["state"] == "swept" and row["charged_seconds"] == 60 and row["end_reason"] == "swept"
    assert reservation_row(env, live)["state"] == "reserved"
    [window] = window_rows(env, p.account)
    assert (window["used_seconds"], window["reserved_seconds"]) == (60, 60)
    # the agent's late `ended` after the sweep charges nothing more
    assert await settle(env, rid, now + timedelta(seconds=200), "stopped") == 0
    assert window_rows(env, p.account)[0]["used_seconds"] == 60


async def _limits(limits: Limits) -> Limits:
    return limits


async def test_window_rollover_under_a_live_session_settles_the_old_window_first(env: Env) -> None:
    p = Party(env)
    now = far_future()
    short = Limits(600, 1, 60, 20, 120)  # a one-hour window
    rid, _ = await start(env, p, now, short)
    # renew right up to the end of the window: the ticket reaches past window_end
    t = now + timedelta(hours=1) - timedelta(seconds=30)
    assert await renew(env, rid, t, short) is not None
    # the next renewal happens after the window ended: the old window is charged up to its end (3600 s measured,
    # capped at the 120 s reserved) and a new window opens now with the next chunk
    later = now + timedelta(hours=1, seconds=20)
    ticket = await renew(env, rid, later, short)
    assert ticket is not None and ticket.remaining_seconds == 540 and ticket.resets_at == later + timedelta(hours=1)
    old, new = window_rows(env, p.account)
    assert (old["used_seconds"], old["reserved_seconds"]) == (120, 0)
    assert (new["used_seconds"], new["reserved_seconds"]) == (0, 60) and new["window_start"] == later
    row = reservation_row(env, rid)
    assert row["window_start"] == later and row["segment_started_at"] == later and row["outstanding_seconds"] == 60
    # the end charges the new window for the new segment only
    assert await settle(env, rid, later + timedelta(seconds=25), "stopped", short) == 30
    assert window_rows(env, p.account)[1]["used_seconds"] == 30


async def test_unlimited_plans_get_chunks_without_a_window(env: Env) -> None:
    p, now = Party(env, "pro"), far_future()
    assert await view(env, p.account, now, UNLIMITED) == Ticket(None, True, None, None)
    rid, ticket = await start(env, p, now, UNLIMITED)
    assert ticket.unlimited and ticket.granted_until == now + timedelta(seconds=60)
    assert ticket.allowance_state() == {"unlimited": True, "remaining_seconds": None, "resets_at": None}
    t = await renew(env, rid, now + timedelta(seconds=40), UNLIMITED)
    assert t is not None and t.unlimited and t.granted_until == now + timedelta(seconds=120)
    assert await settle(env, rid, now + timedelta(seconds=100), "stopped", UNLIMITED) == 100
    assert window_rows(env, p.account) == []
    assert reservation_row(env, rid)["charged_seconds"] == 100


async def test_charge_from_started_moves_the_clock_to_the_first_frame(env: Env) -> None:
    p, now = Party(env), far_future()
    limits = Limits(600, 5, 60, 20, 120, charge_from="started")
    rid, _ = await start(env, p, now, limits)
    await started(env, rid, now + timedelta(seconds=4), limits)
    assert reservation_row(env, rid)["segment_started_at"] == now + timedelta(seconds=4)
    assert await settle(env, rid, now + timedelta(seconds=35), "stopped", limits) == 40  # 31 s, rounded up
    # the default keeps the forward as the start: setup time counts
    rid2, _ = await start(env, p, now)
    await started(env, rid2, now + timedelta(seconds=4))
    assert await settle(env, rid2, now + timedelta(seconds=35), "stopped") == 40  # 35 s rounded up is also 40
    rid3, _ = await start(env, p, now)
    await started(env, rid3, now + timedelta(seconds=9))
    assert await settle(env, rid3, now + timedelta(seconds=31), "stopped") == 40  # 31 s from the forward


async def test_bind_session_and_ids(env: Env) -> None:
    p, now = Party(env), far_future()
    rid, _ = await start(env, p, now)
    sid = new_screen_session_id()
    async with transaction(env.services.db) as db:
        assert await metering.bind_session(db, rid, sid)
    assert reservation_row(env, rid)["screen_session_id"] == sid and reservation_row(env, rid)["state"] == "live"
    assert await settle(env, rid, now + timedelta(seconds=5), "stopped") == 10
    async with transaction(env.services.db) as db:
        assert not await metering.bind_session(db, rid, new_screen_session_id())  # settled rows are final
        assert not await metering.mark_started(db, rid, now, FREE)
        assert (await metering.renew(db, rid, now, FREE)) == (None, None)
        assert (await metering.renew(db, uuid.uuid4(), now, FREE)) == (None, None)
        assert (await metering.settle(db, uuid.uuid4(), now, FREE, reason="stopped")) == (None, 0)


def test_round_up() -> None:
    assert metering.round_up(0) == 0 and metering.round_up(-5) == 0
    assert metering.round_up(0.1) == 10 and metering.round_up(10) == 10 and metering.round_up(10.1) == 20
    assert metering.round_up(59.9) == 60 and metering.round_up(60) == 60


# ----- review round (2026-10-22): stale rows under FOR UPDATE, a plan change mid-session, the consent wait ------------


async def test_a_settle_committed_elsewhere_after_an_unlocked_read_is_never_charged_twice(env: Env) -> None:
    """The callers load the reservation without a lock, then call a metering function that locks it in the same
    session. Without a fresh read the FOR UPDATE select returns the identity-mapped object as first seen (`live`), and
    a late `ended` racing the sweeper settles the reservation twice."""
    p, now = Party(env), far_future()
    rid, _ = await start(env, p, now)
    for _ in range(2):
        assert await renew(env, rid, now, FREE) is not None  # 180 s reserved
    r = ScreenReservation
    async with transaction(env.services.db) as db:
        seen = await db.scalar(select(r).where(r.id == rid))  # the unlocked read (e.g. _reservation_for)
        assert seen is not None and seen.state == "reserved"
        # meanwhile another session (the sweeper) settles it and commits
        assert await settle(env, rid, now + timedelta(seconds=40), "swept") == 40
        _row, charged = await metering.settle(db, rid, now + timedelta(seconds=45), FREE, reason="stopped")
        assert charged == 0 and seen.state == "settled"  # the locked read saw the committed state
    [window] = window_rows(env, p.account)
    assert (window["used_seconds"], window["reserved_seconds"]) == (40, 0)  # one charge, nothing released twice
    # a renewal racing a settle likewise leaves the window alone
    rid2, _ = await start(env, p, now)
    async with transaction(env.services.db) as db:
        seen = await db.scalar(select(r).where(r.id == rid2))
        assert seen is not None
        assert await settle(env, rid2, now + timedelta(seconds=20), "stopped") == 20
        assert await metering.renew(db, rid2, now + timedelta(seconds=30), FREE) == (None, None)
    [window] = window_rows(env, p.account)
    assert (window["used_seconds"], window["reserved_seconds"]) == (60, 0)


async def test_a_plan_change_under_a_live_session_closes_the_segment_first(env: Env) -> None:
    """Pro -> Free under a live session must not charge the Pro time to the new Free window, and Free -> Pro must not
    book Pro time into the Free window or release other sessions' reservations."""
    p, now = Party(env, "pro"), far_future()
    rid, _ = await start(env, p, now, UNLIMITED)
    for i in range(1, 60):  # an hour on Pro, renewed every minute
        assert await renew(env, rid, now + timedelta(seconds=60 * i - 20), UNLIMITED) is not None
    changed = now + timedelta(minutes=60)
    ticket = await renew(env, rid, changed, FREE)  # the account is Free now
    assert ticket is not None and not ticket.unlimited and ticket.remaining_seconds == 540
    [window] = window_rows(env, p.account)
    assert (window["used_seconds"], window["reserved_seconds"], window["window_start"]) == (0, 60, changed)
    row = reservation_row(env, rid)
    assert row["segment_started_at"] == changed and row["outstanding_seconds"] == 60
    assert await settle(env, rid, changed + timedelta(seconds=25), "stopped") == 30  # only the Free part counts
    assert window_rows(env, p.account)[0]["used_seconds"] == 30
    # Free -> Pro: the Free segment is settled against its window, then nothing more is booked there
    q = Party(env)
    other, _ = await start(env, q, now)  # another live session of the same account holds 60 s
    rid2, _ = await start(env, q, now)
    assert await renew(env, rid2, now + timedelta(seconds=40), FREE) is not None  # 120 s for this one
    upgraded = now + timedelta(seconds=50)
    t = await renew(env, rid2, upgraded, UNLIMITED)
    assert t is not None and t.unlimited
    [window] = window_rows(env, q.account)
    assert (window["used_seconds"], window["reserved_seconds"]) == (50, 60)  # 50 s charged; `other` keeps its 60
    assert await settle(env, rid2, upgraded + timedelta(seconds=45), "stopped", UNLIMITED) == 50  # no window
    [window] = window_rows(env, q.account)
    assert (window["used_seconds"], window["reserved_seconds"]) == (50, 60)
    assert reservation_row(env, other)["state"] == "reserved"


async def test_a_start_refused_at_the_pc_is_forgiven_on_its_own_counter(env: Env) -> None:
    """A Deny or no answer within the consent timeout (30 s) + 5 s costs nothing, three times per window, and does not
    use up the three forgiven connection failures; a later or fourth refusal is charged its measured seconds."""
    p, now = Party(env), far_future()
    for i in range(3):
        rid, _ = await start(env, p, now + timedelta(seconds=40 * i))
        assert await settle(env, rid, now + timedelta(seconds=40 * i + 31), metering.CONSENT_REFUSED) == 0
    rid, _ = await start(env, p, now + timedelta(seconds=200))
    assert await settle(env, rid, now + timedelta(seconds=210), metering.CONSENT_REFUSED) == 10  # the fourth
    [window] = window_rows(env, p.account)
    assert (window["consent_forgiven"], window["zero_charged_attempts"], window["used_seconds"]) == (3, 0, 10)
    rid, _ = await start(env, p, now + timedelta(seconds=300))
    assert await settle(env, rid, now + timedelta(seconds=310), "connection_failed") == 0  # still forgiven
    q = Party(env)
    rid, _ = await start(env, q, now)
    assert await settle(env, rid, now + timedelta(seconds=36), metering.CONSENT_REFUSED) == 40  # past 30 + 5 s
    rid, _ = await start(env, q, now)
    await started(env, rid, now + timedelta(seconds=5))  # a picture was shown: never forgiven
    assert await settle(env, rid, now + timedelta(seconds=9), metering.CONSENT_REFUSED) == 10
