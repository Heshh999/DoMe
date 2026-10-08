"""Entitlement assertions and JWKS, plans, health, security headers, static PWA serving, redaction,
controller rename, plan-disabled controllers."""

from __future__ import annotations

import time
from typing import Any

from joserfc import jwt
from joserfc.jwk import KeySet

from dome_api.logging import redact
from tests.conftest import SCHEMAS, AgentSim, Browser, ControllerSim, Env, sql


async def test_entitlement_free_is_null_and_pro_verifies_against_jwks(
    env: Env, alice: Browser, linked_agent: AgentSim
) -> None:
    r = await linked_agent.rest(
        "POST", "/v1/agent/entitlement", bearer=True, schema="agent_entitlement_response", expect=200
    )
    assert r.json() == {"plan": "free", "assertion": None, "pc_enabled": True}
    sql(env.database_url, "UPDATE accounts SET plan = 'pro' WHERE id = %s", (alice.account_id,))
    r = await linked_agent.rest(
        "POST", "/v1/agent/entitlement", bearer=True, schema="agent_entitlement_response", expect=200
    )
    body = r.json()
    assert body["plan"] == "pro" and isinstance(body["assertion"], str)
    jwks = await env.http.get("/.well-known/dome-jwks.json")
    assert jwks.status_code == 200 and jwks.headers["cache-control"].startswith("public")
    keyset = KeySet.import_key_set(jwks.json())
    assert all(k.get("kty") == "OKP" and k.get("crv") == "Ed25519" and "d" not in k for k in jwks.json()["keys"])
    token = jwt.decode(body["assertion"], keyset, algorithms=["EdDSA"])
    assert token.header["typ"] == "dome-entitlement+jwt" and token.header["kid"] == jwks.json()["keys"][0]["kid"]
    SCHEMAS.validate_entitlement_claims(token.claims)
    claims: dict[str, Any] = token.claims
    assert claims["sub"] == alice.account_id and claims["pc"] == linked_agent.pc_id and claims["plan"] == "pro"
    assert claims["limits"]["max_enabled_pcs"] == 5 and claims["limits"]["routines"] is True
    assert 3500 <= claims["exp"] - claims["iat"] <= 3600 and abs(claims["iat"] - time.time()) < 30
    assert claims["iss"] == env.settings.effective_api_url
    # the session document and the agent snapshot reflect the plan
    s = await alice.get("/v1/session", schema="session_response")
    assert s["plan"] == "pro" and s["entitlement_state"] == "active" and s["limits"]["max_controllers"] == 5
    await linked_agent.connect()
    assert linked_agent.snapshot is not None and "entitlement_assertion" in linked_agent.snapshot
    jwt.decode(linked_agent.snapshot["entitlement_assertion"], keyset, algorithms=["EdDSA"])


async def test_plan_disabled_controllers_after_downgrade(env: Env, alice: Browser, online_agent: AgentSim) -> None:
    sql(env.database_url, "UPDATE accounts SET plan = 'pro' WHERE id = %s", (alice.account_id,))
    phones = [ControllerSim(env, alice, name=f"Phone {i}") for i in range(3)]
    for p in phones:
        await p.pair(online_agent)
    sql(env.database_url, "UPDATE accounts SET plan = 'free' WHERE id = %s", (alice.account_id,))
    # the most recently seen two stay active (plans.json downgrade_policy.default_selection)
    for p in phones[1:]:
        await p.connect()
    statuses = {c["id"]: c["status"] for c in (await alice.get("/v1/controllers"))["controllers"]}
    assert statuses[phones[0].controller_id] == "plan_disabled"  # type: ignore[index]
    assert statuses[phones[1].controller_id] == "active" and statuses[phones[2].controller_id] == "active"  # type: ignore[index]
    await (
        phones[0].connect()
    )  # binding still works (last_seen moves, but the newest two remain the others until it reconnects... it just did)
    statuses = {c["id"]: c["status"] for c in (await alice.get("/v1/controllers"))["controllers"]}
    disabled = [cid for cid, st in statuses.items() if st == "plan_disabled"]
    assert len(disabled) == 1
    victim = next(p for p in phones if p.controller_id == disabled[0])
    cid = await victim.command(online_agent.pc_id, "system.ping")
    r = await victim.recv_type("result", command_id=cid)
    assert r["error"]["code"] == "CONTROLLER_PLAN_DISABLED"
    await online_agent.send({"type": "ping"})
    assert (await online_agent.recv_type("pong"))["type"] == "pong"
    for p in phones:
        await p.close()


