"""Protocol 1.1 relay half of manual input (rules.input_sessions, rules.grant_update): signed input_batch
routing and every rejection rule (error frames, socket kept open except kid mismatch), grant coverage per
event type, PC offline, the per-controller input budget, input_ack / input_session delivery, grant_update
widen/narrow with snapshots and the REST grants list, pairing with the new capabilities."""

from __future__ import annotations

import asyncio
import datetime as dt
import time
import uuid
from typing import Any

from dome_protocol import dumps_compact, loads_strict, now_utc

from tests.conftest import (
    AgentSim,
    Browser,
    ControllerSim,
    Env,
    close_code,
    expect_nothing,
    new_input_session_id,
    sql,
)

INPUT_CAPS = ("status", "media", "volume", "pointer", "keyboard")
MOVE = {"type": "pointer_move", "dx": 5, "dy": -3}
CLICK = {"type": "pointer_button", "button": "left", "action": "click"}
TEXT = {"type": "text", "text": "hello"}
KEY = {"type": "key", "key": "enter"}


async def _expect_input_error(ctrl: ControllerSim, pc_id: str, code: str) -> dict[str, Any]:
    err = await ctrl.recv_type("error")
    assert err["error"]["code"] == code, err
    assert err["ref_pc_id"] == pc_id
    return err


async def _input_pair(env: Env, alice: Browser, agent: AgentSim, caps: tuple[str, ...] = INPUT_CAPS) -> ControllerSim:
    ctrl = ControllerSim(env, alice, name="Touchpad phone")
    await ctrl.pair(agent, caps)
    await ctrl.connect()
    return ctrl


# ----- forwarding ----------------------------------------------------------------------------------


async def test_input_batch_forwarded_verbatim_without_command_row_or_per_batch_event(
    env: Env, alice: Browser, online_agent: AgentSim
) -> None:
    ctrl = await _input_pair(env, alice, online_agent)
    try:
        sid = new_input_session_id()
        before = sql(env.database_url, "SELECT count(*) FROM commands WHERE account_id = %s", (alice.account_id,))[0][0]
        events_before = len((await alice.get("/v1/account/security-events?limit=200"))["events"])
        env1 = await ctrl.input_batch(online_agent.pc_id, sid, 1, [MOVE, MOVE, CLICK])
        fwd = await online_agent.recv_type("input_batch")
        assert fwd["envelope"] == env1  # verbatim: the relay never re-encodes the signed envelope
        assert uuid.UUID(fwd["relay"]["connection_id"]) and fwd["relay"]["received_at"]
        payload = loads_strict(fwd["envelope"]["payload"])
        assert payload["type"] == "input_batch" and payload["seq"] == 1 and payload["events"][2] == CLICK
        # keepalive (empty events) and a keyboard batch flow the same way
        await ctrl.input_batch(online_agent.pc_id, sid, 2, [])
        assert loads_strict((await online_agent.recv_type("input_batch"))["envelope"]["payload"])["events"] == []
        await ctrl.input_batch(online_agent.pc_id, sid, 3, [TEXT, KEY])
        assert loads_strict((await online_agent.recv_type("input_batch"))["envelope"]["payload"])["seq"] == 3
        await expect_nothing(ctrl.ws, 0.3)  # type: ignore[arg-type]  # no result, no ack from the relay
        after = sql(env.database_url, "SELECT count(*) FROM commands WHERE account_id = %s", (alice.account_id,))[0][0]
        assert after == before  # no commands row per batch
        events_after = (await alice.get("/v1/account/security-events?limit=200"))["events"]
        assert len(events_after) == events_before  # no security event per accepted batch
        assert not any(e["kind"] == "input_rejected" for e in events_after)
    finally:
        await ctrl.close()


