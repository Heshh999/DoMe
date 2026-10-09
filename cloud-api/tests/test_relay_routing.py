"""Relay: hello/kid binding, subscribe semantics, command routing acceptance and every rejection rule
(each surfaced as a relay-origin result), cross-account isolation, duplicates, confirmation forwarding."""

from __future__ import annotations

import asyncio
import datetime as dt
import secrets
import uuid
from typing import Any

from dome_protocol import (
    KeyRecord,
    challenge_digest,
    dumps_compact,
    format_rfc3339,
    loads_strict,
    now_utc,
    sign_payload,
    verify_and_parse_command,
    verify_and_parse_confirmation,
)
from dome_protocol.keys import b64url_encode
from websockets.exceptions import InvalidStatus

from tests.conftest import (
    REGISTRY,
    AgentSim,
    Browser,
    ControllerSim,
    Env,
    close_code,
    expect_nothing,
    free_port,
    recv_frame,
)


async def test_hello_binds_kid_and_subscribe_delivers_status_and_cached_state(
    env: Env, alice: Browser, online_agent: AgentSim, paired: ControllerSim
) -> None:
    ack = await paired.connect()  # second socket for the same controller
    assert ack["controller_id"] == paired.controller_id
    state = await online_agent.state(volume={"value": 33, "muted": False})
    await asyncio.sleep(0.1)
    await paired.subscribe(online_agent.pc_id)
    status = await paired.recv()
    assert (
        status["type"] == "pc_status"
        and status["pc_id"] == online_agent.pc_id
        and status["connection"] == "online"
        and status["enabled"] is True
    )
    cached = await paired.recv()
    assert cached["type"] == "state" and cached["at"] == state["at"] and cached["state"]["volume"]["value"] == 33
    # live state frames reach subscribers
    live = await online_agent.state(volume={"value": 34, "muted": True})
    frame = await paired.recv_type("state")
    assert frame["at"] == live["at"]
    # subscribe is idempotent and replaces the set
    await paired.subscribe(online_agent.pc_id)
    assert (await paired.recv())["type"] == "pc_status"


async def test_hello_without_known_kid_cannot_subscribe_or_command(
    env: Env, alice: Browser, online_agent: AgentSim
) -> None:
    stranger = ControllerSim(env, alice)  # same account, never paired
    ack = await stranger.connect()
    assert "controller_id" not in ack
    await stranger.subscribe(online_agent.pc_id)
    err = await stranger.recv()
    assert err["type"] == "error" and err["error"]["code"] == "GRANT_MISSING" and err["ref_pc_id"] == online_agent.pc_id
    cid = await stranger.command(online_agent.pc_id, "system.ping")
    result = await stranger.recv_type("result", command_id=cid)
    assert result["origin"] == "relay" and result["state"] == "failed" and result["error"]["code"] == "GRANT_MISSING"
    await stranger.send({"type": "cancel", "pc_id": online_agent.pc_id, "command_id": cid})
    err = await stranger.recv()
    assert err["type"] == "error" and err["error"]["code"] == "GRANT_MISSING"
    await stranger.close()


