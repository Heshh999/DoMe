"""Protocol 1.4 relay half of Screen view against real PostgreSQL (docs/design/screen-view.md §4, §7, §10, §12, §13;
rules.screen_signaling, screen_sessions, screen_metering, pc_key_pinning): the ICE request and its refusals, the start
hook (the agent must have announced 1.4; the first chunk is reserved; ``relay.screen`` rides along), the PC-signed
answer reaching only the issuing socket, lifecycle frames to the owner and 1.4 subscribers, allowance tickets, the
input-batch stamp gate that shields 1.2 agents, SCREEN_* rejections routed like INPUT_*, logout / session revocation /
controller revocation ending a stream within one ticket with the credential revoked, the lying agent, pairing with the
PC key MAC, and no SDP or credential in any row, event or log line."""

from __future__ import annotations

import asyncio
import datetime as dt
import uuid
from typing import Any

import pytest
from dome_protocol import loads_strict, verify_pc_key_mac

from dome_api.routes.pairing import PAIRING_APPROVED_READABLE_SECONDS
from dome_api.screen.service import ScreenService
from tests.conftest import (
    OFFER_SDP,
    SCREEN_CAPS,
    SDP_SENTINELS,
    AgentSim,
    Browser,
    ControllerSim,
    Env,
    expect_nothing,
    fake_turn,
    new_input_session_id,
    new_screen_session_id,
    screen_clock,
    sql,
    wait_pairing_state,
)

MOVE = {"type": "pointer_move", "dx": 5, "dy": -3}
CLICK = {"type": "pointer_button", "button": "left", "action": "click"}
MOVE_TO = {"type": "pointer_move_to", "x": 41000, "y": 22100, "gen": 1}
WHEEL = {"type": "pointer_wheel", "dx": 0, "dy": -240}


async def screen_phone(env: Env, alice: Browser, agent: AgentSim, caps: tuple[str, ...] = SCREEN_CAPS) -> ControllerSim:
    ctrl = ControllerSim(env, alice, name="Screen phone")
    await ctrl.pair(agent, caps)
    await ctrl.connect()
    return ctrl


async def expect_error(ctrl: ControllerSim, pc_id: str, code: str) -> dict[str, Any]:
    err = await ctrl.recv_type("error")
    assert err["error"]["code"] == code, err
    assert err["ref_pc_id"] == pc_id
    return err


async def expect_relay_failure(ctrl: ControllerSim, cid: str, code: str) -> dict[str, Any]:
    result = await ctrl.recv_type("result", command_id=cid)
    assert result["origin"] == "relay" and result["state"] == "failed" and result["error"]["code"] == code, result
    return result


async def start_session(
    env: Env, ctrl: ControllerSim, agent: AgentSim, *, remaining: int | None = 540
) -> tuple[str, str, dict[str, Any]]:
    """ICE request, signed start, the agent's result and `started`: returns (command_id, screen_session_id, command)."""
    ice = await ctrl.screen_ice_request(agent.pc_id)
    assert ice["type"] == "screen_ice_servers", ice
    cid = await ctrl.screen_start(agent.pc_id)
    cmd = await agent.recv_type("command")
    assert loads_strict(cmd["envelope"]["payload"])["command_id"] == cid
    sid = new_screen_session_id()
    await agent.ack(cid, "accepted")
    await agent.result(cid, "succeeded", result=agent.screen_result(sid, remaining_seconds=remaining))
    await ctrl.recv_type("result", command_id=cid)
    await agent.screen_session(sid, ctrl.controller_id or "", "started")
    await ctrl.recv_type("screen_session", screen_session_id=sid, event="started")
    return cid, sid, cmd


def reservation_rows(env: Env, account_id: str) -> list[dict[str, Any]]:
    cols = "state, outstanding_seconds, charged_seconds, end_reason, screen_session_id, final_reason, started_at"
    rows = sql(
        env.database_url,
        f"SELECT {cols} FROM screen_reservations WHERE account_id = %s ORDER BY created_at",
        (account_id,),
    )
    return [dict(zip([c.strip() for c in cols.split(",")], r, strict=True)) for r in rows]


def credential(env: Env, controller_id: str) -> dict[str, Any] | None:
    rows = sql(
        env.database_url,
        "SELECT state, revoke_reason, command_id, screen_session_id FROM turn_credentials WHERE controller_id = %s",
        (controller_id,),
    )
    return (
        dict(zip(("state", "revoke_reason", "command_id", "screen_session_id"), rows[0], strict=True)) if rows else None
    )


def window(env: Env, account_id: str) -> dict[str, Any] | None:
    rows = sql(
        env.database_url,
        "SELECT used_seconds, reserved_seconds FROM screen_view_windows WHERE account_id = %s ORDER BY window_start DESC",
        (account_id,),
    )
    return {"used": rows[0][0], "reserved": rows[0][1]} if rows else None


async def events(alice: Browser, kind: str) -> list[dict[str, Any]]:
    out = (await alice.get("/v1/account/security-events?limit=200"))["events"]
    return [e for e in out if e["kind"] == kind]


# ----- screen_ice_request ----------------------------------------------------------------------------------------------


async def test_ice_request_needs_1_4_socket_switch_binding_and_screen_grant(
    env: Env, alice: Browser, online_agent: AgentSim, screen: ScreenService
) -> None:
    pc = online_agent.pc_id
    # a phone that announced only 1.3 may not use the frame (it would not pass the schema on its own socket either)
    old = ControllerSim(env, alice, name="1.3 phone")
    await old.pair(online_agent, SCREEN_CAPS)
    await old.connect(protocol_versions=("1.0", "1.1", "1.2", "1.3"), registry_version="1.3")
    assert (await old.screen_ice_request(pc))["error"]["code"] == "PROTOCOL_INCOMPATIBLE"
    await old.close()
    await alice.delete(f"/v1/controllers/{old.controller_id}")  # Free allows two phones: make room
    # the operator switch
    import dataclasses

    screen.config = dataclasses.replace(screen.config, enabled=False)
    ctrl = await screen_phone(env, alice, online_agent)
    assert (await ctrl.screen_ice_request(pc))["error"]["code"] == "SCREEN_VIEW_DISABLED"
    screen.config = dataclasses.replace(screen.config, enabled=True)
    # no screen grant
    touch = await screen_phone(env, alice, online_agent, ("status", "pointer"))
    err = await touch.screen_ice_request(pc)
    assert err["error"]["code"] == "GRANT_MISSING" and err["ref_pc_id"] == pc
    await touch.close()
    await alice.delete(f"/v1/controllers/{touch.controller_id}")
    # an unbound socket (no proof) cannot ask
    unbound = ControllerSim(env, alice, name="unbound", key=ctrl.key)
    unbound.controller_id = ctrl.controller_id
    await unbound.connect(proof=False)
    assert (await unbound.screen_ice_request(pc))["error"]["code"] == "GRANT_MISSING"
    await unbound.close()
    # the happy path: STUN first, then the phone's TURN credential; the allowance; TTL = remaining + 60
    ice = await ctrl.screen_ice_request(pc)
    assert ice["type"] == "screen_ice_servers" and ice["pc_id"] == pc and ice["turn"] == "available"
    assert ice["ice_servers"][0] == {"urls": ["stun:stun.cloudflare.com:3478"]}
    turn = ice["ice_servers"][1]
    assert turn["urls"] == fake_turn(screen).urls and turn["credential"].startswith("FAKE-TURN-CREDENTIAL-")
    assert ice["allowance"] == {"unlimited": False, "remaining_seconds": 600, "resets_at": None}
    assert ice["expires_at"] is not None
    [minted] = fake_turn(screen).minted
    assert minted["ttl"] == 660 and minted["custom_identifier"] == alice.account_id
    row = credential(env, ctrl.controller_id or "")
    assert row is not None and row["state"] == "outstanding"
    kinds = {e["kind"] for e in (await alice.get("/v1/account/security-events"))["events"]}
    assert "screen_ice_rejected" in kinds
    await ctrl.close()


