"""TURN credentials for the phone (docs/design/screen-view.md §13; rules.screen_signaling (1), screen_metering (4);
threat T30) against real PostgreSQL with the fake provider, and the Cloudflare adapter against an httpx mock transport:
one outstanding credential per controller (a new mint revokes the previous), TTL from the allowance, revocation on
every §13 path with a security event each, the daily and monthly caps, the provisional bound and the monthly guard,
the circuit breaker, reconciliation parsing, and that no token or credential appears in a log line or an error."""

from __future__ import annotations

import dataclasses
import json
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
import pytest

from dome_api.db.engine import transaction
from dome_api.screen import metering
from dome_api.screen.turn import (
    GB,
    RELAYED_CAP_BYTES_PER_SECOND,
    CloudflareTurn,
    FakeTurn,
    MintOutcome,
    TurnConfig,
    TurnProviderError,
    TurnService,
    UsageRow,
    parse_usage_response,
)
from dome_api.settings import Settings
from tests.conftest import Env, new_screen_session_id, sql
from tests.test_screen_metering import FREE, Party, far_future

CONFIG = TurnConfig(
    configured=True,
    reconciliation_configured=True,
    soft_cap_gb=700,
    hard_cap_gb=900,
    free_daily_gb=2,
    mints_per_account_month=600,
    ttl_unlimited_seconds=14400,
    start_lifetime_seconds=60,
)


def service(provider: FakeTurn | None = None, **over: Any) -> TurnService:
    return TurnService(dataclasses.replace(CONFIG, **over), provider or FakeTurn())


def credential_row(env: Env, controller_id: uuid.UUID) -> dict[str, Any] | None:
    cols = "username, state, ttl_seconds, command_id, screen_session_id, revoke_reason, minted_at, expires_at"
    rows = sql(env.database_url, f"SELECT {cols} FROM turn_credentials WHERE controller_id = %s", (str(controller_id),))
    return dict(zip([c.strip() for c in cols.split(",")], rows[0], strict=True)) if rows else None


def revocation_events(env: Env, account_id: uuid.UUID) -> list[str]:
    rows = sql(
        env.database_url,
        "SELECT detail->>'reason' FROM security_events WHERE account_id = %s AND kind = 'turn_credential_revoked' "
        "ORDER BY id",
        (str(account_id),),
    )
    return [r[0] for r in rows]


def cap_events(env: Env, account_id: uuid.UUID) -> list[str]:
    rows = sql(
        env.database_url,
        "SELECT detail->>'cap' FROM security_events WHERE account_id = %s AND kind = 'turn_cap_reached' ORDER BY id",
        (str(account_id),),
    )
    return [r[0] for r in rows]


async def mint(
    env: Env, svc: TurnService, p: Party, now: datetime, *, free: bool = True, remaining: int | None = 600
) -> MintOutcome:
    async with transaction(env.services.db) as db:
        return await svc.mint(
            db, account_id=p.account, controller_id=p.controller, free_plan=free, remaining_seconds=remaining, now=now
        )


# ----- minting and the one-per-controller rule -------------------------------------------------------------------------


async def test_mint_binds_one_credential_per_controller_with_ttl_from_the_allowance(env: Env) -> None:
    p, now, svc = Party(env), far_future(), service()
    fake = svc.provider
    assert isinstance(fake, FakeTurn)
    out = await mint(env, svc, p, now, remaining=540)
    assert out.turn == "available" and out.reason == "ok" and out.expires_at == now + timedelta(seconds=600)
    assert out.ice_server is not None and out.ice_server["urls"] == fake.urls
    assert out.ice_server["credential"].startswith("FAKE-TURN-CREDENTIAL-")
    assert fake.minted == [{"ttl": 600, "custom_identifier": str(p.account), "username": out.ice_server["username"]}]
    row = credential_row(env, p.controller)
    assert row is not None and row["state"] == "outstanding" and row["ttl_seconds"] == 600
    assert row["username"] == out.ice_server["username"]
    # the credential itself is never stored anywhere
    for table in ("turn_credentials", "security_events", "screen_accounts"):
        dump = sql(env.database_url, f"SELECT {table}::text FROM {table}")
        assert not any("FAKE-TURN-CREDENTIAL" in str(r) for r in dump), table
    # a second mint for the same controller revokes the first at the provider, with the reason on record
    out2 = await mint(env, svc, p, now + timedelta(seconds=5), remaining=100)
    assert out2.ice_server is not None and fake.revoked == [out.ice_server["username"]]
    row = credential_row(env, p.controller)
    assert row is not None and row["username"] == out2.ice_server["username"] and row["ttl_seconds"] == 160
    assert revocation_events(env, p.account) == ["replaced"]
    # TTL rules: Free is remaining + 60 capped at 3600; unlimited plans get the session maximum
    assert svc.ttl_for(free_plan=True, remaining_seconds=5000) == 3600
    assert svc.ttl_for(free_plan=True, remaining_seconds=0) == 60
    assert svc.ttl_for(free_plan=False, remaining_seconds=None) == 14400
    assert svc.ttl_for(free_plan=False, remaining_seconds=10) == 14400
    # the mint counter
    [(count,)] = sql(
        env.database_url, "SELECT turn_mints_this_month FROM screen_accounts WHERE account_id = %s", (str(p.account),)
    )
    assert count == 2