async def test_forged_replayed_stale_batches_are_errors_and_keep_the_socket(
    env: Env, alice: Browser, online_agent: AgentSim
) -> None:
    ctrl = await _input_pair(env, alice, online_agent)
    pc = online_agent.pc_id
    sid = new_input_session_id()
    try:
        # forged signature
        envl = ctrl.input_envelope(pc, sid, 1, [MOVE])
        envl["sig"] = envl["sig"][:-2] + ("AA" if envl["sig"][-2:] != "AA" else "BB")
        await ctrl.send_envelope(pc, envl, kind="input_batch")
        await _expect_input_error(ctrl, pc, "SIGNATURE_INVALID")
        # payload names another controller of the same account / another account
        second = ControllerSim(env, alice, name="Second phone")
        await second.pair(online_agent)
        await ctrl.input_batch(pc, sid, 1, [MOVE], controller_id=second.controller_id)
        await _expect_input_error(ctrl, pc, "CONTROLLER_MISMATCH")
        await ctrl.input_batch(pc, sid, 1, [MOVE], account_id=str(uuid.uuid4()))
        await _expect_input_error(ctrl, pc, "ACCOUNT_MISMATCH")
        # stale: issued far outside the 5 s window
        await ctrl.input_batch(pc, sid, 1, [MOVE], issued_at=now_utc() - dt.timedelta(seconds=30))
        await _expect_input_error(ctrl, pc, "INPUT_STALE")
        # over-long window
        await ctrl.input_batch(pc, sid, 1, [MOVE], lifetime=60)
        await _expect_input_error(ctrl, pc, "MALFORMED_MESSAGE")
        # frame pc_id != payload target_pc_id, and a PC that is not on the account
        other_pc = str(uuid.uuid4())
        await ctrl.send_envelope(pc, ctrl.input_envelope(other_pc, sid, 1, [MOVE]), kind="input_batch")
        await _expect_input_error(ctrl, pc, "TARGET_PC_MISMATCH")
        await ctrl.input_batch(other_pc, sid, 1, [MOVE])
        await _expect_input_error(ctrl, other_pc, "ACCOUNT_MISMATCH")
        # the socket is still open and still works
        await ctrl.input_batch(pc, sid, 1, [MOVE])
        assert (await online_agent.recv_type("input_batch"))["envelope"]["kid"] == ctrl.kid
        # replay of a forwarded seq on this socket is dropped at the relay (the agent would drop it too)
        await ctrl.input_batch(pc, sid, 1, [CLICK])
        await _expect_input_error(ctrl, pc, "INPUT_SEQUENCE_INVALID")
        await ctrl.input_batch(pc, sid, 2, [CLICK])
        assert loads_strict((await online_agent.recv_type("input_batch"))["envelope"]["payload"])["seq"] == 2
        await expect_nothing(online_agent.ws, 0.3)  # type: ignore[arg-type]  # nothing rejected reached the PC
        # at most one input_rejected security event per minute per controller
        rejected = [
            e
            for e in (await alice.get("/v1/account/security-events?limit=200"))["events"]
            if e["kind"] == "input_rejected"
        ]
        assert len(rejected) == 1 and rejected[0]["detail"]["reason"] == "SIGNATURE_INVALID"
        assert "payload" not in str(rejected)  # never content
        await second.close()
    finally:
        await ctrl.close()


async def test_input_batch_kid_mismatch_closes_like_commands(env: Env, alice: Browser, online_agent: AgentSim) -> None:
    ctrl = await _input_pair(env, alice, online_agent)
    other = ControllerSim(env, alice)
    await ctrl.input_batch(online_agent.pc_id, new_input_session_id(), 1, [MOVE], key=other.key)
    await _expect_input_error(ctrl, online_agent.pc_id, "UNKNOWN_KEY")
    assert await close_code(ctrl.ws) == 4003  # type: ignore[arg-type]
    ctrl.ws = None


async def test_unpaired_and_protocol_1_0_sockets_cannot_send_input(
    env: Env, alice: Browser, online_agent: AgentSim
) -> None:
    stranger = ControllerSim(env, alice)
    await stranger.connect()
    await stranger.input_batch(online_agent.pc_id, new_input_session_id(), 1, [MOVE])
    await _expect_input_error(stranger, online_agent.pc_id, "GRANT_MISSING")
    await stranger.close()
    legacy = await _input_pair(env, alice, online_agent)
    await legacy.close()
    await legacy.connect(protocol_versions=("1.0",))
    await legacy.input_batch(online_agent.pc_id, new_input_session_id(), 1, [MOVE])
    await _expect_input_error(legacy, online_agent.pc_id, "PROTOCOL_INCOMPATIBLE")
    await legacy.close()