async def test_ice_request_without_a_turn_key_is_stun_only_with_the_honest_reason(
    env: Env, alice: Browser, online_agent: AgentSim, screen: ScreenService
) -> None:
    import dataclasses

    screen.turn.config = dataclasses.replace(screen.turn.config, configured=False)
    ctrl = await screen_phone(env, alice, online_agent)
    ice = await ctrl.screen_ice_request(online_agent.pc_id)
    assert ice["turn"] == "unavailable" and ice["expires_at"] is None
    assert ice["ice_servers"] == [{"urls": ["stun:stun.cloudflare.com:3478"]}]
    assert fake_turn(screen).minted == [] and credential(env, ctrl.controller_id or "") is None
    await ctrl.close()


async def test_ice_request_against_an_offline_or_old_pc_and_the_rate_limits(
    env: Env, alice: Browser, linked_agent: AgentSim, screen: ScreenService
) -> None:
    pc = linked_agent.pc_id
    await linked_agent.connect(protocol_versions=("1.0", "1.1", "1.2"), registry_version="1.2")
    ctrl = await screen_phone(env, alice, linked_agent)
    err = await ctrl.screen_ice_request(pc)
    assert err["error"]["code"] == "PROTOCOL_INCOMPATIBLE"
    assert err["error"]["detail"] == {"peer": "agent", "required": "1.4"}
    await linked_agent.close()
    await asyncio.sleep(0.2)
    assert (await ctrl.screen_ice_request(pc))["error"]["code"] == "PC_OFFLINE"
    await linked_agent.connect()
    # 6 per minute per controller (burst 6), then RATE_LIMITED; refused requests mint nothing
    answers = [await ctrl.screen_ice_request(pc) for _ in range(7)]
    assert [a["type"] for a in answers] == ["screen_ice_servers"] * 6 + ["error"]
    assert answers[-1]["error"]["code"] == "RATE_LIMITED"
    assert len(fake_turn(screen).minted) == 6
    # each new credential revoked the previous one: one outstanding per controller
    assert fake_turn(screen).revoked == [m["username"] for m in fake_turn(screen).minted[:-1]]
    reasons = [e["detail"]["reason"] for e in await events(alice, "turn_credential_revoked")]
    assert reasons == ["replaced"] * 5
    await ctrl.close()


async def test_ice_request_refused_when_the_allowance_is_used_up(
    env: Env, alice: Browser, online_agent: AgentSim, screen: ScreenService
) -> None:
    ctrl = await screen_phone(env, alice, online_agent)
    now = dt.datetime.now(dt.UTC)
    sql(
        env.database_url,
        "INSERT INTO screen_view_windows (id, account_id, window_start, window_end, used_seconds, reserved_seconds) "
        "VALUES (%s, %s, %s, %s, 600, 0)",
        (str(uuid.uuid4()), alice.account_id, now - dt.timedelta(minutes=1), now + dt.timedelta(hours=4)),
    )
    err = await ctrl.screen_ice_request(online_agent.pc_id)
    assert err["error"]["code"] == "SCREEN_ALLOWANCE_EXHAUSTED" and err["error"]["detail"]["resets_at"]
    assert fake_turn(screen).minted == []
    cid = await ctrl.screen_start(online_agent.pc_id)
    failure = await expect_relay_failure(ctrl, cid, "SCREEN_ALLOWANCE_EXHAUSTED")
    assert failure["error"]["detail"]["resets_at"]
    await expect_nothing(online_agent.ws)  # type: ignore[arg-type]  # the PC never saw the command
    assert len(await events(alice, "screen_allowance_exhausted")) == 2
    await ctrl.close()


# ----- the start hook, the answer and the session frames ---------------------------------------------------------------


async def test_start_reserves_the_first_chunk_and_the_answer_reaches_only_the_issuing_socket(
    env: Env, alice: Browser, online_agent: AgentSim, screen: ScreenService
) -> None:
    pc = online_agent.pc_id
    ctrl = await screen_phone(env, alice, online_agent)
    second = ControllerSim(env, alice, name="second socket of the same phone", key=ctrl.key)
    second.controller_id = ctrl.controller_id
    await second.connect()
    old = ControllerSim(env, alice, name="1.3 socket of the same phone", key=ctrl.key)
    old.controller_id = ctrl.controller_id
    await old.connect(protocol_versions=("1.0", "1.1", "1.2", "1.3"), registry_version="1.3")
    watcher = await screen_phone(env, alice, online_agent, ("status", "screen"))
    await watcher.subscribe(pc)
    await watcher.recv_type("pc_status")
    try:
        ice = await ctrl.screen_ice_request(pc)
        assert ice["type"] == "screen_ice_servers"
        cid = await ctrl.screen_start(pc)
        cmd = await online_agent.recv_type("command")
        payload = loads_strict(cmd["envelope"]["payload"])
        assert payload["action"] == "screen.session_start" and payload["protocol_version"] == "1.4"
        assert payload["params"]["offer_sdp"] == OFFER_SDP  # the envelope travels verbatim
        relay = cmd["relay"]["screen"]
        assert relay["unlimited"] is False and relay["stun_urls"] == ["stun:stun.cloudflare.com:3478"]
        assert "relayed_max_kbps" not in relay
        granted = dt.datetime.fromisoformat(relay["granted_until"].replace("Z", "+00:00"))
        received = dt.datetime.fromisoformat(cmd["relay"]["received_at"].replace("Z", "+00:00"))
        assert abs((granted - received - dt.timedelta(seconds=60)).total_seconds()) < 1
        await asyncio.sleep(0.2)  # the relay binds the credential right after the forward
        [row] = reservation_rows(env, alice.account_id)
        assert row["state"] == "reserved" and row["outstanding_seconds"] == 60
        assert window(env, alice.account_id) == {"used": 0, "reserved": 60}
        cred = credential(env, ctrl.controller_id or "")
        assert cred is not None and cred["state"] == "bound" and cred["command_id"] == uuid.UUID(cid)
        # the commands row stores the digest only
        [(action, digest)] = sql(env.database_url, "SELECT action, digest FROM commands WHERE id = %s", (cid,))
        assert action == "screen.session_start" and len(digest) == 43
        # the PC answers: the signed answer frame and the result (no SDP in it)
        sid = new_screen_session_id()
        await online_agent.ack(cid, "accepted")
        envelope = await online_agent.screen_answer(ctrl.controller_id or "", cid, sid)
        answer = await ctrl.recv_type("screen_answer")
        assert answer == {"type": "screen_answer", "pc_id": pc, "envelope": envelope}  # verbatim, reference stripped
        await online_agent.result(cid, "succeeded", result=online_agent.screen_result(sid))
        result = await ctrl.recv_type("result", command_id=cid)
        assert result["result"]["screen_session_id"] == sid
        await asyncio.sleep(0.2)
        [row] = reservation_rows(env, alice.account_id)
        assert row["screen_session_id"] == sid and row["state"] == "live"
        # nobody else saw the answer: not the phone's second socket (ack and result go to the issuing socket alone),
        # not its 1.3 socket, not a subscriber
        await expect_nothing(second.ws, 0.5)  # type: ignore[arg-type]
        await expect_nothing(old.ws, 0.3)  # type: ignore[arg-type]
        await expect_nothing(watcher.ws, 0.3)  # type: ignore[arg-type]
        # `started`: the owner and the 1.4 subscriber get it, the 1.3 socket does not
        await online_agent.screen_session(sid, ctrl.controller_id or "", "started", path="direct")
        owner_copy = await ctrl.recv_type("screen_session", screen_session_id=sid)
        assert owner_copy["event"] == "started" and owner_copy["path"] == "direct"
        assert (await watcher.recv_type("screen_session", screen_session_id=sid))["event"] == "started"
        await second.recv_type("screen_session", screen_session_id=sid)
        await expect_nothing(old.ws, 0.3)  # type: ignore[arg-type]
        started = await events(alice, "screen_session_started")
        assert started and started[-1]["detail"]["path"] == "direct"
        # no SDP, no credential anywhere the relay writes
        for table in ("commands", "security_events", "screen_reservations", "turn_credentials"):
            dump = str(sql(env.database_url, f"SELECT {table}::text FROM {table}"))
            assert not any(s in dump for s in SDP_SENTINELS) and "FAKE-TURN-CREDENTIAL" not in dump, table
    finally:
        for c in (ctrl, second, old, watcher):
            await c.close()