async def test_hello_is_required_and_validated(env: Env, alice: Browser) -> None:
    ctrl = ControllerSim(env, alice)
    from websockets.asyncio.client import connect

    ws = await connect(
        env.ws_base + "/ws/controller",
        additional_headers={"Origin": env.origin, "Cookie": f"dome_session={alice.session_cookie}"},
    )
    await ws.send(dumps_compact({"type": "subscribe", "pc_ids": [str(uuid.uuid4())]}))
    err = await recv_frame(ws, "relay_to_controller")
    assert err["type"] == "error" and err["error"]["code"] == "MALFORMED_MESSAGE"
    assert await close_code(ws) == 4000
    # controller hello without kid
    ws = await connect(
        env.ws_base + "/ws/controller",
        additional_headers={"Origin": env.origin, "Cookie": f"dome_session={alice.session_cookie}"},
    )
    await ws.send(
        dumps_compact(
            {
                "type": "hello",
                "component": "controller",
                "component_version": "t",
                "protocol_versions": ["1.0"],
                "registry_version": "1.0",
            }
        )
    )
    err = await recv_frame(ws, "relay_to_controller")
    assert err["error"]["code"] == "MALFORMED_MESSAGE"
    assert await close_code(ws) == 4000
    # incompatible protocol
    ws = await connect(
        env.ws_base + "/ws/controller",
        additional_headers={"Origin": env.origin, "Cookie": f"dome_session={alice.session_cookie}"},
    )
    await ws.send(
        dumps_compact(
            {
                "type": "hello",
                "component": "controller",
                "kid": ctrl.kid,
                "component_version": "t",
                "protocol_versions": ["2.0"],
                "registry_version": "1.0",
            }
        )
    )
    err = await recv_frame(ws, "relay_to_controller")
    assert err["error"]["code"] == "PROTOCOL_INCOMPATIBLE" and err["error"]["detail"]["supported"] == ["1.1"]
    assert await close_code(ws) == 4000


async def test_controller_socket_requires_session_and_exact_origin(env: Env, alice: Browser) -> None:
    ctrl = ControllerSim(env, alice)
    for kwargs, status in (
        ({"origin": "https://evil.example"}, 403),
        ({"cookie": False}, 401),
        ({"origin": env.origin.upper()}, 200),
    ):
        try:
            await ctrl.connect(**kwargs)
        except InvalidStatus as exc:
            assert exc.response.status_code == status, kwargs
        else:
            assert status == 200, kwargs  # origin comparison is case-insensitive on scheme/host
            await ctrl.close()


async def test_agent_socket_auth_rules(env: Env, alice: Browser, linked_agent: AgentSim) -> None:
    from websockets.asyncio.client import connect

    for headers, status in (
        ({}, 401),
        ({"Authorization": "Bearer " + "A" * 43}, 401),
        ({"Authorization": f"Bearer {linked_agent.token}", "Origin": env.origin}, 403),
    ):
        try:
            await connect(env.ws_base + "/ws/agent", additional_headers=headers)
        except InvalidStatus as exc:
            assert exc.response.status_code == status, headers
        else:
            raise AssertionError(f"upgrade accepted with {headers}")
    try:
        await connect(env.ws_base + f"/ws/agent?access_token={linked_agent.token}")
    except InvalidStatus as exc:
        assert exc.response.status_code == 401


async def test_command_accepted_end_to_end(
    env: Env, alice: Browser, online_agent: AgentSim, paired: ControllerSim
) -> None:
    cid = await paired.command(online_agent.pc_id, "system.ping")
    cmd = await online_agent.recv_type("command")
    assert cmd["relay"]["connection_id"] and cmd["envelope"]["kid"] == paired.kid
    # the PC verifies exactly like the shared library prescribes (key record from its local store)
    verified = verify_and_parse_command(
        cmd["envelope"],
        lambda kid: KeyRecord(paired.controller_id or "", alice.account_id, paired.jwk) if kid == paired.kid else None,
    )
    assert verified.command_id == cid and verified.spec.name == "system.ping"
    await online_agent.ack(cid, "accepted")
    await online_agent.ack(cid, "executing")
    await online_agent.result(cid, "succeeded", result=online_agent.ping_result(), duration_ms=7)
    acks = [await paired.recv(), await paired.recv()]
    assert [a["state"] for a in acks] == ["accepted", "executing"]
    result = await paired.recv()
    assert (
        result["type"] == "result"
        and result["origin"] == "agent"
        and result["state"] == "succeeded"
        and result["duration_ms"] == 7
    )
    REGISTRY.validate_result("system.ping", result["result"])
    rows = (await alice.get(f"/v1/commands?pc_id={online_agent.pc_id}", schema="commands_response"))["commands"]
    assert (
        rows[0]["command_id"] == cid
        and rows[0]["state"] == "succeeded"
        and rows[0]["action"] == "system.ping"
        and rows[0]["duration_ms"] == 7
    )
    assert "params" not in rows[0] and "result" not in rows[0]
    await alice.request("GET", "/v1/commands?pc_id=nope", expect=404)
    # another account sees nothing
    bob = await _fresh_account(env)
    try:
        assert (await bob.get("/v1/commands"))["commands"] == []
    finally:
        await bob.aclose()


