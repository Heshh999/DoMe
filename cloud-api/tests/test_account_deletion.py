"""Self-service account deletion, ``DELETE /v1/account`` (DECISIONS #121; docs/BILLING.md §11), through the real API,
relay sockets, ``tools/dev-idp`` and PostgreSQL, with the in-memory fake Stripe and fake TURN provider.

Refused without CSRF, from another Origin, without the exact phrase, or with a session that no re-authentication
created within the last 10 minutes (``REAUTH_REQUIRED``): a fresh plain sign-in is not one, because a provider that
remembers the person answers it without asking for the password; only ``GET /v1/auth/login?prompt=login`` (with
``max_age=0``) is, once the callback found the provider's ``auth_time`` within that attempt. A deletion cancels the subscription at the provider (or records the cancel as pending, retried by the
reconciler), revokes every session (their cookies get 401), sends ``revoked{account_deleted}`` to the account's phone
and PC sockets and closes them, soft-deletes the PCs, revokes the PC credentials and the phones' TURN credentials,
withdraws the assistant consent, deletes the support diagnostics, soft-deletes the layouts and routines, blanks the
email and the name at once, writes one ``account_deleted`` event without personal data and keeps every financial row.
A sign-in with the same identity afterwards lands on ``/?sign_in=account_deleted`` without a session. A deletion a
crash interrupted is finished by the purge job; one whose second step fails still closes the sockets at once. Open
Checkout pages are expired at deletion, and a page paid into a deleted account is never applied or credited (its
subscription is cancelled; the payment is the operator's to refund). The same PC links to another account afterwards. ``GET /v1/auth/login?prompt=login`` asks the provider for its login
page and ``max_age=0``; any other ``prompt`` is refused."""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from datetime import timedelta
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import pytest
from structlog.testing import capture_logs

from dome_api import account_deletion
from dome_api.auth.oidc import AUTH_TIME_LEEWAY, checked_auth_time
from dome_api.billing.service import BillingService
from dome_api.db.models import Account
from dome_api.routes.auth import ACCOUNT_DELETED_REDIRECT
from dome_api.screen.service import ScreenService
from dome_api.util import utcnow
from tests.conftest import (
    SCHEMAS,
    AgentSim,
    Browser,
    ControllerSim,
    Env,
    close_code,
    deliver_all,
    fake_stripe,
    fake_turn,
    sql,
)
from tests.test_billing_lifecycle import account_plan, subscribe, use_clock
from tests.test_screen_routing import credential, reservation_rows, screen_phone, start_session

PHRASE = {"confirm": "delete my account"}


async def delete_account(browser: Browser, body: Any = PHRASE, **kw: Any) -> httpx.Response:
    return await browser.request("DELETE", "/v1/account", body, **kw)


def reauthenticated_ago(env: Env, account_id: str, minutes: int) -> None:
    """Every re-authenticated session of the account had its password entry confirmed ``minutes`` ago."""
    sql(
        env.database_url,
        "UPDATE sessions SET authenticated_at = now() - make_interval(mins => %s) "
        "WHERE account_id = %s AND authenticated_at IS NOT NULL",
        (minutes, account_id),
    )


async def sign_in_again(browser: Browser, *, return_to: str = "/app/settings") -> str:
    """The phone's way past REAUTH_REQUIRED, in the same browser: ``GET /v1/auth/login?prompt=login`` through the
    provider and back. The new session cookie replaces the old one (whose session stays valid until it is revoked).
    Returns the authorize URL."""
    r = await browser.client.get("/v1/auth/login", params={"return_to": return_to, "prompt": "login"})
    assert r.status_code == 303, r.text
    authorize = str(r.headers["location"])
    async with httpx.AsyncClient(follow_redirects=False, timeout=10) as idp:
        r2 = await idp.get(authorize + "&" + urlencode({"dev_user": browser.email}))
    assert r2.status_code == 303, r2.text
    r3 = await browser.client.get(str(r2.headers["location"]))
    assert r3.status_code == 303 and r3.headers["location"] == return_to, r3.text
    session = await browser.get("/v1/session", schema="session_response")
    browser.csrf = session["csrf_token"]
    return authorize


def authenticated_at(env: Env, browser: Browser) -> Any:
    """``sessions.authenticated_at`` of the browser's current session (looked up by its CSRF token)."""
    [(value,)] = sql(env.database_url, "SELECT authenticated_at FROM sessions WHERE csrf_token = %s", (browser.csrf,))
    return value


def account_row(env: Env, account_id: str) -> dict[str, Any]:
    cols = ("issuer", "subject", "email", "display_name", "deleted_at", "assistant_consent_version")
    [row] = sql(env.database_url, f"SELECT {', '.join(cols)} FROM accounts WHERE id = %s", (account_id,))
    return dict(zip(cols, row, strict=True))


def deleted_events(env: Env, account_id: str) -> list[tuple[Any, ...]]:
    return sql(
        env.database_url,
        "SELECT actor, severity, subject_id, detail, ip_hash FROM security_events "
        "WHERE account_id = %s AND kind = 'account_deleted'",
        (account_id,),
    )


def count(env: Env, query: str, *params: Any) -> int:
    return int(sql(env.database_url, query, params)[0][0])


