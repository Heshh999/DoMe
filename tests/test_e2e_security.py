"""Spec §17.5–8 and §17.13 against the real relay + real agent: tenancy, pairing, revocation,
replay/expiry/target rules, and plan gates that client state cannot unlock."""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

import pytest
from dome_agent.testing.fake_extension import FakeTab
from dome_protocol import dumps_compact, format_rfc3339, now_utc, pairing_code_handle, sign_payload

from conftest import (
    ALL_CAPS,
    MEDIA_CAPS,
    SCHEMAS,
    Browser,
    ControllerSim,
    Env,
    RealAgent,
    confirmation_envelope,
    drain_subscribe,
    run_command,
    sql,
    wait_pairing_state,
    youtube_target,
)


async def test_two_accounts_cannot_see_or_control_each_other(env: Env, agent: RealAgent, phone: Any, bob: Browser) -> None:
    # Bob cannot even list Alice's PC
    pcs = await bob.get("/v1/pcs", schema="pcs_response")
    assert all(pc["id"] != agent.pc_id for pc in pcs["pcs"])
    assert (await bob.request("GET", f"/v1/pcs/{agent.pc_id}/grants")).status_code == 404
    assert (await bob.request("PATCH", f"/v1/pcs/{agent.pc_id}", {"name": "pwned"})).status_code == 404
    assert (await bob.request("DELETE", f"/v1/controllers/{phone.controller_id}")).status_code == 404

    # Bob's phone (never paired with Alice's PC) gets a socket but no identity on it
    bobs = ControllerSim(env, bob, name="Bob's phone")
    ack = await bobs.connect()
    assert "controller_id" not in ack
    await bobs.subscribe(agent.pc_id)
    err = await bobs.recv_type("error")
    assert err["error"]["code"] == "GRANT_MISSING" and err["ref_pc_id"] == agent.pc_id
    cid = await bobs.command(agent.pc_id, "windows.set_volume", {"value": 5}, account_id=bob.account_id)
    res = await bobs.recv_type("result", command_id=cid)
    assert res["origin"] == "relay" and res["state"] == "failed" and res["error"]["code"] == "GRANT_MISSING"
    await bobs.close()

    # Bob cannot claim Alice's pairing code even if he intercepts it
    started = await agent.start_pairing()
    r = await bob.request(
        "POST",
        "/v1/pairing/claim",
        {
            "code_hash": pairing_code_handle(started["code"]),
            "public_jwk": bobs.jwk,
            "display_name": "Bob's phone",
            "requested_capabilities": ["media"],
        },
    )
    assert r.status_code >= 400 and r.json()["error"]["code"] == "PAIRING_CODE_INVALID"
    # Alice's PC never saw a request for it
    status = await agent.control("pair_status")
    assert status["pending"] == []

    # Bob cannot forge Alice's controller_id with his own key: binding is enforced at the relay
    forged = ControllerSim(env, bob, name="forger")
    await forged.connect()
    cid, env_ = forged.envelope(agent.pc_id, "windows.set_volume", {"value": 1}, controller_id=phone.controller_id, account_id=agent.account_id)
    await forged.send_envelope(agent.pc_id, env_)
    res = await forged.recv_type("result", command_id=cid)
    assert res["state"] == "failed" and res["error"]["code"] in ("GRANT_MISSING", "CONTROLLER_MISMATCH", "UNKNOWN_KEY")
    await forged.close()

    # Alice's own PC still works
    res = await run_command(phone, agent.pc_id, "system.ping")
    assert res["state"] == "succeeded"


