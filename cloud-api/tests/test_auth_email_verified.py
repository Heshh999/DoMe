"""Sign-in is refused unless the provider says the email address is confirmed (``email_verified`` exactly true;
DECISIONS #118). Through the real callback, ``tools/dev-idp`` and PostgreSQL: a refusal redirects the browser to the
PWA's "Confirm your email first" notice, creates or updates no account, starts no session, sets no session cookie, logs
no email or subject, and uses up the login attempt. Its only cookie is the re-prompt marker (DECISIONS #119): sign-ins
from that browser ask the provider for its login page (``prompt=login``) until one succeeds, so the person can reach
"Forgot password?" or another account instead of being answered "unverified" again at once."""

from __future__ import annotations

import base64
import hashlib
import secrets
from collections.abc import Callable, MutableMapping
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import pytest
from fastapi.responses import RedirectResponse
from structlog.testing import capture_logs

from dome_api.auth.oidc import OIDCClient
from dome_api.auth.sessions import clear_session_cookie, set_session_cookie
from dome_api.routes.auth import (
    EMAIL_UNVERIFIED_REDIRECT,
    REPROMPT_COOKIE,
    clear_reprompt_cookie,
    set_reprompt_cookie,
)
from dome_api.settings import Settings
from tests.conftest import Browser, Env, sql

# The re-prompt marker exactly as a refusal sets it on the plain-http test origin (no Secure there, like the session
# cookie): value 1, HttpOnly, one day, only the auth routes, SameSite=Lax.
MARKER = "dome_reprompt=1; HttpOnly; Max-Age=86400; Path=/v1/auth; SameSite=lax"


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
    return await _finish(client, str(r.headers["location"]), email, verified)


async def _finish(
    client: httpx.AsyncClient, authorize_url: str, email: str, verified: str | None
) -> tuple[str, httpx.Response]:
    """The provider's half of a sign-in started earlier, then the callback in ``client``."""
    choice = {"dev_user": email} | ({"dev_email_verified": verified} if verified is not None else {})
    async with httpx.AsyncClient(follow_redirects=False, timeout=10) as idp:
        r2 = await idp.get(authorize_url + "&" + urlencode(choice))
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
    # no session cookie: the one cookie is the re-prompt marker, exactly as agreed (DECISIONS #119)
    assert r.headers.get_list("set-cookie") == [MARKER]
    assert "dome_session" not in client.cookies
    assert client.cookies.get(REPROMPT_COOKIE, path="/v1/auth") == "1"
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


# ----- the re-prompt marker (DECISIONS #119) ---------------------------------------------------------------------


def _expected_authorize_url(env: Env, location: str, *, prompt: str | None) -> str:
    """The exact URL ``GET /v1/auth/login`` must redirect to, rebuilt from the login attempt it stored: Authlib's
    parameter order, with ``prompt`` (when given) after ``nonce`` and before the PKCE challenge."""
    state = parse_qs(urlsplit(location).query)["state"][0]
    [(nonce, verifier)] = sql(
        env.database_url, "SELECT nonce, code_verifier FROM auth_flows WHERE state = %s", (state,)
    )
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    pairs = [
        ("response_type", "code"),
        ("client_id", env.settings.oidc_client_id),
        ("redirect_uri", env.settings.redirect_uri),
        ("scope", env.settings.oidc_scopes),
        ("state", state),
        ("nonce", nonce),
        *([("prompt", prompt)] if prompt is not None else []),
        ("code_challenge", challenge),
        ("code_challenge_method", "S256"),
    ]
    return env.settings.oidc_issuer + "/authorize?" + urlencode(pairs)


def _return_to(env: Env, location: str) -> str:
    state = parse_qs(urlsplit(location).query)["state"][0]
    return str(sql(env.database_url, "SELECT return_to FROM auth_flows WHERE state = %s", (state,))[0][0])


def _cookies_sent(client: httpx.AsyncClient, path: str) -> str:
    """The Cookie header this browser would send to ``path``: its jar applies each cookie's Path."""
    return str(client.build_request("GET", path).headers.get("cookie", ""))


def _cookie_names(r: httpx.Response) -> list[str]:
    return [c.split("=", 1)[0] for c in r.headers.get_list("set-cookie")]