async def test_start_to_a_1_2_agent_is_protocol_incompatible_from_the_relay_and_revokes_the_credential(
    env: Env, alice: Browser, linked_agent: AgentSim, screen: ScreenService
) -> None:
    await linked_agent.connect(protocol_versions=("1.0", "1.1", "1.2"), registry_version="1.2")
    ctrl = await screen_phone(env, alice, linked_agent)
    # mint a credential first (an older relay would have let the ICE request through: force the row for the test)
    sql(
        env.database_url,
        "INSERT INTO turn_credentials (controller_id, account_id, username, minted_at, expires_at, ttl_seconds, state) "
        "VALUES (%s, %s, 'u-old', now(), now() + interval '600 seconds', 600, 'outstanding')",
        (ctrl.controller_id, alice.account_id),
    )
    cid = await ctrl.screen_start(linked_agent.pc_id)
    failure = await expect_relay_failure(ctrl, cid, "PROTOCOL_INCOMPATIBLE")
    assert failure["error"]["detail"] == {"peer": "agent", "required": "1.4"}
    await expect_nothing(linked_agent.ws)  # type: ignore[arg-type]
    assert reservation_rows(env, alice.account_id) == []
    cred = credential(env, ctrl.controller_id or "")
    assert cred is not None and cred["state"] == "revoked" and cred["revoke_reason"] == "start_refused"
    assert fake_turn(screen).revoked == ["u-old"]
    # screen.session_stop is refused the same way (never forwarded to a PC that cannot run it)
    cid = await ctrl.command(linked_agent.pc_id, "screen.session_stop", {"screen_session_id": new_screen_session_id()})
    await expect_relay_failure(ctrl, cid, "PROTOCOL_INCOMPATIBLE")
    await ctrl.close()


async def test_data_saver_past_the_soft_cap_rides_on_the_forwarded_start_and_is_recorded(
    env: Env, alice: Browser, online_agent: AgentSim, screen: ScreenService
) -> None:
    """Design §13: past the monthly soft cap (the provider's figures or the provisional bound, whichever is larger)
    a relayed Free start carries ``relay.screen.relayed_max_kbps``; §12 records it as ``turn_cap_reached{cap: soft}``.
    The bound is global, so the row that trips it is removed again afterwards."""
    import dataclasses

    # the guard is global: other tests' credentials that were never revoked (their sockets just closed, the sweeper is
    # paused) stay in its session-less term, so the hard cap is set out of reach and only the soft cap is exercised
    screen.turn.config = dataclasses.replace(screen.turn.config, soft_cap_gb=1, hard_cap_gb=1_000_000)
    ctrl = await screen_phone(env, alice, online_agent)
    tripwire = str(uuid.uuid4())
    # a settled reservation of 7300 charged seconds this month: just over 1 GB at the relayed cap (0.5 GB/h)
    sql(
        env.database_url,
        "INSERT INTO screen_reservations (id, account_id, pc_id, controller_id, command_id, unlimited, forwarded_at, "
        "segment_started_at, reserved_until, outstanding_seconds, charged_seconds, state, end_reason, ended_at) "
        "VALUES (%s, %s, %s, %s, %s, false, now(), now(), now(), 0, 7300, 'settled', 'stopped', now())",
        (tripwire, alice.account_id, online_agent.pc_id, ctrl.controller_id, str(uuid.uuid4())),
    )
    try:
        ice = await ctrl.screen_ice_request(online_agent.pc_id)
        assert ice["type"] == "screen_ice_servers" and ice["turn"] == "available"  # below the hard cap: TURN minted
        cid = await ctrl.screen_start(online_agent.pc_id)
        cmd = await online_agent.recv_type("command")
        assert cmd["relay"]["screen"]["relayed_max_kbps"] == 500
        [event] = [e for e in await events(alice, "turn_cap_reached") if e["detail"].get("cap") == "soft"]
        assert event["actor"] == "system" and event["subject_id"] == ctrl.controller_id
        assert event["detail"] == {"cap": "soft", "relayed_max_kbps": 500}
        # the PC refuses nothing here; the relay-side result path is unchanged
        await online_agent.result(
            cid, "failed", error={"code": "SCREEN_CONNECTION_FAILED", "message": "no path", "retryable": True}
        )
        await ctrl.recv_type("result", command_id=cid)
    finally:
        sql(env.database_url, "DELETE FROM screen_reservations WHERE id = %s", (tripwire,))
        await ctrl.close()


async def test_start_refused_by_the_switch_and_a_failed_start_result_settles_and_revokes(
    env: Env, alice: Browser, online_agent: AgentSim, screen: ScreenService
) -> None:
    import dataclasses

    pc = online_agent.pc_id
    ctrl = await screen_phone(env, alice, online_agent)
    assert (await ctrl.screen_ice_request(pc))["type"] == "screen_ice_servers"
    screen.config = dataclasses.replace(screen.config, enabled=False)
    cid = await ctrl.screen_start(pc)
    await expect_relay_failure(ctrl, cid, "SCREEN_VIEW_DISABLED")
    await expect_nothing(online_agent.ws)  # type: ignore[arg-type]
    cred = credential(env, ctrl.controller_id or "")
    assert cred is not None and cred["state"] == "revoked" and cred["revoke_reason"] == "start_refused"
    screen.config = dataclasses.replace(screen.config, enabled=True)
    # a start the PC refuses (consent declined) is forgiven by the zero-charge rule and revokes the credential
    assert (await ctrl.screen_ice_request(pc))["type"] == "screen_ice_servers"
    cid = await ctrl.screen_start(pc)
    await online_agent.recv_type("command")
    await online_agent.result(
        cid,
        "failed",
        error={
            "code": "SCREEN_CONSENT_DECLINED",
            "message": "Not allowed at the PC.",
            "retryable": False,
            "detail": {"reason": "backoff"},
        },
    )
    assert (await ctrl.recv_type("result", command_id=cid))["state"] == "failed"
    await asyncio.sleep(0.2)
    [row] = reservation_rows(env, alice.account_id)
    # refused at the PC: nothing was shown, forgiven on the consent counter (never the connection failures')
    assert row["state"] == "settled" and row["charged_seconds"] == 0 and row["end_reason"] == "consent_refused"
    assert window(env, alice.account_id) == {"used": 0, "reserved": 0}
    cred = credential(env, ctrl.controller_id or "")
    assert cred is not None and cred["state"] == "revoked"
    declined = await events(alice, "screen_consent_declined")
    assert declined and declined[-1]["detail"] == {"reason": "backoff"} and declined[-1]["actor"] == "pc"
    await ctrl.close()