async def test_pairing_requires_pc_approval_and_codes_are_single_use(env: Env, alice: Browser, agent: RealAgent) -> None:
    ctrl = ControllerSim(env, alice, name="Alice's iPad")
    started = await agent.start_pairing()
    code = started["code"]
    claimed = await ctrl.claim(code, MEDIA_CAPS)
    assert claimed["state"] == "claimed"
    # no grant exists before the PC owner approves
    controllers = await alice.get("/v1/controllers", schema="controllers_response")
    assert all(c["kid"] != ctrl.kid for c in controllers["controllers"])
    pending = await agent.wait_pending(started["pairing_id"])
    assert set(pending["requested_capabilities"]) == set(MEDIA_CAPS)
    # the PC owner may grant a subset
    await agent.control("pair_approve", pairing_id=started["pairing_id"], capabilities=["status", "media"])
    final = await wait_pairing_state(alice, started["pairing_id"], "approved")
    assert sorted(final["granted_capabilities"]) == ["media", "status"]
    ctrl.controller_id = final["controller_id"]

    # the same code cannot be claimed again (by anyone, even the same account with another key)
    other = ControllerSim(env, alice, name="Second claim")
    r = await alice.request(
        "POST",
        "/v1/pairing/claim",
        {"code_hash": pairing_code_handle(code), "public_jwk": other.jwk, "display_name": "x", "requested_capabilities": ["media"]},
    )
    assert r.status_code >= 400 and r.json()["error"]["code"] == "PAIRING_CODE_INVALID"

    # a grant without `volume` is enforced by the PC independently of the relay
    await ctrl.connect()
    res = await run_command(ctrl, agent.pc_id, "windows.set_volume", {"value": 50})
    assert res["state"] == "failed" and res["error"]["code"] == "GRANT_MISSING"
    res = await run_command(ctrl, agent.pc_id, "system.ping")
    assert res["state"] == "succeeded"
    await ctrl.close()


async def test_expired_code_is_rejected(env: Env, alice: Browser, agent: RealAgent, database_url: str) -> None:
    ctrl = ControllerSim(env, alice, name="Late phone")
    started = await agent.start_pairing()
    sql(database_url, "UPDATE pairing_sessions SET expires_at = now() - interval '1 minute' WHERE id = %s", (started["pairing_id"],))
    r = await alice.request(
        "POST",
        "/v1/pairing/claim",
        {"code_hash": pairing_code_handle(started["code"]), "public_jwk": ctrl.jwk, "display_name": "Late", "requested_capabilities": ["media"]},
    )
    assert r.status_code >= 400 and r.json()["error"]["code"] == "PAIRING_CODE_INVALID"


async def test_declined_pairing_grants_nothing(env: Env, alice: Browser, agent: RealAgent) -> None:
    ctrl = ControllerSim(env, alice, name="Declined phone")
    started = await agent.start_pairing()
    await ctrl.claim(started["code"], MEDIA_CAPS)
    await agent.wait_pending(started["pairing_id"])
    await agent.control("pair_decline", pairing_id=started["pairing_id"])
    final = await wait_pairing_state(alice, started["pairing_id"], "declined")
    assert "controller_id" not in final
    await ctrl.connect()
    cid = await ctrl.command(agent.pc_id, "system.ping")
    res = await ctrl.recv_type("result", command_id=cid)
    assert res["state"] == "failed" and res["error"]["code"] == "GRANT_MISSING"
    await ctrl.close()


async def test_claim_while_pc_offline_is_delivered_on_reconnect(env: Env, alice: Browser, agent: RealAgent) -> None:
    """The PC shows a code, then loses its connection; the phone claims anyway; the request is
    delivered when the PC comes back (the agent process — and its pairing session — stays alive)."""
    ctrl = ControllerSim(env, alice, name="Patient phone")
    started = await agent.start_pairing()
    await agent.displace()
    claimed = await ctrl.claim(started["code"], MEDIA_CAPS)
    assert claimed["state"] == "claimed" and claimed["pc_online"] is False
    await agent.resume()
    pending = await agent.wait_pending(started["pairing_id"])
    assert pending["display_name"] == "Patient phone"
    await agent.control("pair_approve", pairing_id=started["pairing_id"])
    final = await wait_pairing_state(alice, started["pairing_id"], "approved")
    ctrl.controller_id = final["controller_id"]
    await ctrl.connect()
    res = await run_command(ctrl, agent.pc_id, "system.ping")
    assert res["state"] == "succeeded"
    await ctrl.close()