async def test_unconfigured_provider_failure_and_breaker_are_honest_unavailable(env: Env) -> None:
    p, now = Party(env), far_future()
    off = service(configured=False)
    assert await mint(env, off, p, now) == MintOutcome("unavailable", None, None, "unconfigured")
    assert off.status(now) == "unconfigured"
    failing = service(FakeTurn(fail_mint="timeout"))
    for _ in range(2):
        assert (await mint(env, failing, p, now)).reason == "provider_error"
        assert failing.status(now) == "ok"  # two failures: still trying
    assert (await mint(env, failing, p, now)).reason == "provider_error"
    assert failing.breaker_open and failing.status(now) == "error"
    assert (await mint(env, failing, p, now)).reason == "breaker_open"
    fake = failing.provider
    assert isinstance(fake, FakeTurn) and len(fake.minted) == 0
    # the breaker closes after the cooldown and a success resets the count
    fake.fail_mint = None
    later = now + timedelta(seconds=301)
    assert (await mint(env, failing, p, later)).turn == "available" and failing.failures == 0
    assert credential_row(env, p.controller) is not None


# ----- revocation paths --------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "no_start",
        "start_refused",
        "session_ended",
        "swept",
        "controller_revoked",
        "grant_narrowed",
        "logout",
        "plan_change",
    ],
)
async def test_every_revocation_path_revokes_at_the_provider_and_writes_the_event(env: Env, path: str) -> None:
    p, now, svc = Party(env), far_future(), service()
    fake = svc.provider
    assert isinstance(fake, FakeTurn)
    out = await mint(env, svc, p, now)
    assert out.ice_server is not None
    username = out.ice_server["username"]
    sid = new_screen_session_id()
    async with transaction(env.services.db) as db:
        if path == "no_start":
            # the sweep is global (one relay process), so other tests' stale rows may go with it: check this row
            await svc.sweep(db, now + timedelta(seconds=59))
            assert username not in fake.revoked  # not yet
            await svc.sweep(db, now + timedelta(seconds=61))
        elif path in ("session_ended", "swept"):
            await svc.bind_start(db, p.controller, uuid.uuid4())
            await svc.bind_session(db, p.controller, sid)
            await svc.sweep(db, now + timedelta(seconds=120))
            assert username not in fake.revoked  # bound: the no-start rule does not apply
            assert await svc.revoke_session(db, sid, path, now)
        elif path == "plan_change":
            assert await svc.revoke_account(db, p.account, path, now) == 1
        else:
            assert await svc.revoke_controller(db, p.controller, path, now)
    assert username in fake.revoked
    row = credential_row(env, p.controller)
    assert row is not None and row["state"] == "revoked" and row["revoke_reason"] == path
    assert revocation_events(env, p.account) == [path]
    # revoking again is a no-op
    before = list(fake.revoked)
    async with transaction(env.services.db) as db:
        assert not await svc.revoke_controller(db, p.controller, path, now)
        assert not await svc.revoke_session(db, sid, path, now)
    assert fake.revoked == before


async def test_bound_credential_outlives_the_start_lifetime_and_expiry_is_marked(env: Env) -> None:
    p, now, svc = Party(env), far_future(), service()
    out = await mint(env, svc, p, now, remaining=10)  # TTL 70 s
    assert out.ice_server is not None
    async with transaction(env.services.db) as db:
        await svc.bind_start(db, p.controller, uuid.uuid4())
        await svc.sweep(db, now + timedelta(seconds=65))
    row = credential_row(env, p.controller)
    assert row is not None and row["state"] == "bound"  # past the start lifetime, but bound to a start
    async with transaction(env.services.db) as db:
        await svc.sweep(db, now + timedelta(seconds=71))
    row = credential_row(env, p.controller)
    assert row is not None and row["state"] == "expired"
    fake = svc.provider
    assert isinstance(fake, FakeTurn) and out.ice_server["username"] not in fake.revoked  # expiry needs no call