async def _fresh_account(env: Env) -> Browser:
    b = Browser(env)
    await b.login(f"user-{secrets.token_hex(6)}@example.test")
    return b


async def _expect_relay_failure(ctrl: ControllerSim, cid: str, code: str) -> dict[str, Any]:
    result = await ctrl.recv_type("result", command_id=cid)
    assert result["origin"] == "relay" and result["state"] == "failed" and result["error"]["code"] == code, result
    return result


async def test_rejection_rules_each_become_a_relay_result(
    env: Env, alice: Browser, online_agent: AgentSim, paired: ControllerSim
) -> None:
    pc = online_agent.pc_id
    # 3. bad signature
    cid, envl = paired.envelope(pc, "system.ping")
    envl["sig"] = envl["sig"][:-2] + ("AA" if envl["sig"][-2:] != "AA" else "BB")
    await paired.send_envelope(pc, envl)
    await _expect_relay_failure(paired, cid, "SIGNATURE_INVALID")
    # 3. CONTROLLER_MISMATCH: payload names another controller of the same account
    second = ControllerSim(env, alice, name="Second phone")
    await second.pair(online_agent)
    cid = await paired.command(pc, "system.ping", controller_id=second.controller_id)
    await _expect_relay_failure(paired, cid, "CONTROLLER_MISMATCH")
    # 3. ACCOUNT_MISMATCH inside the payload
    cid = await paired.command(pc, "system.ping", account_id=str(uuid.uuid4()))
    await _expect_relay_failure(paired, cid, "ACCOUNT_MISMATCH")
    # 3. expired window
    cid = await paired.command(pc, "system.ping", issued_at=now_utc() - dt.timedelta(seconds=120), lifetime=30)
    await _expect_relay_failure(paired, cid, "COMMAND_EXPIRED")
    # 3. unknown action / invalid params / target required
    cid = await paired.command(pc, "system.nope")
    await _expect_relay_failure(paired, cid, "UNKNOWN_ACTION")
    cid = await paired.command(pc, "windows.set_volume", {"value": 250})
    await _expect_relay_failure(paired, cid, "INVALID_PARAMETERS")
    cid = await paired.command(pc, "youtube.next")
    await _expect_relay_failure(paired, cid, "TARGET_REQUIRED")
    # 4. frame pc_id != payload target_pc_id
    other_pc = str(uuid.uuid4())
    cid, envl = paired.envelope(other_pc, "system.ping")
    await paired.send_envelope(pc, envl)
    await _expect_relay_failure(paired, cid, "TARGET_PC_MISMATCH")
    # 5. PC not on this account
    cid = await paired.command(other_pc, "system.ping")
    await _expect_relay_failure(paired, cid, "ACCOUNT_MISMATCH")
    # 6. grant lacks the capability (paired has status/media/volume, not lock)
    cid = await paired.command(pc, "windows.lock")
    await _expect_relay_failure(paired, cid, "GRANT_MISSING")
    # every rejection is a security event
    kinds = [
        e
        for e in (await alice.get("/v1/account/security-events?limit=50"))["events"]
        if e["kind"] == "command_rejected"
    ]
    assert {e["detail"]["reason"] for e in kinds} >= {
        "SIGNATURE_INVALID",
        "CONTROLLER_MISMATCH",
        "GRANT_MISSING",
        "TARGET_PC_MISMATCH",
    }
    await second.close()