# ----- grant coverage ------------------------------------------------------------------------------


async def test_grant_coverage_per_event_type(env: Env, alice: Browser, online_agent: AgentSim) -> None:
    pc = online_agent.pc_id
    keyboard_only = await _input_pair(env, alice, online_agent, ("status", "keyboard"))
    sid = new_input_session_id()
    try:
        await keyboard_only.input_batch(pc, sid, 1, [TEXT, KEY, {"type": "shortcut", "name": "ctrl_a"}])
        assert loads_strict((await online_agent.recv_type("input_batch"))["envelope"]["payload"])["seq"] == 1
        await keyboard_only.input_batch(pc, sid, 2, [MOVE])
        err = await _expect_input_error(keyboard_only, pc, "INPUT_NOT_PERMITTED")
        assert err["error"]["detail"] == {"missing": ["pointer"]}
        await keyboard_only.input_batch(pc, sid, 2, [TEXT, CLICK])  # mixed: the whole batch is refused
        await _expect_input_error(keyboard_only, pc, "INPUT_NOT_PERMITTED")
        await keyboard_only.input_batch(pc, sid, 2, [{"type": "pointer_scroll", "dx": 0, "dy": 3}])
        await _expect_input_error(keyboard_only, pc, "INPUT_NOT_PERMITTED")
        # input.session_start is permitted by the keyboard capability alone (ActionSpec.satisfied_by)
        cid = await keyboard_only.command(pc, "input.session_start", {"takeover": False})
        fwd = await online_agent.recv_type("command")
        assert loads_strict(fwd["envelope"]["payload"])["command_id"] == cid
        await online_agent.ack(cid, "accepted")
        await online_agent.result(
            cid,
            "succeeded",
            result={
                "input_session_id": sid,
                "lease_seconds": 3,
                "input_age_budget_ms": 1000,
                "max_batch_events": 64,
                "pointer": False,
                "keyboard": True,
            },
        )
        assert (await keyboard_only.recv_type("result", command_id=cid))["state"] == "succeeded"
        # a session stop needs no pointer either
        cid = await keyboard_only.command(pc, "input.session_stop", {"input_session_id": sid})
        assert loads_strict((await online_agent.recv_type("command"))["envelope"]["payload"])["command_id"] == cid
        await online_agent.result(cid, "succeeded", result={"stopped": True, "released_holds": 0})
        assert (await keyboard_only.recv_type("result", command_id=cid))["state"] == "succeeded"
    finally:
        await keyboard_only.close()
    # a grant with neither capability (the default pairing) is INPUT_NOT_PERMITTED even for a keepalive,
    # and input.session_start is GRANT_MISSING
    plain = ControllerSim(env, alice, name="Media only")
    await plain.pair(online_agent)
    await plain.connect()
    try:
        await plain.input_batch(pc, sid, 1, [])
        await _expect_input_error(plain, pc, "INPUT_NOT_PERMITTED")
        cid = await plain.command(pc, "input.session_start")
        result = await plain.recv_type("result", command_id=cid)
        assert result["origin"] == "relay" and result["error"]["code"] == "GRANT_MISSING"
        await expect_nothing(online_agent.ws, 0.3)  # type: ignore[arg-type]
    finally:
        await plain.close()


async def test_pointer_only_grant_refuses_text(env: Env, alice: Browser, online_agent: AgentSim) -> None:
    pointer_only = await _input_pair(env, alice, online_agent, ("status", "pointer"))
    sid = new_input_session_id()
    try:
        await pointer_only.input_batch(online_agent.pc_id, sid, 1, [MOVE, CLICK])
        assert (await online_agent.recv_type("input_batch"))["type"] == "input_batch"
        await pointer_only.input_batch(online_agent.pc_id, sid, 2, [TEXT])
        err = await _expect_input_error(pointer_only, online_agent.pc_id, "INPUT_NOT_PERMITTED")
        assert err["error"]["detail"] == {"missing": ["keyboard"]}
    finally:
        await pointer_only.close()