def assert_error(r: httpx.Response, status: int, code: str) -> None:
    assert r.status_code == status, r.text
    SCHEMAS.validate_rest("error_body", r.json())
    assert r.json()["error"]["code"] == code


def assert_untouched(env: Env, browser: Browser) -> None:
    row = account_row(env, browser.account_id)
    assert row["deleted_at"] is None and row["email"] == browser.email
    assert deleted_events(env, browser.account_id) == []


def seed_records(env: Env, account_id: str) -> None:
    """What the account owns besides devices: consent, a support diagnostic, a layout, a routine, a top-up."""
    url = env.database_url
    sql(
        url,
        "UPDATE accounts SET assistant_consent_version = '2026-10-11', assistant_consented_at = now() WHERE id = %s",
        (account_id,),
    )
    sql(
        url,
        "INSERT INTO support_diagnostics (id, account_id, redacted_bundle, expires_at) "
        "VALUES (%s, %s, '{}'::jsonb, now() + interval '14 days')",
        (str(uuid.uuid4()), account_id),
    )
    sql(
        url,
        "INSERT INTO layouts (id, account_id, name, definition) VALUES (%s, %s, 'Couch', '{}'::jsonb)",
        (str(uuid.uuid4()), account_id),
    )
    sql(
        url,
        "INSERT INTO routines (id, account_id, name, steps) VALUES (%s, %s, 'Movie night', '{}'::jsonb)",
        (str(uuid.uuid4()), account_id),
    )
    sql(
        url,
        "INSERT INTO topup_purchases (id, account_id, provider_checkout_session_id, requests, amount_cents, currency, "
        "status) VALUES (%s, %s, %s, 100, 299, 'USD', 'credited')",
        (str(uuid.uuid4()), account_id, "cs_test_" + uuid.uuid4().hex[:12]),
    )


def financial_rows(env: Env, account_id: str) -> tuple[int, int, int]:
    return (
        count(env, "SELECT count(*) FROM subscriptions WHERE account_id = %s", account_id),
        count(env, "SELECT count(*) FROM topup_purchases WHERE account_id = %s", account_id),
        count(env, "SELECT count(*) FROM billing_events WHERE account_id = %s", account_id),
    )


# ----- refusals ---------------------------------------------------------------------------------------------------------


async def test_refused_without_csrf_from_another_origin_with_a_stale_session_or_another_phrase(
    env: Env, alice: Browser
) -> None:
    assert_error(await delete_account(alice, csrf=False), 403, "FORBIDDEN")
    assert_error(await delete_account(alice, headers={"X-DoMe-CSRF": "x" * 43}), 403, "FORBIDDEN")
    assert_error(await delete_account(alice, origin=False), 403, "FORBIDDEN")
    assert_error(await delete_account(alice, headers={"Origin": "https://evil.example"}), 403, "FORBIDDEN")
    for wrong in ("Delete my account", "DELETE MY ACCOUNT", "delete my account ", "delete", ""):
        assert_error(await delete_account(alice, {"confirm": wrong}), 400, "MALFORMED_MESSAGE")
    assert_error(await delete_account(alice, {"confirm": "delete my account", "why": "x"}), 400, "MALFORMED_MESSAGE")
    assert_error(await delete_account(alice, None), 400, "MALFORMED_MESSAGE")  # no body at all
    # a password entry confirmed more than 10 minutes ago: sign in again first (the phrase is checked before)
    await sign_in_again(alice)
    reauthenticated_ago(env, alice.account_id, 11)
    r = await delete_account(alice)
    assert_error(r, 403, "REAUTH_REQUIRED")
    assert r.json()["error"]["retryable"] is False
    assert r.json()["error"]["message"] == "For your safety, sign in again, then try once more."
    assert_error(await delete_account(alice, {"confirm": "delete"}), 400, "MALFORMED_MESSAGE")
    # nothing changed, and the session still works
    assert_untouched(env, alice)
    assert (await alice.get("/v1/session", schema="session_response"))["account"]["email"] == alice.email
    # without a session at all
    async with httpx.AsyncClient(base_url=env.origin, timeout=10) as anon:
        r = await anon.request("DELETE", "/v1/account", json=PHRASE, headers={"Origin": env.origin})
    assert_error(r, 401, "UNAUTHENTICATED")
    assert_untouched(env, alice)


def test_the_recent_sign_in_window_is_ten_minutes() -> None:
    now, window = utcnow(), account_deletion.REAUTH_WINDOW
    assert window == timedelta(minutes=10)
    assert account_deletion.session_is_recent(now - window, now)
    assert not account_deletion.session_is_recent(now - window - timedelta(milliseconds=1), now)
    assert not account_deletion.session_is_recent(None, now)  # no re-authentication created the session


