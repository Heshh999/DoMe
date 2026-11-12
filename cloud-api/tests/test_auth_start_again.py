"""A sign-in callback that cannot be finished, and whose whole remedy is a fresh sign-in, sends the browser to the
PWA's "Sign in again" notice instead of a page of technical text (DECISIONS #120). Through the real callback,
``tools/dev-idp`` and PostgreSQL. The cases are exactly three: the provider reported an error (or the person cancelled
there) or ``code``/``state`` is missing; the login attempt is unknown or was already used (the callback address opened
a second time); the attempt is older than its 10 minutes (the "Forgot password?" detour often takes longer). Each
answers 303 to one constant with ``Cache-Control: no-store`` and no cookie of any kind, reads and writes nothing about
an account, starts no session, leaves the re-prompt marker (DECISIONS #119) as it found it, and logs one fixed reason
word and nothing the provider or the browser sent. Everything else answers as before: a state or code of an impossible
length, a failed token exchange, an ID token that does not verify, the unconfirmed-email refusal and the login rate
limit; a deleted account has its own notice since DECISIONS #121."""

from __future__ import annotations

import asyncio
import hashlib
import secrets
from collections.abc import MutableMapping
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import pytest
from structlog.testing import capture_logs

from dome_api.auth.oidc import FLOW_LIFETIME, OIDCClient, StartAgainError
from dome_api.routes.auth import (
    ACCOUNT_DELETED_REDIRECT,
    EMAIL_UNVERIFIED_REDIRECT,
    REPROMPT_COOKIE,
    START_AGAIN_REDIRECT,
)
from dome_api.security.ratelimit import SlidingWindowLimiter
from tests.conftest import Env, sql

Logs = list[MutableMapping[str, Any]]
# What a provider or a tampering browser might send along: none of it may reach a redirect, a page or a log line.
PROVIDER_WORDS = "The+user+cancelled-9f3c"
MARKED = {"Cookie": f"{REPROMPT_COOKIE}=1"}


def _email() -> str:
    return f"again-{secrets.token_hex(6)}@example.test"


def _subject(email: str) -> str:
    """The subject tools/dev-idp gives an email (``_sub_for``)."""
    return "dev|" + hashlib.sha256(email.lower().encode()).hexdigest()[:24]


def _browser(env: Env) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=env.origin, follow_redirects=False, timeout=10)


async def _start(client: httpx.AsyncClient, return_to: str = "/app/devices") -> tuple[str, str]:
    """Start a sign-in as the PWA does; returns the provider's authorization URL and the attempt's state."""
    r = await client.get("/v1/auth/login", params={"return_to": return_to})
    assert r.status_code == 303, r.text
    url = str(r.headers["location"])
    return url, parse_qs(urlsplit(url).query)["state"][0]


async def _provider(authorize_url: str, email: str, verified: str | None = None) -> str:
    """The provider's half of a sign-in; returns the callback address it sends the browser to."""
    choice = {"dev_user": email} | ({"dev_email_verified": verified} if verified is not None else {})
    async with httpx.AsyncClient(follow_redirects=False, timeout=10) as idp:
        r = await idp.get(authorize_url + "&" + urlencode(choice))
    assert r.status_code == 303, r.text
    return str(r.headers["location"])


def _code(callback: str) -> str:
    return parse_qs(urlsplit(callback).query)["code"][0]


def _totals(env: Env) -> tuple[int, int, int]:
    """Accounts, sessions and security events in the whole database (the suite runs one test at a time)."""
    [row] = sql(
        env.database_url,
        "SELECT (SELECT count(*) FROM accounts), (SELECT count(*) FROM sessions), (SELECT count(*) FROM security_events)",
    )
    return int(row[0]), int(row[1]), int(row[2])


def _accounts(env: Env, email: str) -> list[tuple[Any, ...]]:
    return sql(
        env.database_url,
        "SELECT id, email, deleted_at FROM accounts WHERE subject = %s OR email = %s",
        (_subject(email), email),
    )


def _attempts(env: Env, state: str) -> int:
    return int(sql(env.database_url, "SELECT count(*) FROM auth_flows WHERE state = %s", (state,))[0][0])