# ----- PC state ------------------------------------------------------------------------------------


async def test_input_batch_to_offline_or_legacy_pc(env: Env, alice: Browser, online_agent: AgentSim) -> None:
    ctrl = await _input_pair(env, alice, online_agent)
    sid = new_input_session_id()
    try:
        await online_agent.close()
        await asyncio.sleep(0.2)
        await ctrl.input_batch(online_agent.pc_id, sid, 1, [MOVE])
        await _expect_input_error(ctrl, online_agent.pc_id, "PC_OFFLINE")
        # a 1.0 agent never receives input frames: the phone is told the PC needs an update
        await online_agent.connect(protocol_versions=("1.0",))
        await ctrl.input_batch(online_agent.pc_id, sid, 2, [MOVE])
        await _expect_input_error(ctrl, online_agent.pc_id, "PROTOCOL_INCOMPATIBLE")
        await expect_nothing(online_agent.ws, 0.3)  # type: ignore[arg-type]
        # disabled PC
        await alice.patch(f"/v1/pcs/{online_agent.pc_id}", {"enabled": False}, schema="pc")
        await ctrl.input_batch(online_agent.pc_id, sid, 3, [MOVE])
        await _expect_input_error(ctrl, online_agent.pc_id, "PC_PLAN_DISABLED")
    finally:
        await ctrl.close()


async def test_input_rate_budget_per_controller(env: Env, alice: Browser, online_agent: AgentSim) -> None:
    ctrl = await _input_pair(env, alice, online_agent)
    sid = new_input_session_id()
    try:
        total = 400
        # pre-sign, then flood the socket without per-frame client validation so the relay sees wire speed
        wire = [
            dumps_compact(
                {
                    "type": "input_batch",
                    "pc_id": online_agent.pc_id,
                    "envelope": ctrl.input_envelope(online_agent.pc_id, sid, seq, [MOVE]),
                }
            )
            for seq in range(1, total + 1)
        ]
        assert ctrl.ws is not None
        started = time.monotonic()
        for text in wire:
            await ctrl.ws.send(text)
        forwarded = 0
        limited = 0
        agent_done = asyncio.Event()

        async def drain_agent() -> None:
            nonlocal forwarded
            while True:
                try:
                    frame = await online_agent.recv(timeout=1.0)
                except TimeoutError:
                    agent_done.set()
                    return
                if frame["type"] == "input_batch":
                    forwarded += 1

        async def drain_ctrl() -> None:
            nonlocal limited
            while (
                not agent_done.is_set()
            ):  # refusals interleave with the forwarded burst; drain until the agent is quiet
                try:
                    frame = await ctrl.recv(timeout=0.3)
                except TimeoutError:
                    continue
                if frame["type"] == "error" and frame["error"]["code"] == "RATE_LIMITED":
                    limited += 1

        await asyncio.gather(drain_agent(), drain_ctrl())
        elapsed = time.monotonic() - started
        burst, per_second = 80, 40  # plans.json input_rate_limit; version.json input_batches_per_second
        assert limited >= 1  # refusals are error frames (throttled to one per second), not a closed socket
        assert burst <= forwarded <= burst + per_second * (elapsed + 0.5), (forwarded, elapsed)
        assert forwarded < total
        # the socket survived the flood and still forwards once the bucket refilled
        await asyncio.sleep(0.3)
        await ctrl.input_batch(online_agent.pc_id, sid, total + 1, [])
        assert loads_strict((await online_agent.recv_type("input_batch"))["envelope"]["payload"])["seq"] == total + 1
    finally:
        await ctrl.close()


# ----- agent → controller frames -------------------------------------------------------------------