async def test_account_revocation_terminates_live_control(env: Env, alice: Browser, agent: RealAgent, phone: Any) -> None:
    res = await run_command(phone, agent.pc_id, "system.ping")
    assert res["state"] == "succeeded"
    await alice.delete(f"/v1/controllers/{phone.controller_id}")
    revoked = await phone.recv_type("revoked")
    assert revoked["reason"] == "controller_revoked"
    with pytest.raises(Exception):
        await phone.recv()  # the socket is closed
    # the PC also refuses the key now (snapshot intersection), even over a fresh socket
    again = ControllerSim(env, alice, name="same phone")
    again.key, again.controller_id = phone.key, phone.controller_id
    ack = await again.connect()
    assert "controller_id" not in ack
    cid = await again.command(agent.pc_id, "system.ping")
    res = await again.recv_type("result", command_id=cid)
    assert res["state"] == "failed" and res["error"]["code"] in ("GRANT_MISSING", "CONTROLLER_REVOKED")
    await again.close()
    grants = await agent.control("status")
    assert all(g["revoked_at"] for g in grants["grants"] if g["kid"] == phone.kid)


async def test_local_emergency_disable_and_local_revoke(env: Env, alice: Browser, agent: RealAgent, phone: Any) -> None:
    await agent.control("disable")
    res = await run_command(phone, agent.pc_id, "system.ping")
    assert res["origin"] == "agent" and res["state"] == "failed" and res["error"]["code"] == "PC_REMOTE_DISABLED"
    # nothing remote can re-enable it: the account toggle does not exist, only the PC does
    await agent.control("enable")
    res = await run_command(phone, agent.pc_id, "system.ping")
    assert res["state"] == "succeeded"

    # local revocation from the PC revokes this PC's grant at the account and closes the phone's socket
    await agent.control("revoke", controller_id=phone.controller_id)
    revoked = await phone.recv_type("revoked", timeout=10)
    assert revoked["reason"] in ("controller_revoked", "grant_revoked")
    grants = await alice.get(f"/v1/pcs/{agent.pc_id}/grants", schema="grants_response")
    assert all(g["controller_id"] != phone.controller_id for g in grants["grants"])
    # the installation itself still exists (it may hold grants on other PCs) — not a deletion
    controllers = await alice.get("/v1/controllers", schema="controllers_response")
    assert any(c["id"] == phone.controller_id for c in controllers["controllers"])
    status = await agent.control("status")
    assert all(g["revoked_at"] for g in status["grants"] if g["controller_id"] == phone.controller_id)


