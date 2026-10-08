"""Device-code shaped PC linking: happy path, denial, expiry, device limit, single-use poll,
credential → token, Origin-absent rule for agent endpoints."""

from __future__ import annotations

import datetime as dt

from dome_protocol import generate_private_key, jwk_from_public_key

from tests.conftest import AgentSim, Browser, Env, sql


async def test_link_happy_path_then_token(env: Env, alice: Browser) -> None:
    agent = AgentSim(env)
    linked = await agent.link(alice, name="Living room")
    assert linked["enabled"] is True and linked["pc_name"] == "Living room"
    assert linked["account_id"] == alice.account_id
    assert linked["relay_url"].startswith("ws://") and linked["relay_url"].endswith("/ws/agent")
    assert linked["start"]["verification_uri_complete"] == f"{env.origin}/link?user_code={linked['start']['user_code']}"
    assert linked["start"]["expires_in"] == 600 and linked["start"]["interval"] == 5
    # the credential can be exchanged repeatedly; the device code only once
    assert await agent.fetch_token()
    again = await agent.rest("POST", "/v1/agent-link/poll", {"device_code": linked["start"]["device_code"]}, expect=410)
    assert again.json()["error"]["code"] == "LINK_EXPIRED"
    pcs = await alice.get("/v1/pcs", schema="pcs_response")
    assert [p["id"] for p in pcs["pcs"]] == [agent.pc_id]
    assert pcs["pcs"][0]["connection"] == "offline" and pcs["pcs"][0]["platform"] == "development"
    kinds = {e["kind"] for e in (await alice.get("/v1/account/security-events"))["events"]}
    assert {"pc_linked", "pc_credential_issued"} <= kinds


async def test_only_hashes_are_stored(env: Env, alice: Browser) -> None:
    agent = AgentSim(env)
    linked = await agent.link(alice)
    rows = sql(env.database_url, "SELECT device_code_hash FROM device_link_codes WHERE pc_id = %s", (agent.pc_id,))
    assert rows and isinstance(rows[0][0], (bytes, memoryview))
    assert linked["start"]["device_code"].encode() not in bytes(rows[0][0])
    creds = sql(env.database_url, "SELECT credential_hash FROM pc_credentials WHERE pc_id = %s", (agent.pc_id,))
    assert creds and agent.credential.encode() not in bytes(creds[0][0])


async def test_link_denied(env: Env, alice: Browser) -> None:
    agent = AgentSim(env)
    start = (
        await agent.rest(
            "POST",
            "/v1/agent-link/start",
            {"pc_public_jwk": agent.jwk, "agent_version": "t", "platform": "development"},
            schema="agent_link_start_response",
            expect=200,
        )
    ).json()
    await alice.request("POST", f"/v1/agent-link/{start['user_code']}/deny", expect=204)
    r = await agent.rest("POST", "/v1/agent-link/poll", {"device_code": start["device_code"]}, expect=410)
    assert r.json()["error"]["code"] == "LINK_DENIED"
    await alice.request("GET", f"/v1/agent-link/{start['user_code']}", expect=410)


async def test_link_expired(env: Env, alice: Browser) -> None:
    agent = AgentSim(env)
    start = (
        await agent.rest(
            "POST",
            "/v1/agent-link/start",
            {"pc_public_jwk": agent.jwk, "agent_version": "t", "platform": "development"},
            expect=200,
        )
    ).json()
    sql(
        env.database_url,
        "UPDATE device_link_codes SET expires_at = %s WHERE user_code = %s",
        (dt.datetime.now(dt.UTC) - dt.timedelta(seconds=1), start["user_code"]),
    )
    await alice.request("GET", f"/v1/agent-link/{start['user_code']}", expect=410)
    r = await agent.rest("POST", "/v1/agent-link/poll", {"device_code": start["device_code"]}, expect=410)
    assert r.json()["error"]["code"] == "LINK_EXPIRED"
    body = {"pc_name": "late", "remote_enabled": True}
    await alice.request("POST", f"/v1/agent-link/{start['user_code']}/approve", body, expect=410)


async def test_user_code_is_case_insensitive_and_unknown_is_404(env: Env, alice: Browser) -> None:
    agent = AgentSim(env)
    start = (
        await agent.rest(
            "POST",
            "/v1/agent-link/start",
            {"pc_public_jwk": agent.jwk, "agent_version": "t", "platform": "development"},
            expect=200,
        )
    ).json()
    lowered = start["user_code"].lower().replace("-", "")
    preview = await alice.get(f"/v1/agent-link/{lowered}", schema="agent_link_preview_response")
    assert preview["user_code"] == start["user_code"]
    await alice.request("GET", "/v1/agent-link/ZZZZ-ZZZZ", expect=404)
    await alice.request("GET", "/v1/agent-link/not-a-code!", expect=404)


async def test_second_pc_exceeds_free_limit_but_is_created_disabled(env: Env, alice: Browser) -> None:
    first = AgentSim(env)
    await first.link(alice, "One")
    second = AgentSim(env)
    linked = await second.link(alice, "Two")
    assert linked["enabled"] is False and linked["approve"]["reason"] == "DEVICE_LIMIT_REACHED"
    pcs = {p["name"]: p["enabled"] for p in (await alice.get("/v1/pcs"))["pcs"]}
    assert pcs == {"One": True, "Two": False}
    # enabling it explicitly is refused transactionally while the first stays enabled
    await alice.request("PATCH", f"/v1/pcs/{second.pc_id}", {"enabled": True}, expect=403)
    # disable the first, then the second may be enabled
    await alice.patch(f"/v1/pcs/{first.pc_id}", {"enabled": False}, schema="pc")
    body = await alice.patch(f"/v1/pcs/{second.pc_id}", {"enabled": True}, schema="pc")
    assert body["enabled"] is True