async def test_a_failed_revocation_is_kept_and_retried_until_the_provider_confirms(env: Env) -> None:
    """Design §13: "revoked on every path". A provider error must not be recorded as a revocation, and the next mint
    for the same controller (which replaces its row) must not erase the username still to be revoked."""
    fake = FakeTurn(fail_revoke=True)
    p, now, svc = Party(env), far_future(), service(fake)
    first = await mint(env, svc, p, now)
    assert first.ice_server is not None
    async with transaction(env.services.db) as db:
        assert await svc.revoke_controller(db, p.controller, "logout", now)
    row = credential_row(env, p.controller)
    assert row is not None and row["state"] == "revoke_pending"
    assert revocation_events(env, p.account) == []  # nothing claims a revocation the provider did not confirm
    failed = sql(
        env.database_url,
        "SELECT detail->>'reason', detail->>'error' FROM security_events WHERE account_id = %s "
        "AND kind = 'turn_revoke_failed'",
        (str(p.account),),
    )
    assert failed == [("logout", "provider_error")]
    pending = sql(env.database_url, "SELECT username, attempts, reason FROM turn_pending_revocations")
    assert (first.ice_server["username"], 1, "logout") in pending
    # the phone mints again: its row is replaced, the pending username is not
    second = await mint(env, svc, p, now + timedelta(seconds=5))
    assert second.ice_server is not None and credential_row(env, p.controller)["state"] == "outstanding"  # type: ignore[index]
    async with transaction(env.services.db) as db:
        await svc.retry_revocations(db, now + timedelta(seconds=20))  # within 30 s of the last try: not yet
        await svc.retry_revocations(db, now + timedelta(seconds=31))  # still failing: retried and kept
    attempts = sql(
        env.database_url,
        "SELECT attempts FROM turn_pending_revocations WHERE username = %s",
        (first.ice_server["username"],),
    )
    assert attempts == [(2,)]
    fake.fail_revoke = False
    async with transaction(env.services.db) as db:
        await svc.sweep(db, now + timedelta(seconds=62))  # the sweeper retries; the provider confirms
    assert first.ice_server["username"] in fake.revoked
    assert (
        sql(
            env.database_url,
            "SELECT 1 FROM turn_pending_revocations WHERE username = %s",
            (first.ice_server["username"],),
        )
        == []
    )
    retried = sql(
        env.database_url,
        "SELECT detail->>'reason', detail->>'retried' FROM security_events WHERE account_id = %s "
        "AND kind = 'turn_credential_revoked' ORDER BY id",
        (str(p.account),),
    )
    assert ("logout", "true") in retried
    # an unconfirmed revocation whose credential has expired needs no call any more and is dropped
    fake.fail_revoke = True
    async with transaction(env.services.db) as db:
        assert await svc.revoke_controller(db, p.controller, "logout", now + timedelta(seconds=70))
    async with transaction(env.services.db) as db:
        result = await svc.retry_revocations(db, now + timedelta(hours=2))
    assert result["expired"] >= 1
    assert (
        sql(
            env.database_url,
            "SELECT 1 FROM turn_pending_revocations WHERE username = %s",
            (second.ice_server["username"],),
        )
        == []
    )