async def test_envelope_kid_must_match_socket(
    env: Env, alice: Browser, online_agent: AgentSim, paired: ControllerSim
) -> None:
    other = ControllerSim(env, alice)
    cid, envl = paired.envelope(online_agent.pc_id, "system.ping", key=other.key)  # kid of another key
    await paired.send_envelope(online_agent.pc_id, envl)
    await _expect_relay_failure(paired, cid, "UNKNOWN_KEY")
    assert await close_code(paired.ws) == 4003  # type: ignore[arg-type]
    paired.ws = None


async def test_cross_account_isolation(
    env: Env, alice: Browser, bob: Browser, online_agent: AgentSim, paired: ControllerSim
) -> None:
    bob_agent = AgentSim(env)
    await bob_agent.link(bob, "Bob PC")
    await bob_agent.connect()
    mallory = ControllerSim(env, bob, name="Bob phone")
    await mallory.pair(bob_agent)
    await mallory.connect()
    try:
        # subscribe to alice's PC
        await mallory.subscribe(online_agent.pc_id, bob_agent.pc_id)
        frames = [await mallory.recv(), await mallory.recv()]
        err = next(f for f in frames if f["type"] == "error")
        assert err["error"]["code"] == "GRANT_MISSING" and err["ref_pc_id"] == online_agent.pc_id
        assert next(f for f in frames if f["type"] == "pc_status")["pc_id"] == bob_agent.pc_id
        # command alice's PC (signed by bob's controller, naming bob's account)
        cid = await mallory.command(online_agent.pc_id, "system.ping")
        await _expect_relay_failure(mallory, cid, "ACCOUNT_MISMATCH")
        # ... or forging alice's account id in the payload
        cid = await mallory.command(online_agent.pc_id, "system.ping", account_id=alice.account_id)
        await _expect_relay_failure(mallory, cid, "ACCOUNT_MISMATCH")
        await expect_nothing(online_agent.ws)  # type: ignore[arg-type]
        # REST isolation
        assert [p["id"] for p in (await bob.get("/v1/pcs"))["pcs"]] == [bob_agent.pc_id]
        await bob.request("GET", f"/v1/pcs/{online_agent.pc_id}/grants", expect=404)
        await bob.request("PATCH", f"/v1/pcs/{online_agent.pc_id}", {"name": "mine now"}, expect=404)
        await bob.request("DELETE", f"/v1/pcs/{online_agent.pc_id}", expect=404)
        await bob.request("DELETE", f"/v1/controllers/{paired.controller_id}", expect=404)
        await bob.request("DELETE", f"/v1/grants/{paired.grant_id}", expect=404)
        assert (await bob.get(f"/v1/commands?pc_id={online_agent.pc_id}"))["commands"] == []
        # alice's PC is untouched and still works
        cid = await paired.command(online_agent.pc_id, "system.ping")
        payload = await online_agent.serve_one()
        assert payload["command_id"] == cid
        assert (await paired.recv_type("result", command_id=cid))["state"] == "succeeded"
    finally:
        await mallory.close()
        await bob_agent.close()


async def test_offline_pc_is_never_queued(
    env: Env, alice: Browser, online_agent: AgentSim, paired: ControllerSim
) -> None:
    await paired.subscribe(online_agent.pc_id)
    assert (await paired.recv())["type"] == "pc_status"
    await online_agent.close()
    status = await paired.recv_type("pc_status", pc_id=online_agent.pc_id)
    assert status["connection"] == "offline"
    cid = await paired.command(online_agent.pc_id, "system.ping")
    await _expect_relay_failure(paired, cid, "PC_OFFLINE")
    await online_agent.connect()
    await expect_nothing(online_agent.ws)  # type: ignore[arg-type]  # nothing replayed
    status = await paired.recv_type("pc_status", pc_id=online_agent.pc_id)
    assert status["connection"] == "online"
    assert (await alice.get("/v1/commands"))["commands"] == []  # never forwarded -> no lifecycle row
    events = (await alice.get("/v1/account/security-events?limit=10"))["events"]
    assert any(e["kind"] == "command_rejected" and e["detail"]["reason"] == "PC_OFFLINE" for e in events)