async def test_replay_duplicate_expiry_and_confirmation_rules(env: Env, agent: RealAgent, phone: Any) -> None:
    # identical re-send → the previous result, no second execution
    cid = await phone.command(agent.pc_id, "windows.set_volume", {"value": 42})
    first = await phone.recv_type("result", command_id=cid)
    assert first["state"] == "succeeded"
    # resend the very same envelope bytes
    cid2, env_ = phone.envelope(agent.pc_id, "windows.set_volume", {"value": 43}, command_id=cid)
    await phone.send_envelope(agent.pc_id, env_)
    reused = await phone.recv_type("result", command_id=cid)
    assert reused["state"] == "failed" and reused["error"]["code"] == "COMMAND_ID_REUSED"
    res = await run_command(phone, agent.pc_id, "windows.get_volume")
    assert res["result"]["value"] == 42  # the second value never applied

    # expired command
    res = await run_command(phone, agent.pc_id, "system.ping", issued_at=now_utc() - timedelta(seconds=120))
    assert res["state"] == "failed" and res["error"]["code"] == "COMMAND_EXPIRED" and res["error"]["retryable"] is True

    # unknown action / out-of-range params never reach the PC
    cid, env_ = phone.envelope(agent.pc_id, "windows.set_volume", {"value": 150})
    await phone.send_envelope(agent.pc_id, env_)
    res = await phone.recv_type("result", command_id=cid)
    assert res["origin"] == "relay" and res["error"]["code"] == "INVALID_PARAMETERS"

    # confirmation with a tampered digest is refused and the action does not run
    cid = await phone.command(agent.pc_id, "power.sleep", {"countdown_seconds": 5}, lifetime=90)
    req = await phone.recv_type("confirmation_required", command_id=cid, timeout=10)
    challenge = SCHEMAS.validate_challenge_text(req["challenge_text"])
    assert challenge["action"] == "power.sleep" and challenge["pc_id"] == agent.pc_id
    bad = confirmation_envelope(phone, agent.pc_id, cid, req["challenge_text"], digest="A" * 43)
    await phone.send_envelope(agent.pc_id, bad, kind="confirmation")
    res = await phone.recv_type("result", command_id=cid, timeout=10)
    assert res["state"] == "failed" and res["error"]["code"] == "CONFIRMATION_INVALID"
    status = await agent.control("status")
    assert status["pending_power"] is None


async def test_client_state_cannot_unlock_pro(env: Env, agent: RealAgent, phone: Any) -> None:
    """A routine step on a Free account is refused by the PC regardless of what the client claims."""
    now = now_utc()
    payload = {
        "type": "command",
        "protocol_version": "1.0",
        "command_id": str(uuid.uuid4()),
        "account_id": agent.account_id,
        "controller_id": phone.controller_id,
        "target_pc_id": agent.pc_id,
        "action": "windows.set_volume",
        "params": {"value": 20},
        "target": None,
        "origin": {"kind": "routine", "routine_id": str(uuid.uuid4()), "step": 0},
        "issued_at": format_rfc3339(now),
        "expires_at": format_rfc3339(now + timedelta(seconds=30)),
        "nonce": "AAAAAAAAAAAAAAAAAAAAAA",
    }
    env_ = sign_payload(phone.key, dumps_compact(payload)).to_dict()
    await phone.send_envelope(agent.pc_id, env_)
    res = await phone.recv_type("result", command_id=payload["command_id"])
    assert res["state"] == "failed" and res["error"]["code"] == "ENTITLEMENT_REQUIRED"
    res = await run_command(phone, agent.pc_id, "windows.get_volume")
    assert res["result"]["value"] != 20
    session = await phone.browser.get("/v1/session", schema="session_response")
    assert session["plan"] == "free" and session["limits"]["routines"] is False


async def test_second_pc_is_limited_on_free(env: Env, alice: Browser, agent: RealAgent) -> None:
    """Free allows one enabled PC; a second link succeeds but stays disabled (settings kept)."""
    second = RealAgent(env, alice, name="Bedroom PC")
    try:
        approve = await second.link()
        assert approve["enabled"] is False and approve["reason"] == "DEVICE_LIMIT_REACHED"
        await second.start(wait_connected=True)
        ctrl = ControllerSim(env, alice, name="Alice's iPhone 2")
        await second.pair(ctrl, MEDIA_CAPS)
        await ctrl.connect()
        cid = await ctrl.command(second.pc_id, "system.ping")
        res = await ctrl.recv_type("result", command_id=cid)
        assert res["state"] == "failed" and res["error"]["code"] == "PC_PLAN_DISABLED"
        await ctrl.close()
        pcs = await alice.get("/v1/pcs", schema="pcs_response")
        mine = {pc["id"]: pc for pc in pcs["pcs"]}
        assert mine[agent.pc_id]["enabled"] is True and mine[second.pc_id]["enabled"] is False
    finally:
        await second.stop()
        second.cleanup()