async def test_acks_reach_owner_sockets_and_session_events_reach_subscribers(
    env: Env, alice: Browser, online_agent: AgentSim
) -> None:
    pc = online_agent.pc_id
    owner = await _input_pair(env, alice, online_agent)
    owner2 = ControllerSim(env, alice, key=owner.key, controller_id=owner.controller_id)  # same phone, second tab
    await owner2.connect()
    watcher = ControllerSim(env, alice, name="Other phone")
    await watcher.pair(online_agent)
    await watcher.connect()
    await watcher.subscribe(pc)
    assert (await watcher.recv())["type"] == "pc_status"
    sid = new_input_session_id()
    try:
        # an ack before the agent announced the session goes nowhere (no guessing)
        await online_agent.input_ack(sid, 0)
        await expect_nothing(owner.ws, 0.4)  # type: ignore[arg-type]
        started = await online_agent.input_session(sid, owner.controller_id or "", "started", "started")
        for sock in (owner, owner2, watcher):
            frame = await sock.recv_type("input_session")
            assert frame == started
        ack = await online_agent.input_ack(sid, 7, accepted=12, held_buttons=["left"])
        assert (await owner.recv_type("input_ack")) == ack
        assert (await owner2.recv_type("input_ack")) == ack
        await expect_nothing(watcher.ws, 0.4)  # type: ignore[arg-type]  # acks are owner-only
        ended = await online_agent.input_session(
            sid, owner.controller_id or "", "ended", "lease_expired", holds_released=1
        )
        assert (await owner.recv_type("input_session"))["reason"] == "lease_expired"
        assert (await watcher.recv_type("input_session")) == ended
        # after `ended` the owner mapping is gone: a late ack is dropped
        await online_agent.input_ack(sid, 8)
        await expect_nothing(owner.ws, 0.4)  # type: ignore[arg-type]
        # a session frame naming a controller outside the account is refused and audited
        await online_agent.input_session(new_input_session_id(), str(uuid.uuid4()), "started", "started")
        await expect_nothing(watcher.ws, 0.4)  # type: ignore[arg-type]
        kinds = [
            e for e in (await alice.get("/v1/account/security-events"))["events"] if e["kind"] == "relay_frame_rejected"
        ]
        assert any(e["detail"].get("frame") == "input_session" for e in kinds)
    finally:
        await owner.close()
        await owner2.close()
        await watcher.close()


async def test_protocol_1_0_sockets_never_receive_input_frames(
    env: Env, alice: Browser, online_agent: AgentSim
) -> None:
    owner = await _input_pair(env, alice, online_agent)
    legacy = ControllerSim(env, alice, key=owner.key, controller_id=owner.controller_id)
    await legacy.connect(protocol_versions=("1.0",))
    await legacy.subscribe(online_agent.pc_id)
    assert (await legacy.recv())["type"] == "pc_status"
    sid = new_input_session_id()
    try:
        await online_agent.input_session(sid, owner.controller_id or "", "started", "started")
        assert (await owner.recv_type("input_session"))["input_session_id"] == sid
        await online_agent.input_ack(sid, 1)
        assert (await owner.recv_type("input_ack"))["last_seq"] == 1
        await expect_nothing(legacy.ws, 0.5)  # type: ignore[arg-type]
    finally:
        await owner.close()
        await legacy.close()


# ----- grant_update --------------------------------------------------------------------------------