async def test_queue_depth_and_rate_limit(
    env: Env, alice: Browser, online_agent: AgentSim, paired: ControllerSim
) -> None:
    depth = env.settings.relay_per_pc_queue_depth
    burst = 30  # plans.json free.manual_command_rate_limit.burst (refills at 120/min)
    extra = 3  # tolerate up to 3 tokens refilled while the burst is being sent
    total = burst + extra
    cids = [await paired.command(online_agent.pc_id, "system.ping") for _ in range(total)]
    forwarded = 0
    for _ in range(depth):
        await online_agent.recv_type("command")
        forwarded += 1
    results: dict[str, str] = {}
    for _ in range(total - depth):
        r = await paired.recv_type("result")
        results[r["command_id"]] = r["error"]["code"]
    codes = list(results.values())
    assert forwarded == depth and len(results) == total - depth
    assert set(codes) <= {"QUEUE_FULL", "RATE_LIMITED"}
    assert codes.count("RATE_LIMITED") >= 1 and codes.count("QUEUE_FULL") >= burst - depth
    assert results[cids[-1]] == "RATE_LIMITED"  # the very last one is over budget even with refill
    # finishing frees slots; the agent's results flow to the controller
    for cid in cids[:depth]:
        await online_agent.result(cid, "succeeded", result=online_agent.ping_result())
    seen = {(await paired.recv_type("result"))["command_id"] for _ in range(depth)}
    assert seen == set(cids[:depth])


async def test_duplicate_and_reuse(env: Env, alice: Browser, online_agent: AgentSim, paired: ControllerSim) -> None:
    cid, envl = paired.envelope(online_agent.pc_id, "system.ping")
    await paired.send_envelope(online_agent.pc_id, envl)
    await online_agent.serve_one()
    first = await paired.recv_type("result", command_id=cid)
    assert first["state"] == "succeeded"
    # identical bytes → previous result re-emitted, nothing reaches the PC
    await paired.send_envelope(online_agent.pc_id, envl)
    dup = await paired.recv_type("result", command_id=cid)
    assert dup["state"] == "succeeded" and dup["origin"] == "relay" and "warning" in dup
    await expect_nothing(online_agent.ws)  # type: ignore[arg-type]
    # same id, different bytes → COMMAND_ID_REUSED
    _, envl2 = paired.envelope(online_agent.pc_id, "system.ping", command_id=cid)
    await paired.send_envelope(online_agent.pc_id, envl2)
    await _expect_relay_failure(paired, cid, "COMMAND_ID_REUSED")
    assert len((await alice.get("/v1/commands"))["commands"]) == 1


async def test_duplicate_while_running_returns_current_ack(
    env: Env, alice: Browser, online_agent: AgentSim, paired: ControllerSim
) -> None:
    cid, envl = paired.envelope(online_agent.pc_id, "system.ping")
    await paired.send_envelope(online_agent.pc_id, envl)
    await online_agent.recv_type("command")
    await online_agent.ack(cid, "executing")
    assert (await paired.recv_type("ack", command_id=cid))["state"] == "executing"
    await paired.send_envelope(online_agent.pc_id, envl)
    again = await paired.recv_type("ack", command_id=cid)
    assert again["state"] == "executing"
    await online_agent.result(cid, "succeeded", result=online_agent.ping_result())
    assert (await paired.recv_type("result", command_id=cid))["state"] == "succeeded"


