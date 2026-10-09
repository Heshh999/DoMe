"""Spec §10A / §17 scenarios 18-20 through the real relay and the real agent process (fake input
adapter): manual-input permission, the signed input stream, ordering and replay rules, lease expiry
releasing held input, takeover, revocation, and content hygiene.

Evidence tag: integration-tested (Linux, DOME_AGENT_PLATFORM=fake). The fake input adapter records
injections inside the agent process; the test observes them only through the contract's
``input_ack`` / ``input_session`` frames, which is exactly what a phone sees. Windows SendInput and
the real phone keyboard are not exercised here.
"""

from __future__ import annotations

import asyncio
import time
from datetime import timedelta
from typing import Any

from dome_protocol import now_utc

from conftest import ALL_CAPS, ControllerSim, Env, RealAgent, drain_subscribe, run_command

INPUT_CAPS = ("pointer", "keyboard")
MARKER = "zq-typed-content-7731"


async def _grant_input(agent: RealAgent, phone: Any) -> None:
    """The PC owner adds touchpad/keyboard for an already-paired phone (rules.grant_update)."""
    res = await agent.control("grant", controller_id=phone.controller_id, add=list(INPUT_CAPS))
    assert set(INPUT_CAPS) <= set(res["capabilities"])
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        grants = await phone.browser.get(f"/v1/pcs/{agent.pc_id}/grants")
        mine = [g for g in grants["grants"] if g["controller_id"] == phone.controller_id]
        status = await agent.control("grant", controller_id=phone.controller_id, add=[])
        if mine and set(INPUT_CAPS) <= set(mine[0]["capabilities"]) and set(INPUT_CAPS) <= set(status["effective_capabilities"]):
            return
        await asyncio.sleep(0.2)
    raise AssertionError("grant_update never reached the relay / came back in the snapshot")


async def _start(phone: Any, pc_id: str, **params: Any) -> dict[str, Any]:
    res = await run_command(phone, pc_id, "input.session_start", params or {})
    return res


async def _ack(phone: Any, session_id: str, last_seq: int, timeout: float = 10) -> dict[str, Any]:
    """Wait for an input_ack that covers ``last_seq`` (acks are rate-limited, so earlier ones may arrive first)."""
    deadline = time.monotonic() + timeout
    while True:
        ack = await phone.recv_type("input_ack", timeout=max(0.1, deadline - time.monotonic()), input_session_id=session_id)
        if ack["last_seq"] >= last_seq:
            return ack


async def test_input_needs_a_local_grant_then_streams_in_order_and_releases_on_lease_expiry(
    agent: RealAgent, phone: Any
) -> None:
    # 1. Existing pairings never gain pointer/keyboard implicitly: the relay refuses the session.
    res = await _start(phone, agent.pc_id)
    assert res["state"] == "failed" and res["error"]["code"] in ("GRANT_MISSING", "INPUT_NOT_PERMITTED"), res

    # 2. The PC owner grants them locally; grant_update widens the relay's grant and the snapshot follows.
    await _grant_input(agent, phone)
    res = await _start(phone, agent.pc_id)
    assert res["state"] == "succeeded", res
    session = res["result"]
    sid = session["input_session_id"]
    assert session["pointer"] is True and session["keyboard"] is True
    assert session["lease_seconds"] >= 1 and session["max_batch_events"] <= 64
    # the `started` frame can arrive before the command result (run_command skips it while waiting), so the
    # session is proven live by the first ack below rather than by frame order

    # 3. A batch with motion, a held button, literal text and a key is accepted by the (fake) OS in order.
    await phone.input_batch(
        agent.pc_id,
        sid,
        1,
        [
            {"type": "pointer_move", "dx": 10, "dy": 5},
            {"type": "pointer_move", "dx": -3, "dy": 2},
            {"type": "pointer_button", "button": "left", "action": "down"},
            {"type": "text", "text": MARKER},
            {"type": "key", "key": "enter"},
        ],
    )
    ack = await _ack(phone, sid, 1)
    assert ack["accepted_events"] >= 4 and ack["held_buttons"] == ["left"], ack

    # 4. A replayed sequence number is refused (the relay drops it as defence in depth before the PC's own
    #    authoritative check); the phone is told why and the session continues with the next one.
    accepted_before = ack["accepted_events"]
    await phone.input_batch(agent.pc_id, sid, 1, [{"type": "key", "key": "escape"}])
    err = await phone.recv_type("error", timeout=10)
    assert err["error"]["code"] == "INPUT_SEQUENCE_INVALID" and err["ref_pc_id"] == agent.pc_id, err
    await phone.input_batch(agent.pc_id, sid, 2, [{"type": "pointer_scroll", "dx": 0, "dy": -1}])
    ack = await _ack(phone, sid, 2)
    assert ack["last_seq"] == 2 and ack["accepted_events"] == accepted_before + 1, ack  # the escape never ran

    # 5. A batch older than the input-age budget (but inside its signed window) is discarded by the PC, not played
    #    back; the rejection reaches this phone with the session it named.
    before = ack["accepted_events"]
    await phone.input_batch(agent.pc_id, sid, 3, [{"type": "key", "key": "tab"}], issued_at=now_utc() - timedelta(seconds=2))
    err = await phone.recv_type("error", timeout=10)
    assert err["error"]["code"] == "INPUT_STALE" and err.get("ref_input_session_id") == sid, err
    assert "ref_controller_id" not in err
    await phone.input_batch(agent.pc_id, sid, 4, [])  # keepalive
    ack = await _ack(phone, sid, 4)
    assert ack["accepted_events"] == before and ack["dropped_events"] >= 1, ack

    # 6. The phone goes quiet with the left button held: the PC-side lease expires on its own, the session is
    #    retired first and the held button is released (no stuck drag).
    ended = await phone.recv_type("input_session", timeout=15, input_session_id=sid, event="ended")
    assert ended["reason"] == "lease_expired" and ended["holds_released"] >= 1, ended

    # 7. Traffic for the retired session is refused, never replayed.
    await phone.input_batch(agent.pc_id, sid, 5, [{"type": "pointer_move", "dx": 1, "dy": 1}])
    err = await phone.recv_type("error", timeout=10)
    assert err["error"]["code"] in ("INPUT_SESSION_EXPIRED", "INPUT_SESSION_REQUIRED"), err

    # 8. Typed content never reaches the relay's lifecycle rows or anything the agent wrote to disk.
    rows = (await phone.browser.get(f"/v1/commands?pc_id={agent.pc_id}&limit=50"))["commands"]
    assert {r["action"] for r in rows} <= {"input.session_start", "input.session_stop"}
    for path in agent.state_dir.rglob("*"):
        if path.is_file():
            assert MARKER.encode() not in path.read_bytes(), f"typed text found in {path}"