def _assert_marker_kept(r: httpx.Response, client: httpx.AsyncClient) -> None:
    """The answer touches no cookie, and the browser still carries the marker."""
    assert "set-cookie" not in r.headers
    assert client.cookies.get(REPROMPT_COOKIE, path="/v1/auth") == "1"


def _assert_marker_cleared(r: httpx.Response) -> None:
    [cleared] = [c for c in r.headers.get_list("set-cookie") if c.startswith(REPROMPT_COOKIE + "=")]
    assert cleared.startswith('dome_reprompt=""; expires=')
    assert cleared.endswith("; HttpOnly; Max-Age=0; Path=/v1/auth; SameSite=lax")


async def test_the_sign_in_after_a_refusal_asks_for_the_login_page(env: Env) -> None:
    """The provider remembers the refused person and would answer "unverified" at once; the marker makes the next
    sign-in ask for its login page (where "Forgot password?" is). Login only reads the marker: it sets no cookie."""
    email = _email()
    async with _browser(env) as client:
        _, refused = await _sign_in(client, email, "false")
        _assert_refused(refused, client)
        [cookie] = [c for c in client.cookies.jar if c.name == REPROMPT_COOKIE]
        assert cookie.value == "1" and cookie.path == "/v1/auth" and not cookie.secure
        assert cookie.has_nonstandard_attr("HttpOnly")
        # the browser sends it to the auth routes as mounted (logout too, which ignores it), and nowhere else
        auth_routes = ("/v1/auth/login", "/v1/auth/callback", "/v1/auth/logout")
        assert [_cookies_sent(client, p) for p in auth_routes] == ["dome_reprompt=1"] * 3
        assert [_cookies_sent(client, p) for p in ("/", "/app", "/v1/session", "/v1/pcs")] == [""] * 4
        with capture_logs() as logs:
            r = await client.get("/v1/auth/login", params={"return_to": "/app"})
        assert r.status_code == 303 and r.headers["cache-control"] == "no-store"
        location = str(r.headers["location"])
        assert location == _expected_authorize_url(env, location, prompt="login")
        assert parse_qs(urlsplit(location).query)["prompt"] == ["login"]
        assert _return_to(env, location) == "/app"  # the return_to rules are untouched
        _assert_marker_kept(r, client)  # the tap does not use the marker up: only the callback ends it
        assert "prompt" not in repr(logs)  # nothing new is logged: no line names the marker or the prompt
    assert _accounts(env, email) == []


async def test_leaving_the_login_page_without_signing_in_does_not_use_the_marker_up(env: Env) -> None:
    """The marker is ended by a sign-in's outcome, not by the tap. Someone who opens the provider's login page and
    leaves it (swipes back to look for the email, closes the tab) never reaches the callback. The provider still
    remembers them, so an ordinary sign-in would be answered "unverified" at once and land on the notice again: every
    second try. Each try keeps asking for the login page until one signs in."""
    email = _email()
    async with _browser(env) as client:
        _, refused = await _sign_in(client, email, "false")
        _assert_refused(refused, client)
        # three taps that never come back from the provider
        for return_to in ("/app", "/app/devices", "/app"):
            r = await client.get("/v1/auth/login", params={"return_to": return_to})
            assert r.status_code == 303 and r.headers["cache-control"] == "no-store"
            location = str(r.headers["location"])
            assert location == _expected_authorize_url(env, location, prompt="login")
            assert _return_to(env, location) == return_to
            _assert_marker_kept(r, client)
        # a callback that fails (the provider reports an error) is not a sign-in either: the marker stays
        failed = await client.get("/v1/auth/callback", params={"error": "access_denied", "state": "x" * 40})
        assert failed.status_code == 400
        _assert_marker_kept(failed, client)
        r = await client.get("/v1/auth/login", params={"return_to": "/app"})
        location = str(r.headers["location"])
        assert location == _expected_authorize_url(env, location, prompt="login")
        _assert_marker_kept(r, client)
        assert _accounts(env, email) == []
        # the address is confirmed meanwhile (a password reset does that): this try signs in and ends the marker
        _, ok = await _finish(client, location, email, "true")
        assert ok.status_code == 303 and ok.headers["location"] == "/app"
        assert sorted(_cookie_names(ok)) == ["dome_reprompt", "dome_session"]
        _assert_marker_cleared(ok)
        assert REPROMPT_COOKIE not in client.cookies
        # signed in: the sign-in after that is an ordinary one again
        r = await client.get("/v1/auth/login", params={"return_to": "/app"})
        assert r.status_code == 303 and "set-cookie" not in r.headers
        assert r.headers["location"] == _expected_authorize_url(env, r.headers["location"], prompt=None)
    [row] = _accounts(env, email)
    assert _session_count(env, row[0]) == 1


