"""The retention purge (DECISIONS #122; docs/DATA_RETENTION.md), against real PostgreSQL. Every rule deletes the rows
past its period and keeps the rows that are not (each case sits a day or more on either side of its boundary); the
batch limit holds per table per pass; an account deleted more than 30 days ago is hard-deleted with everything that
cascades from it while its subscription, top-up and billing-event rows stay with ``account_id`` NULL; an account whose
subscription cancel is still open, or whose provider subscription is not cancelled, or that was deleted less than 30
days ago, is kept; a deletion a crash left unfinished is finished first; one log line per pass carries counts only; the
kill switch stops the hourly loop.

Rows are written with SQL at timestamps relative to one ``now`` that is handed to the pass. The suite shares one
database and runs one test at a time, so a pass may also remove other tests' old rows: the assertions name this test's
rows only, except the batch test, whose rows are dated years before anything else."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import secrets
import uuid
from collections.abc import Iterator
from datetime import datetime, timedelta
from typing import Any

import pytest
from structlog.testing import capture_logs

from dome_api.purge import RULES, PurgeConfig, PurgeService
from dome_api.util import utcnow
from tests.conftest import Env, sql

DAY = timedelta(days=1)
JWK = json.dumps({"kty": "EC", "crv": "P-256", "x": "x", "y": "y"})


@pytest.fixture
def purge(env: Env) -> Iterator[PurgeService]:
    svc = env.services.purge
    saved = svc.config
    yield svc
    svc.config = saved


def kid() -> str:
    return secrets.token_urlsafe(32)[:43]


class Owner:
    """An account with one PC and ``phones`` controllers, written directly (no sign-in needed)."""

    def __init__(self, env: Env, *, phones: int = 1, deleted_at: datetime | None = None) -> None:
        self.url = env.database_url
        self.id = str(uuid.uuid4())
        sql(
            self.url,
            "INSERT INTO accounts (id, issuer, subject, email, display_name, deleted_at) VALUES (%s, %s, %s, '', '', %s)",
            (self.id, "https://issuer.example", "purge|" + self.id, deleted_at),
        )
        self.pc = str(uuid.uuid4())
        sql(
            self.url,
            "INSERT INTO pcs (id, account_id, name, public_jwk, kid, enabled, remote_enabled_reported, platform, "
            "agent_version) VALUES (%s, %s, 'PC', %s, %s, true, false, 'development', '')",
            (self.pc, self.id, JWK, kid()),
        )
        self.phones = [str(uuid.uuid4()) for _ in range(phones)]
        for phone in self.phones:
            sql(
                self.url,
                "INSERT INTO controllers (id, account_id, kid, public_jwk, display_name) VALUES (%s, %s, %s, %s, 'P')",
                (phone, self.id, kid(), JWK),
            )

    def insert(self, table: str, **values: Any) -> str:
        row_id = values.setdefault("id", str(uuid.uuid4()))
        cols = ", ".join(values)
        marks = ", ".join(["%s"] * len(values))
        sql(self.url, f"INSERT INTO {table} ({cols}) VALUES ({marks})", tuple(values.values()))
        return str(row_id)


def present(env: Env, table: str, ids: list[str], pk: str = "id") -> set[str]:
    rows = sql(env.database_url, f"SELECT {pk}::text FROM {table} WHERE {pk}::text = ANY(%s)", (ids,))
    return {r[0] for r in rows}


def seed_rules(env: Env, now: datetime) -> dict[str, tuple[str, list[str], list[str]]]:
    """Per table: (primary key column, ids that must go, ids that must stay)."""
    o = Owner(env, phones=2)
    a = o.id
    out: dict[str, tuple[str, list[str], list[str]]] = {}

    def session(expires: datetime, revoked: datetime | None = None) -> str:
        return o.insert(
            "sessions",
            account_id=a,
            token_hash=secrets.token_bytes(32),
            csrf_token="c",
            created_at=now - 200 * DAY,
            expires_at=expires,
            absolute_expires_at=expires,
            revoked_at=revoked,
        )

    out["sessions"] = (
        "id",
        [session(now - 31 * DAY), session(now + DAY, revoked=now - 31 * DAY)],
        [session(now - 29 * DAY), session(now + DAY, revoked=now - 29 * DAY), session(now + DAY)],
    )

    def flow(expires: datetime) -> str:
        return o.insert(
            "auth_flows",
            state=secrets.token_urlsafe(32),
            nonce="n",
            code_verifier="v",
            return_to="/",
            expires_at=expires,
        )

    out["auth_flows"] = ("id", [flow(now - 25 * timedelta(hours=1))], [flow(now - 23 * timedelta(hours=1))])

    def token(expires: datetime) -> str:
        return o.insert("pc_access_tokens", pc_id=o.pc, token_hash=secrets.token_bytes(32), expires_at=expires)

    out["pc_access_tokens"] = ("id", [token(now - 25 * timedelta(hours=1))], [token(now - 23 * timedelta(hours=1))])

    def link(expires: datetime) -> str:
        return o.insert(
            "device_link_codes",
            device_code_hash=secrets.token_bytes(32),
            user_code=secrets.token_hex(4).upper()[:4] + "-" + secrets.token_hex(2).upper(),
            pc_public_jwk=JWK,
            kid=kid(),
            agent_version="0.1",
            platform="development",
            state="expired",
            expires_at=expires,
        )

    out["device_link_codes"] = ("id", [link(now - 25 * timedelta(hours=1))], [link(now - 23 * timedelta(hours=1))])

    def pairing(expires: datetime, decided: datetime | None, state: str) -> str:
        return o.insert(
            "pairing_sessions",
            account_id=a,
            pc_id=o.pc,
            code_hash=secrets.token_bytes(32),
            state=state,
            attempts=0,
            expires_at=expires,
            decided_at=decided,
        )

    out["pairing_sessions"] = (
        "id",
        [pairing(now - 8 * DAY, None, "expired"), pairing(now - 8 * DAY, now - 8 * DAY, "approved")],
        [pairing(now - 6 * DAY, None, "expired"), pairing(now - 6 * DAY, now - 6 * DAY, "approved")],
    )

    def command(created: datetime) -> str:
        return o.insert(
            "commands",
            account_id=a,
            controller_id=o.phones[0],
            pc_id=o.pc,
            action="system.ping",
            digest="d" * 43,
            state="succeeded",
            created_at=created,
        )

    out["commands"] = ("id", [command(now - 31 * DAY)], [command(now - 29 * DAY)])

    def event(created: datetime, account: str | None) -> str:
        [(row_id,)] = sql(
            env.database_url,
            "INSERT INTO security_events (account_id, kind, severity, actor, detail, created_at) "
            "VALUES (%s, 'login', 'info', 'account', '{}'::jsonb, %s) RETURNING id::text",
            (account, created),
        )
        return str(row_id)

    out["security_events"] = (
        "id",
        [event(now - 366 * DAY, a), event(now - 91 * DAY, None)],
        [event(now - 364 * DAY, a), event(now - 89 * DAY, None)],
    )

    def activation(created: datetime) -> str:
        [(row_id,)] = sql(
            env.database_url,
            "INSERT INTO activation_events (account_id, kind, detail, created_at) VALUES (%s, 'x', '{}'::jsonb, %s) "
            "RETURNING id::text",
            (a, created),
        )
        return str(row_id)

    out["activation_events"] = ("id", [activation(now - 91 * DAY)], [activation(now - 89 * DAY)])

    def diagnostic(expires: datetime) -> str:
        return o.insert("support_diagnostics", account_id=a, redacted_bundle="{}", expires_at=expires)

    out["support_diagnostics"] = (
        "id",
        [diagnostic(now - timedelta(minutes=1)), diagnostic(now)],
        [diagnostic(now + DAY)],
    )

    def turn(phone: str, expires: datetime) -> str:
        """One row per phone (the primary key is the controller)."""
        sql(
            env.database_url,
            "INSERT INTO turn_credentials (controller_id, account_id, username, minted_at, expires_at, ttl_seconds, "
            "state) VALUES (%s, %s, %s, %s, %s, 660, 'expired')",
            (phone, a, "u-" + secrets.token_hex(8), expires - timedelta(minutes=11), expires),
        )
        return phone

    out["turn_credentials"] = (
        "controller_id",
        [turn(o.phones[0], now - 31 * DAY)],
        [turn(o.phones[1], now - 29 * DAY)],
    )

    def view_window(end: datetime) -> str:
        return o.insert("screen_view_windows", account_id=a, window_start=end - timedelta(hours=5), window_end=end)

    out["screen_view_windows"] = ("id", [view_window(now - 31 * DAY)], [view_window(now - 29 * DAY)])

    def reservation(created: datetime, state: str) -> str:
        return o.insert(
            "screen_reservations",
            account_id=a,
            pc_id=o.pc,
            controller_id=o.phones[0],
            command_id=str(uuid.uuid4()),
            unlimited=False,
            forwarded_at=created,
            segment_started_at=created,
            # an unsettled row's ticket still runs, or the Screen view sweeper would settle it (`swept`) meanwhile
            reserved_until=created + timedelta(minutes=1) if state in ("settled", "swept") else now + DAY,
            state=state,
            created_at=created,
        )

    out["screen_reservations"] = (
        "id",
        [reservation(now - 91 * DAY, "settled"), reservation(now - 91 * DAY, "swept")],
        [
            reservation(now - 89 * DAY, "settled"),
            reservation(now - 91 * DAY, "live"),
            reservation(now - 91 * DAY, "reserved"),
        ],
    )

    def turn_row(created: datetime, status: str) -> str:
        return o.insert(
            "assistant_turns",
            account_id=a,
            client_turn_id=str(uuid.uuid4()),
            status=status,
            period_start=created,
            month_start=created,
            reserved_micro_usd=1,
            created_at=created,
        )

    out["assistant_turns"] = (
        "id",
        [turn_row(now - 401 * DAY, "answered"), turn_row(now - 401 * DAY, "abandoned")],
        [turn_row(now - 399 * DAY, "answered"), turn_row(now - 401 * DAY, "pending")],
    )

    def pending(completed: datetime | None) -> str:
        holder = Owner(env, phones=0)
        return holder.insert(
            "pending_deletions",
            account_id=holder.id,
            requested_at=now - 60 * DAY,
            provider_cancel_state="canceled" if completed else "pending",
            attempts=1,
            completed_at=completed,
        )

    out["pending_deletions"] = ("id", [pending(now - 31 * DAY)], [pending(now - 29 * DAY), pending(None)])

    for table, column in (("layouts", "definition"), ("routines", "steps")):

        def soft(deleted: datetime | None, table: str = table, column: str = column) -> str:
            return o.insert(table, account_id=a, name="n", **{column: "{}"}, deleted_at=deleted)

        out[table] = ("id", [soft(now - 31 * DAY)], [soft(now - 29 * DAY), soft(None)])
    return out


async def test_each_rule_deletes_what_is_past_its_time_and_keeps_what_is_not(env: Env, purge: PurgeService) -> None:
    now = utcnow()
    cases = seed_rules(env, now)
    assert set(cases) == {rule.table for rule in RULES}  # every rule has its cases here
    for table, (pk, gone, kept) in cases.items():
        assert present(env, table, gone + kept, pk) == set(gone + kept), table
    counts = await purge.purge(now)
    for table, (pk, gone, kept) in cases.items():
        assert present(env, table, gone + kept, pk) == set(kept), table
        assert counts[table] >= len(gone), table


async def test_the_batch_limit_holds_per_table_per_pass(env: Env, purge: PurgeService) -> None:
    purge.config = dataclasses.replace(purge.config, batch_size=3)
    o = Owner(env)
    ancient = utcnow() - 3650 * DAY  # older than anything else in the shared database: deleted first
    ids = [
        o.insert(
            "commands",
            account_id=o.id,
            controller_id=o.phones[0],
            pc_id=o.pc,
            action="system.ping",
            digest="d" * 43,
            state="succeeded",
            created_at=ancient + timedelta(seconds=i),
        )
        for i in range(5)
    ]
    first = await purge.purge()
    assert first["commands"] == 3
    assert present(env, "commands", ids) == set(ids[3:])  # oldest first
    second = await purge.purge()
    assert second["commands"] >= 2 and present(env, "commands", ids) == set()
    assert all(n <= 3 for name, n in second.items() if name not in ("deletions_finished", "accounts_kept_billing_open"))


def finished(env: Env, owner: Owner) -> None:
    sql(
        env.database_url,
        "INSERT INTO security_events (account_id, kind, severity, actor, detail) "
        "VALUES (%s, 'account_deleted', 'notice', 'account', '{}'::jsonb)",
        (owner.id,),
    )


def financial(env: Env, owner: Owner, *, state: str = "canceled") -> tuple[str, str, str]:
    sub = owner.insert(
        "subscriptions",
        account_id=owner.id,
        provider="stripe",
        plan="pro",
        state=state,
        cancel_at_period_end=False,
        provider_subscription_id="sub_" + secrets.token_hex(8),
    )
    topup = owner.insert(
        "topup_purchases",
        account_id=owner.id,
        provider_checkout_session_id="cs_" + secrets.token_hex(8),
        requests=100,
        amount_cents=299,
        currency="USD",
        status="credited",
    )
    event_id = "evt_" + secrets.token_hex(8)
    sql(
        env.database_url,
        "INSERT INTO billing_events (provider, provider_event_id, event_type, account_id, status) "
        "VALUES ('stripe', %s, 'invoice.paid', %s, 'processed')",
        (event_id, owner.id),
    )
    return sub, topup, event_id


def accounts_left(env: Env, *owners: Owner) -> set[str]:
    return present(env, "accounts", [o.id for o in owners])


async def test_accounts_are_hard_deleted_after_30_days_keeping_the_financial_rows(
    env: Env, purge: PurgeService
) -> None:
    now = utcnow()
    gone = Owner(env, deleted_at=now - 31 * DAY)
    finished(env, gone)
    sub, topup, event_id = financial(env, gone)
    gone.insert(
        "sessions",
        account_id=gone.id,
        token_hash=secrets.token_bytes(32),
        csrf_token="c",
        expires_at=now,
        absolute_expires_at=now,
    )
    # kept: the cancel is still pending at the provider
    pending = Owner(env, deleted_at=now - 31 * DAY)
    finished(env, pending)
    pending.insert("pending_deletions", account_id=pending.id, provider_cancel_state="pending", attempts=3)
    # kept: a provider subscription that is not cancelled (never orphan a subscription that still charges)
    live = Owner(env, deleted_at=now - 31 * DAY)
    finished(env, live)
    financial(env, live, state="active")
    # kept: a Checkout page still pending (the billing reconciler may still apply it), for a plan or for a top-up
    checkout = Owner(env, deleted_at=now - 31 * DAY)
    finished(env, checkout)
    checkout.insert(
        "subscriptions",
        account_id=checkout.id,
        provider="stripe",
        plan="pro",
        state="checkout_pending",
        cancel_at_period_end=False,
    )
    paying = Owner(env, deleted_at=now - 31 * DAY)
    finished(env, paying)
    paying.insert(
        "topup_purchases",
        account_id=paying.id,
        provider_checkout_session_id="cs_" + secrets.token_hex(8),
        requests=100,
        amount_cents=299,
        currency="USD",
        status="pending",
    )
    # kept: deleted 29 days ago
    recent = Owner(env, deleted_at=now - 29 * DAY)
    finished(env, recent)
    # kept: never deleted
    alive = Owner(env)
    # a deletion a crash left unfinished (no account_deleted event): finished first, then hard-deleted in the same pass
    unfinished = Owner(env, deleted_at=now - 31 * DAY)
    with capture_logs() as logs:
        counts = await purge.purge(now)
    assert accounts_left(env, gone, pending, live, checkout, paying, recent, alive, unfinished) == {
        pending.id,
        live.id,
        checkout.id,
        paying.id,
        recent.id,
        alive.id,
    }
    assert counts["accounts"] >= 2 and counts["accounts_kept_billing_open"] >= 4 and counts["deletions_finished"] >= 1
    # everything that cascades went with the account
    assert present(env, "pcs", [gone.pc, unfinished.pc]) == set()
    assert present(env, "controllers", gone.phones + unfinished.phones) == set()
    assert sql(env.database_url, "SELECT count(*) FROM sessions WHERE account_id = %s", (gone.id,)) == [(0,)]
    assert sql(env.database_url, "SELECT count(*) FROM security_events WHERE account_id = %s", (gone.id,)) == [(0,)]
    # the financial rows stay, without the account
    assert sql(env.database_url, "SELECT account_id, state FROM subscriptions WHERE id = %s", (sub,)) == [
        (None, "canceled")
    ]
    assert sql(env.database_url, "SELECT account_id, status FROM topup_purchases WHERE id = %s", (topup,)) == [
        (None, "credited")
    ]
    assert sql(env.database_url, "SELECT account_id FROM billing_events WHERE provider_event_id = %s", (event_id,)) == [
        (None,)
    ]
    # one line for the pass, counts only
    [line] = [entry for entry in logs if entry.get("event") == "purge.run"]
    assert set(line) == {"event", "log_level"} | {rule.table for rule in RULES} | {
        "accounts",
        "accounts_kept_billing_open",
        "deletions_finished",
    }
    assert all(isinstance(v, int) for k, v in line.items() if k not in ("event", "log_level"))
    assert gone.id not in repr([e for e in logs if e.get("event") == "purge.run"])
    # once the cancel completes and 30 days have passed since, both rows go in a later pass
    sql(
        env.database_url,
        "UPDATE pending_deletions SET completed_at = %s, provider_cancel_state = 'canceled' WHERE account_id = %s",
        (now - 31 * DAY, pending.id),
    )
    await purge.purge(now)
    assert accounts_left(env, pending, live, recent, alive) == {live.id, recent.id, alive.id}


async def test_the_kill_switch_stops_the_hourly_loop(env: Env) -> None:
    assert PurgeConfig.from_settings(env.settings).enabled is True  # on unless DOME_PURGE_ENABLED=false
    off = env.settings.model_copy(update={"purge_enabled": False})
    assert PurgeConfig.from_settings(off).enabled is False
    assert PurgeConfig.from_settings(env.settings).interval_seconds == 3600.0
    for enabled, expect_runs in ((False, False), (True, True)):
        svc = PurgeService(
            config=PurgeConfig(enabled=enabled, interval_seconds=0.02),
            db=env.services.db,
            billing=env.services.billing,
            relay=env.services.relay,
        )
        runs: list[datetime | None] = []

        async def fake(now: datetime | None = None, runs: list[datetime | None] = runs) -> dict[str, int]:
            runs.append(now)
            return {}

        svc.purge = fake  # type: ignore[method-assign]
        await svc.start()
        await asyncio.sleep(0.2)
        await svc.stop()
        assert bool(runs) is expect_runs
