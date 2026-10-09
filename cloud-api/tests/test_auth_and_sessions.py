"""Login through the real dev OIDC issuer, session document, CSRF + Origin enforcement, logout,
session inventory/revocation, security events."""

from __future__ import annotations

import pytest

from tests.conftest import AccountFactory, Browser, Env


async def test_login_creates_session_and_session_document(alice: Browser) -> None:
    assert alice.account["email"] == alice.email
    s = await alice.get("/v1/session", schema="session_response")
    assert s["plan"] == "free" and s["limits"]["max_enabled_pcs"] == 1 and s["limits"]["max_controllers"] == 2
    assert s["protocol_version"] == "1.1"
    assert len(s["csrf_token"]) >= 32
    # the cookie is HttpOnly and SameSite=Lax; not Secure on a plain-http test origin
    cookie = alice.client.cookies.jar
    assert any(c.name == "dome_session" and c.has_nonstandard_attr("HttpOnly") for c in cookie)


async def test_unauthenticated_session_is_401(env: Env) -> None:
    r = await env.http.get("/v1/session")
    assert r.status_code == 401
    body = r.json()
    assert body["error"]["code"] == "UNAUTHENTICATED"
    assert r.headers["cache-control"] == "no-store"


async def test_relogin_same_user_maps_to_same_account(env: Env, alice: Browser) -> None:
    other = Browser(env)
    await other.login(alice.email)
    try:
        assert other.account_id == alice.account_id
    finally:
        await other.aclose()


async def test_csrf_and_origin_required_for_state_changes(alice: Browser) -> None:
    body = {"pc_name": "x", "remote_enabled": True}
    r = await alice.request("POST", "/v1/agent-link/ABCD-EFGH/approve", body, csrf=False)
    assert r.status_code == 403 and r.json()["error"]["code"] == "FORBIDDEN"
    r = await alice.request("POST", "/v1/agent-link/ABCD-EFGH/approve", body, origin=False)
    assert r.status_code == 403
    r = await alice.request(
        "POST", "/v1/agent-link/ABCD-EFGH/approve", body, headers={"Origin": "https://evil.example"}
    )
    assert r.status_code == 403
    r = await alice.request(
        "POST", "/v1/agent-link/ABCD-EFGH/approve", body, headers={"X-DoMe-CSRF": "wrong-token-value-0123456789abcdef"}
    )
    assert r.status_code == 403
    # with both headers the request passes the CSRF layer (and fails later on the unknown code)
    r = await alice.request("POST", "/v1/agent-link/ABCD-EFGH/approve", body)
    assert r.status_code == 404


async def test_return_to_must_be_relative(env: Env) -> None:
    r = await env.http.get("/v1/auth/login", params={"return_to": "https://evil.example/"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "MALFORMED_MESSAGE"
    r = await env.http.get("/v1/auth/login", params={"return_to": "//evil.example/"})
    assert r.status_code == 400
    r = await env.http.get("/v1/auth/login")
    assert r.status_code == 303 and r.headers["location"].startswith(env.settings.oidc_issuer + "/authorize?")
    assert "code_challenge=" in r.headers["location"] and "nonce=" in r.headers["location"]


async def test_callback_with_unknown_state_is_rejected(env: Env) -> None:
    r = await env.http.get("/v1/auth/callback", params={"code": "abc", "state": "x" * 40})
    assert r.status_code == 400
    r = await env.http.get("/v1/auth/callback", params={"error": "access_denied", "state": "x" * 40})
    assert r.status_code == 400


async def test_logout_revokes_session_and_clears_cookie(make_account: AccountFactory) -> None:
    b = await make_account()
    r = await b.request("POST", "/v1/auth/logout", expect=204)
    assert "dome_session=" in r.headers.get("set-cookie", "") and (
        "Max-Age=0" in r.headers["set-cookie"] or "expires=" in r.headers["set-cookie"].lower()
    )
    r = await b.client.get("/v1/session")
    assert r.status_code == 401


async def test_session_inventory_and_revocation(env: Env, make_account: AccountFactory) -> None:
    b = await make_account()
    second = Browser(env)
    await second.login(b.email)
    try:
        sessions = (await b.get("/v1/account/sessions"))["sessions"]
        assert len(sessions) == 2 and sum(1 for s in sessions if s["current"]) == 1
        other_id = next(s["id"] for s in sessions if not s["current"])
        await b.delete(f"/v1/account/sessions/{other_id}")
        assert (await second.client.get("/v1/session")).status_code == 401
        assert len((await b.get("/v1/account/sessions"))["sessions"]) == 1
        # another account cannot revoke it
        c = await make_account()
        await c.request("DELETE", f"/v1/account/sessions/{sessions[0]['id']}", expect=404)
        await b.request("DELETE", "/v1/account/sessions/not-a-uuid", expect=404)
    finally:
        await second.aclose()


async def test_security_events_are_account_scoped(alice: Browser, bob: Browser) -> None:
    events = (await alice.get("/v1/account/security-events?limit=10", schema="security_events_response"))["events"]
    kinds = {e["kind"] for e in events}
    assert "account_created" in kinds
    assert all("email" not in (e.get("detail") or {}) for e in events)
    bob_events = (await bob.get("/v1/account/security-events", schema="security_events_response"))["events"]
    assert {e["id"] for e in events}.isdisjoint({e["id"] for e in bob_events})
    await alice.request("GET", "/v1/account/security-events?limit=0", expect=400)


@pytest.mark.parametrize(
    "path", ["/v1/pcs", "/v1/controllers", "/v1/commands", "/v1/account/sessions", "/v1/account/security-events"]
)
async def test_private_endpoints_require_session(env: Env, path: str) -> None:
    r = await env.http.get(path)
    assert r.status_code == 401 and r.json()["error"]["code"] == "UNAUTHENTICATED"