def _challenge_text(
    agent: AgentSim, ctrl: ControllerSim, cid: str, action: str = "power.sleep", **overrides: object
) -> str:
    now = now_utc()
    challenge = {
        "challenge_id": str(uuid.uuid4()),
        "command_id": cid,
        "controller_id": ctrl.controller_id,
        "pc_id": agent.pc_id,
        "action": action,
        "params": {"countdown_seconds": 10},
        "target": None,
        "target_state_digest": b64url_encode(secrets.token_bytes(32)),
        "issued_at": format_rfc3339(now),
        "expires_at": format_rfc3339(now + dt.timedelta(seconds=60)),
        "display": {"pc_name": "Desk PC", "action_label": "Sleep", "detail": "Media title (untrusted) <script>"},
    }
    challenge.update(overrides)
    text: str = dumps_compact(challenge)
    return text


async def test_confirmation_transaction_is_forwarded_verbatim(env: Env, alice: Browser, online_agent: AgentSim) -> None:
    ctrl = ControllerSim(env, alice)
    await ctrl.pair(online_agent, ("status", "power"))
    await ctrl.connect()
    try:
        cid = await ctrl.command(online_agent.pc_id, "power.sleep", {"countdown_seconds": 10}, lifetime=90)
        await online_agent.recv_type("command")
        await online_agent.ack(cid, "awaiting_confirmation")
        text = _challenge_text(online_agent, ctrl, cid)
        await online_agent.send({"type": "confirmation_required", "command_id": cid, "challenge_text": text})
        assert (await ctrl.recv_type("ack", command_id=cid))["state"] == "awaiting_confirmation"
        req = await ctrl.recv_type("confirmation_required", command_id=cid)
        assert req["challenge_text"] == text  # byte-identical
        rows = (await alice.get("/v1/commands"))["commands"]
        assert rows[0]["state"] == "awaiting_confirmation"
        # the phone signs a confirmation over the digest of the exact string
        now = now_utc()
        payload = {
            "type": "confirmation",
            "protocol_version": "1.0",
            "command_id": cid,
            "challenge_id": loads_strict(text)["challenge_id"],
            "challenge_digest": challenge_digest(text),
            "controller_id": ctrl.controller_id,
            "target_pc_id": online_agent.pc_id,
            "decision": "approve",
            "issued_at": format_rfc3339(now),
            "expires_at": format_rfc3339(now + dt.timedelta(seconds=60)),
            "nonce": b64url_encode(secrets.token_bytes(16)),
        }
        envl = sign_payload(ctrl.key, dumps_compact(payload)).to_dict()
        await ctrl.send_envelope(online_agent.pc_id, envl, kind="confirmation")
        fwd = await online_agent.recv_type("confirmation")
        assert fwd["envelope"] == envl and fwd["relay"]["connection_id"]
        conf = verify_and_parse_confirmation(
            fwd["envelope"],
            lambda kid: KeyRecord(ctrl.controller_id or "", alice.account_id, ctrl.jwk) if kid == ctrl.kid else None,
        )
        assert conf.approved and conf.command_id == cid
        await online_agent.result(
            cid,
            "succeeded",
            result={
                "accepted": True,
                "countdown_seconds": 10,
                "fires_at": format_rfc3339(now + dt.timedelta(seconds=10)),
            },
            duration_ms=100,
        )
        res = await ctrl.recv_type("result", command_id=cid)
        REGISTRY.validate_result("power.sleep", res["result"])
        pcs = (await alice.get("/v1/pcs"))["pcs"]
        assert pcs[0]["last_power_request"]["action"] == "power.sleep"
    finally:
        await ctrl.close()