async def test_agent_endpoints_refuse_browser_origins(env: Env, alice: Browser) -> None:
    jwk = jwk_from_public_key(generate_private_key().public_key())
    r = await env.http.post(
        "/v1/agent-link/start",
        json={"pc_public_jwk": jwk, "agent_version": "t", "platform": "development"},
        headers={"Origin": env.origin},
    )
    assert r.status_code == 403 and r.json()["error"]["code"] == "FORBIDDEN"
    r = await env.http.post("/v1/agent/token", json={"pc_credential": "x" * 43}, headers={"Origin": env.origin})
    assert r.status_code == 403


async def test_start_rejects_bad_bodies(env: Env) -> None:
    r = await env.http.post(
        "/v1/agent-link/start",
        content=b'{"pc_public_jwk": 1, "agent_version": "t", "platform": "development"}',
        headers={"content-type": "application/json"},
    )
    assert r.status_code == 400 and r.json()["error"]["code"] == "MALFORMED_MESSAGE"
    r = await env.http.post(
        "/v1/agent-link/start", content=b'{"a":1,"a":2}', headers={"content-type": "application/json"}
    )
    assert r.status_code == 400
    jwk = jwk_from_public_key(generate_private_key().public_key())
    r = await env.http.post(
        "/v1/agent-link/start", json={"pc_public_jwk": jwk, "agent_version": "t", "platform": "development", "extra": 1}
    )
    assert r.status_code == 400
    bad_point = dict(jwk, y="A" * 43)
    r = await env.http.post(
        "/v1/agent-link/start", json={"pc_public_jwk": bad_point, "agent_version": "t", "platform": "development"}
    )
    assert r.status_code == 400
    r = await env.http.post("/v1/agent-link/start", content=b"{}", headers={"content-type": "text/plain"})
    assert r.status_code == 400


async def test_token_with_unknown_or_revoked_credential_is_401(env: Env, alice: Browser) -> None:
    r = await env.http.post("/v1/agent/token", json={"pc_credential": "A" * 43})
    assert r.status_code == 401 and r.json()["error"]["code"] == "UNAUTHENTICATED"
    agent = AgentSim(env)
    await agent.link(alice)
    await alice.delete(f"/v1/pcs/{agent.pc_id}")
    r = await agent.rest("POST", "/v1/agent/token", {"pc_credential": agent.credential}, expect=401)
    r = await agent.rest("POST", "/v1/agent/entitlement", bearer=True, expect=401)
    assert r.json()["error"]["code"] == "UNAUTHENTICATED"


async def test_relink_after_unlink_reuses_row_without_grants(env: Env, alice: Browser) -> None:
    agent = AgentSim(env)
    await agent.link(alice, "Desk")
    old_credential = agent.credential
    await alice.delete(f"/v1/pcs/{agent.pc_id}")
    again = await agent.link(alice, "Desk again")
    assert again["pc_id"] == agent.pc_id and again["pc_name"] == "Desk again"
    assert (await alice.get("/v1/pcs"))["pcs"][0]["name"] == "Desk again"
    assert (await alice.get(f"/v1/pcs/{agent.pc_id}/grants", schema="grants_response"))["grants"] == []
    r = await env.http.post("/v1/agent/token", json={"pc_credential": old_credential})
    assert r.status_code == 401


async def test_copied_public_key_cannot_take_over_a_linked_pc(env: Env, alice: Browser) -> None:
    """Link start carries no proof of possession, so a code naming the key of a PC that is still linked is
    refused at approval: the real PC keeps its credential, socket and grants."""
    victim = AgentSim(env)
    await victim.link(alice, "Real PC")
    await victim.connect()
    impostor = AgentSim(env, key=victim.key)  # knows only the public half in practice; same JWK on the wire
    start = (
        await impostor.rest(
            "POST",
            "/v1/agent-link/start",
            {"pc_public_jwk": victim.jwk, "agent_version": "t", "platform": "development"},
            expect=200,
        )
    ).json()
    r = await alice.request(
        "POST", f"/v1/agent-link/{start['user_code']}/approve", {"pc_name": "taken", "remote_enabled": True}, expect=409
    )
    assert r.json()["error"]["code"] == "PC_ALREADY_LINKED"
    # nothing was handed out, the code stays pending, the real PC is untouched
    pending = await impostor.rest("POST", "/v1/agent-link/poll", {"device_code": start["device_code"]}, expect=428)
    assert pending.json()["status"] in ("authorization_pending", "slow_down")
    assert await victim.fetch_token()
    pcs = (await alice.get("/v1/pcs"))["pcs"]
    assert [(p["name"], p["connection"]) for p in pcs] == [("Real PC", "online")]
    await victim.send({"type": "ping"})
    assert (await victim.recv())["type"] == "pong"
    await victim.close()


async def test_pc_key_linked_to_another_account_is_refused(env: Env, alice: Browser, bob: Browser) -> None:
    agent = AgentSim(env)
    await agent.link(alice)
    start = (
        await agent.rest(
            "POST",
            "/v1/agent-link/start",
            {"pc_public_jwk": agent.jwk, "agent_version": "t", "platform": "development"},
            expect=200,
        )
    ).json()
    await bob.request(
        "POST",
        f"/v1/agent-link/{start['user_code']}/approve",
        {"pc_name": "stolen", "remote_enabled": True},
        expect=409,
    )