async def test_revoking_for_one_start_never_touches_the_phones_newer_credential(env: Env) -> None:
    """Design §13 / the review's lost-start scenario: start #1's credential is bound to its command; the phone re-mints
    for start #2, which goes live relayed. Start #1's late failure or its swept reservation must revoke only #1's."""
    p, now, svc = Party(env), far_future(), service()
    fake = svc.provider
    assert isinstance(fake, FakeTurn)
    c1 = await mint(env, svc, p, now)
    cmd1, cmd2 = uuid.uuid4(), uuid.uuid4()
    async with transaction(env.services.db) as db:
        await svc.bind_start(db, p.controller, cmd1)
    c2 = await mint(env, svc, p, now + timedelta(seconds=30))  # replaces (and revokes) #1
    assert c1.ice_server is not None and c2.ice_server is not None
    sid2 = new_screen_session_id()
    async with transaction(env.services.db) as db:
        await svc.bind_start(db, p.controller, cmd2)
        await svc.bind_session(db, p.controller, sid2)
        # start #1's failure arrives late, or its reservation is swept: only start #1's credential may go
        assert not await svc.revoke_for_start(db, p.controller, cmd1, now + timedelta(seconds=1), "start_refused", now)
        assert not await svc.revoke_for_start(db, p.controller, cmd1, now + timedelta(seconds=1), "swept", now)
    row = credential_row(env, p.controller)
    assert row is not None and row["state"] == "bound" and row["screen_session_id"] == sid2
    assert c2.ice_server["username"] not in fake.revoked
    # its own start's failure does revoke it
    async with transaction(env.services.db) as db:
        assert await svc.revoke_for_start(db, p.controller, cmd2, now + timedelta(seconds=31), "start_refused", now)
    assert c2.ice_server["username"] in fake.revoked
    # a credential minted before a start that was never bound (a lost forward) is that start's too
    c3 = await mint(env, svc, p, now + timedelta(seconds=60))
    assert c3.ice_server is not None
    async with transaction(env.services.db) as db:
        assert await svc.revoke_for_start(
            db, p.controller, uuid.uuid4(), now + timedelta(seconds=61), "start_refused", now
        )
    assert c3.ice_server["username"] in fake.revoked


async def test_session_less_credentials_stay_in_the_guard_after_the_phone_mints_again(env: Env) -> None:
    """The provisional bound counts at the relayed cap the TTL of every credential that is neither bound to a session
    nor confirmed revoked; the per-controller row would forget all but the latest. A confirmed revocation releases the
    unused rest; an unconfirmed one keeps the whole TTL."""
    fake = FakeTurn()
    p, now, svc = Party(env), far_future(), service(fake)
    since = now - timedelta(minutes=1)

    async def ttl_term() -> int:
        async with env.services.db() as db:
            return await svc.provisional_bytes(db, since, p.account) // RELAYED_CAP_BYTES_PER_SECOND

    await mint(env, svc, p, now, remaining=600)  # TTL 660
    assert await ttl_term() == 660
    await mint(env, svc, p, now + timedelta(seconds=5), remaining=600)  # #1 revoked after 5 s: 655 s released
    assert await ttl_term() == 5 + 660
    fake.fail_revoke = True
    await mint(env, svc, p, now + timedelta(seconds=10), remaining=600)  # #2's revocation fails: its TTL stays
    assert await ttl_term() == 5 + 660 + 660
    async with transaction(env.services.db) as db:
        await svc.bind_session(db, p.controller, new_screen_session_id())  # #3 is a session's: its TTL leaves
        await svc.bind_session(db, p.controller, new_screen_session_id())  # once only
    assert await ttl_term() == 5 + 660


# ----- caps and the guard --------------------------------------------------------------------------------------------------


async def test_monthly_mint_cap_refuses_with_disabled_and_revokes(env: Env) -> None:
    p, now, svc = Party(env), far_future(), service(mints_per_account_month=3)
    for _ in range(3):
        assert (await mint(env, svc, p, now)).turn == "available"
    out = await mint(env, svc, p, now)
    assert out == MintOutcome("disabled", None, None, "cap_monthly_mints")
    assert cap_events(env, p.account) == ["monthly_mints"]
    row = credential_row(env, p.controller)
    assert row is not None and row["state"] == "revoked" and row["revoke_reason"] == "cap_reached"
    # a new month starts the count again
    assert (await mint(env, svc, p, now + timedelta(days=40))).turn == "available"