def _assert_start_again(r: httpx.Response) -> None:
    assert r.status_code == 303
    assert r.headers["location"] == START_AGAIN_REDIRECT == "/?sign_in=try_again"  # the constant, nothing appended
    assert r.headers["cache-control"] == "no-store"
    assert "set-cookie" not in r.headers  # no session cookie, and the re-prompt marker neither set nor cleared
    assert r.content == b""  # a redirect, never a page with technical words


def _assert_error(r: httpx.Response, status: int, code: str, message: str) -> None:
    """The JSON error these cases have always answered: no redirect, no cookie."""
    assert r.status_code == status, r.text
    assert r.json()["error"]["code"] == code and r.json()["error"]["message"] == message
    assert "location" not in r.headers and "set-cookie" not in r.headers
    assert r.headers["cache-control"] == "no-store"


def _oidc_lines(logs: Logs) -> Logs:
    return [entry for entry in logs if str(entry.get("event", "")).startswith("oidc.")]


def _assert_reason_logged(logs: Logs, event: str, reason: str, *never: str) -> MutableMapping[str, Any]:
    """Exactly one sign-in line, with the fixed reason word and no other field than the ones named here; nothing in
    any captured line (the request log included) carries what the provider or the browser sent."""
    [line] = _oidc_lines(logs)
    assert line["event"] == event and line["reason"] == reason and line["log_level"] == "info"
    fields = {"event", "reason", "log_level"} | ({"provider_error"} if event == "oidc.callback_error" else set())
    assert set(line) == fields
    everything = repr(logs)
    for value in never:
        assert value and value not in everything
    return line


def _assert_no_start_again_line(logs: Logs) -> None:
    assert "oidc.callback_start_again" not in repr(logs) and "try_again" not in repr(logs)
    assert all("reason" not in entry or entry["event"] != "oidc.callback_error" for entry in logs)


# ----- the three cases --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("params", "provider_error"),
    [
        ({"error": "access_denied", "state": "s" * 43}, True),
        ({"error": "access_denied", "error_description": PROVIDER_WORDS, "state": "s" * 43}, True),
        ({"error": "server_error-9f3c", "error_description": PROVIDER_WORDS}, True),
        ({"error": "access_denied", "code": "c0de-9f3c", "state": "s" * 43}, True),
        ({"error": ""}, False),
        ({}, False),
        ({"code": "c0de-9f3c"}, False),
        ({"state": "s" * 43}, False),
    ],
)
async def test_a_provider_error_or_a_missing_parameter_lands_on_the_notice(
    env: Env, params: dict[str, str], provider_error: bool
) -> None:
    before = _totals(env)
    async with _browser(env) as client:
        with capture_logs() as logs:
            r = await client.get("/v1/auth/callback", params=params)
        _assert_start_again(r)
        # nothing the provider or the browser sent, in the log or anywhere in the answer
        line = _assert_reason_logged(
            logs, "oidc.callback_error", "cancelled_or_provider_error", "9f3c", "s" * 43, "access_denied"
        )
        assert line["provider_error"] is provider_error
        assert "9f3c" not in repr(r.headers) and "s" * 43 not in repr(r.headers)
        assert (await client.get("/v1/session")).status_code == 401
        # a browser that carries the re-prompt marker keeps it: this answer neither sets nor clears it
        marked = await client.get("/v1/auth/callback", params=params, headers=MARKED)
        _assert_start_again(marked)
    assert _totals(env) == before


async def test_an_unknown_state_lands_on_the_notice(env: Env) -> None:
    """A state this service never issued, or one whose attempt is gone: no attempt is found, the provider is never
    asked, and the browser is told to start again."""
    before = _totals(env)
    state, code = secrets.token_urlsafe(32), "c0de-" + secrets.token_urlsafe(16)
    async with _browser(env) as client:
        with capture_logs() as logs:
            r = await client.get("/v1/auth/callback", params={"code": code, "state": state})
        _assert_start_again(r)
        _assert_reason_logged(logs, "oidc.callback_start_again", "unknown_state", state, code)
        assert "oidc.token_exchange_failed" not in repr(logs)  # the provider was never contacted
        assert (await client.get("/v1/session")).status_code == 401
        marked = await client.get("/v1/auth/callback", params={"code": code, "state": state}, headers=MARKED)
        _assert_start_again(marked)
    assert _totals(env) == before