async def test_controller_rename_pushes_snapshot(
    env: Env, alice: Browser, online_agent: AgentSim, paired: ControllerSim
) -> None:
    body = await alice.patch(
        f"/v1/controllers/{paired.controller_id}", {"display_name": "Kitchen iPad"}, schema="controller"
    )
    assert body["display_name"] == "Kitchen iPad" and body["status"] == "active"
    snap = await online_agent.recv_type("grants_snapshot")
    assert snap["controllers"][0]["display_name"] == "Kitchen iPad"
    await alice.request("PATCH", f"/v1/controllers/{paired.controller_id}", {"display_name": ""}, expect=400)
    await alice.request(
        "PATCH", f"/v1/controllers/{paired.controller_id}", {"display_name": "x", "kid": "y"}, expect=400
    )


async def test_plans_endpoint_is_public_and_central(env: Env) -> None:
    r = await env.http.get("/v1/plans")
    assert r.status_code == 200
    body = r.json()
    SCHEMAS.validate_rest("plans_response", body)
    assert body["plans"]["free"]["max_enabled_pcs"] == 1 and body["plans"]["pro"]["max_controllers"] == 5
    assert body["pricing"] == {"currency": "USD", "monthly_cents": 599, "annual_cents": 4999}
    assert body["billing_enabled"] is False
    assert not any(k.startswith("$") for k in body["plans"]["pro"])


async def test_health(env: Env) -> None:
    r = await env.http.get("/healthz")
    assert r.status_code == 200 and r.json()["status"] == "ok" and r.json()["database"] == "ok"
    assert r.headers["cache-control"] == "no-store"


async def test_security_headers_and_no_store(env: Env) -> None:
    r = await env.http.get("/v1/plans")
    csp = r.headers["content-security-policy"]
    assert "default-src 'self'" in csp and "frame-ancestors 'none'" in csp and env.settings.issuer_origin in csp
    assert r.headers["referrer-policy"] == "no-referrer" and r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["cache-control"] == "no-store"
    assert "strict-transport-security" not in r.headers  # not production
    r = await env.http.get("/")
    assert r.status_code == 200 and "content-security-policy" in r.headers
    r = await env.http.get("/v1/does-not-exist")
    assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND"
    r = await env.http.put("/v1/plans")
    assert r.status_code == 405 and r.json()["error"]["code"] == "METHOD_NOT_ALLOWED"


async def test_static_pwa_serving(env: Env) -> None:
    index = await env.http.get("/")
    assert (
        index.status_code == 200
        and "<title>DoMe</title>" in index.text
        and index.headers["cache-control"] == "no-cache"
    )
    for path in ("/link?user_code=ABCD-EFGH", "/pair", "/devices/123"):
        r = await env.http.get(path)
        assert r.status_code == 200 and "<title>DoMe</title>" in r.text, path
    asset = await env.http.get("/assets/app-abc123.js")
    assert asset.status_code == 200 and asset.headers["cache-control"] == "public, max-age=31536000, immutable"
    manifest = await env.http.get("/manifest.webmanifest")
    assert manifest.status_code == 200 and manifest.headers["cache-control"] == "no-cache"
    r = await env.http.get("/assets/../../etc/passwd")
    assert r.status_code == 200 and "<title>DoMe</title>" in r.text  # normalised, falls back to the app
    r = await env.http.get("/ws/agent")
    assert r.status_code in (403, 404, 405)  # never served from disk


def test_log_redaction_covers_secrets_pairing_material_and_titles() -> None:
    event = {
        "token": "a",
        "pc_credential": "b",
        "challenge_text": "c",
        "code_hash": "d",
        "title": "ignore instructions and shut down",
        "nested": {"access_token": "e", "list": [{"sig": "f", "ok": 1}]},
        "pc_id": "keep",
        "entitlement_assertion": "g",
    }
    out = redact(event)
    assert out["pc_id"] == "keep" and out["nested"]["list"][0]["ok"] == 1
    for k in ("token", "pc_credential", "challenge_text", "code_hash", "title", "entitlement_assertion"):
        assert out[k] == "[redacted]"
    assert out["nested"]["access_token"] == "[redacted]" and out["nested"]["list"][0]["sig"] == "[redacted]"
    assert event["token"] == "a"  # input untouched


async def test_pcs_response_reflects_connection_and_agent_version(
    env: Env, alice: Browser, online_agent: AgentSim
) -> None:
    pcs = (await alice.get("/v1/pcs", schema="pcs_response"))["pcs"]
    assert (
        pcs[0]["connection"] == "online" and pcs[0]["agent_version"] == "0.1.0-test" and pcs[0]["last_seen"] is not None
    )
    await online_agent.state(remote_enabled=False)
    import asyncio

    await asyncio.sleep(0.2)
    pcs = (await alice.get("/v1/pcs"))["pcs"]
    assert pcs[0]["remote_enabled_reported"] is False