async def test_free_daily_cap_bites_on_the_provisional_bound_before_any_provider_figure(env: Env) -> None:
    p, now, svc = Party(env), far_future(), service(free_daily_gb=1)
    # a ticketed session of 2 h counts as relayed at the cap: 1 GB
    async with transaction(env.services.db) as db:
        reservation, _ = await metering.reserve_start(
            db,
            account_id=p.account,
            pc_id=p.pc,
            controller_id=p.controller,
            command_id=uuid.uuid4(),
            now=now,
            limits=FREE,
        )
        reservation.charged_seconds = 7200
    async with env.services.db() as db:
        provisional = await svc.provisional_bytes(db, now - timedelta(hours=1), p.account)
    assert provisional == (7200 + 60) * RELAYED_CAP_BYTES_PER_SECOND and provisional >= 1 * GB  # + the first chunk
    out = await mint(env, svc, p, now)
    assert out == MintOutcome("disabled", None, None, "cap_daily") and cap_events(env, p.account) == ["daily"]
    # paid plans are not bound by the Free daily cap
    pro = Party(env, "pro")
    sql(
        env.database_url,
        "UPDATE screen_reservations SET account_id = %s WHERE id = %s",
        (str(pro.account), str(reservation.id)),
    )
    sql(
        env.database_url,
        "UPDATE screen_reservations SET pc_id = %s, controller_id = %s WHERE id = %s",
        (str(pro.pc), str(pro.controller), str(reservation.id)),
    )
    assert (await mint(env, svc, pro, now, free=False, remaining=None)).turn == "available"


async def test_reconciled_daily_figure_also_bites(env: Env) -> None:
    p, now, svc = Party(env), far_future(), service(free_daily_gb=2)
    sql(
        env.database_url,
        "INSERT INTO turn_usage_days (account_id, day, egress_bytes, reconciled_at) VALUES (%s, %s, %s, %s)",
        (str(p.account), now.date(), 2 * GB, now),
    )
    assert (await mint(env, svc, p, now)).reason == "cap_daily"
    # yesterday's bytes do not count today
    sql(
        env.database_url,
        "UPDATE turn_usage_days SET day = %s WHERE account_id = %s",
        (now.date() - timedelta(days=1), str(p.account)),
    )
    assert (await mint(env, svc, p, now)).turn == "available"


async def test_monthly_guard_data_saver_at_soft_cap_and_stun_only_for_free_at_hard_cap(env: Env) -> None:
    # tiny caps so a short reservation trips them; the guard is global, so a far-future month keeps other tests out
    p, now, svc = Party(env), far_future(), service(soft_cap_gb=1, hard_cap_gb=2, free_daily_gb=1000)
    async with env.services.db() as db:
        assert not await svc.data_saver(db, now, free_plan=True)
    async with transaction(env.services.db) as db:
        reservation, _ = await metering.reserve_start(
            db,
            account_id=p.account,
            pc_id=p.pc,
            controller_id=p.controller,
            command_id=uuid.uuid4(),
            now=now,
            limits=FREE,
        )
        reservation.charged_seconds = 7300  # just over 1 GB provisional
    async with env.services.db() as db:
        assert await svc.data_saver(db, now, free_plan=True)
        assert not await svc.data_saver(db, now, free_plan=False)  # Pro is unaffected
        summary = await svc.month_summary(db, now)
    assert summary.provisional_bytes >= 1 * GB and summary.reconciled_bytes == 0 and summary.reconciled_at is None
    assert (await mint(env, svc, p, now)).turn == "available"  # below the hard cap: TURN still minted
    sql(
        env.database_url, "UPDATE screen_reservations SET charged_seconds = 14500 WHERE id = %s", (str(reservation.id),)
    )
    out = await mint(env, svc, p, now)
    assert out == MintOutcome("disabled", None, None, "guard_hard") and "hard" in cap_events(env, p.account)
    pro = Party(env, "pro")
    assert (await mint(env, svc, pro, now, free=False, remaining=None)).turn == "available"


# ----- reconciliation -----------------------------------------------------------------------------------------------------