async def test_invalid_challenge_text_fails_the_command(env: Env, alice: Browser, online_agent: AgentSim) -> None:
    ctrl = ControllerSim(env, alice)
    await ctrl.pair(online_agent, ("status", "power"))
    await ctrl.connect()
    try:
        cid = await ctrl.command(online_agent.pc_id, "power.sleep", {"countdown_seconds": 5}, lifetime=90)
        await online_agent.recv_type("command")
        # challenge bound to a different command id
        text = _challenge_text(online_agent, ctrl, str(uuid.uuid4()))
        await online_agent.send({"type": "confirmation_required", "command_id": cid, "challenge_text": text})
        cancel = await online_agent.recv_type("cancel", command_id=cid)
        assert cancel["controller_id"] == ctrl.controller_id
        res = await ctrl.recv_type("result", command_id=cid)
        assert res["origin"] == "relay" and res["state"] == "failed" and res["error"]["code"] == "MALFORMED_MESSAGE"
        # a confirmation for a command that is not pending is answered with an error frame
        payload = {
            "type": "confirmation",
            "protocol_version": "1.0",
            "command_id": cid,
            "challenge_id": str(uuid.uuid4()),
            "challenge_digest": challenge_digest(text),
            "controller_id": ctrl.controller_id,
            "target_pc_id": online_agent.pc_id,
            "decision": "approve",
            "issued_at": format_rfc3339(now_utc()),
            "expires_at": format_rfc3339(now_utc() + dt.timedelta(seconds=30)),
            "nonce": b64url_encode(secrets.token_bytes(16)),
        }
        await ctrl.send_envelope(
            online_agent.pc_id, sign_payload(ctrl.key, dumps_compact(payload)).to_dict(), kind="confirmation"
        )
        err = await ctrl.recv_type("error")
        assert err["error"]["code"] == "CONFIRMATION_INVALID"
    finally:
        await ctrl.close()


async def test_cancel_is_forwarded_only_for_own_commands(
    env: Env, alice: Browser, online_agent: AgentSim, paired: ControllerSim
) -> None:
    cid = await paired.command(online_agent.pc_id, "system.ping")
    await online_agent.recv_type("command")
    await paired.send({"type": "cancel", "pc_id": online_agent.pc_id, "command_id": cid})
    cancel = await online_agent.recv_type("cancel", command_id=cid)
    assert cancel["controller_id"] == paired.controller_id
    other = ControllerSim(env, alice, name="Other")
    await other.pair(online_agent)
    await other.connect()
    try:
        await other.send({"type": "cancel", "pc_id": online_agent.pc_id, "command_id": cid})
        await expect_nothing(online_agent.ws)  # type: ignore[arg-type]
    finally:
        await other.close()
    await online_agent.result(
        cid, "canceled", error={"code": "POWER_CANCELED", "message": "cancelled", "retryable": False}
    )
    assert (await paired.recv_type("result", command_id=cid))["state"] == "canceled"


async def test_oversized_frame_closes_1009_and_malformed_frames_get_errors(
    env: Env, alice: Browser, online_agent: AgentSim, paired: ControllerSim
) -> None:
    assert paired.ws is not None
    await paired.ws.send("not json")
    err = await paired.recv()
    assert err["type"] == "error" and err["error"]["code"] == "MALFORMED_MESSAGE"
    await paired.ws.send(dumps_compact({"type": "subscribe", "pc_ids": [online_agent.pc_id], "extra": 1}))
    assert (await paired.recv())["error"]["code"] == "MALFORMED_MESSAGE"
    await paired.ws.send("{" + '"x":"' + "a" * (env.settings.relay_max_frame_bytes + 10) + '"}')
    assert await close_code(paired.ws) == 1009
    paired.ws = None


async def test_relay_connection_cap_returns_503(env: Env, alice: Browser, online_agent: AgentSim) -> None:
    original = env.services.settings
    env.services.relay.settings = original.model_copy(
        update={"relay_max_connections": env.services.relay.connection_count()}
    )
    try:
        ctrl = ControllerSim(env, alice)
        try:
            await ctrl.connect()
        except InvalidStatus as exc:
            assert exc.response.status_code == 503
        else:
            raise AssertionError("socket accepted above the connection cap")
    finally:
        env.services.relay.settings = original


def test_free_port_helper_is_sane() -> None:
    assert 1024 < free_port() < 65536