async def test_a_fresh_sign_in_without_prompt_login_cannot_delete(env: Env, alice: Browser) -> None:
    """A provider that remembers the person answers a plain sign-in at once, without asking for the password, so a
    session that is seconds old proves nothing: only a re-authentication the callback confirmed counts. The re-prompt
    marker's ``prompt=login`` (DECISIONS #119) is not one either."""
    [(age, confirmed)] = sql(
        env.database_url,
        "SELECT now() - created_at, authenticated_at FROM sessions WHERE account_id = %s",
        (alice.account_id,),
    )
    assert age < timedelta(minutes=1) and confirmed is None
    r = await delete_account(alice)
    assert_error(r, 403, "REAUTH_REQUIRED")
    assert_untouched(env, alice)
    # a second plain sign-in a moment later: still no
    again = Browser(env)
    await again.login(alice.email)
    assert authenticated_at(env, again) is None
    assert_error(await delete_account(again), 403, "REAUTH_REQUIRED")
    # a sign-in that carries the re-prompt marker asks for the login page, but is not a re-authentication
    marked = Browser(env)
    r = await marked.client.get("/v1/auth/login", params={"return_to": "/app"}, headers={"Cookie": "dome_reprompt=1"})
    query = parse_qs(urlsplit(str(r.headers["location"])).query)
    assert query["prompt"] == ["login"] and "max_age" not in query
    assert sql(env.database_url, "SELECT reauth FROM auth_flows WHERE state = %s", (query["state"][0],)) == [(False,)]
    async with httpx.AsyncClient(follow_redirects=False, timeout=10) as idp:
        r2 = await idp.get(str(r.headers["location"]) + "&" + urlencode({"dev_user": alice.email}))
    r3 = await marked.client.get(str(r2.headers["location"]))
    assert r3.status_code == 303 and r3.headers["location"] == "/app"
    marked.csrf = (await marked.get("/v1/session", schema="session_response"))["csrf_token"]
    assert authenticated_at(env, marked) is None
    assert_error(await delete_account(marked), 403, "REAUTH_REQUIRED")
    assert_untouched(env, alice)
    await again.aclose()
    await marked.aclose()


def test_auth_time_counts_only_when_it_falls_within_the_attempt() -> None:
    """``auth_time`` of a re-authentication: an integer no earlier than the attempt's start and not in the future,
    each within 60 seconds. An older one means the provider answered from the sign-in it remembered."""
    now = utcnow().replace(microsecond=0)
    started = now - timedelta(minutes=2)
    assert AUTH_TIME_LEEWAY == timedelta(seconds=60)
    assert checked_auth_time({"auth_time": int(now.timestamp())}, started, now) == now
    assert checked_auth_time({"auth_time": int(started.timestamp())}, started, now) == started
    earliest = started - AUTH_TIME_LEEWAY
    assert checked_auth_time({"auth_time": int(earliest.timestamp())}, started, now) == earliest
    latest = now + AUTH_TIME_LEEWAY
    assert checked_auth_time({"auth_time": int(latest.timestamp())}, started, now) == latest
    remembered = started - timedelta(days=3)
    for bad in (
        int(remembered.timestamp()),  # the provider's remembered sign-in
        int(earliest.timestamp()) - 1,
        int(latest.timestamp()) + 1,
        True,
        str(int(now.timestamp())),
        float(int(now.timestamp())),
        None,
        10**20,  # out of range
    ):
        assert checked_auth_time({"auth_time": bad}, started, now) is None, bad
    assert checked_auth_time({}, started, now) is None


# ----- a deletion -------------------------------------------------------------------------------------------------------


