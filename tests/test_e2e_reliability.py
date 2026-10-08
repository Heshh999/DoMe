"""Spec §17.9–11 and the confirmed power path (§10, §17 note): truthful outcomes when the PC dies
mid-execution, nothing queued for an offline PC, slider bursts, and the confirmation transaction."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from dome_agent.testing.fake_extension import FakeTab

from conftest import REGISTRY, SCHEMAS, RealAgent, confirmation_envelope, run_command, wait_state, youtube_target


async def test_crash_after_side_effect_is_outcome_unknown_and_never_repeated(agent: RealAgent, phone: Any) -> None:
    tab = FakeTab(tab_id=5, video_id="before000001", next_videos=["after0000002"])
    ext = await agent.connect_extension(tab, op_delay=2.5)  # the browser "takes a while" to report the transition

    cid = await phone.command(agent.pc_id, "youtube.next", {}, youtube_target(ext, tab))
    await phone.recv_type("ack", command_id=cid, state="executing", timeout=10)
    await asyncio.sleep(0.3)  # the op has reached the browser (side effect in flight)
    assert [r["op"] for r in ext.requests] == ["next"]
    await agent.kill()  # SIGKILL between the OS action and recording its result

    res = await phone.recv_type("result", command_id=cid, timeout=15)
    assert res["origin"] == "relay" and res["state"] == "outcome_unknown"
    status = await phone.recv_type("pc_status", pc_id=agent.pc_id, timeout=10)
    assert status["connection"] in ("offline", "reconnecting")

    # restart: the journal says outcome_unknown and the agent does not try again
    await agent.start()
    store = agent.store()
    try:
        row = store.journal_get(cid)
    finally:
        store.close()
    assert row is not None and row.state == "outcome_unknown"
    ext2 = await agent.connect_extension(FakeTab(tab_id=5, video_id="after0000002", tab_token=tab.tab_token))
    await asyncio.sleep(1.5)
    assert ext2.requests == []  # no re-execution of the ambiguous Next
    # a fresh user retry is a new command and works
    res = await run_command(phone, agent.pc_id, "system.ping")
    assert res["state"] == "succeeded"


async def test_offline_pc_never_queues(agent: RealAgent, phone: Any) -> None:
    ext = await agent.connect_extension(FakeTab(tab_id=1))
    await agent.stop()
    status = await phone.recv_type("pc_status", pc_id=agent.pc_id, timeout=10)
    assert status["connection"] == "offline"
    t0 = time.monotonic()
    res = await run_command(phone, agent.pc_id, "windows.set_volume", {"value": 77}, timeout=10)
    assert res["origin"] == "relay" and res["state"] == "failed" and res["error"]["code"] == "PC_OFFLINE"
    assert time.monotonic() - t0 < 5  # answered immediately, nothing queued
    assert res["error"]["retryable"] is True

    await agent.start()
    await phone.recv_type("pc_status", pc_id=agent.pc_id, connection="online", timeout=15)
    await asyncio.sleep(1.0)
    res = await run_command(phone, agent.pc_id, "windows.get_volume")
    assert res["result"]["value"] != 77  # the offline command was never executed later
    assert ext.requests == []


async def test_volume_slider_burst_coalesces_to_the_latest_value(agent: RealAgent, phone: Any) -> None:
    ids: list[str] = []
    for value in range(1, 21):
        ids.append(await phone.command(agent.pc_id, "windows.set_volume", {"value": value}))
        await asyncio.sleep(0.05)
    results: dict[str, dict[str, Any]] = {}
    deadline = time.monotonic() + 20
    while len(results) < len(ids) and time.monotonic() < deadline:
        frame = await phone.recv(timeout=10)
        if frame["type"] == "result" and frame["command_id"] in ids:
            results[frame["command_id"]] = frame
    assert len(results) == len(ids), "every command in the burst must terminate"
    codes = {r["error"]["code"] for r in results.values() if r["state"] != "succeeded"}
    assert codes <= {"COMMAND_SUPERSEDED"}, codes  # no RATE_LIMITED, no QUEUE_FULL
    assert results[ids[-1]]["state"] == "succeeded"
    res = await run_command(phone, agent.pc_id, "windows.get_volume")
    assert res["result"]["value"] == 20


async def test_confirmed_power_countdown_and_cancel(agent: RealAgent, phone: Any) -> None:
    # 1) sleep with a short countdown: challenge → approve → armed → fires (fake platform records it)
    cid = await phone.command(agent.pc_id, "power.sleep", {"countdown_seconds": 2}, lifetime=90)
    req = await phone.recv_type("confirmation_required", command_id=cid, timeout=10)
    challenge = SCHEMAS.validate_challenge_text(req["challenge_text"])
    assert challenge["command_id"] == cid and challenge["controller_id"] == phone.controller_id
    assert challenge["params"] == {"countdown_seconds": 2} and challenge["display"]["pc_name"] == agent.name
    await phone.send_envelope(agent.pc_id, confirmation_envelope(phone, agent.pc_id, cid, req["challenge_text"]), kind="confirmation")
    res = await phone.recv_type("result", command_id=cid, timeout=15)
    assert res["state"] == "succeeded", res
    REGISTRY.validate_result("power.sleep", res["result"])
    assert res["result"]["accepted"] is True and res["result"]["countdown_seconds"] == 2

    # 2) a long countdown can be cancelled from the phone
    cid = await phone.command(agent.pc_id, "power.restart", {"countdown_seconds": 60}, lifetime=90)
    req = await phone.recv_type("confirmation_required", command_id=cid, timeout=10)
    await phone.send_envelope(agent.pc_id, confirmation_envelope(phone, agent.pc_id, cid, req["challenge_text"]), kind="confirmation")
    await phone.recv_type("ack", command_id=cid, state="executing", timeout=10)
    state = await wait_state(phone, agent.pc_id, lambda s: s.get("pending_power_action") is not None)
    pending = state["state"]["pending_power_action"]
    assert pending["action"] == "power.restart" and pending["command_id"] == cid
    cancel_id = await phone.command(agent.pc_id, "power.cancel")
    got = {}
    for _ in range(2):
        frame = await phone.recv_type("result", timeout=10)
        got[frame["command_id"]] = frame
    assert got[cancel_id]["state"] == "succeeded" and got[cancel_id]["result"]["canceled"] is True
    assert got[cid]["state"] == "canceled" and got[cid]["error"]["code"] == "POWER_CANCELED"

    # 3) declining on the phone cancels the command
    cid = await phone.command(agent.pc_id, "power.shutdown", {}, lifetime=90)
    req = await phone.recv_type("confirmation_required", command_id=cid, timeout=10)
    await phone.send_envelope(
        agent.pc_id, confirmation_envelope(phone, agent.pc_id, cid, req["challenge_text"], decision="decline"), kind="confirmation"
    )
    res = await phone.recv_type("result", command_id=cid, timeout=10)
    assert res["state"] in ("failed", "canceled") and res["error"]["code"] == "CONFIRMATION_DECLINED"
    status = await agent.control("status")
    assert status["pending_power"] is None


async def test_superseded_then_reconnect_refreshes_state_and_keeps_grants(agent: RealAgent, phone: Any) -> None:
    """Another instance took over this PC's connection (4001): the agent does not fight back, the
    relay reports the PC offline when that instance leaves, and a manual Reconnect restores service
    with grants re-synchronised before any command is accepted."""
    ext = await agent.connect_extension(FakeTab(tab_id=2))
    await agent.displace()
    await phone.recv_type("pc_status", pc_id=agent.pc_id, connection="offline", timeout=20)
    res = await run_command(phone, agent.pc_id, "system.ping")
    assert res["origin"] == "relay" and res["error"]["code"] == "PC_OFFLINE"
    await agent.resume()
    await phone.recv_type("pc_status", pc_id=agent.pc_id, connection="online", timeout=20)
    # the PC re-synchronised its grants before accepting commands and sent fresh state
    state = await wait_state(phone, agent.pc_id, lambda s: s["remote_enabled"] is True, timeout=15)
    assert state["state"]["extension_connected"] is True
    res = await run_command(phone, agent.pc_id, "system.ping")
    assert res["state"] == "succeeded" and res["result"]["protocol_version"] == "1.0"
    assert ext.requests == []