async def test_one_owner_per_pc_takeover_and_revocation_end_the_session(
    env: Env, alice: Any, agent: RealAgent, phone: Any
) -> None:
    await _grant_input(agent, phone)
    res = await _start(phone, agent.pc_id)
    assert res["state"] == "succeeded", res
    first = res["result"]["input_session_id"]
    await phone.input_batch(agent.pc_id, first, 1, [{"type": "pointer_button", "button": "left", "action": "down"}])
    await _ack(phone, first, 1)

    # A second phone on the same account, paired with touchpad permission granted at pairing.
    tablet = ControllerSim(env, alice, name="Alice's iPad")
    await agent.pair(tablet, (*ALL_CAPS[:3], *INPUT_CAPS))
    ack = await tablet.connect()
    assert ack["controller_id"] == tablet.controller_id
    await drain_subscribe(tablet, agent.pc_id)
    try:
        res = await _start(tablet, agent.pc_id)
        assert res["state"] == "failed" and res["error"]["code"] == "INPUT_SESSION_OWNED", res

        # Explicit takeover: the old session ends (holds released first), the new one starts.
        res = await _start(tablet, agent.pc_id, takeover=True)
        assert res["state"] == "succeeded", res
        second = res["result"]["input_session_id"]
        ended = await phone.recv_type("input_session", timeout=10, input_session_id=first, event="ended")
        assert ended["reason"] == "takeover" and ended["holds_released"] >= 1, ended
        await tablet.input_batch(agent.pc_id, second, 1, [{"type": "pointer_move", "dx": 4, "dy": 4}])
        await _ack(tablet, second, 1)

        # The old owner's batches are refused now.
        await phone.input_batch(agent.pc_id, first, 2, [{"type": "pointer_move", "dx": 1, "dy": 1}])
        err = await phone.recv_type("error", timeout=10)
        assert err["error"]["code"].startswith("INPUT_"), err

        # Revoking the new owner on the PC ends its session; subscribers see the reason.
        await agent.control("revoke", controller_id=tablet.controller_id)
        ended = await phone.recv_type("input_session", timeout=15, input_session_id=second, event="ended")
        assert ended["reason"] in ("controller_revoked", "grant_removed"), ended
    finally:
        await tablet.close()


async def test_keyboard_only_grant_cannot_move_the_pointer(agent: RealAgent, phone: Any) -> None:
    res = await agent.control("grant", controller_id=phone.controller_id, add=["keyboard"])
    assert "keyboard" in res["capabilities"] and "pointer" not in res["capabilities"]
    deadline = time.monotonic() + 15
    while True:
        res = await _start(phone, agent.pc_id)
        if res["state"] == "succeeded" or time.monotonic() > deadline:
            break
        await asyncio.sleep(0.3)
    assert res["state"] == "succeeded", res
    session = res["result"]
    assert session["keyboard"] is True and session["pointer"] is False
    sid = session["input_session_id"]
    await phone.input_batch(agent.pc_id, sid, 1, [{"type": "pointer_move", "dx": 5, "dy": 5}])
    err = await phone.recv_type("error", timeout=10)
    assert err["error"]["code"] == "INPUT_NOT_PERMITTED", err
    await phone.input_batch(agent.pc_id, sid, 2, [{"type": "text", "text": "ok"}])
    ack = await _ack(phone, sid, 2)
    assert ack["accepted_events"] >= 1
    res = await run_command(phone, agent.pc_id, "input.session_stop", {"input_session_id": sid})
    assert res["state"] == "succeeded" and res["result"]["stopped"] is True, res