async def test_reconcile_writes_per_account_days_from_the_providers_figures_only(env: Env) -> None:
    p, other, now = Party(env), Party(env), far_future()
    fake = FakeTurn()
    day = now.date()
    fake.usage_rows = [
        UsageRow(str(p.account), "u1", day, 10 * GB),
        UsageRow(str(p.account), "u2", day, 5 * GB),
        UsageRow(str(p.account), "u1", day - timedelta(days=1), 1 * GB),
        UsageRow(str(other.account), "u3", day, 2 * GB),
        UsageRow(str(uuid.uuid4()), "u9", day, 99 * GB),  # an account this relay does not know: skipped
        UsageRow("not-a-uuid", "u9", day, 99 * GB),
    ]
    svc = service(fake)
    async with transaction(env.services.db) as db:
        assert await svc.reconcile(db, now) == 3
    rows = sql(
        env.database_url,
        "SELECT day, egress_bytes FROM turn_usage_days WHERE account_id = %s ORDER BY day",
        (str(p.account),),
    )
    assert rows == [(day - timedelta(days=1), 1 * GB), (day, 15 * GB)]
    assert svc.last_reconciled_at == now
    async with env.services.db() as db:
        summary = await svc.month_summary(db, now)
        assert summary.reconciled_bytes >= 18 * GB and summary.reconciled_at == now
        assert await svc.reconciled_bytes(db, now, p.account) == 15 * GB
    # a second pass replaces (never adds to) the day's figure
    fake.usage_rows = [UsageRow(str(p.account), "u1", day, 12 * GB)]
    async with transaction(env.services.db) as db:
        assert await svc.reconcile(db, now + timedelta(hours=1)) == 1
    assert sql(
        env.database_url,
        "SELECT egress_bytes FROM turn_usage_days WHERE account_id = %s AND day = %s",
        (str(p.account), day),
    ) == [(12 * GB,)]
    # a provider failure keeps the last figures and records the reason
    fake.fail_usage = True
    async with transaction(env.services.db) as db:
        assert await svc.reconcile(db, now + timedelta(hours=2)) == 0
    assert svc.last_reconcile_error == "provider_error" and svc.last_reconciled_at == now + timedelta(hours=1)
    # without an analytics token nothing is asked
    quiet = service(FakeTurn(usage_rows=fake.usage_rows), reconciliation_configured=False)
    async with transaction(env.services.db) as db:
        assert await quiet.reconcile(db, now) == 0


async def test_a_finished_days_total_survives_the_next_hourly_passes(env: Env) -> None:
    """The analytics API filters by minute and groups by date: a window that started mid-day would overwrite a finished
    day's total with its last hours. Every pass now reads whole UTC days (yesterday and today), so day D keeps its
    full figure through every pass that still reads it."""
    p, now = Party(env), far_future()
    midnight = datetime(now.year, now.month, now.day, tzinfo=UTC)
    fake = FakeTurn()
    for hour in range(48):  # 1 GB relayed in every hour of day D and day D+1
        fake.minute_usage.append((midnight + timedelta(hours=hour, minutes=30), str(p.account), "u1", 1 * GB))
    svc = service(fake)
    for hour in range(26):  # the hourly passes from D+1 00:05 to D+2 01:05
        at = midnight + timedelta(days=1, hours=hour, minutes=5)
        async with transaction(env.services.db) as db:
            await svc.reconcile(db, at)
        stored = sql(
            env.database_url,
            "SELECT egress_bytes FROM turn_usage_days WHERE account_id = %s AND day = %s",
            (str(p.account), midnight.date()),
        )
        assert stored == [(24 * GB,)], (hour, stored)
    assert all(since.hour == 0 and since.minute == 0 for since, _until in fake.usage_calls)  # whole days only
    day_after = sql(
        env.database_url,
        "SELECT egress_bytes FROM turn_usage_days WHERE account_id = %s AND day = %s",
        (str(p.account), midnight.date() + timedelta(days=1)),
    )
    assert day_after == [(24 * GB,)]


async def test_the_guard_trusts_fresh_reconciled_figures_so_direct_sessions_do_not_trip_it(env: Env) -> None:
    """COST_MODEL's "High" month: many streamed hours, few of them relayed. Counting every ticketed second of the month
    as relayed would put Free on Data saver (then STUN only) while real TURN use is far below the caps. While the
    latest reconciliation is fresh the guard is the provider's figure plus the provisional bound only after it; stale
    or unconfigured, the whole month's provisional bound."""
    p, now = Party(env), far_future()
    fake = FakeTurn()
    svc = service(fake, soft_cap_gb=1, hard_cap_gb=2, free_daily_gb=1000)
    earlier = now - timedelta(days=5)
    for i in range(3):  # three hours of direct sessions earlier in the month: 1.5 GB provisional
        async with transaction(env.services.db) as db:
            reservation, _ = await metering.reserve_start(
                db,
                account_id=p.account,
                pc_id=p.pc,
                controller_id=p.controller,
                command_id=uuid.uuid4(),
                now=earlier + timedelta(hours=i),
                limits=FREE,
            )
            reservation.charged_seconds = 3600 - 60
    async with env.services.db() as db:
        assert await svc.data_saver(db, now, free_plan=True)  # no pass yet: the provisional bound rules
        before = await svc.month_summary(db, now)
    assert before.basis == "provisional" and before.guard_bytes >= 1 * GB
    fake.usage_rows = [UsageRow(str(p.account), "u1", earlier.date(), 200_000_000)]  # what was really relayed
    async with transaction(env.services.db) as db:
        await svc.reconcile(db, now)
    async with env.services.db() as db:
        summary = await svc.month_summary(db, now)
        assert not await svc.data_saver(db, now, free_plan=True)
        stale = await svc.month_summary(db, now + timedelta(hours=4))
    assert summary.basis == "reconciled" and summary.reconciled_at == now
    assert summary.guard_bytes == 200_000_000 and summary.provisional_bytes >= 1 * GB
    assert stale.basis == "provisional" and stale.guard_bytes == summary.provisional_bytes
    # activity after the pass is still counted provisionally
    async with transaction(env.services.db) as db:
        await metering.reserve_start(
            db,
            account_id=p.account,
            pc_id=p.pc,
            controller_id=p.controller,
            command_id=uuid.uuid4(),
            now=now + timedelta(minutes=10),
            limits=FREE,
        )
    async with env.services.db() as db:
        later = await svc.month_summary(db, now + timedelta(minutes=11))
    assert later.guard_bytes == 200_000_000 + 60 * RELAYED_CAP_BYTES_PER_SECOND
    quiet = service(fake, reconciliation_configured=False)
    async with env.services.db() as db:
        assert (await quiet.month_summary(db, now)).basis == "provisional"