async def test_the_answer_is_refused_for_a_foreign_controller_or_one_without_the_grant(
    env: Env, alice: Browser, bob: Browser, online_agent: AgentSim, screen: ScreenService
) -> None:
    ctrl = await screen_phone(env, alice, online_agent)
    touch = await screen_phone(env, alice, online_agent, ("status", "pointer"))
    bob_agent = AgentSim(env)
    await bob_agent.link(bob)
    await bob_agent.connect()
    bob_phone = ControllerSim(env, bob, name="Bob's phone")
    await bob_phone.pair(bob_agent, SCREEN_CAPS)
    await bob_phone.connect()
    try:
        sid = new_screen_session_id()
        before = len((await alice.get("/v1/account/security-events?limit=200"))["events"])
        # Bob's controller named from Alice's PC
        await online_agent.screen_answer(bob_phone.controller_id or "", str(uuid.uuid4()), sid)
        # Alice's touchpad-only phone
        await online_agent.screen_answer(touch.controller_id or "", str(uuid.uuid4()), sid)
        await expect_nothing(bob_phone.ws, 0.5)  # type: ignore[arg-type]
        await expect_nothing(touch.ws, 0.3)  # type: ignore[arg-type]
        await expect_nothing(ctrl.ws, 0.3)  # type: ignore[arg-type]
        rejected = [
            e
            for e in (await alice.get("/v1/account/security-events?limit=200"))["events"]
            if e["kind"] == "relay_frame_rejected"
        ]
        assert {e["detail"]["reason"] for e in rejected} >= {"controller_not_in_account", "no_screen_grant"}
        assert len((await alice.get("/v1/account/security-events?limit=200"))["events"]) >= before + 2
        # a session frame for a foreign controller is refused as well
        await online_agent.screen_session(sid, bob_phone.controller_id or "", "started")
        await expect_nothing(bob_phone.ws, 0.3)  # type: ignore[arg-type]
    finally:
        await ctrl.close()
        await touch.close()
        await bob_phone.close()
        await bob_agent.close()


async def test_one_session_per_pc_a_second_phone_is_refused_then_takes_over(
    env: Env, alice: Browser, online_agent: AgentSim, screen: ScreenService
) -> None:
    """rules.screen_sessions (1): the PC holds one session and answers a second phone SCREEN_SESSION_OWNED unless it
    sends ``takeover``. The relay's half: the refused start's chunk is forgiven and only that phone's credential is
    revoked; a takeover's ``ended{takeover}`` settles the first phone's reservation on the relay clock and revokes its
    credential, while the new session keeps its own reservation, credential and tickets."""
    pc = online_agent.pc_id
    first = await screen_phone(env, alice, online_agent)
    second = await screen_phone(env, alice, online_agent)
    clock = screen_clock(screen)
    try:
        _, sid1, _ = await start_session(env, first, online_agent)
        # the second phone without takeover: refused at the PC
        assert (await second.screen_ice_request(pc))["turn"] == "available"
        cid = await second.screen_start(pc)
        await online_agent.recv_type("command")
        owned = {"code": "SCREEN_SESSION_OWNED", "message": "Another phone is viewing this PC.", "retryable": False}
        await online_agent.result(cid, "failed", error=owned)
        assert (await second.recv_type("result", command_id=cid))["error"]["code"] == "SCREEN_SESSION_OWNED"
        await asyncio.sleep(0.2)
        rows = reservation_rows(env, alice.account_id)
        assert [r["state"] for r in rows] == ["live", "settled"]
        assert rows[1]["charged_seconds"] == 0 and rows[1]["end_reason"] == "start_failed"
        refused = credential(env, second.controller_id or "")
        assert refused is not None and refused["state"] == "revoked" and refused["revoke_reason"] == "start_refused"
        viewing = credential(env, first.controller_id or "")
        assert viewing is not None and viewing["state"] == "bound" and viewing["screen_session_id"] == sid1
        # the second phone takes over 25 s into the first session
        clock.advance(25)
        assert (await second.screen_ice_request(pc))["turn"] == "available"
        params = {"offer_sdp": OFFER_SDP, "surface": "primary", "audio": True, "takeover": True}
        cid = await second.command(pc, "screen.session_start", params, None, lifetime=60)
        cmd = await online_agent.recv_type("command")
        assert loads_strict(cmd["envelope"]["payload"])["params"]["takeover"] is True
        # the PC ends the old session first, then answers the new one
        await online_agent.screen_session(sid1, first.controller_id or "", "ended", "takeover", live_seconds=1)
        ended = await first.recv_type("screen_session", screen_session_id=sid1, event="ended")
        assert ended["reason"] == "takeover"
        sid2 = new_screen_session_id()
        await online_agent.ack(cid, "accepted")
        await online_agent.result(cid, "succeeded", result=online_agent.screen_result(sid2))
        await second.recv_type("result", command_id=cid)
        await online_agent.screen_session(sid2, second.controller_id or "", "started")
        await second.recv_type("screen_session", screen_session_id=sid2, event="started")
        by_sid = {r["screen_session_id"]: r for r in reservation_rows(env, alice.account_id)}
        old = by_sid[sid1]
        assert old["state"] == "settled" and old["end_reason"] == "takeover" and old["charged_seconds"] == 30
        assert by_sid[sid2]["state"] == "live"
        assert window(env, alice.account_id) == {"used": 30, "reserved": 60}
        gone = credential(env, first.controller_id or "")
        assert gone is not None and gone["state"] == "revoked" and gone["revoke_reason"] == "session_ended"
        taken = credential(env, second.controller_id or "")
        assert taken is not None and taken["state"] == "bound" and taken["screen_session_id"] == sid2
        # the new owner's tickets renew; the old id gets final and no more minutes
        await online_agent.screen_usage(sid2)
        assert (await online_agent.recv_type("screen_allowance", screen_session_id=sid2))["final"] is False
        await online_agent.screen_usage(sid1)
        assert (await online_agent.recv_type("screen_allowance", screen_session_id=sid1))["final"] is True
    finally:
        await first.close()
        await second.close()


async def test_start_rate_limit_refuses_the_seventh_start_in_a_minute_and_revokes_the_credential(
    env: Env, alice: Browser, online_agent: AgentSim, screen: ScreenService
) -> None:
    """plans.json screen_start_rate_limit (6 per minute per controller, on top of the manual command bucket): the
    seventh start is a relay-origin RATE_LIMITED, never reaches the PC, reserves nothing and revokes the credential."""
    pc = online_agent.pc_id
    ctrl = await screen_phone(env, alice, online_agent)
    owned = {"code": "SCREEN_SESSION_OWNED", "message": "Another phone is viewing this PC.", "retryable": False}
    try:
        for _ in range(6):
            cid = await ctrl.screen_start(pc)
            await online_agent.recv_type("command")
            await online_agent.result(cid, "failed", error=owned)
            await ctrl.recv_type("result", command_id=cid)
        assert (await ctrl.screen_ice_request(pc))["turn"] == "available"
        cid = await ctrl.screen_start(pc)
        await expect_relay_failure(ctrl, cid, "RATE_LIMITED")
        await expect_nothing(online_agent.ws)  # type: ignore[arg-type]
        await asyncio.sleep(0.2)
        assert len(reservation_rows(env, alice.account_id)) == 6
        cred = credential(env, ctrl.controller_id or "")
        assert cred is not None and cred["state"] == "revoked" and cred["revoke_reason"] == "start_refused"
    finally:
        await ctrl.close()