async def test_grant_update_widens_then_narrows_with_snapshots_and_rest(
    env: Env, alice: Browser, online_agent: AgentSim
) -> None:
    pc = online_agent.pc_id
    ctrl = ControllerSim(env, alice, name="Phone")
    await ctrl.pair(online_agent)  # status/media/volume: no input yet
    await ctrl.connect()
    await ctrl.subscribe(pc)
    assert (await ctrl.recv())["type"] == "pc_status"
    sid = new_input_session_id()
    try:
        await ctrl.input_batch(pc, sid, 1, [MOVE])
        await _expect_input_error(ctrl, pc, "INPUT_NOT_PERMITTED")
        # widen locally on the PC
        await online_agent.grant_update(
            ctrl.controller_id or "", ctrl.kid, ["status", "media", "volume", "pointer", "keyboard"]
        )
        snap = await online_agent.recv_type("grants_snapshot")
        assert snap["controllers"][0]["capabilities"] == ["keyboard", "media", "pointer", "status", "volume"]
        assert (await ctrl.recv_type("pc_status"))["pc_id"] == pc  # subscribers are nudged to refresh
        grants = (await alice.get(f"/v1/pcs/{pc}/grants", schema="grants_response"))["grants"]
        assert grants[0]["id"] == ctrl.grant_id and grants[0]["capabilities"] == [
            "keyboard",
            "media",
            "pointer",
            "status",
            "volume",
        ]
        await ctrl.input_batch(pc, sid, 2, [MOVE, TEXT])
        assert loads_strict((await online_agent.recv_type("input_batch"))["envelope"]["payload"])["seq"] == 2
        # narrow: keyboard stays, pointer goes
        await online_agent.grant_update(ctrl.controller_id or "", ctrl.kid, ["status", "keyboard"])
        snap = await online_agent.recv_type("grants_snapshot")
        assert snap["controllers"][0]["capabilities"] == ["keyboard", "status"]
        await ctrl.recv_type("pc_status")
        grants = (await alice.get(f"/v1/pcs/{pc}/grants", schema="grants_response"))["grants"]
        assert grants[0]["capabilities"] == ["keyboard", "status"]
        await ctrl.input_batch(pc, sid, 3, [TEXT])
        assert loads_strict((await online_agent.recv_type("input_batch"))["envelope"]["payload"])["seq"] == 3
        await ctrl.input_batch(pc, sid, 4, [MOVE])
        await _expect_input_error(ctrl, pc, "INPUT_NOT_PERMITTED")
        cid = await ctrl.command(pc, "windows.set_volume", {"value": 10})  # volume was removed too
        assert (await ctrl.recv_type("result", command_id=cid))["error"]["code"] == "GRANT_MISSING"
        updates = [
            e for e in (await alice.get("/v1/account/security-events"))["events"] if e["kind"] == "grant_updated"
        ]
        assert len(updates) == 2
        assert updates[0]["detail"]["removed"] == ["media", "pointer", "volume"] and updates[0]["actor"] == "pc"
        assert updates[1]["detail"]["added"] == ["keyboard", "pointer"]
    finally:
        await ctrl.close()


async def test_grant_update_for_foreign_or_unrelated_controller_is_refused(
    env: Env, alice: Browser, bob: Browser, online_agent: AgentSim, paired: ControllerSim
) -> None:
    bob_agent = AgentSim(env)
    await bob_agent.link(bob, "Bob PC")
    await bob_agent.connect()
    mallory = ControllerSim(env, bob, name="Bob phone")
    await mallory.pair(bob_agent)
    try:
        # alice's PC names bob's controller: refused, bob's grant untouched
        await online_agent.grant_update(mallory.controller_id or "", mallory.kid, ["status", "pointer", "keyboard"])
        await expect_nothing(online_agent.ws, 0.5)  # type: ignore[arg-type]  # no snapshot push
        grants = (await bob.get(f"/v1/pcs/{bob_agent.pc_id}/grants"))["grants"]
        assert grants[0]["capabilities"] == ["media", "status", "volume"]
        # alice's own controller but wrong kid: refused
        await online_agent.grant_update(paired.controller_id or "", mallory.kid, ["status", "pointer"])
        await expect_nothing(online_agent.ws, 0.5)  # type: ignore[arg-type]
        # a controller of the account without a grant on THIS PC: refused (grant_update cannot create grants)
        bob_only = ControllerSim(env, alice, name="Other PC phone")
        second_pc = AgentSim(env)
        sql(env.database_url, "UPDATE accounts SET plan = 'pro' WHERE id = %s", (alice.account_id,))
        await second_pc.link(alice, "Second PC")
        await second_pc.connect()
        await bob_only.pair(second_pc)
        await online_agent.grant_update(bob_only.controller_id or "", bob_only.kid, ["status", "pointer"])
        await expect_nothing(online_agent.ws, 0.5)  # type: ignore[arg-type]
        assert (await alice.get(f"/v1/pcs/{online_agent.pc_id}/grants"))["grants"][0]["capabilities"] == [
            "media",
            "status",
            "volume",
        ]
        assert len((await alice.get(f"/v1/pcs/{online_agent.pc_id}/grants"))["grants"]) == 1
        rejected = [
            e for e in (await alice.get("/v1/account/security-events"))["events"] if e["kind"] == "relay_frame_rejected"
        ]
        assert {e["detail"]["reason"] for e in rejected if e["detail"].get("frame") == "grant_update"} == {
            "controller_not_in_account",
            "no_live_grant",
        }
        await second_pc.close()
    finally:
        await mallory.close()
        await bob_agent.close()