def test_parse_usage_response_shapes() -> None:
    body = {
        "data": {
            "viewer": {
                "accounts": [
                    {
                        "callsTurnUsageAdaptiveGroups": [
                            {
                                "dimensions": {"customIdentifier": "a", "username": "u", "date": "2026-10-21"},
                                "sum": {"egressBytes": 123},
                            },
                            {
                                "dimensions": {"customIdentifier": "a", "username": "u", "date": "nonsense"},
                                "sum": {"egressBytes": 1},
                            },
                            {"dimensions": {"customIdentifier": 5}, "sum": {"egressBytes": 1}},
                            {
                                "dimensions": {
                                    "customIdentifier": "b",
                                    "username": "v",
                                    "date": "2026-10-21T00:00:00Z",
                                },
                                "sum": {"egressBytes": -4},
                            },
                            "garbage",
                        ]
                    }
                ]
            }
        }
    }
    assert parse_usage_response(body) == [
        UsageRow("a", "u", date(2026, 10, 21), 123),
        UsageRow("b", "v", date(2026, 10, 21), 0),
    ]
    with pytest.raises(TurnProviderError):
        parse_usage_response({"errors": [{"message": "x"}]})


# ----- the Cloudflare adapter against a mock transport ----------------------------------------------------------------------


KEY_ID = "0123456789abcdef0123456789abcdef"
API_TOKEN = "cf-turn-api-token-" + "FAKEFAKEFAKE0123456789"  # split so no scanner sees a key-shaped literal
ANALYTICS_TOKEN = "cf-analytics-token-" + "FAKEFAKEFAKE0123456789"


def cloudflare(handler: Any) -> CloudflareTurn:
    settings = Settings(
        env="test",
        public_origin="http://127.0.0.1:1",
        database_url="postgresql+psycopg://dome@/unused",
        session_secret="test-session-secret-0123456789",
        oidc_issuer="http://idp.invalid",
        oidc_client_id="c",
        oidc_client_secret="s",
        entitlement_signing_key_pem_path="/nonexistent.pem",  # type: ignore[arg-type]
        turn_key_id=KEY_ID,
        turn_api_token=API_TOKEN,
        turn_analytics_token=ANALYTICS_TOKEN,
    )
    return CloudflareTurn(settings, transport=httpx.MockTransport(handler))