async def test_stop_passes_the_switch_and_a_running_session_is_not_cut_by_it(
    env: Env, alice: Browser, online_agent: AgentSim, screen: ScreenService
) -> None:
    """DECISIONS #86: DOME_SCREEN_VIEW_ENABLED=false refuses new starts and ICE requests only; a running session keeps
    its tickets and ``screen.session_stop`` is forwarded (without ``relay.screen``) so the phone can always stop."""
    import dataclasses

    pc = online_agent.pc_id
    ctrl = await screen_phone(env, alice, online_agent)
    try:
        _, sid, _ = await start_session(env, ctrl, online_agent)
        screen.config = dataclasses.replace(screen.config, enabled=False)
        await online_agent.screen_usage(sid)
        assert (await online_agent.recv_type("screen_allowance", screen_session_id=sid))["final"] is False
        cid = await ctrl.command(pc, "screen.session_stop", {"screen_session_id": sid})
        cmd = await online_agent.recv_type("command")
        assert loads_strict(cmd["envelope"]["payload"])["action"] == "screen.session_stop"
        assert "screen" not in cmd["relay"]
        await online_agent.result(cid, "succeeded", result={"stopped": True, "live_seconds": 4})
        assert (await ctrl.recv_type("result", command_id=cid))["state"] == "succeeded"
        await online_agent.screen_session(sid, ctrl.controller_id or "", "ended", "stopped", live_seconds=4)
        await ctrl.recv_type("screen_session", screen_session_id=sid, event="ended")
        [row] = reservation_rows(env, alice.account_id)
        assert row["state"] == "settled" and row["end_reason"] == "stopped"
        # the next start is refused by the switch and the ICE request too
        cid = await ctrl.screen_start(pc)
        await expect_relay_failure(ctrl, cid, "SCREEN_VIEW_DISABLED")
        assert (await ctrl.screen_ice_request(pc))["error"]["code"] == "SCREEN_VIEW_DISABLED"
    finally:
        await ctrl.close()


# ----- tickets, settlement and the lying agent --------------------------------------------------------------------------


async def test_tickets_renew_in_chunks_until_exhausted_and_the_end_is_charged_on_the_relay_clock(
    env: Env, alice: Browser, online_agent: AgentSim, screen: ScreenService
) -> None:
    import dataclasses

    screen.config = dataclasses.replace(screen.config, free_seconds_override=150)
    ctrl = await screen_phone(env, alice, online_agent)
    clock = screen_clock(screen)
    try:
        cid, sid, cmd = await start_session(env, ctrl, online_agent)
        granted0 = cmd["relay"]["screen"]["granted_until"]
        clock.advance(40)
        await online_agent.screen_usage(sid, live_seconds=40)
        t1 = await online_agent.recv_type("screen_allowance", screen_session_id=sid)
        assert t1["final"] is False and t1["unlimited"] is False
        g0 = dt.datetime.fromisoformat(granted0.replace("Z", "+00:00"))
        g1 = dt.datetime.fromisoformat(t1["granted_until"].replace("Z", "+00:00"))
        assert g1 == g0 + dt.timedelta(seconds=60)  # from the previous ticket's end, not from now
        clock.advance(60)
        await online_agent.screen_usage(sid, live_seconds=100)
        t2 = await online_agent.recv_type("screen_allowance", screen_session_id=sid)
        g2 = dt.datetime.fromisoformat(t2["granted_until"].replace("Z", "+00:00"))
        assert g2 == g1 + dt.timedelta(seconds=30)  # only 30 s were left in the window
        clock.advance(40)
        await online_agent.screen_usage(sid, live_seconds=140)
        t3 = await online_agent.recv_type("screen_allowance", screen_session_id=sid)
        assert t3["final"] is True and t3["granted_until"] is None and t3["reason"] == "allowance_exhausted"
        assert t3["resets_at"]
        assert len(await events(alice, "screen_allowance_exhausted")) == 1
        # the agent ends it; the lying figure in the frame changes nothing: 150 s measured, 150 reserved
        clock.advance(10)
        await online_agent.screen_session(sid, ctrl.controller_id or "", "ended", "allowance_exhausted", live_seconds=1)
        ended = await ctrl.recv_type("screen_session", screen_session_id=sid, event="ended")
        assert ended["reason"] == "allowance_exhausted"
        [row] = reservation_rows(env, alice.account_id)
        assert (
            row["state"] == "settled" and row["charged_seconds"] == 150 and row["end_reason"] == "allowance_exhausted"
        )
        assert window(env, alice.account_id) == {"used": 150, "reserved": 0}
        cred = credential(env, ctrl.controller_id or "")
        assert cred is not None and cred["state"] == "revoked" and cred["revoke_reason"] == "session_ended"
        done = await events(alice, "screen_session_ended")
        assert done[-1]["detail"]["reason"] == "allowance_exhausted" and done[-1]["detail"]["charged_seconds"] == 150
        # the next start is refused until the window resets
        assert (await ctrl.screen_ice_request(online_agent.pc_id))["error"]["code"] == "SCREEN_ALLOWANCE_EXHAUSTED"
    finally:
        await ctrl.close()


async def test_a_lying_agent_is_charged_the_relay_measured_time(
    env: Env, alice: Browser, online_agent: AgentSim, screen: ScreenService
) -> None:
    import dataclasses

    screen.config = dataclasses.replace(screen.config, free_seconds_override=120)
    ctrl = await screen_phone(env, alice, online_agent)
    clock = screen_clock(screen)
    try:
        # streams 59 s, then claims connection_failed with 0 live seconds: 60 s charged
        _, sid, _ = await start_session(env, ctrl, online_agent)
        clock.advance(59)
        await online_agent.screen_session(sid, ctrl.controller_id or "", "ended", "connection_failed", live_seconds=0)
        await ctrl.recv_type("screen_session", screen_session_id=sid, event="ended")
        assert window(env, alice.account_id) == {"used": 60, "reserved": 0}
        # a genuine quick failure with no `started` frame within 20 s is forgiven (three per window)
        ice = await ctrl.screen_ice_request(online_agent.pc_id)
        assert ice["allowance"]["remaining_seconds"] == 60
        cid = await ctrl.screen_start(online_agent.pc_id)
        await online_agent.recv_type("command")
        clock.advance(5)
        await online_agent.result(
            cid, "failed", error={"code": "SCREEN_CONNECTION_FAILED", "message": "no path", "retryable": True}
        )
        await ctrl.recv_type("result", command_id=cid)
        await asyncio.sleep(0.2)
        assert window(env, alice.account_id) == {"used": 60, "reserved": 0}
        rows = reservation_rows(env, alice.account_id)
        assert rows[-1]["end_reason"] == "connection_failed" and rows[-1]["charged_seconds"] == 0
        # the last 60 s, used in full
        _, sid, _ = await start_session(env, ctrl, online_agent)
        clock.advance(70)
        await online_agent.screen_session(sid, ctrl.controller_id or "", "ended", "stopped", live_seconds=0)
        await ctrl.recv_type("screen_session", screen_session_id=sid, event="ended")
        assert window(env, alice.account_id) == {"used": 120, "reserved": 0}
        assert (await ctrl.screen_ice_request(online_agent.pc_id))["error"]["code"] == "SCREEN_ALLOWANCE_EXHAUSTED"
    finally:
        await ctrl.close()


