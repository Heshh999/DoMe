"""Sign-in is refused unless the provider says the email address is confirmed (``email_verified`` exactly true;
DECISIONS #118). Through the real callback, ``tools/dev-idp`` and PostgreSQL: a refusal redirects the browser to the
PWA's "Confirm your email first" notice, creates or updates no account, starts no session, sets no cookie, logs no
email or subject, and uses up the login attempt."""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import Callable, MutableMapping
from typing import Any
from urllib.parse import urlencode

import httpx
import pytest
from structlog.testing import capture_logs

from dome_api.auth.oidc import OIDCClient
from dome_api.routes.auth import EMAIL_UNVERIFIED_REDIRECT
from tests.conftest import Browser, Env, sql


def _subject(email: str) -> str:
    """The subject tools/dev-idp gives an email (``_sub_for``)."""
    return "dev|" + hashlib.sha256(email.lower().encode()).hexdigest()[:24]


def _email() -> str:
    return f"unconfirmed-{secrets.token_hex(6)}@example.test"


def _browser(env: Env) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=env.origin, follow_redirects=False, timeout=10)


async def _sign_in(client: httpx.AsyncClient, email: str, verified: str | None) -> tuple[str, httpx.Response]:
    """One sign-in round trip as the PWA does it; ``verified`` is dev-idp's ``dev_email_verified`` (None: default).
    Returns the callback URL the provider sent the browser to and the callback's answer."""
    r = await client.get("/v1/auth/login", params={"return_to": "/app/devices"})
    assert r.status_code == 303, r.text
    choice = {"dev_user": email} | ({"dev_email_verified": verified} if verified is not None else {})
    async with httpx.AsyncClient(follow_redirects=False, timeout=10) as idp:
        r2 = await idp.get(r.headers["location"] + "&" + urlencode(choice))
    assert r2.status_code == 303, r2.text
    callback = str(r2.headers["location"])
    return callback, await client.get(callback)


def _accounts(env: Env, email: str) -> list[tuple[Any, ...]]:
    return sql(
        env.database_url,
        "SELECT id, email, display_name, deleted_at FROM accounts WHERE subject = %s OR email = %s",
        (_subject(email), email),
    )


def _session_count(env: Env, account_id: Any) -> int:
    return int(sql(env.database_url, "SELECT count(*) FROM sessions WHERE account_id = %s", (account_id,))[0][0])


def _assert_refused(r: httpx.Response, client: httpx.AsyncClient) -> None:
    assert r.status_code == 303
    assert r.headers["location"] == EMAIL_UNVERIFIED_REDIRECT == "/?sign_in=email_unverified"
    assert r.headers["cache-control"] == "no-store"
    assert "set-cookie" not in r.headers
    assert "dome_session" not in client.cookies
    assert r.content == b""  # a redirect, never an error page with technical words


def _assert_logs_clean(logs: list[MutableMapping[str, Any]], email: str, claim: str) -> None:
    [line] = [entry for entry in logs if entry.get("event") == "oidc.email_unverified"]
    assert line["claim"] == claim and line["log_level"] == "info"
    assert set(line) <= {"event", "claim", "log_level"}
    everything = repr(logs)
    assert email not in everything and email.split("@")[0] not in everything
    assert _subject(email) not in everything


async def test_verified_email_signs_in_as_before(env: Env) -> None:
    email = _email()
    async with _browser(env) as client:
        _, r = await _sign_in(client, email, "true")
        assert r.status_code == 303 and r.headers["location"] == "/app/devices"
        assert "dome_session=" in r.headers["set-cookie"]
        session = (await client.get("/v1/session")).json()
        assert session["account"]["email"] == email
    [row] = _accounts(env, email)
    assert row[1] == email and _session_count(env, row[0]) == 1


@pytest.mark.parametrize(("verified", "claim"), [("false", "false"), ("absent", "absent")])
async def test_unverified_or_missing_claim_is_refused_without_an_account(env: Env, verified: str, claim: str) -> None:
    email = _email()
    async with _browser(env) as client:
        with capture_logs() as logs:
            callback, r = await _sign_in(client, email, verified)
        _assert_refused(r, client)
        _assert_logs_clean(logs, email, claim)
        assert (await client.get("/v1/session")).status_code == 401
        # the login attempt was used up: the same callback URL cannot be replayed
        replay = await client.get(callback)
        assert replay.status_code == 400 and "set-cookie" not in replay.headers
    assert _accounts(env, email) == []


async def test_a_string_true_is_not_true(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    """Only the JSON value true counts; a provider that sends the string "true" is refused like false."""
    oidc: OIDCClient = env.services.oidc
    original: Callable[..., Any] = oidc._verify_id_token

    async def with_string_claim(id_token: str, nonce: str) -> dict[str, Any]:
        claims: dict[str, Any] = await original(id_token, nonce)
        claims["email_verified"] = "true"
        return claims

    monkeypatch.setattr(oidc, "_verify_id_token", with_string_claim)
    email = _email()
    async with _browser(env) as client:
        with capture_logs() as logs:
            _, r = await _sign_in(client, email, None)
        _assert_refused(r, client)
        _assert_logs_clean(logs, email, "not_boolean")
    assert _accounts(env, email) == []


async def test_existing_account_now_unverified_is_refused_and_left_untouched(env: Env) -> None:
    """An account that signed in while confirmed, whose provider now reports the address unconfirmed (it changed its
    email, or an operator reset the flag), gets no new session; its row, sessions and security events stay as they
    were, and its earlier session keeps working (only new sign-ins are refused)."""
    email = _email()
    first = Browser(env)
    try:
        await first.login(email)
        [before] = _accounts(env, email)
        sessions_before = _session_count(env, before[0])
        events_before = sql(
            env.database_url, "SELECT count(*) FROM security_events WHERE account_id = %s", (before[0],)
        )
        async with _browser(env) as client:
            with capture_logs() as logs:
                _, r = await _sign_in(client, email, "false")
            _assert_refused(r, client)
            _assert_logs_clean(logs, email, "false")
            assert (await client.get("/v1/session")).status_code == 401
        assert _accounts(env, email) == [before]
        assert _session_count(env, before[0]) == sessions_before
        events_after = sql(env.database_url, "SELECT count(*) FROM security_events WHERE account_id = %s", (before[0],))
        assert events_after == events_before
        assert (await first.get("/v1/session", schema="session_response"))["account"]["id"] == str(before[0])
        # confirmed again: signs in to the same account as before
        async with _browser(env) as client:
            _, r = await _sign_in(client, email, "true")
            assert r.status_code == 303 and r.headers["location"] == "/app/devices"
            assert (await client.get("/v1/session")).json()["account"]["id"] == str(before[0])
    finally:
        await first.aclose()