async def test_a_replayed_callback_after_a_sign_in_lands_on_the_notice_and_signs_no_one_in(env: Env) -> None:
    """The callback address opened a second time (Back, reload, or someone else who got hold of it): the attempt was
    used up by the sign-in, so the second opening signs no one in, in that browser or any other."""
    email = _email()
    async with _browser(env) as client, _browser(env) as other:
        url, state = await _start(client)
        callback = await _provider(url, email)
        ok = await client.get(callback)
        assert ok.status_code == 303 and ok.headers["location"] == "/app/devices"
        assert "dome_session=" in ok.headers["set-cookie"]
        assert _attempts(env, state) == 0  # used up, and committed
        [account] = _accounts(env, email)
        before = _totals(env)
        for replaying in (other, client):
            with capture_logs() as logs:
                replay = await replaying.get(callback)
            _assert_start_again(replay)
            _assert_reason_logged(logs, "oidc.callback_start_again", "unknown_state", state, _code(callback), email)
            assert _subject(email) not in repr(logs)
        # no new account, session or security event (a sign-in would have filed a "login" event)
        assert _totals(env) == before
        assert sql(env.database_url, "SELECT count(*) FROM sessions WHERE account_id = %s", (account[0],)) == [(1,)]
        assert "dome_session" not in other.cookies
        assert (await other.get("/v1/session")).status_code == 401
        # the first browser still holds the one session its sign-in gave it
        assert (await client.get("/v1/session")).json()["account"]["email"] == email


async def test_two_callbacks_at_the_same_moment_sign_in_once(env: Env) -> None:
    """The same callback address opened twice at once, in two browsers. The attempt is taken in one statement, so
    exactly one of them gets it and signs in; the other finds no attempt, lands on the notice and never reaches the
    provider with the code (it does not depend on the provider refusing a code it has already seen)."""
    for _ in range(5):
        email = _email()
        async with _browser(env) as one, _browser(env) as two:
            url, state = await _start(one)
            callback = await _provider(url, email)
            before = _totals(env)
            with capture_logs() as logs:
                answers = await asyncio.gather(one.get(callback), two.get(callback))
            signed_in = [r for r in answers if r.headers.get("location") == "/app/devices"]
            assert len(signed_in) == 1 and "dome_session=" in signed_in[0].headers["set-cookie"]
            [late] = [r for r in answers if r is not signed_in[0]]
            _assert_start_again(late)
            _assert_reason_logged(logs, "oidc.callback_start_again", "unknown_state", state, _code(callback), email)
            after = _totals(env)
            assert (after[0] - before[0], after[1] - before[1]) == (1, 1)  # one account, one session
            assert sorted([(await c.get("/v1/session")).status_code for c in (one, two)]) == [200, 401]
            assert _attempts(env, state) == 0


async def test_an_expired_attempt_lands_on_the_notice_and_a_fresh_sign_in_then_works(env: Env) -> None:
    """The sign-in was finished more than 10 minutes after it was started (asking for a password-reset email, waiting
    for it and setting the password in another tab takes that long). The attempt is refused and used up; the person
    starts again and gets in."""
    email = _email()
    before = _totals(env)
    async with _browser(env) as client:
        url, state = await _start(client)
        # the attempt was started 11 minutes ago
        late = FLOW_LIFETIME + (FLOW_LIFETIME / 10)
        sql(
            env.database_url,
            "UPDATE auth_flows SET created_at = created_at - %s, expires_at = expires_at - %s WHERE state = %s",
            (late, late, state),
        )
        callback = await _provider(url, email)
        with capture_logs() as logs:
            r = await client.get(callback)
        _assert_start_again(r)
        _assert_reason_logged(logs, "oidc.callback_start_again", "expired", state, _code(callback), email)
        assert _subject(email) not in repr(logs)
        assert "oidc.token_exchange_failed" not in repr(logs)  # refused before the provider is asked for anything
        assert (await client.get("/v1/session")).status_code == 401
        assert _accounts(env, email) == [] and _totals(env) == before
        # the expired attempt is gone for good (the redirect commits its deletion): opening the address again finds
        # no attempt, and still signs no one in
        assert _attempts(env, state) == 0
        with capture_logs() as logs:
            replay = await client.get(callback)
        _assert_start_again(replay)
        _assert_reason_logged(logs, "oidc.callback_start_again", "unknown_state", state, _code(callback), email)
        assert _accounts(env, email) == [] and _totals(env) == before
        # "Sign in again": a fresh attempt signs in
        url, _ = await _start(client, "/app")
        ok = await client.get(await _provider(url, email))
        assert ok.status_code == 303 and ok.headers["location"] == "/app"
        assert [c.split("=", 1)[0] for c in ok.headers.get_list("set-cookie")] == ["dome_session"]
        assert (await client.get("/v1/session")).json()["account"]["email"] == email
    [account] = _accounts(env, email)
    assert sql(env.database_url, "SELECT count(*) FROM sessions WHERE account_id = %s", (account[0],)) == [(1,)]