async def test_usage_budget_and_unknown_sessions(
    env: Env, alice: Browser, online_agent: AgentSim, screen: ScreenService
) -> None:
    from dome_api.security.ratelimit import SlidingWindowLimiter

    env.services.limiters.screen_usage = SlidingWindowLimiter(2, 60)
    ctrl = await screen_phone(env, alice, online_agent)
    try:
        _, sid, _ = await start_session(env, ctrl, online_agent)
        for _ in range(2):
            await online_agent.screen_usage(sid)
            assert (await online_agent.recv_type("screen_allowance", screen_session_id=sid))["final"] is False
        await online_agent.screen_usage(sid)  # the third within a minute is dropped
        await expect_nothing(online_agent.ws, 0.5)  # type: ignore[arg-type]
        # a session the relay never reserved gets no answer: the agent ends it at its ticket's deadline with the
        # honest allowance_unconfirmed, never "Free time is used up"
        unknown = new_screen_session_id()
        await online_agent.screen_usage(unknown)
        await expect_nothing(online_agent.ws, 0.5)  # type: ignore[arg-type]
    finally:
        await ctrl.close()


async def test_sweeper_charges_a_vanished_session_in_full_and_revokes(
    env: Env, alice: Browser, online_agent: AgentSim, screen: ScreenService
) -> None:
    ctrl = await screen_phone(env, alice, online_agent)
    clock = screen_clock(screen)
    try:
        _, sid, _ = await start_session(env, ctrl, online_agent)
        clock.advance(75)  # the ticket ran out 15 s ago and no `ended` came
        counts = await screen.sweep()
        assert counts["swept"] >= 1  # the sweep is global; other tests' stale rows may go with it
        [row] = reservation_rows(env, alice.account_id)
        assert row["state"] == "swept" and row["charged_seconds"] == 60
        cred = credential(env, ctrl.controller_id or "")
        assert cred is not None and cred["state"] == "revoked" and cred["revoke_reason"] == "swept"
        done = await events(alice, "screen_session_ended")
        assert done[-1]["detail"]["reason"] == "swept" and done[-1]["actor"] == "system"
        # the agent's late `ended` still reaches the phone but charges nothing more
        await online_agent.screen_session(sid, ctrl.controller_id or "", "ended", "stopped", live_seconds=500)
        await ctrl.recv_type("screen_session", screen_session_id=sid, event="ended")
        assert window(env, alice.account_id) == {"used": 60, "reserved": 0}
    finally:
        await ctrl.close()


async def test_a_credential_without_a_start_is_revoked_within_the_start_lifetime(
    env: Env, alice: Browser, online_agent: AgentSim, screen: ScreenService
) -> None:
    ctrl = await screen_phone(env, alice, online_agent)
    try:
        ice = await ctrl.screen_ice_request(online_agent.pc_id)
        assert ice["turn"] == "available"
        screen_clock(screen).advance(61)
        assert (await screen.sweep())["turn_revoked"] >= 1  # the sweep is global; other tests' rows may go with it
        cred = credential(env, ctrl.controller_id or "")
        assert cred is not None and cred["state"] == "revoked" and cred["revoke_reason"] == "no_start"
        assert [e["detail"]["reason"] for e in await events(alice, "turn_credential_revoked")] == ["no_start"]
    finally:
        await ctrl.close()


# ----- the owner must stay present -------------------------------------------------------------------------------------


async def test_logout_of_the_owner_ends_the_stream_within_one_ticket(
    env: Env, alice: Browser, online_agent: AgentSim, screen: ScreenService
) -> None:
    ctrl = await screen_phone(env, alice, online_agent)
    _, sid, _ = await start_session(env, ctrl, online_agent)
    await alice.post("/v1/auth/logout", expect=204)
    # the PC hears it at once, without asking
    final = await online_agent.recv_type("screen_allowance", screen_session_id=sid)
    assert final == {
        "type": "screen_allowance",
        "screen_session_id": sid,
        "granted_until": None,
        "unlimited": False,
        "final": True,
        "reason": "controller_offline",
    }
    cred = credential(env, ctrl.controller_id or "")
    assert cred is not None and cred["state"] == "revoked" and cred["revoke_reason"] == "logout"
    # the next ticket request gets the same answer, so a PC that missed the frame still stops within one chunk
    await online_agent.screen_usage(sid)
    again = await online_agent.recv_type("screen_allowance", screen_session_id=sid)
    assert again["final"] is True and again["reason"] == "controller_offline"
    # the PC ends it and the ledger settles on the relay's clock
    await online_agent.screen_session(sid, ctrl.controller_id or "", "ended", "controller_offline", live_seconds=30)
    await asyncio.sleep(0.2)
    [row] = reservation_rows(env, alice.account_id)
    assert row["state"] == "settled" and row["end_reason"] == "controller_offline" and row["charged_seconds"] == 10
    ctrl.ws = None  # the socket was closed by the logout


async def test_account_session_revocation_and_controller_revocation_end_the_stream(
    env: Env, online_agent: AgentSim, screen: ScreenService, alice: Browser
) -> None:
    ctrl = await screen_phone(env, alice, online_agent)
    try:
        _, sid, _ = await start_session(env, ctrl, online_agent)
        sessions = (await alice.get("/v1/account/sessions"))["sessions"]
        [current] = [s for s in sessions if s["current"]]
        # revoking the very web session the phone's socket runs on
        r = await alice.request("DELETE", f"/v1/account/sessions/{current['id']}", expect=204)
        assert r.status_code == 204
        final = await online_agent.recv_type("screen_allowance", screen_session_id=sid)
        assert final["final"] is True and final["reason"] == "controller_offline"
        cred = credential(env, ctrl.controller_id or "")
        assert cred is not None and cred["revoke_reason"] == "logout"
    finally:
        ctrl.ws = None
    # controller revocation: a fresh phone and session, then DELETE /v1/controllers/{id}
    await alice.login(alice.email)
    ctrl2 = await screen_phone(env, alice, online_agent)
    _, sid2, _ = await start_session(env, ctrl2, online_agent)
    await alice.delete(f"/v1/controllers/{ctrl2.controller_id}")
    final = await online_agent.recv_type("screen_allowance", screen_session_id=sid2)
    assert final["final"] is True and final["reason"] == "controller_offline"
    cred = credential(env, ctrl2.controller_id or "")
    assert cred is not None and cred["state"] == "revoked" and cred["revoke_reason"] == "controller_revoked"
    ctrl2.ws = None


async def test_an_owner_absent_for_60s_gets_final_but_a_blip_does_not(
    env: Env, alice: Browser, online_agent: AgentSim, screen: ScreenService
) -> None:
    ctrl = await screen_phone(env, alice, online_agent)
    clock = screen_clock(screen)
    _, sid, _ = await start_session(env, ctrl, online_agent)
    await ctrl.close()  # the phone's socket drops (Wi-Fi blip), no logout
    await asyncio.sleep(0.2)
    clock.advance(30)
    await online_agent.screen_usage(sid)
    t = await online_agent.recv_type("screen_allowance", screen_session_id=sid)
    assert t["final"] is False  # within the grace period the stream continues
    clock.advance(31)
    counts = await screen.sweep()
    assert counts["offline"] >= 1  # the sweep is global; the frame below is this test's own
    final = await online_agent.recv_type("screen_allowance", screen_session_id=sid)
    assert final["final"] is True and final["reason"] == "controller_offline"
    [row] = reservation_rows(env, alice.account_id)
    assert row["final_reason"] == "controller_offline"
    # the phone reconnects: the session is already final; a new start is a fresh session
    await ctrl.connect()
    await online_agent.screen_usage(sid)
    assert (await online_agent.recv_type("screen_allowance", screen_session_id=sid))["reason"] == "controller_offline"
    await ctrl.close()