async def test_every_refusal_marks_again_until_the_address_is_confirmed(env: Env) -> None:
    """What the founder met on staging (2026-11-12), end to end: refused, the next sign-in shows the provider's login
    page; signing in there while still unconfirmed is refused and marked again, as often as it happens; once the
    address is confirmed (a password reset does that) the same step signs in and leaves no marker behind."""
    email = _email()
    async with _browser(env) as client:
        _, refused = await _sign_in(client, email, "false")
        _assert_refused(refused, client)
        for _ in range(2):
            r = await client.get("/v1/auth/login", params={"return_to": "/app"})
            location = str(r.headers["location"])
            assert location == _expected_authorize_url(env, location, prompt="login")
            _assert_marker_kept(r, client)
            _, refused = await _finish(client, location, email, "false")
            _assert_refused(refused, client)
            assert (await client.get("/v1/session")).status_code == 401
        assert _accounts(env, email) == []
        r = await client.get("/v1/auth/login", params={"return_to": "/app"})
        location = str(r.headers["location"])
        assert location == _expected_authorize_url(env, location, prompt="login")
        _assert_marker_kept(r, client)
        _, ok = await _finish(client, location, email, "true")
        assert ok.status_code == 303 and ok.headers["location"] == "/app"
        # the session cookie, and the marker cleared on that same answer
        assert sorted(_cookie_names(ok)) == ["dome_reprompt", "dome_session"]
        _assert_marker_cleared(ok)
        assert REPROMPT_COOKIE not in client.cookies
        assert (await client.get("/v1/session")).json()["account"]["email"] == email
    [row] = _accounts(env, email)
    assert _session_count(env, row[0]) == 1


async def test_a_sign_in_without_the_marker_has_no_prompt(env: Env) -> None:
    async with _browser(env) as client:
        r = await client.get("/v1/auth/login", params={"return_to": "/app/devices"})
        assert r.status_code == 303 and "set-cookie" not in r.headers
        location = str(r.headers["location"])
        assert location == _expected_authorize_url(env, location, prompt=None)
        assert "prompt" not in parse_qs(urlsplit(location).query)
        assert _return_to(env, location) == "/app/devices"


@pytest.mark.parametrize("value", ["", "0", "01", "2", "11", "true", "login", "yes"])
async def test_a_marker_with_any_other_value_is_ignored(env: Env, value: str) -> None:
    async with _browser(env) as client:
        r = await client.get(
            "/v1/auth/login", params={"return_to": "/app"}, headers={"Cookie": f"{REPROMPT_COOKIE}={value}"}
        )
        assert r.status_code == 303 and "set-cookie" not in r.headers
        location = str(r.headers["location"])
        assert location == _expected_authorize_url(env, location, prompt=None)
        # the same request with the one value that counts
        r = await client.get("/v1/auth/login", params={"return_to": "/app"}, headers={"Cookie": f"{REPROMPT_COOKIE}=1"})
        assert r.status_code == 303 and "set-cookie" not in r.headers
        location = str(r.headers["location"])
        assert location == _expected_authorize_url(env, location, prompt="login")


async def test_the_marker_never_changes_the_return_to_rules(env: Env) -> None:
    """A return_to that is refused is refused the same way with the marker, which stays for the next try."""
    async with _browser(env) as client:
        bad = {"return_to": "https://evil.example/"}
        plain = await client.get("/v1/auth/login", params=bad)
        marked = await client.get("/v1/auth/login", params=bad, headers={"Cookie": f"{REPROMPT_COOKIE}=1"})
        for r in (plain, marked):
            assert r.status_code == 400 and r.json()["error"]["code"] == "MALFORMED_MESSAGE"
            assert "set-cookie" not in r.headers
        assert marked.json()["error"]["message"] == plain.json()["error"]["message"]