async def test_an_expired_attempt_swept_by_a_later_login_is_logged_as_unknown_state(env: Env) -> None:
    """Every login start removes all expired attempts (``OIDCClient.begin``). So when anyone has started a sign-in
    after the 10 minutes ran out, the late callback finds no attempt at all: the person lands on the same notice,
    and the log line reads ``unknown_state``, not ``expired``. ``expired`` therefore counts only the slow sign-ins
    that came back before the next login start, and ``unknown_state`` alone does not mean a replay."""
    email = _email()
    before = _totals(env)
    async with _browser(env) as client, _browser(env) as other:
        url, state = await _start(client)
        late = FLOW_LIFETIME + (FLOW_LIFETIME / 10)
        sql(
            env.database_url,
            "UPDATE auth_flows SET created_at = created_at - %s, expires_at = expires_at - %s WHERE state = %s",
            (late, late, state),
        )
        assert _attempts(env, state) == 1  # expired, and still there until someone starts a sign-in
        # someone else starts a sign-in: its sweep removes the expired attempt and leaves its own
        _, other_state = await _start(other)
        assert _attempts(env, state) == 0 and _attempts(env, other_state) == 1
        callback = await _provider(url, email)
        with capture_logs() as logs:
            r = await client.get(callback)
        _assert_start_again(r)
        _assert_reason_logged(logs, "oidc.callback_start_again", "unknown_state", state, _code(callback), email)
        assert _subject(email) not in repr(logs)
        assert "oidc.token_exchange_failed" not in repr(logs)  # the provider was never contacted
        assert (await client.get("/v1/session")).status_code == 401
        assert _accounts(env, email) == [] and _totals(env) == before
        # the other person's attempt is untouched by the late callback, and a fresh sign-in works for the late one
        assert _attempts(env, other_state) == 1
        url, _ = await _start(client, "/app")
        ok = await client.get(await _provider(url, email))
        assert ok.status_code == 303 and ok.headers["location"] == "/app"
        assert (await client.get("/v1/session")).json()["account"]["email"] == email


async def test_an_attempt_just_inside_its_ten_minutes_still_signs_in(env: Env) -> None:
    email = _email()
    async with _browser(env) as client:
        url, state = await _start(client)
        nearly = FLOW_LIFETIME - (FLOW_LIFETIME / 10)
        sql(
            env.database_url,
            "UPDATE auth_flows SET created_at = created_at - %s, expires_at = expires_at - %s WHERE state = %s",
            (nearly, nearly, state),
        )
        with capture_logs() as logs:
            ok = await client.get(await _provider(url, email))
        assert ok.status_code == 303 and ok.headers["location"] == "/app/devices"
        assert "dome_session=" in ok.headers["set-cookie"]
        assert _oidc_lines(logs) == []