# ----- pairing with the new capabilities -----------------------------------------------------------


async def test_pairing_may_request_pointer_and_keyboard(env: Env, alice: Browser, online_agent: AgentSim) -> None:
    ctrl = ControllerSim(env, alice)
    final = await ctrl.pair(
        online_agent, ("status", "media", "pointer", "keyboard"), granted=["status", "media", "keyboard", "lock"]
    )
    assert final["granted_capabilities"] == ["keyboard", "media", "status"]  # granted ∩ requested
    assert online_agent.snapshot is not None
    assert online_agent.snapshot["controllers"][0]["capabilities"] == ["keyboard", "media", "status"]
    grants = (await alice.get(f"/v1/pcs/{online_agent.pc_id}/grants", schema="grants_response"))["grants"]
    assert grants[0]["capabilities"] == ["keyboard", "media", "status"]


async def test_agent_input_rejections_reach_only_the_batch_controller(
    env: Env, alice: Browser, bob: Browser, online_agent: AgentSim
) -> None:
    """rules.input_sessions: an agent INPUT_* error frame naming ref_controller_id is delivered to that controller's
    1.1 sockets with ref_pc_id added and ref_controller_id stripped; other phones never see it; a controller outside
    the PC's account or contradicting the known session owner is refused and audited; plain agent errors stay logs."""
    pc = online_agent.pc_id
    owner = await _input_pair(env, alice, online_agent)
    watcher = ControllerSim(env, alice, name="Other phone")
    await watcher.pair(online_agent)
    await watcher.connect()
    await watcher.subscribe(pc)
    assert (await watcher.recv())["type"] == "pc_status"
    sid = new_input_session_id()
    try:
        # a retired session (unknown to the relay): routed by ref_controller_id after the account check
        await online_agent.send(
            {
                "type": "error",
                "error": {"code": "INPUT_SESSION_EXPIRED", "message": "x", "retryable": True},
                "ref_input_session_id": sid,
                "ref_controller_id": owner.controller_id,
            }
        )
        err = await owner.recv_type("error")
        assert err == {
            "type": "error",
            "error": {"code": "INPUT_SESSION_EXPIRED", "message": "x", "retryable": True},
            "ref_pc_id": pc,
            "ref_input_session_id": sid,
        }
        await expect_nothing(watcher.ws, 0.4)  # type: ignore[arg-type]

        # a live session owned by `owner`: an error naming another controller for it is refused
        await online_agent.input_session(sid, owner.controller_id or "", "started", "started")
        await owner.recv_type("input_session")
        await watcher.recv_type("input_session")
        await online_agent.send(
            {
                "type": "error",
                "error": {"code": "INPUT_STALE", "message": "x", "retryable": True},
                "ref_input_session_id": sid,
                "ref_controller_id": watcher.controller_id,
            }
        )
        await expect_nothing(watcher.ws, 0.4)  # type: ignore[arg-type]
        await expect_nothing(owner.ws, 0.2)  # type: ignore[arg-type]

        # a controller of another account is never a route
        stranger = ControllerSim(env, bob, name="Bob's phone")
        await online_agent.send(
            {
                "type": "error",
                "error": {"code": "INPUT_STALE", "message": "x", "retryable": True},
                "ref_controller_id": str(uuid.uuid4()),
            }
        )
        await expect_nothing(owner.ws, 0.4)  # type: ignore[arg-type]
        assert stranger.ws is None
        reasons = [
            e["detail"].get("reason")
            for e in (await alice.get("/v1/account/security-events"))["events"]
            if e["kind"] == "relay_frame_rejected" and e["detail"].get("frame") == "error"
        ]
        assert "owner_mismatch" in reasons and "controller_not_in_account" in reasons

        # a non-input agent error is only logged
        await online_agent.send(
            {"type": "error", "error": {"code": "MALFORMED_MESSAGE", "message": "x", "retryable": False}}
        )
        await expect_nothing(owner.ws, 0.4)  # type: ignore[arg-type]
    finally:
        await owner.close()
        await watcher.close()