async def test_grant_narrowing_that_removes_screen_revokes_the_credential(
    env: Env, alice: Browser, online_agent: AgentSim, screen: ScreenService
) -> None:
    ctrl = await screen_phone(env, alice, online_agent)
    try:
        assert (await ctrl.screen_ice_request(online_agent.pc_id))["turn"] == "available"
        await online_agent.grant_update(ctrl.controller_id or "", ctrl.kid, ["status", "pointer"])
        await online_agent.recv_type("grants_snapshot")
        await asyncio.sleep(0.2)
        cred = credential(env, ctrl.controller_id or "")
        assert cred is not None and cred["state"] == "revoked" and cred["revoke_reason"] == "grant_narrowed"
        assert (await ctrl.screen_ice_request(online_agent.pc_id))["error"]["code"] == "GRANT_MISSING"
    finally:
        await ctrl.close()


# ----- the input-batch stamp gate and SCREEN_* rejections ----------------------------------------------------------------


async def test_stamp_gate_shields_a_1_2_agent_from_1_4_batches(
    env: Env, alice: Browser, linked_agent: AgentSim, screen: ScreenService
) -> None:
    await linked_agent.connect(protocol_versions=("1.0", "1.1", "1.2"), registry_version="1.2")
    ctrl = await screen_phone(env, alice, linked_agent)
    pc, sid = linked_agent.pc_id, new_input_session_id()
    try:
        # a plain touchpad batch (stamped 1.1 by content) still flows
        env1 = await ctrl.input_batch(pc, sid, 1, [MOVE, CLICK])
        assert loads_strict(env1["payload"])["protocol_version"] == "1.1"
        assert (await linked_agent.recv_type("input_batch"))["envelope"] == env1
        # a tap on the picture is stamped 1.4 and refused before forwarding: the agent never sees it
        env2 = await ctrl.input_batch(pc, sid, 2, [MOVE_TO, CLICK])
        assert loads_strict(env2["payload"])["protocol_version"] == "1.4"
        err = await expect_error(ctrl, pc, "PROTOCOL_INCOMPATIBLE")
        assert err["error"]["detail"] == {"peer": "agent", "required": "1.4"}
        await ctrl.input_batch(pc, sid, 2, [WHEEL])
        await expect_error(ctrl, pc, "PROTOCOL_INCOMPATIBLE")
        await ctrl.input_batch(pc, sid, 2, [{"type": "key", "key": "f11"}])
        await expect_error(ctrl, pc, "PROTOCOL_INCOMPATIBLE")
        await expect_nothing(linked_agent.ws, 0.3)  # type: ignore[arg-type]
        # the session continues: seq 2 was never forwarded, so it is still valid
        await ctrl.input_batch(pc, sid, 2, [MOVE])
        assert loads_strict((await linked_agent.recv_type("input_batch"))["envelope"]["payload"])["seq"] == 2
    finally:
        await ctrl.close()


async def test_1_4_batches_reach_a_1_4_agent_and_screen_rejections_reach_only_the_issuing_1_4_socket(
    env: Env, alice: Browser, online_agent: AgentSim, screen: ScreenService
) -> None:
    pc = online_agent.pc_id
    ctrl = await screen_phone(env, alice, online_agent)
    old = ControllerSim(env, alice, name="1.3 socket", key=ctrl.key)
    old.controller_id = ctrl.controller_id
    await old.connect(protocol_versions=("1.0", "1.1", "1.2", "1.3"), registry_version="1.3")
    watcher = await screen_phone(env, alice, online_agent, ("status", "screen"))
    await watcher.subscribe(pc)
    await watcher.recv_type("pc_status")
    sid = new_input_session_id()
    try:
        envl = await ctrl.input_batch(pc, sid, 1, [MOVE_TO, CLICK, WHEEL])
        fwd = await online_agent.recv_type("input_batch")
        assert fwd["envelope"] == envl
        await online_agent.input_session(sid, ctrl.controller_id or "")
        await ctrl.recv_type("input_session", input_session_id=sid)
        await old.recv_type("input_session", input_session_id=sid)  # a 1.1 frame still reaches the 1.3 socket
        await watcher.recv_type("input_session", input_session_id=sid)  # and the subscriber, as before
        # the agent refuses a move_to without a screen session: the rejection goes to the controller's 1.4 socket
        await online_agent.send(
            {
                "type": "error",
                "error": {"code": "SCREEN_SESSION_REQUIRED", "message": "Start Screen view first.", "retryable": False},
                "ref_input_session_id": sid,
                "ref_controller_id": ctrl.controller_id,
            }
        )
        err = await ctrl.recv_type("error")
        assert err["error"]["code"] == "SCREEN_SESSION_REQUIRED" and err["ref_pc_id"] == pc
        assert err["ref_input_session_id"] == sid and "ref_controller_id" not in err
        await online_agent.send(
            {
                "type": "error",
                "error": {"code": "SCREEN_GEOMETRY_CHANGED", "message": "Tap again.", "retryable": True},
                "ref_input_session_id": sid,
                "ref_controller_id": ctrl.controller_id,
            }
        )
        assert (await ctrl.recv_type("error"))["error"]["code"] == "SCREEN_GEOMETRY_CHANGED"
        await expect_nothing(old.ws, 0.3)  # type: ignore[arg-type]  # a 1.3 socket never sees a SCREEN_* code
        await expect_nothing(watcher.ws, 0.3)  # type: ignore[arg-type]  # nor does a subscriber
        # a SCREEN_* code for a controller outside the account is dropped
        await online_agent.send(
            {
                "type": "error",
                "error": {"code": "SCREEN_SESSION_REQUIRED", "message": "x", "retryable": False},
                "ref_controller_id": str(uuid.uuid4()),
            }
        )
        await expect_nothing(ctrl.ws, 0.3)  # type: ignore[arg-type]
    finally:
        for c in (ctrl, old, watcher):
            await c.close()


# ----- pairing: the PC key MAC -------------------------------------------------------------------------------------------


async def test_pairing_stores_pc_key_mac_and_returns_it_only_once_approved(
    env: Env, alice: Browser, bob: Browser, online_agent: AgentSim
) -> None:
    ctrl = ControllerSim(env, alice, name="Pinning phone")
    code, started = await online_agent.start_pairing()
    claimed = await ctrl.claim(code, SCREEN_CAPS[:3])
    assert "pc_key" not in claimed
    request = await online_agent.recv_type("pairing_request", pairing_id=started["pairing_id"])
    status = await alice.get(f"/v1/pairing/{started['pairing_id']}", schema="pairing_status_response")
    assert status["state"] == "claimed" and "pc_key" not in status  # never before approval
    await online_agent.decide_pairing(request, code)
    await online_agent.recv_type("grants_snapshot")
    final = await wait_pairing_state(alice, started["pairing_id"], "approved")
    assert final["pc_key"]["public_jwk"] == online_agent.jwk
    assert verify_pc_key_mac(
        code, started["pairing_id"], online_agent.pc_id, ctrl.kid, online_agent.kid, final["pc_key"]["mac"]
    )
    assert not verify_pc_key_mac(
        "WRONG" + code[5:],
        started["pairing_id"],
        online_agent.pc_id,
        ctrl.kid,
        online_agent.kid,
        final["pc_key"]["mac"],
    )
    # invisible to another account
    await bob.get(f"/v1/pairing/{started['pairing_id']}", expect=404)
    # readable for at least five minutes after approval (rules.pc_key_pinning (2); the route's constant pins it), even
    # past the code's own lifetime
    assert PAIRING_APPROVED_READABLE_SECONDS >= 300
    sql(
        env.database_url,
        "UPDATE pairing_sessions SET decided_at = now() - make_interval(secs => %s), "
        "expires_at = now() - interval '100 seconds' WHERE id = %s",
        (PAIRING_APPROVED_READABLE_SECONDS - 1, started["pairing_id"]),
    )
    later = await alice.get(f"/v1/pairing/{started['pairing_id']}", schema="pairing_status_response")
    assert later["state"] == "approved" and later["pc_key"]["mac"] == final["pc_key"]["mac"]
    # the stored MAC is exactly what the agent sent (the server cannot compute it: it never holds the code)
    [(stored,)] = sql(
        env.database_url, "SELECT pc_key_mac FROM pairing_sessions WHERE id = %s", (started["pairing_id"],)
    )
    assert stored == final["pc_key"]["mac"]