async def test_cloudflare_mint_revoke_and_usage_request_shapes() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        path = request.url.path
        if path.endswith("/credentials/generate-ice-servers"):
            return httpx.Response(
                201,
                json={
                    "iceServers": {
                        "urls": [
                            "stun:stun.cloudflare.com:3478",
                            "turn:turn.cloudflare.com:3478?transport=udp",
                            "turns:turn.cloudflare.com:5349?transport=tcp",
                        ],
                        "username": "g0123456789",
                        "credential": "REAL-LOOKING-CREDENTIAL-NEVER-LOGGED",
                    }
                },
            )
        if path.endswith("/revoke"):
            return httpx.Response(204)
        if path == "/client/v4/accounts":
            return httpx.Response(200, json={"result": [{"id": "acc0tag"}]})
        if path == "/client/v4/graphql":
            body = json.loads(request.content)
            assert body["variables"]["accountTag"] == "acc0tag" and "callsTurnUsageAdaptiveGroups" in body["query"]
            return httpx.Response(
                200,
                json={
                    "data": {
                        "viewer": {
                            "accounts": [
                                {
                                    "callsTurnUsageAdaptiveGroups": [
                                        {
                                            "dimensions": {
                                                "customIdentifier": "11111111-1111-4111-8111-111111111111",
                                                "username": "g0",
                                                "date": "2026-10-21",
                                            },
                                            "sum": {"egressBytes": 42},
                                        }
                                    ]
                                }
                            ]
                        }
                    }
                },
            )
        return httpx.Response(404)

    provider = cloudflare(handler)
    now = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)
    minted = await provider.mint(ttl_seconds=660, custom_identifier="11111111-1111-4111-8111-111111111111", now=now)
    assert minted.username == "g0123456789" and minted.credential == "REAL-LOOKING-CREDENTIAL-NEVER-LOGGED"
    assert minted.urls == [
        "turn:turn.cloudflare.com:3478?transport=udp",
        "turns:turn.cloudflare.com:5349?transport=tcp",
    ]
    assert minted.expires_at == now + timedelta(seconds=660)
    req = seen[0]
    assert (
        req.method == "POST"
        and str(req.url) == f"https://rtc.live.cloudflare.com/v1/turn/keys/{KEY_ID}/credentials/generate-ice-servers"
    )
    assert req.headers["authorization"] == f"Bearer {API_TOKEN}"
    assert json.loads(req.content) == {"ttl": 660, "customIdentifier": "11111111-1111-4111-8111-111111111111"}
    await provider.revoke("g0123456789")
    assert seen[1].method == "POST" and seen[1].url.path.endswith("/credentials/g0123456789/revoke")
    assert seen[1].headers["authorization"] == f"Bearer {API_TOKEN}"
    rows = await provider.usage(since=now - timedelta(hours=1), until=now)
    assert rows == [UsageRow("11111111-1111-4111-8111-111111111111", "g0", date(2026, 10, 21), 42)]
    assert seen[2].url.path == "/client/v4/accounts" and seen[2].headers["authorization"] == f"Bearer {ANALYTICS_TOKEN}"
    assert seen[3].url.path == "/client/v4/graphql" and seen[3].headers["authorization"] == f"Bearer {ANALYTICS_TOKEN}"
    await provider.usage(since=now - timedelta(hours=1), until=now)
    assert len(seen) == 5  # the account tag is looked up once
    await provider.aclose()


@pytest.mark.parametrize(
    ("status", "body", "reason"),
    [
        (401, {"success": False}, "auth"),
        (403, {}, "auth"),
        (400, {"errors": [{"message": "bad ttl"}]}, "bad_request"),
        (500, {}, "provider_error"),
        (200, {"iceServers": {"urls": ["stun:only"], "username": "u", "credential": "c"}}, "provider_error"),
        (200, "not json", "provider_error"),
    ],
)
async def test_cloudflare_errors_carry_a_reason_never_a_body_or_token(status: int, body: Any, reason: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if isinstance(body, str):
            return httpx.Response(status, content=body.encode())
        return httpx.Response(status, json=body)

    provider = cloudflare(handler)
    with pytest.raises(TurnProviderError) as ei:
        await provider.mint(ttl_seconds=60, custom_identifier="x", now=datetime.now(UTC))
    assert ei.value.reason == reason
    text = str(ei.value) + repr(ei.value.__dict__)
    assert API_TOKEN not in text and "bad ttl" not in text
    await provider.aclose()


async def test_cloudflare_timeout_is_a_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    provider = cloudflare(handler)
    with pytest.raises(TurnProviderError) as ei:
        await provider.mint(ttl_seconds=60, custom_identifier="x", now=datetime.now(UTC))
    assert ei.value.reason == "timeout"
    await provider.aclose()


async def test_log_lines_never_carry_a_credential_or_token(env: Env, capfd: pytest.CaptureFixture[str]) -> None:
    p, now, svc = Party(env), far_future(), service()
    out = await mint(env, svc, p, now)
    assert out.ice_server is not None
    async with transaction(env.services.db) as db:
        await svc.revoke_controller(db, p.controller, "logout", now)
    captured = capfd.readouterr()
    assert "FAKE-TURN-CREDENTIAL" not in captured.out + captured.err