async def test_the_marker_is_left_alone_by_an_expired_or_replayed_callback(env: Env) -> None:
    """After an unconfirmed-email refusal the browser carries the re-prompt marker. The person takes the "Forgot
    password?" detour, comes back too late and lands on "Sign in again": the marker is still there, so the next
    sign-in still asks the provider for its login page, and only the sign-in that succeeds clears it."""
    email = _email()
    async with _browser(env) as client:
        url, _ = await _start(client)
        refusal = await _provider(url, email, "false")
        refused = await client.get(refusal)
        assert refused.status_code == 303 and refused.headers["location"] == EMAIL_UNVERIFIED_REDIRECT
        assert client.cookies.get(REPROMPT_COOKIE, path="/v1/auth") == "1"
        # the refused callback opened again: the notice for starting again, not the email notice, and no cookie
        _assert_start_again(await client.get(refusal))
        url, state = await _start(client, "/app")
        assert parse_qs(urlsplit(url).query)["prompt"] == ["login"]
        sql(
            env.database_url,
            "UPDATE auth_flows SET expires_at = now() - interval '1 second' WHERE state = %s",
            (state,),
        )
        _assert_start_again(await client.get(await _provider(url, email, "true")))
        assert client.cookies.get(REPROMPT_COOKIE, path="/v1/auth") == "1"
        assert _accounts(env, email) == []
        url, _ = await _start(client, "/app")
        assert parse_qs(urlsplit(url).query)["prompt"] == ["login"]  # still marked
        ok = await client.get(await _provider(url, email, "true"))
        assert ok.status_code == 303 and ok.headers["location"] == "/app"
        assert REPROMPT_COOKIE not in client.cookies and "dome_session" in client.cookies


def test_the_typed_reason_is_one_of_two_fixed_words() -> None:
    assert StartAgainError("unknown_state").reason == "unknown_state"
    assert StartAgainError("expired").reason == "expired"


# ----- everything else answers as before --------------------------------------------------------------------------


@pytest.mark.parametrize(("state_length", "code_length"), [(15, 20), (129, 20), (0, 20), (43, 0), (43, 2049)], ids=str)
async def test_a_state_or_code_of_an_impossible_length_is_still_a_plain_400(
    env: Env, state_length: int, code_length: int
) -> None:
    """Never reached by a browser coming back from a sign-in started here: the service only issues 43-character
    states and a provider's code is never empty. A hand-made address keeps the technical answer."""
    before = _totals(env)
    with capture_logs() as logs:
        r = await env.http.get("/v1/auth/callback", params={"code": "c" * code_length, "state": "s" * state_length})
    _assert_error(r, 400, "MALFORMED_MESSAGE", "Invalid login response")
    assert _oidc_lines(logs) == []
    assert _totals(env) == before


async def test_the_longest_and_shortest_accepted_state_are_looked_up(env: Env) -> None:
    for length in (16, 128):
        r = await env.http.get("/v1/auth/callback", params={"code": "c", "state": "s" * length})
        _assert_start_again(r)