async def test_a_successful_sign_in_never_sets_the_marker_and_clears_the_one_it_finds(env: Env) -> None:
    email = _email()
    async with _browser(env) as client:
        _, ok = await _sign_in(client, email, "true")
        assert ok.status_code == 303
        assert _cookie_names(ok) == ["dome_session"]
        assert REPROMPT_COOKIE not in client.cookies
    # Two tabs: one sign-in starts (before any marker, so without prompt), a second is refused (marker set), then the
    # first completes with a confirmed address. It signs in, and the marker is cleared on that same answer.
    other = _email()
    async with _browser(env) as client:
        first = await client.get("/v1/auth/login", params={"return_to": "/app"})
        assert "prompt" not in parse_qs(urlsplit(str(first.headers["location"])).query)
        _, refused = await _sign_in(client, other, "false")
        _assert_refused(refused, client)
        _, ok = await _finish(client, str(first.headers["location"]), email, "true")
        assert ok.status_code == 303 and ok.headers["location"] == "/app"
        assert sorted(_cookie_names(ok)) == ["dome_reprompt", "dome_session"]
        _assert_marker_cleared(ok)
        assert REPROMPT_COOKIE not in client.cookies
        assert (await client.get("/v1/session")).json()["account"]["email"] == email
    assert _accounts(env, other) == []


async def test_logout_neither_reads_nor_changes_the_marker(env: Env) -> None:
    """The marker's Path also covers POST /v1/auth/logout, which ignores it. A browser still signed in from before
    (an earlier session survives a refusal) signs out as usual and stays marked for its next sign-in."""
    email = _email()
    browser = Browser(env)
    try:
        await browser.login(email)
        _, refused = await _sign_in(browser.client, email, "false")
        assert refused.status_code == 303 and refused.headers["location"] == EMAIL_UNVERIFIED_REDIRECT
        assert refused.headers.get_list("set-cookie") == [MARKER]
        assert _cookies_sent(browser.client, "/v1/auth/logout") == "dome_reprompt=1; " + (
            "dome_session=" + browser.session_cookie
        )
        r = await browser.request("POST", "/v1/auth/logout", expect=204)
        assert _cookie_names(r) == ["dome_session"]
        assert "dome_session" not in browser.client.cookies
        assert browser.client.cookies.get(REPROMPT_COOKIE, path="/v1/auth") == "1"
        r = await browser.client.get("/v1/auth/login", params={"return_to": "/app"})
        location = str(r.headers["location"])
        assert location == _expected_authorize_url(env, location, prompt="login")
        _assert_marker_kept(r, browser.client)
    finally:
        await browser.aclose()


def _cookie_settings(origin: str) -> Settings:
    return Settings(
        env="test",
        public_origin=origin,
        database_url="postgresql+psycopg://dome@/unused",
        session_secret="unit-test-session-secret-0123456789",
        oidc_issuer="https://idp.example.test",
        oidc_client_id="dome-unit",
        oidc_client_secret="unit-test-client-secret",
        entitlement_signing_key_pem_path=Path("/nonexistent/entitlement.pem"),
    )


@pytest.mark.parametrize(
    ("origin", "secure"), [("https://staging.example.test", True), ("http://127.0.0.1:8000", False)]
)
def test_the_marker_is_secure_exactly_when_the_session_cookie_is(origin: str, secure: bool) -> None:
    settings = _cookie_settings(origin)
    marker, session = RedirectResponse("/"), RedirectResponse("/")
    set_reprompt_cookie(marker, settings)
    set_session_cookie(session, settings, "t" * 43)
    assert session.headers["set-cookie"].endswith("; Secure") is secure
    assert marker.headers["set-cookie"] == MARKER + ("; Secure" if secure else "")
    marker, session = RedirectResponse("/"), RedirectResponse("/")
    clear_reprompt_cookie(marker, settings)
    clear_session_cookie(session, settings)
    assert session.headers["set-cookie"].endswith("; Secure") is secure
    assert marker.headers["set-cookie"].endswith("; Secure") is secure
    assert "Max-Age=0; Path=/v1/auth;" in marker.headers["set-cookie"]