async def test_pairing_without_a_mac_from_an_older_agent_has_no_pc_key(
    env: Env, alice: Browser, online_agent: AgentSim
) -> None:
    ctrl = ControllerSim(env, alice, name="Phone of an old PC")
    code, started = await online_agent.start_pairing()
    await ctrl.claim(code)
    request = await online_agent.recv_type("pairing_request", pairing_id=started["pairing_id"])
    await online_agent.decide_pairing(request, code, pin_pc_key=False)
    await online_agent.recv_type("grants_snapshot")
    final = await wait_pairing_state(alice, started["pairing_id"], "approved")
    assert final["state"] == "approved" and "pc_key" not in final


async def test_no_sdp_or_credential_in_log_output(
    env: Env, alice: Browser, online_agent: AgentSim, screen: ScreenService, capfd: pytest.CaptureFixture[str]
) -> None:
    ctrl = await screen_phone(env, alice, online_agent)
    try:
        cid, sid, _ = await start_session(env, ctrl, online_agent)
        await online_agent.screen_answer(ctrl.controller_id or "", cid, sid)
        await ctrl.recv_type("screen_answer")
        await online_agent.screen_session(sid, ctrl.controller_id or "", "ended", "stopped")
        await ctrl.recv_type("screen_session", event="ended")
    finally:
        await ctrl.close()
    captured = capfd.readouterr()  # read once: a second read returns only what was written since
    text = captured.out + captured.err
    assert "screen.session_ended" in text  # the relay's logs (stderr) were captured: the check below is not vacuous
    for sentinel in (*SDP_SENTINELS, "FAKE-TURN-CREDENTIAL"):
        assert sentinel not in text


# ----- review round (2026-10-22): one start's revocation, a relay that forgot a pending start -------------------------


async def _lost_start_then_a_live_second(
    env: Env, ctrl: ControllerSim, agent: AgentSim, screen: ScreenService
) -> tuple[str, str, str]:
    """Start #1 is forwarded and never answered (the PC's socket blipped); the phone mints again and start #2 goes
    live. Returns (command #1, command #2, session #2)."""
    pc = agent.pc_id
    assert (await ctrl.screen_ice_request(pc))["turn"] == "available"
    cid1 = await ctrl.screen_start(pc)
    await agent.recv_type("command")
    screen_clock(screen).advance(10)
    cid2, sid2, _ = await start_session(env, ctrl, agent)  # a new ICE request (C2 replaces C1), then start #2
    return cid1, cid2, sid2


async def test_a_lost_starts_sweep_never_revokes_the_live_sessions_credential(
    env: Env, alice: Browser, online_agent: AgentSim, screen: ScreenService
) -> None:
    ctrl = await screen_phone(env, alice, online_agent)
    clock = screen_clock(screen)
    try:
        _cid1, _cid2, sid2 = await _lost_start_then_a_live_second(env, ctrl, online_agent, screen)
        live = credential(env, ctrl.controller_id or "")
        assert live is not None and live["state"] == "bound" and live["screen_session_id"] == sid2
        clock.advance(40)
        await online_agent.screen_usage(sid2)  # session #2 renews; start #1's reservation is left to lapse
        assert (await online_agent.recv_type("screen_allowance", screen_session_id=sid2))["final"] is False
        clock.advance(30)  # start #1's ticket ran out 10+ s ago
        assert (await screen.sweep())["swept"] >= 1
        rows = reservation_rows(env, alice.account_id)
        assert [r["state"] for r in rows] == ["swept", "live"]
        still = credential(env, ctrl.controller_id or "")
        assert still is not None and still["state"] == "bound" and still["screen_session_id"] == sid2
    finally:
        await ctrl.close()


async def test_a_late_failure_of_an_earlier_start_never_revokes_the_live_sessions_credential(
    env: Env, alice: Browser, online_agent: AgentSim, screen: ScreenService
) -> None:
    ctrl = await screen_phone(env, alice, online_agent)
    try:
        cid1, _cid2, sid2 = await _lost_start_then_a_live_second(env, ctrl, online_agent, screen)
        timeout = {"code": "SCREEN_CONSENT_TIMEOUT", "message": "No one answered at the PC.", "retryable": False}
        await online_agent.result(cid1, "failed", error=timeout)  # start #1's consent window timed out, late
        assert (await ctrl.recv_type("result", command_id=cid1))["state"] == "failed"
        await asyncio.sleep(0.2)
        still = credential(env, ctrl.controller_id or "")
        assert still is not None and still["state"] == "bound" and still["screen_session_id"] == sid2
        rows = reservation_rows(env, alice.account_id)
        assert rows[0]["end_reason"] == "consent_refused" and rows[0]["charged_seconds"] == 0
        assert rows[1]["state"] == "live"
    finally:
        await ctrl.close()


async def test_a_relay_that_forgot_a_pending_start_binds_the_reservation_instead_of_final(
    env: Env, alice: Browser, online_agent: AgentSim, screen: ScreenService
) -> None:
    """The PC's socket drops (or the relay restarts) between the forward and the result: the consent prompt and DTLS
    fit in that gap. The relay's memory of the pending start is gone, but its reservation is not: the agent's
    `started` and `screen_usage` bind it, and the tickets keep coming (never `final` with no reason, which the agent
    would show as Free time used up)."""
    pc = online_agent.pc_id
    ctrl = await screen_phone(env, alice, online_agent)
    try:
        assert (await ctrl.screen_ice_request(pc))["turn"] == "available"
        cid = await ctrl.screen_start(pc)
        await online_agent.recv_type("command")
        agent_conn = screen.relay.agent_for(uuid.UUID(pc))
        assert agent_conn is not None and agent_conn.screen_pending_starts
        agent_conn.screen_pending_starts.clear()  # what a reconnect or a relay restart leaves behind
        sid = new_screen_session_id()
        await online_agent.screen_session(sid, ctrl.controller_id or "", "started")
        await ctrl.recv_type("screen_session", screen_session_id=sid, event="started")
        [row] = reservation_rows(env, alice.account_id)
        assert row["screen_session_id"] == sid and row["state"] == "live" and row["started_at"] is not None
        await online_agent.screen_usage(sid)
        ticket = await online_agent.recv_type("screen_allowance", screen_session_id=sid)
        assert ticket["final"] is False and ticket["granted_until"] is not None
        bound = credential(env, ctrl.controller_id or "")
        assert bound is not None and bound["state"] == "bound" and bound["screen_session_id"] == sid
        del cid
    finally:
        await ctrl.close()