async def test_a_deletion_cancels_revokes_closes_blanks_and_keeps_the_financial_rows(
    env: Env, billing: BillingService, alice: Browser, online_agent: AgentSim, paired: ControllerSim
) -> None:
    sub_id = await subscribe(env, billing, alice)
    fake = fake_stripe(billing)
    account_id = alice.account_id
    seed_records(env, account_id)
    # a second signed-in browser of the same account
    other = Browser(env)
    await other.login(alice.email)
    cookies = [alice.session_cookie, other.session_cookie]
    credential_before = online_agent.credential
    financial_before = financial_rows(env, account_id)
    assert financial_before[0] >= 1 and financial_before[1] == 1 and financial_before[2] >= 1
    # signed in again with prompt=login nine minutes ago: still recent enough
    await sign_in_again(alice)
    cookies.append(alice.session_cookie)
    reauthenticated_ago(env, account_id, 9)
    with capture_logs() as logs:
        r = await delete_account(alice, expect=204)
    assert r.content == b"" and r.headers["cache-control"] == "no-store"
    [cleared] = r.headers.get_list("set-cookie")
    assert cleared.startswith('dome_session=""; expires=') and "Max-Age=0" in cleared
    # the phone and the PC were told and their sockets closed
    assert await paired.recv_type("revoked") == {"type": "revoked", "reason": "account_deleted"}
    assert await close_code(paired.ws) == 4003  # type: ignore[arg-type]
    assert (await online_agent.recv_type("revoked"))["reason"] == "account_deleted"
    assert await close_code(online_agent.ws) == 4003  # type: ignore[arg-type]
    # every session is revoked: neither cookie works any more
    for cookie in cookies:
        resp = await env.http.get("/v1/session", headers={"Cookie": f"dome_session={cookie}"})
        assert resp.status_code == 401
    assert count(env, "SELECT count(*) FROM sessions WHERE account_id = %s AND revoked_at IS NULL", account_id) == 0
    # the subscription is cancelled at the provider, at once (no refund is made)
    assert fake.subscriptions[sub_id].status == "canceled"
    assert sql(env.database_url, "SELECT state FROM subscriptions WHERE account_id = %s", (account_id,)) == [
        ("canceled",)
    ]
    assert "refund" not in " ".join(fake.calls)
    # devices: PCs soft-deleted and disabled, the PC credential refused, phones and grants revoked
    assert sql(
        env.database_url, "SELECT deleted_at IS NOT NULL, enabled FROM pcs WHERE account_id = %s", (account_id,)
    ) == [(True, False)]
    await online_agent.rest("POST", "/v1/agent/token", {"pc_credential": credential_before}, expect=401)
    assert count(env, "SELECT count(*) FROM controllers WHERE account_id = %s AND revoked_at IS NULL", account_id) == 0
    assert count(env, "SELECT count(*) FROM grants WHERE account_id = %s AND revoked_at IS NULL", account_id) == 0
    # the email and the name are blank at once; issuer and subject stay until the hard delete
    row = account_row(env, account_id)
    assert row["deleted_at"] is not None and row["email"] == "" and row["display_name"] == ""
    assert row["issuer"] == env.settings.oidc_issuer and row["subject"].startswith("dev|")
    assert row["assistant_consent_version"] is None
    # support diagnostics deleted, layouts and routines soft-deleted
    assert count(env, "SELECT count(*) FROM support_diagnostics WHERE account_id = %s", account_id) == 0
    for table in ("layouts", "routines"):
        assert count(env, f"SELECT count(*) FROM {table} WHERE account_id = %s AND deleted_at IS NULL", account_id) == 0
        assert count(env, f"SELECT count(*) FROM {table} WHERE account_id = %s", account_id) == 1
    # every financial row is still there
    assert financial_rows(env, account_id) == financial_before
    # exactly one account_deleted event: counts and the subscription outcome, nothing personal
    [(actor, severity, subject_id, detail, ip_hash)] = deleted_events(env, account_id)
    assert (actor, severity, subject_id, ip_hash) == ("account", "notice", None, None)
    assert detail["subscription"] == "canceled"
    # the first sign-in, the second browser and the sign-in again before deleting
    assert detail["sessions"] == 3 and detail["phones"] == 1 and detail["pcs"] == 1 and detail["pc_credentials"] == 1
    assert detail["assistant_consent"] == 1 and detail["diagnostics"] == 1
    assert detail["layouts"] == 1 and detail["routines"] == 1
    assert all(isinstance(v, int) for k, v in detail.items() if k != "subscription")
    name = alice.account["display_name"]
    everything = repr(logs) + repr(detail)
    assert alice.email not in everything and alice.email.split("@")[0] not in everything and name not in everything
    assert sub_id not in repr(logs)
    # the old cookie cannot delete again, or do anything else
    again = await env.http.request(
        "DELETE",
        "/v1/account",
        json=PHRASE,
        headers={"Cookie": f"dome_session={cookies[-1]}", "Origin": env.origin, "X-DoMe-CSRF": alice.csrf or ""},
    )
    assert_error(again, 401, "UNAUTHENTICATED")
    assert len(deleted_events(env, account_id)) == 1
    await other.aclose()


async def test_a_provider_failure_records_the_cancel_as_pending_and_the_reconciler_finishes_it(
    env: Env, billing: BillingService, alice: Browser
) -> None:
    sub_id = await subscribe(env, billing, alice)
    fake = fake_stripe(billing)
    fake.fail_next.append("cancel_subscription_now")
    await sign_in_again(alice)
    await delete_account(alice, expect=204)
    # deleted all the same, and the cancel is recorded as pending, never claimed
    assert account_row(env, alice.account_id)["deleted_at"] is not None
    assert fake.subscriptions[sub_id].status == "active"
    pending = sql(
        env.database_url,
        "SELECT attempts, last_error, completed_at FROM pending_deletions WHERE account_id = %s",
        (alice.account_id,),
    )
    assert pending == [(1, "provider_error", None)]
    [(_, _, _, detail, _)] = deleted_events(env, alice.account_id)
    assert detail["subscription"] == "pending"
    assert (await alice.get("/v1/session", expect=401))["error"]["code"] == "UNAUTHENTICATED"
    # the billing reconciler retries until the provider confirms
    counts = await billing.reconcile()
    assert counts["deletions_retried"] >= 1 and fake.subscriptions[sub_id].status == "canceled"
    assert sql(
        env.database_url,
        "SELECT provider_cancel_state, completed_at IS NOT NULL FROM pending_deletions WHERE account_id = %s",
        (alice.account_id,),
    ) == [("canceled", True)]


async def test_a_live_screen_view_ends_and_the_turn_credential_is_revoked(
    env: Env, screen: ScreenService, alice: Browser, online_agent: AgentSim
) -> None:
    ctrl = await screen_phone(env, alice, online_agent)
    _, sid, _ = await start_session(env, ctrl, online_agent)
    before = credential(env, ctrl.controller_id or "")
    assert before is not None and before["state"] == "bound"
    await sign_in_again(alice)
    await delete_account(alice, expect=204)
    after = credential(env, ctrl.controller_id or "")
    assert after is not None and after["state"] == "revoked" and after["revoke_reason"] == "account_deleted"
    assert len(fake_turn(screen).revoked) == 1
    # the PC is told the session's last ticket is final, then that the account is gone
    final = await online_agent.recv_type("screen_allowance", screen_session_id=sid)
    assert final["final"] is True
    assert (await online_agent.recv_type("revoked"))["reason"] == "account_deleted"
    [reservation] = reservation_rows(env, alice.account_id)
    assert reservation["final_reason"] == "controller_offline"
    assert (await ctrl.recv_type("revoked"))["reason"] == "account_deleted"
    await ctrl.close()