async def test_a_failed_token_exchange_is_still_a_502(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    """A real attempt, and the provider's token endpoint cannot be reached: the same error as before, not the notice."""
    before = _totals(env)
    oidc: OIDCClient = env.services.oidc
    monkeypatch.setitem(await oidc.discovery(), "token_endpoint", "http://127.0.0.1:1/token")
    async with _browser(env) as client:
        _, state = await _start(client)
        with capture_logs() as logs:
            r = await client.get("/v1/auth/callback", params={"code": "c0de-9f3c", "state": state})
        _assert_error(r, 502, "INTERNAL", "Sign-in could not be completed. Try again.")
        [line] = _oidc_lines(logs)
        assert line["event"] == "oidc.token_exchange_failed" and set(line) == {"event", "error", "log_level"}
        _assert_no_start_again_line(logs)
        assert "9f3c" not in repr(logs) and state not in repr(logs)
        assert (await client.get("/v1/session")).status_code == 401
    assert _totals(env) == before


async def test_a_code_the_provider_refuses_is_still_a_502(env: Env) -> None:
    """A real attempt, but a code the provider does not know (dev-idp answers without an ID token): the same error as
    before, not the notice. An error answer rolls the attempt's deletion back with the rest of the request, as it
    always has, so opening the address again asks the provider again, which refuses again: no one is signed in."""
    before = _totals(env)
    async with _browser(env) as client:
        _, state = await _start(client)
        for _ in range(2):
            with capture_logs() as logs:
                r = await client.get("/v1/auth/callback", params={"code": "not-a-code-9f3c", "state": state})
            _assert_error(r, 502, "INTERNAL", "The identity provider returned no ID token")
            _assert_no_start_again_line(logs)
            assert "9f3c" not in repr(logs) and state not in repr(logs)
            assert _attempts(env, state) == 1
        assert (await client.get("/v1/session")).status_code == 401
    assert _totals(env) == before


async def test_an_id_token_that_does_not_verify_is_still_a_400(env: Env) -> None:
    """The provider's ID token carries the nonce of the attempt it was issued for; with another nonce on record the
    token is rejected, with the same error as before."""
    email = _email()
    before = _totals(env)
    async with _browser(env) as client:
        url, state = await _start(client)
        sql(env.database_url, "UPDATE auth_flows SET nonce = %s WHERE state = %s", (secrets.token_urlsafe(32), state))
        with capture_logs() as logs:
            r = await client.get(await _provider(url, email))
        _assert_error(r, 400, "MALFORMED_MESSAGE", "The identity token was rejected")
        [line] = _oidc_lines(logs)
        assert line["event"] == "oidc.id_token_rejected" and set(line) == {"event", "reason", "log_level"}
        assert "try_again" not in repr(logs) and "oidc.callback_start_again" not in repr(logs)
        assert email not in repr(logs) and _subject(email) not in repr(logs)
        assert (await client.get("/v1/session")).status_code == 401
    assert _accounts(env, email) == [] and _totals(env) == before


async def test_a_deleted_account_lands_on_its_own_notice(env: Env) -> None:
    """Since account deletion (DECISIONS #121) a deleted account's identity is redirected to its own notice like the
    other sign-in problems, instead of the JSON 403: still no session, no cookie and nothing written."""
    email = _email()
    async with _browser(env) as client:
        url, _ = await _start(client)
        ok = await client.get(await _provider(url, email))
        assert ok.status_code == 303 and "dome_session=" in ok.headers["set-cookie"]
    [account] = _accounts(env, email)
    sql(env.database_url, "UPDATE accounts SET deleted_at = now() WHERE id = %s", (account[0],))
    before, [deleted] = _totals(env), _accounts(env, email)
    async with _browser(env) as client:
        url, _ = await _start(client)
        with capture_logs() as logs:
            r = await client.get(await _provider(url, email))
        assert r.status_code == 303 and r.headers["location"] == ACCOUNT_DELETED_REDIRECT == "/?sign_in=account_deleted"
        assert r.headers["cache-control"] == "no-store" and r.content == b""
        assert "set-cookie" not in r.headers  # no session cookie, and the re-prompt marker neither set nor cleared
        assert [line["event"] for line in _oidc_lines(logs)] == ["oidc.callback_account_deleted"]
        assert email not in repr(logs) and _subject(email) not in repr(logs)
        assert (await client.get("/v1/session")).status_code == 401
    assert _totals(env) == before
    assert _accounts(env, email) == [deleted]  # the row is as it was: nothing about the account written


async def test_an_unconfirmed_email_still_gets_its_own_notice_and_marker(env: Env) -> None:
    email = _email()
    async with _browser(env) as client:
        url, _ = await _start(client)
        with capture_logs() as logs:
            r = await client.get(await _provider(url, email, "false"))
        assert r.status_code == 303 and r.headers["location"] == EMAIL_UNVERIFIED_REDIRECT
        assert [c.split("=", 1)[0] for c in r.headers.get_list("set-cookie")] == [REPROMPT_COOKIE]
        assert [entry["event"] for entry in _oidc_lines(logs)] == ["oidc.email_unverified"]
    assert _accounts(env, email) == []


async def test_a_rate_limited_login_is_still_a_429(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(env.services, "login_limiter", SlidingWindowLimiter(1, 60))
    async with _browser(env) as client:
        first = await client.get("/v1/auth/login", params={"return_to": "/app"})
        assert first.status_code == 303
        limited = await client.get("/v1/auth/login", params={"return_to": "/app"})
        assert limited.status_code == 429 and limited.json()["error"]["code"] == "RATE_LIMITED"
        assert "location" not in limited.headers and "set-cookie" not in limited.headers