async def test_the_sockets_close_at_once_even_when_the_second_step_fails(
    env: Env, alice: Browser, online_agent: AgentSim, paired: ControllerSim, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Step 2 can fail with an error nobody planned for (here its database work). The deletion is recorded and the
    purge job finishes it later, but the open phone and PC sockets do not wait for that: the relay's per-command
    checks never read ``accounts.deleted_at``, so they are told and closed in the same request."""

    async def broken(*_: Any, **__: Any) -> Any:
        raise RuntimeError("step 2 failed")

    monkeypatch.setattr(account_deletion, "_revoke_everything", broken)
    await sign_in_again(alice)
    with capture_logs() as logs:
        await delete_account(alice, expect=204)
    assert await paired.recv_type("revoked") == {"type": "revoked", "reason": "account_deleted"}
    assert await close_code(paired.ws) == 4003  # type: ignore[arg-type]
    assert (await online_agent.recv_type("revoked"))["reason"] == "account_deleted"
    assert await close_code(online_agent.ws) == 4003  # type: ignore[arg-type]
    names = [str(e.get("event")) for e in logs]
    assert "account.deletion_incomplete" in names and "account.deletion_finished" not in names
    [closed] = [e for e in logs if e.get("event") == "account.deletion_sockets_closed"]
    assert (closed["phone_sockets"], closed["pc_sockets"]) == (1, 1)
    # step 2 did not happen: no revocation in the database and no event yet (the purge job finishes it)
    assert deleted_events(env, alice.account_id) == []
    assert count(env, "SELECT count(*) FROM pcs WHERE account_id = %s AND deleted_at IS NULL", alice.account_id) == 1
    # but the account is gone for every path, so nothing opens again
    assert (await alice.get("/v1/session", expect=401))["error"]["code"] == "UNAUTHENTICATED"
    await online_agent.rest("POST", "/v1/agent/token", {"pc_credential": online_agent.credential}, expect=401)


async def test_the_same_pc_links_to_another_account_after_the_deletion(
    env: Env, alice: Browser, bob: Browser, online_agent: AgentSim, paired: ControllerSim
) -> None:
    """The agent keeps its key when it is told ``revoked{account_deleted}`` and asks to be linked again. Nobody can
    unlink it at the deleted account, so linking it to another account must not wait for the hard delete: the old PC
    row and everything under it go, and the key links as a new PC. A key linked to a live account is still refused
    (``test_agent_link.py``)."""
    old_pc = online_agent.pc_id
    await sign_in_again(alice)
    await delete_account(alice, expect=204)
    assert (await online_agent.recv_type("revoked"))["reason"] == "account_deleted"
    with capture_logs() as logs:
        linked = await online_agent.link(bob, "Same PC")
    assert linked["account_id"] == bob.account_id and linked["pc_id"] != old_pc and linked["enabled"] is True
    assert [p["id"] for p in (await bob.get("/v1/pcs", schema="pcs_response"))["pcs"]] == [linked["pc_id"]]
    assert (await bob.get(f"/v1/pcs/{linked['pc_id']}/grants", schema="grants_response"))["grants"] == []
    for table in ("pcs WHERE id", "pc_credentials WHERE pc_id", "grants WHERE pc_id", "pc_access_tokens WHERE pc_id"):
        assert count(env, f"SELECT count(*) FROM {table} = %s", old_pc) == 0, table
    assert await online_agent.fetch_token()  # the new credential works
    [released] = [e for e in logs if e.get("event") == "agent_link.released_from_deleted_account"]
    assert released["released_pc_id"] == old_pc and alice.email not in repr(logs)
    # the deletion is finished all the same, and the purge has nothing left of that PC to remove
    assert len(deleted_events(env, alice.account_id)) == 1


# ----- Checkout pages ----------------------------------------------------------------------------------------------------


def checkout_rows(env: Env, account_id: str) -> list[tuple[Any, ...]]:
    return sql(
        env.database_url,
        "SELECT state, provider_subscription_id FROM subscriptions WHERE account_id = %s ORDER BY updated_at",
        (account_id,),
    )


def topup_status(env: Env, session_id: str) -> tuple[Any, ...]:
    [row] = sql(
        env.database_url,
        "SELECT status, credited_at, provider_payment_intent_id FROM topup_purchases "
        "WHERE provider_checkout_session_id = %s",
        (session_id,),
    )
    return tuple(row)


def needs_operator(logs: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return [e for e in logs if e.get("event") == "billing.needs_operator"]


async def test_a_deletion_closes_an_open_plan_page(env: Env, billing: BillingService, alice: Browser) -> None:
    """A plan page the person opened and left unpaid is expired at the provider and its row removed, so it can never
    be paid into a deleted account, and nothing keeps the hard delete waiting."""
    page = await alice.post(
        "/v1/billing/checkout", {"plan": "pro", "interval": "monthly"}, schema="billing_checkout_response"
    )
    fake = fake_stripe(billing)
    await sign_in_again(alice)
    await delete_account(alice, expect=204)
    assert fake.checkouts[page["session_id"]].status == "expired"
    assert checkout_rows(env, alice.account_id) == []
    [(_, _, _, detail, _)] = deleted_events(env, alice.account_id)
    assert detail["subscription"] == "none"


async def test_a_deletion_closes_an_open_topup_page(env: Env, billing: BillingService, alice: Browser) -> None:
    await subscribe(env, billing, alice)
    page = await alice.post("/v1/billing/topup", {}, schema="billing_checkout_response")
    fake = fake_stripe(billing)
    await sign_in_again(alice)
    await delete_account(alice, expect=204)
    assert fake.checkouts[page["session_id"]].status == "expired"
    assert topup_status(env, page["session_id"]) == ("expired", None, None)


async def test_a_plan_page_paid_before_it_could_be_closed_is_cancelled_not_applied(
    env: Env, billing: BillingService, alice: Browser
) -> None:
    """Paid on the hosted page while the deletion ran (its webhooks are of a deleted account and change nothing): the
    subscription it created is cancelled at once and the payment is left to the operator."""
    page = await alice.post(
        "/v1/billing/checkout", {"plan": "pro", "interval": "monthly"}, schema="billing_checkout_response"
    )
    fake = fake_stripe(billing)
    deliveries = fake.complete_checkout(page["session_id"])  # paid; the webhooks arrive only after the deletion
    sub_id = fake.checkouts[page["session_id"]].subscription_id
    assert sub_id is not None
    await sign_in_again(alice)
    with capture_logs() as logs:
        await delete_account(alice, expect=204)
    await deliver_all(env, deliveries)
    assert fake.subscriptions[sub_id].status == "canceled"
    assert checkout_rows(env, alice.account_id) == [("canceled", sub_id)]
    assert account_plan(env, alice.account_id) == "free"
    [alert] = needs_operator(logs)
    assert alert["kind"] == "refund" and alert["account_id"] == alice.account_id and alert["subscription"] == "canceled"
    assert alice.email not in repr(logs) and sub_id not in repr(logs)


async def test_a_plan_page_paid_after_the_deletion_is_never_applied_by_the_reconciler(
    env: Env, billing: BillingService, alice: Browser
) -> None:
    """The provider could not close the page at deletion, and it was paid afterwards. The webhooks are ignored, and
    the reconciler, which asks about every stale page, cancels the subscription instead of applying it."""
    clock = use_clock(billing)
    page = await alice.post(
        "/v1/billing/checkout", {"plan": "pro", "interval": "monthly"}, schema="billing_checkout_response"
    )
    fake = fake_stripe(billing)
    fake.fail_next.append("expire_checkout")
    await sign_in_again(alice)
    await delete_account(alice, expect=204)
    assert fake.checkouts[page["session_id"]].status == "open"
    assert checkout_rows(env, alice.account_id) == [("checkout_pending", None)]
    await deliver_all(env, fake.complete_checkout(page["session_id"]))  # paid after the deletion: ignored
    sub_id = fake.checkouts[page["session_id"]].subscription_id
    assert sub_id is not None and fake.subscriptions[sub_id].status == "active"
    assert account_plan(env, alice.account_id) == "free"
    clock.advance(hours=25)
    with capture_logs() as logs:
        counts = await billing.reconcile()
    assert counts["checkouts_paid_after_deletion"] == 1 and counts["checkouts_recovered"] == 0
    assert fake.subscriptions[sub_id].status == "canceled"
    assert checkout_rows(env, alice.account_id) == [("canceled", sub_id)]
    assert account_plan(env, alice.account_id) == "free"
    [alert] = needs_operator(logs)
    assert alert["kind"] == "refund" and alert["account_id"] == alice.account_id
    # settled: the next pass finds nothing to do
    again = await billing.reconcile()
    assert again["checkouts_paid_after_deletion"] == 0 and fake.subscriptions[sub_id].status == "canceled"


async def test_a_refused_cancel_of_a_page_paid_after_the_deletion_is_retried(
    env: Env, billing: BillingService, alice: Browser
) -> None:
    clock = use_clock(billing)
    page = await alice.post(
        "/v1/billing/checkout", {"plan": "pro", "interval": "monthly"}, schema="billing_checkout_response"
    )
    fake = fake_stripe(billing)
    fake.fail_next.append("expire_checkout")
    await sign_in_again(alice)
    await delete_account(alice, expect=204)
    fake.complete_checkout(page["session_id"])
    sub_id = fake.checkouts[page["session_id"]].subscription_id
    assert sub_id is not None
    clock.advance(hours=25)
    # refused twice: by the reconciler's settling of the page, and by the deletion retry later in the same pass
    fake.fail_next.extend(["cancel_subscription_now", "cancel_subscription_now"])
    await billing.reconcile()
    assert fake.subscriptions[sub_id].status == "active"
    pending = "SELECT provider_cancel_state, completed_at IS NULL FROM pending_deletions WHERE account_id = %s"
    assert sql(env.database_url, pending, (alice.account_id,)) == [("pending", True)]
    assert account_plan(env, alice.account_id) == "free"
    counts = await billing.reconcile()  # the deletion retry cancels it now
    assert counts["deletions_retried"] >= 1 and counts["checkouts_paid_after_deletion"] == 0
    assert fake.subscriptions[sub_id].status == "canceled"
    assert sql(env.database_url, pending, (alice.account_id,)) == [("canceled", False)]


async def test_a_topup_paid_after_the_deletion_is_never_credited(
    env: Env, billing: BillingService, alice: Browser
) -> None:
    clock = use_clock(billing)
    await subscribe(env, billing, alice)
    page = await alice.post("/v1/billing/topup", {}, schema="billing_checkout_response")
    fake = fake_stripe(billing)
    fake.fail_next.append("expire_checkout")
    await sign_in_again(alice)
    await delete_account(alice, expect=204)
    await deliver_all(env, fake.topup_paid(page["session_id"]))  # ignored: a deleted account
    clock.advance(hours=25)
    with capture_logs() as logs:
        counts = await billing.reconcile()
    assert counts["checkouts_paid_after_deletion"] == 1 and counts["checkouts_recovered"] == 0
    status, credited_at, payment = topup_status(env, page["session_id"])
    assert (status, credited_at) == ("failed", None)
    assert payment == fake.checkouts[page["session_id"]].payment_intent_id
    balance = sql(
        env.database_url, "SELECT topup_remaining FROM ai_balances WHERE account_id = %s", (alice.account_id,)
    )
    assert balance in ([], [(0,)])
    [alert] = needs_operator(logs)
    assert alert["kind"] == "refund" and alert["topup"] is True


# ----- signing in again -------------------------------------------------------------------------------------------------


async def sign_in(env: Env, email: str, *, prompt: str | None = None) -> tuple[httpx.Response, str]:
    """A whole sign-in in a fresh browser; returns the callback's answer and the authorize URL it started from."""
    async with httpx.AsyncClient(base_url=env.origin, follow_redirects=False, timeout=10) as client:
        params = {"return_to": "/app"} | ({"prompt": prompt} if prompt is not None else {})
        r = await client.get("/v1/auth/login", params=params)
        assert r.status_code == 303, r.text
        authorize = str(r.headers["location"])
        async with httpx.AsyncClient(follow_redirects=False, timeout=10) as idp:
            r2 = await idp.get(authorize + "&" + urlencode({"dev_user": email}))
        assert r2.status_code == 303
        callback = await client.get(str(r2.headers["location"]))
        if callback.status_code == 303 and callback.headers["location"] == "/app":
            assert (await client.get("/v1/session")).status_code == 200
        return callback, authorize


async def test_a_sign_in_with_the_deleted_identity_lands_on_the_notice_without_a_session(
    env: Env, alice: Browser
) -> None:
    await sign_in_again(alice)
    await delete_account(alice, expect=204)
    totals = "SELECT (SELECT count(*) FROM accounts), (SELECT count(*) FROM sessions), (SELECT count(*) FROM security_events)"
    before = sql(env.database_url, totals)
    row_before = account_row(env, alice.account_id)
    with capture_logs() as logs:
        r, _ = await sign_in(env, alice.email)
    assert r.status_code == 303
    assert r.headers["location"] == ACCOUNT_DELETED_REDIRECT == "/?sign_in=account_deleted"
    assert r.headers["cache-control"] == "no-store"
    assert "set-cookie" not in r.headers and r.content == b""
    # nothing written: no new account, session or event, and the deleted row is as it was (email still blank)
    assert sql(env.database_url, totals) == before
    assert account_row(env, alice.account_id) == row_before
    assert [e["event"] for e in logs if str(e.get("event", "")).startswith("oidc.")] == [
        "oidc.callback_account_deleted"
    ]
    assert alice.email not in repr(logs)
    SCHEMAS.validate_rest("sign_in_problem", parse_qs(urlsplit(r.headers["location"]).query)["sign_in"][0])


async def test_login_accepts_prompt_login_and_nothing_else(env: Env) -> None:
    async with httpx.AsyncClient(base_url=env.origin, follow_redirects=False, timeout=10) as client:
        r = await client.get("/v1/auth/login", params={"return_to": "/app/settings", "prompt": "login"})
        assert r.status_code == 303 and r.headers["cache-control"] == "no-store" and "set-cookie" not in r.headers
        query = parse_qs(urlsplit(str(r.headers["location"])).query)
        assert query["prompt"] == ["login"] and query["client_id"] == [env.settings.oidc_client_id]
        assert query["max_age"] == ["0"]  # the provider must report when the person signed in (auth_time)
        state = query["state"][0]
        assert sql(env.database_url, "SELECT return_to, reauth FROM auth_flows WHERE state = %s", (state,)) == [
            ("/app/settings", True)
        ]
        flows = count(env, "SELECT count(*) FROM auth_flows")
        for value in ("none", "consent", "select_account", "LOGIN", "login ", "", "login login"):
            refused = await client.get("/v1/auth/login", params={"return_to": "/app", "prompt": value})
            assert_error(refused, 400, "MALFORMED_MESSAGE")
            assert "location" not in refused.headers and "set-cookie" not in refused.headers
        assert count(env, "SELECT count(*) FROM auth_flows") == flows  # a refused start stores no attempt
        # without the parameter, and without the re-prompt marker, no prompt at all, and no re-authentication
        r = await client.get("/v1/auth/login", params={"return_to": "/app"})
        query = parse_qs(urlsplit(str(r.headers["location"])).query)
        assert "prompt" not in query and "max_age" not in query
        assert sql(env.database_url, "SELECT reauth FROM auth_flows WHERE state = %s", (query["state"][0],)) == [
            (False,)
        ]


async def test_a_fresh_sign_in_with_prompt_login_can_delete_at_once(env: Env, alice: Browser) -> None:
    """The phone's way back from REAUTH_REQUIRED: a sign-in that asks for the password gives a session whose
    ``authenticated_at`` is the provider's ``auth_time``."""
    assert_error(await delete_account(alice), 403, "REAUTH_REQUIRED")
    again = Browser(env)
    started = utcnow()
    r = await again.client.get("/v1/auth/login", params={"return_to": "/app", "prompt": "login"})
    query = parse_qs(urlsplit(str(r.headers["location"])).query)
    assert query["prompt"] == ["login"] and query["max_age"] == ["0"]
    async with httpx.AsyncClient(follow_redirects=False, timeout=10) as idp:
        r2 = await idp.get(str(r.headers["location"]) + "&" + urlencode({"dev_user": alice.email}))
    r3 = await again.client.get(str(r2.headers["location"]))
    assert r3.status_code == 303 and r3.headers["location"] == "/app"
    session = await again.get("/v1/session", schema="session_response")
    again.csrf, again.account, again.email = session["csrf_token"], session["account"], alice.email
    assert again.account_id == alice.account_id
    confirmed = authenticated_at(env, again)
    assert confirmed is not None and started - AUTH_TIME_LEEWAY <= confirmed <= utcnow()
    await delete_account(again, expect=204)
    assert account_row(env, alice.account_id)["deleted_at"] is not None
    await again.aclose()


# ----- a deletion a crash interrupted -----------------------------------------------------------------------------------


async def test_a_deletion_interrupted_after_its_first_step_is_finished_by_the_purge(
    env: Env, alice: Browser, online_agent: AgentSim, paired: ControllerSim
) -> None:
    account_id = uuid.UUID(alice.account_id)
    # the first step only, as if the process stopped right after its commit
    async with env.services.db() as db:
        assert await account_deletion.mark_deleted(db, account_id, utcnow())
        await db.commit()
        assert not await account_deletion.mark_deleted(db, account_id, utcnow())  # once only
    assert (await alice.get("/v1/session", expect=401))["error"]["code"] == "UNAUTHENTICATED"
    assert account_row(env, alice.account_id)["email"] == ""
    # a pass leaves a deletion younger than 10 minutes alone (its request may still be running)
    await env.services.purge.purge()
    assert deleted_events(env, alice.account_id) == []
    assert count(env, "SELECT count(*) FROM pcs WHERE account_id = %s AND deleted_at IS NULL", alice.account_id) == 1
    # eleven minutes later the pass finishes it: the same revocations, the same frames, the one event (actor system)
    sql(
        env.database_url,
        "UPDATE accounts SET deleted_at = now() - interval '11 minutes' WHERE id = %s",
        (alice.account_id,),
    )
    counts = await env.services.purge.purge()
    assert counts["deletions_finished"] >= 1
    [(actor, _, _, detail, _)] = deleted_events(env, alice.account_id)
    assert actor == "system" and detail["subscription"] == "none" and detail["pcs"] == 1 and detail["phones"] == 1
    assert (await paired.recv_type("revoked"))["reason"] == "account_deleted"
    assert (await online_agent.recv_type("revoked"))["reason"] == "account_deleted"
    assert (
        count(env, "SELECT count(*) FROM sessions WHERE account_id = %s AND revoked_at IS NULL", alice.account_id) == 0
    )
    assert count(env, "SELECT count(*) FROM pcs WHERE account_id = %s AND deleted_at IS NULL", alice.account_id) == 0
    # finishing again changes nothing and writes no second event
    result = await account_deletion.finish(
        db=env.services.db, billing=env.services.billing, relay=env.services.relay, account_id=account_id
    )
    assert not result.finished_now and len(deleted_events(env, alice.account_id)) == 1
    await env.services.purge.purge()
    assert len(deleted_events(env, alice.account_id)) == 1


async def test_finish_does_nothing_to_an_account_that_is_not_deleted(env: Env, alice: Browser) -> None:
    result = await account_deletion.finish(
        db=env.services.db,
        billing=env.services.billing,
        relay=env.services.relay,
        account_id=uuid.UUID(alice.account_id),
    )
    assert not result.finished_now and result.counts == {}
    assert_untouched(env, alice)
    assert (await alice.get("/v1/session", schema="session_response"))["account"]["id"] == alice.account_id


@pytest.mark.parametrize("column", ["email", "display_name"])
def test_the_blanked_columns_are_not_nullable_so_they_become_empty_strings(column: str) -> None:
    assert not Account.__table__.c[column].nullable
