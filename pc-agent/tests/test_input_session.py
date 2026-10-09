"""Manual touchpad/keyboard sessions end to end: fake relay → agent → fake input adapter
(spec §10A, rules.input_sessions, design brief §3.2/3.3). Nothing here proves Windows injection."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import pytest
from dome_protocol import format_rfc3339, load_schemas, now_utc

from dome_agent.input_session import HOLDS_FILENAME, AgeEstimator, coalesce_events
from dome_agent.platform.protocol import ForegroundApp

from .conftest import AgentHarness
from .helpers import Controller, payload_of, relay_input_batch_frame

MOVE = {"type": "pointer_move", "dx": 3, "dy": -2}
LEFT_DOWN = {"type": "pointer_button", "button": "left", "action": "down"}
LEFT_UP = {"type": "pointer_button", "button": "left", "action": "up"}
CLICK = {"type": "pointer_button", "button": "left", "action": "click"}


async def wait_for(pred: Callable[[], bool], timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("condition not met in time")


async def start_input(h: AgentHarness, controller: Controller, *, takeover: bool = False) -> dict[str, Any]:
    env = controller.command("input.session_start", {"takeover": takeover})
    cid = payload_of(env)["command_id"]
    await h.send_command(env)
    res = await h.result(cid)
    load_schemas().validate_frame("agent_to_relay", res)
    return res


async def stop_input(h: AgentHarness, controller: Controller, sid: str) -> dict[str, Any]:
    env = controller.command("input.session_stop", {"input_session_id": sid})
    cid = payload_of(env)["command_id"]
    await h.send_command(env)
    return await h.result(cid)


async def send_batch(
    h: AgentHarness, controller: Controller, sid: str, seq: int, events: list[dict[str, Any]], **kw: Any
) -> None:
    env = controller.input_batch(sid, seq, events, **kw)
    await h.relay.send(relay_input_batch_frame(env, h.agent.relay.connection_id if h.agent.relay else None))


async def expect_session_event(
    h: AgentHarness, event: str, reason: str | None = None, timeout: float = 5.0
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while True:
        frame = await h.relay.expect("input_session", timeout=max(0.1, deadline - time.monotonic()))
        if frame["event"] == event and (reason is None or frame["reason"] == reason):
            load_schemas().validate_frame("agent_to_relay", frame)
            return frame


async def expect_ack(h: AgentHarness, last_seq: int, timeout: float = 5.0) -> dict[str, Any]:
    """The ack that reports ``last_seq`` (earlier acks are consumed; acks carry the state at send time)."""
    deadline = time.monotonic() + timeout
    while True:
        frame = await h.relay.expect("input_ack", timeout=max(0.1, deadline - time.monotonic()))
        if frame["last_seq"] == last_seq:
            load_schemas().validate_frame("agent_to_relay", frame)
            return frame


async def expect_state(h: AgentHarness, pred: Callable[[dict[str, Any]], bool], timeout: float = 5.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while True:
        frame = await h.relay.expect("state", timeout=max(0.1, deadline - time.monotonic()))
        if pred(frame["state"]):
            return frame


async def expect_error(h: AgentHarness, code: str, timeout: float = 5.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while True:
        frame = await h.relay.expect("error", timeout=max(0.1, deadline - time.monotonic()))
        if frame["error"]["code"] == code:
            return frame


async def second_phone(h: AgentHarness, controller: Controller, caps: tuple[str, ...]) -> Controller:
    other = Controller(controller.account_id, controller.pc_id, display_name="Other phone", capabilities=caps)
    other.grant_locally(h.agent.store)
    h.relay.controllers.append(other.snapshot_entry())
    await h.relay.send_snapshot()
    await h.relay.expect("state")
    return other


@pytest.fixture
def fast(harness: AgentHarness) -> AgentHarness:
    harness.agent.input.lease_seconds = 0.6
    harness.agent.input.lock_poll_seconds = 0.15
    return harness


# ----- unit: coalescing ------------------------------------------------------------------------------------------


def test_coalescing_sums_only_adjacent_motion() -> None:
    events = [
        {"type": "pointer_move", "dx": 1, "dy": 2},
        {"type": "pointer_move", "dx": 3, "dy": 4},
        CLICK,
        {"type": "pointer_move", "dx": 5, "dy": 5},
        {"type": "text", "text": "a"},
        {"type": "pointer_move", "dx": -1, "dy": 0},
        {"type": "pointer_move", "dx": 1, "dy": 0},
    ]
    out = coalesce_events(events)
    assert [(e["type"], n) for e, n in out] == [
        ("pointer_move", 2),
        ("pointer_button", 1),
        ("pointer_move", 1),
        ("text", 1),
        ("pointer_move", 2),
    ]
    assert out[0][0] == {"type": "pointer_move", "dx": 4, "dy": 6}
    assert out[-1][0] == {"type": "pointer_move", "dx": 0, "dy": 0}  # kept and counted, nothing injected


# ----- lifecycle --------------------------------------------------------------------------------------------------


async def test_session_start_result_and_state(harness: AgentHarness, controller: Controller) -> None:
    harness.fake.foreground_app = ForegroundApp("notepad.exe", "Untitled - Notepad", None, False, "100", 42)
    res = await start_input(harness, controller)
    assert res["state"] == "succeeded", res
    r = res["result"]
    assert len(r["input_session_id"]) == 22 and r["pointer"] and r["keyboard"]
    assert r["lease_seconds"] == 3 and r["input_age_budget_ms"] == 1000 and r["max_batch_events"] == 64
    # no window title in the (durably journaled) result: it travels only in memory-only state frames
    assert r["foreground_app"] == {"process_name": "notepad.exe", "elevated": False}
    row = harness.agent.store.journal_get(res["command_id"])
    assert row is not None and "Untitled - Notepad" not in json.dumps(row, default=str)
    started = await expect_session_event(harness, "started", "started")
    assert started["controller_id"] == controller.controller_id and started["holds_released"] == 0
    state = await harness.relay.expect("state", timeout=5)
    assert state["state"]["input_session"]["controller_id"] == controller.controller_id
    assert state["state"]["foreground_app"]["process_name"] == "notepad.exe"
    assert state["state"]["foreground_app"]["window_title"] == "Untitled - Notepad"
    assert state["state"]["input_restricted"] is False
    assert harness.agent.status()["input"]["session"]["state"] == "live"


async def test_keyboard_only_grant_can_start_but_not_point(harness: AgentHarness, controller: Controller) -> None:
    kb = await second_phone(harness, controller, ("status", "keyboard"))
    res = await start_input(harness, kb)
    assert res["state"] == "succeeded" and res["result"]["pointer"] is False and res["result"]["keyboard"] is True
    sid = res["result"]["input_session_id"]
    await send_batch(harness, kb, sid, 1, [MOVE])
    err = await expect_error(harness, "INPUT_NOT_PERMITTED")
    assert err["error"]["retryable"] is False
    await send_batch(harness, kb, sid, 2, [{"type": "text", "text": "ok"}])
    await wait_for(lambda: harness.fake.input_count("text") == 1)
    assert harness.fake.input_count("move") == 0
    ack = await expect_ack(harness, 2)
    assert ack["dropped_events"] == 1 and ack["accepted_events"] == 1


async def test_session_start_refused_when_locked_or_disabled_or_unsupported(
    harness: AgentHarness, controller: Controller
) -> None:
    harness.fake.locked = True
    res = await start_input(harness, controller)
    assert res["state"] == "failed" and res["error"]["code"] == "PC_SESSION_LOCKED"
    harness.fake.locked = False
    await harness.agent.set_remote_enabled(False)
    res = await start_input(harness, controller)
    assert res["error"]["code"] == "PC_REMOTE_DISABLED"
    await harness.agent.set_remote_enabled(True)
    from dome_agent.platform.unsupported import build_unsupported_platform

    harness.agent.services.platform = build_unsupported_platform()
    res = await start_input(harness, controller)
    assert res["error"]["code"] == "PLATFORM_UNSUPPORTED"


async def test_no_input_capability_is_grant_missing(harness: AgentHarness, controller: Controller) -> None:
    plain = await second_phone(harness, controller, ("status", "media"))
    res = await start_input(harness, plain)
    assert res["state"] == "failed" and res["error"]["code"] == "GRANT_MISSING"
    assert "'pointer' or 'keyboard'" in res["error"]["message"]


# ----- batches ----------------------------------------------------------------------------------------------------


async def test_ordering_and_coalescing_through_the_adapter(harness: AgentHarness, controller: Controller) -> None:
    sid = (await start_input(harness, controller))["result"]["input_session_id"]
    events = [
        {"type": "pointer_move", "dx": 1, "dy": 2},
        {"type": "pointer_move", "dx": 3, "dy": 4},
        CLICK,
        {"type": "pointer_move", "dx": 5, "dy": 5},
        {"type": "pointer_scroll", "dx": 0, "dy": -2},
        {"type": "text", "text": "ab"},
        {"type": "key", "key": "enter"},
        {"type": "shortcut", "name": "ctrl_a"},
    ]
    await send_batch(harness, controller, sid, 1, events)
    await wait_for(lambda: len(harness.fake.input_events) >= 7)
    assert [(r.name, r.args) for r in harness.fake.input_events] == [
        ("move", (4, 6)),
        ("button", ("left", "click")),
        ("move", (5, 5)),
        ("scroll", (0, -2)),
        ("text", ("ab",)),
        ("key", ("enter",)),
        ("shortcut", ("ctrl_a",)),
    ]
    ats = [r.at for r in harness.fake.input_events]
    assert ats == sorted(ats)
    ack = await harness.relay.expect("input_ack")
    load_schemas().validate_frame("agent_to_relay", ack)
    assert ack == {**ack, "last_seq": 1, "accepted_events": 8, "dropped_events": 0, "held_buttons": [], "held_keys": []}
    assert harness.fake.held_keys == set()  # ctrl released after the shortcut


async def test_replayed_seq_and_stale_batches_are_dropped(harness: AgentHarness, controller: Controller) -> None:
    sid = (await start_input(harness, controller))["result"]["input_session_id"]
    await send_batch(harness, controller, sid, 1, [MOVE])
    await wait_for(lambda: harness.fake.input_count("move") == 1)
    await send_batch(harness, controller, sid, 1, [MOVE])  # replay
    await expect_error(harness, "INPUT_SEQUENCE_INVALID")
    await send_batch(harness, controller, sid, 2, [MOVE], issued_at=now_utc() - timedelta(seconds=2))  # stale
    err = await expect_error(harness, "INPUT_STALE")
    assert err["error"]["retryable"] is True
    await send_batch(harness, controller, sid, 3, [MOVE])
    await wait_for(lambda: harness.fake.input_count("move") == 2)
    ack = await expect_ack(harness, 3)
    assert ack["dropped_events"] == 2 and ack["accepted_events"] == 2
    assert harness.agent.input.current is not None and harness.agent.input.current.live  # rejections never end it


def test_age_estimator_removes_a_constant_clock_offset() -> None:
    est = AgeEstimator(lower=-5.0, upper=10.0, window_seconds=30.0)
    # phone 2 s behind: every delta is latency + 2; the extra delay over the fastest batch is what counts
    for t, delta in ((0.0, 2.05), (0.5, 2.04), (1.0, 2.06)):
        est.observe(delta, t)
        assert est.age(delta) < 0.05
    est.observe(4.6, 1.5)  # a 2.5 s stall
    assert est.age(4.6) > 2.5
    # phone 2 s ahead: negative deltas; a stall is still visible
    ahead = AgeEstimator(lower=-5.0, upper=10.0)
    for t, delta in ((0.0, -1.95), (0.5, -1.96)):
        ahead.observe(delta, t)
    ahead.observe(0.6, 1.0)
    assert ahead.age(0.6) > 2.5
    # samples expire: after a phone clock correction the baseline follows within the window
    est.observe(0.05, 40.0)
    est.observe(0.06, 40.5)
    assert est.baseline == 0.05 and est.age(0.06) < 0.05
    # the baseline is clamped to what the envelope window admits
    wild = AgeEstimator(lower=-5.0, upper=10.0)
    wild.observe(-30.0, 0.0)
    assert wild.baseline == -5.0


@pytest.mark.parametrize("skew", [-2.0, 2.0])
async def test_phone_clock_skew_neither_kills_input_nor_hides_a_stall(
    harness: AgentHarness, controller: Controller, skew: float
) -> None:
    """Review finding: the 1 s age budget was compared against the PC clock directly, so a phone 2 s behind
    made every batch INPUT_STALE and a phone 2 s ahead let a 2.5 s stall replay old motion."""

    def phone_now() -> Any:
        return now_utc() + timedelta(seconds=skew)

    env = controller.command("input.session_start", {"takeover": False}, issued_at=phone_now())
    await harness.send_command(env)
    res = await harness.result(payload_of(env)["command_id"])
    assert res["state"] == "succeeded", res
    sid = res["result"]["input_session_id"]
    for seq in (1, 2, 3):
        await send_batch(harness, controller, sid, seq, [MOVE], issued_at=phone_now())
    await wait_for(lambda: harness.fake.input_count("move") == 3)
    await send_batch(harness, controller, sid, 4, [MOVE], issued_at=phone_now() - timedelta(seconds=2.5))  # stalled
    await expect_error(harness, "INPUT_STALE")
    await send_batch(harness, controller, sid, 5, [MOVE], issued_at=phone_now())
    await wait_for(lambda: harness.fake.input_count("move") == 4)
    assert harness.agent.input.summary()["session"]["dropped_events"] == 1


async def test_relay_received_at_bounds_the_relay_to_agent_leg(harness: AgentHarness, controller: Controller) -> None:
    sid = (await start_input(harness, controller))["result"]["input_session_id"]
    conn = str(uuid.uuid4())
    for seq in (1, 2):
        await harness.relay.send(relay_input_batch_frame(controller.input_batch(sid, seq, [MOVE]), conn))
    await wait_for(lambda: harness.fake.input_count("move") == 2)
    # fresh phone stamp, but the relay received it 2.5 s ago: it sat between relay and agent
    frame = relay_input_batch_frame(controller.input_batch(sid, 3, [MOVE]), conn)
    frame["relay"]["received_at"] = format_rfc3339(now_utc() - timedelta(seconds=2.5))
    await harness.relay.send(frame)
    await expect_error(harness, "INPUT_STALE")
    assert harness.fake.input_count("move") == 2


async def test_forged_and_misaddressed_batches_are_rejected(harness: AgentHarness, controller: Controller) -> None:
    sid = (await start_input(harness, controller))["result"]["input_session_id"]
    stranger = Controller(controller.account_id, controller.pc_id)
    await send_batch(harness, controller, sid, 1, [MOVE], key=stranger.key)  # signed by another key
    await expect_error(harness, "UNKNOWN_KEY")
    assert any(e["kind"] == "input_rejected" for e in harness.agent.store.list_security_events())
    env = controller.input_batch(sid, 2, [MOVE])
    env["payload"] = env["payload"].replace('"dx":3', '"dx":4')  # tampered bytes
    await harness.relay.send(relay_input_batch_frame(env))
    await expect_error(harness, "SIGNATURE_INVALID")
    await send_batch(harness, controller, sid, 3, [MOVE], target_pc_id=str(uuid.uuid4()))
    await expect_error(harness, "TARGET_PC_MISMATCH")
    await send_batch(harness, controller, "A" * 22, 4, [MOVE])  # not the live session
    await expect_error(harness, "INPUT_SESSION_REQUIRED")
    assert harness.fake.input_events == []


async def test_batch_without_session_is_required(harness: AgentHarness, controller: Controller) -> None:
    await send_batch(harness, controller, "B" * 22, 1, [MOVE])
    await expect_error(harness, "INPUT_SESSION_REQUIRED")


async def test_injection_failures_are_reported_not_faked(harness: AgentHarness, controller: Controller) -> None:
    sid = (await start_input(harness, controller))["result"]["input_session_id"]
    harness.fake.input_fail = "INPUT_INJECTION_FAILED"
    harness.fake.input_fail_remaining = 1
    await send_batch(harness, controller, sid, 1, [LEFT_DOWN, MOVE])
    err = await expect_error(harness, "INPUT_INJECTION_FAILED")
    assert err["error"]["retryable"] is False
    ack = await expect_ack(harness, 1)
    assert ack["dropped_events"] == 2 and ack["held_buttons"] == []  # the failed down is not a hold
    harness.fake.input_fail = "INPUT_RESTRICTED"
    await send_batch(harness, controller, sid, 2, [{"type": "text", "text": "x"}])
    await expect_error(harness, "INPUT_RESTRICTED")
    assert harness.fake.input_events == []


# ----- lease / holds ----------------------------------------------------------------------------------------------


async def test_lease_expiry_releases_held_button_and_ends(fast: AgentHarness, controller: Controller) -> None:
    sid = (await start_input(fast, controller))["result"]["input_session_id"]
    await send_batch(fast, controller, sid, 1, [LEFT_DOWN, MOVE])
    await wait_for(lambda: fast.fake.held_buttons == {"left"})
    ack = await expect_ack(fast, 1)
    assert ack["held_buttons"] == ["left"]
    assert json.loads((fast.settings.state_dir / HOLDS_FILENAME).read_text())["held_buttons"] == ["left"]
    ended = await expect_session_event(fast, "ended", "lease_expired")
    assert ended["holds_released"] == 1 and ended["input_session_id"] == sid
    assert fast.fake.held_buttons == set()
    assert fast.fake.input_events[-1].name == "release" and fast.fake.input_events[-1].args == (("left",), ())
    assert not (fast.settings.state_dir / HOLDS_FILENAME).exists()
    await send_batch(fast, controller, sid, 2, [MOVE])  # delayed traffic for the retired id
    await expect_error(fast, "INPUT_SESSION_EXPIRED")
    assert fast.fake.input_count("move") == 1
    await expect_state(fast, lambda st: st["input_session"] is None)


async def test_click_on_a_held_button_clears_the_hold(harness: AgentHarness, controller: Controller) -> None:
    sid = (await start_input(harness, controller))["result"]["input_session_id"]
    await send_batch(harness, controller, sid, 1, [LEFT_DOWN, MOVE])
    await wait_for(lambda: harness.agent.input.summary()["session"]["held_buttons"] == ["left"])
    await send_batch(harness, controller, sid, 2, [CLICK])  # drag mode, then a tap: Windows released it
    await wait_for(lambda: harness.fake.input_count("button") == 2)
    assert harness.agent.input.summary()["session"]["held_buttons"] == []
    assert not (harness.settings.state_dir / HOLDS_FILENAME).exists()
    stopped = await stop_input(harness, controller, sid)
    assert stopped["result"]["released_holds"] == 0  # no extra UP sent later
    assert harness.fake.input_count("release") == 0


async def test_dispatch_outliving_the_bounded_wait_is_released_when_it_returns(
    harness: AgentHarness, controller: Controller
) -> None:
    """Review finding: ``_end`` waits at most a bounded time for the adapter thread; a press that lands
    after the release ran must be released as soon as the thread returns, not left to the next restart."""
    harness.agent.input.inflight_wait_seconds = 0.1
    sid = (await start_input(harness, controller))["result"]["input_session_id"]
    harness.fake.input_delay_seconds = 0.6  # SendInput "blocks"
    await send_batch(harness, controller, sid, 1, [LEFT_DOWN])
    await asyncio.sleep(0.2)  # the down is in flight
    stopped = await stop_input(harness, controller, sid)
    assert stopped["result"]["stopped"] is True
    await wait_for(lambda: harness.fake.input_count("button") == 1)  # the slow press landed after the end
    await wait_for(lambda: harness.fake.held_buttons == set() and harness.fake.input_count("release") == 1)
    assert not (harness.settings.state_dir / HOLDS_FILENAME).exists()
    harness.fake.input_delay_seconds = 0.0


async def test_shortcut_whose_recovery_release_failed_keeps_the_keys_tracked(
    harness: AgentHarness, controller: Controller
) -> None:
    sid = (await start_input(harness, controller))["result"]["input_session_id"]
    # an ordinary failure: the adapter released the shortcut keys itself, nothing stays tracked
    harness.fake.input_fail, harness.fake.input_fail_remaining = "INPUT_INJECTION_FAILED", 1
    await send_batch(harness, controller, sid, 1, [{"type": "shortcut", "name": "ctrl_c"}])
    await expect_error(harness, "INPUT_INJECTION_FAILED")
    assert harness.agent.input.summary()["session"]["held_keys"] == []
    # the recovery release failed too: CTRL and the letter may be down; keep them for release/crash recovery
    harness.fake.input_fail, harness.fake.input_fail_remaining = "INPUT_INJECTION_FAILED", 1
    harness.fake.input_fail_stuck = True
    await send_batch(harness, controller, sid, 2, [{"type": "shortcut", "name": "ctrl_v"}])
    await wait_for(lambda: harness.agent.input.summary()["session"]["held_keys"] == ["ctrl", "ctrl_v"])
    assert json.loads((harness.settings.state_dir / HOLDS_FILENAME).read_text())["held_keys"] == ["ctrl", "ctrl_v"]
    harness.fake.input_fail_stuck = False
    ended = await stop_input(harness, controller, sid)
    assert ended["result"]["released_holds"] == 2 and harness.fake.held_keys == set()
    assert not (harness.settings.state_dir / HOLDS_FILENAME).exists()


async def test_keepalives_renew_the_lease(fast: AgentHarness, controller: Controller) -> None:
    sid = (await start_input(fast, controller))["result"]["input_session_id"]
    for seq in range(1, 7):
        await send_batch(fast, controller, sid, seq, [])
        await asyncio.sleep(0.2)
    assert fast.agent.input.current is not None and fast.agent.input.current.live
    ack = await expect_ack(fast, 6)
    assert ack["accepted_events"] == 0


async def test_session_stop_releases_holds_and_is_idempotent(harness: AgentHarness, controller: Controller) -> None:
    sid = (await start_input(harness, controller))["result"]["input_session_id"]
    await send_batch(harness, controller, sid, 1, [LEFT_DOWN])
    await wait_for(lambda: harness.fake.held_buttons == {"left"})
    res = await stop_input(harness, controller, sid)
    assert res["state"] == "succeeded" and res["result"] == {"stopped": True, "released_holds": 1}
    await expect_session_event(harness, "ended", "stopped")
    assert harness.fake.held_buttons == set()
    res = await stop_input(harness, controller, sid)
    assert res["result"] == {"stopped": False, "released_holds": 0}
    # another phone cannot stop a session it does not own
    other = await second_phone(harness, controller, ("status", "pointer"))
    sid2 = (await start_input(harness, controller))["result"]["input_session_id"]
    res = await stop_input(harness, other, sid2)
    assert res["result"]["stopped"] is False and harness.agent.input.current is not None


# ----- ownership --------------------------------------------------------------------------------------------------


async def test_takeover_releases_holds_before_the_new_session(harness: AgentHarness, controller: Controller) -> None:
    other = await second_phone(harness, controller, ("status", "pointer", "keyboard"))
    sid_a = (await start_input(harness, controller))["result"]["input_session_id"]
    await send_batch(harness, controller, sid_a, 1, [LEFT_DOWN])
    await wait_for(lambda: harness.fake.held_buttons == {"left"})
    res = await start_input(harness, other)
    assert res["state"] == "failed" and res["error"]["code"] == "INPUT_SESSION_OWNED"
    assert harness.fake.held_buttons == {"left"}
    res = await start_input(harness, other, takeover=True)
    assert res["state"] == "succeeded"
    sid_b = res["result"]["input_session_id"]
    ended = await expect_session_event(harness, "ended", "takeover")
    started = await expect_session_event(harness, "started")
    assert ended["input_session_id"] == sid_a and ended["holds_released"] == 1
    assert started["input_session_id"] == sid_b and started["controller_id"] == other.controller_id
    release_index = next(i for i, r in enumerate(harness.fake.input_events) if r.name == "release")
    assert release_index == len(harness.fake.input_events) - 1  # release happened before anything of B
    await send_batch(harness, controller, sid_a, 2, [MOVE])
    await expect_error(harness, "INPUT_SESSION_EXPIRED")
    await send_batch(harness, other, sid_b, 1, [MOVE])
    await wait_for(lambda: harness.fake.input_count("move") == 1)


async def test_same_phone_restart_replaces_its_own_session(harness: AgentHarness, controller: Controller) -> None:
    sid1 = (await start_input(harness, controller))["result"]["input_session_id"]
    sid2 = (await start_input(harness, controller))["result"]["input_session_id"]
    assert sid1 != sid2
    await expect_session_event(harness, "ended", "stopped")
    await send_batch(harness, controller, sid1, 1, [MOVE])
    await expect_error(harness, "INPUT_SESSION_EXPIRED")


# ----- end triggers -----------------------------------------------------------------------------------------------


async def test_snapshot_revocation_ends_session(harness: AgentHarness, controller: Controller) -> None:
    sid = (await start_input(harness, controller))["result"]["input_session_id"]
    await send_batch(harness, controller, sid, 1, [LEFT_DOWN])
    await wait_for(lambda: harness.fake.held_buttons == {"left"})
    harness.relay.controllers.clear()
    await harness.relay.send_snapshot()
    ended = await expect_session_event(harness, "ended", "controller_revoked")
    assert ended["holds_released"] == 1 and harness.fake.held_buttons == set()
    await send_batch(harness, controller, sid, 2, [MOVE])
    await expect_error(harness, "CONTROLLER_REVOKED")


async def test_local_revocation_ends_session(harness: AgentHarness, controller: Controller) -> None:
    await start_input(harness, controller)
    assert await harness.agent.revoke_controller_locally(controller.controller_id)
    await expect_session_event(harness, "ended", "controller_revoked")


async def test_grant_narrowing_and_removal(harness: AgentHarness, controller: Controller) -> None:
    sid = (await start_input(harness, controller))["result"]["input_session_id"]
    await send_batch(harness, controller, sid, 1, [LEFT_DOWN])
    await wait_for(lambda: harness.fake.held_buttons == {"left"})
    row = await harness.agent.update_grant_capabilities(controller.controller_id, remove=["pointer"])
    assert row is not None and "pointer" not in row.capabilities and "keyboard" in row.capabilities
    frame = await harness.relay.expect("grant_update")
    assert frame["controller_id"] == controller.controller_id and "pointer" not in frame["capabilities"]
    assert harness.fake.held_buttons == set()  # the drag cannot continue without the pointer capability
    assert harness.agent.input.current is not None and harness.agent.input.current.live
    await send_batch(harness, controller, sid, 2, [MOVE])
    await expect_error(harness, "INPUT_NOT_PERMITTED")  # the session's flags follow the grant
    await send_batch(harness, controller, sid, 3, [{"type": "text", "text": "x"}])
    await wait_for(lambda: harness.fake.input_count("text") == 1)
    await harness.agent.update_grant_capabilities(controller.controller_id, remove=["keyboard"])
    await expect_session_event(harness, "ended", "grant_removed")
    # the snapshot path narrows too: relay lists the phone without input capabilities
    await harness.agent.update_grant_capabilities(controller.controller_id, add=["pointer", "keyboard"])
    await harness.relay.expect("grant_update")
    sid = (await start_input(harness, controller))["result"]["input_session_id"]
    harness.relay.controllers[0] = controller.snapshot_entry(capabilities=("status", "media"))
    await harness.relay.send_snapshot()
    await expect_session_event(harness, "ended", "grant_removed")


async def test_lock_ends_session(fast: AgentHarness, controller: Controller) -> None:
    sid = (await start_input(fast, controller))["result"]["input_session_id"]
    await send_batch(fast, controller, sid, 1, [LEFT_DOWN])
    await wait_for(lambda: fast.fake.held_buttons == {"left"})
    fast.fake.locked = True
    ended = await expect_session_event(fast, "ended", "session_locked")
    assert ended["holds_released"] == 1 and fast.fake.held_buttons == set()


async def test_secure_desktop_ends_session_but_elevated_window_only_restricts(
    fast: AgentHarness, controller: Controller
) -> None:
    fast.agent.input.lease_seconds = 10.0  # no keepalives in this test: only the desktop probes may end it
    fast.agent.input.foreground_refresh_seconds = 0.3
    fast.fake.foreground_app = ForegroundApp("taskmgr.exe", "", None, True, "7", 7)
    await start_input(fast, controller)
    fast.fake.input_restricted = True  # elevated window in front: restricted, session stays
    await asyncio.sleep(0.6)
    assert fast.agent.input.current is not None and fast.agent.input.current.live
    await expect_state(fast, lambda st: st["input_restricted"] is True and st["input_session"] is not None)
    fast.fake.secure_desktop = True  # the adapter reports a protected input desktop (UAC consent / sign-in)
    await expect_session_event(fast, "ended", "secure_desktop")


async def test_elevated_window_with_stale_or_unknown_foreground_does_not_end_the_session(
    fast: AgentHarness, controller: Controller
) -> None:
    """Review finding: the watchdog used the cached foreground (refreshed every 2 s, None during a switch,
    ``elevated`` None when unknown) to decide between 'secure desktop' and 'elevated window'."""
    fast.agent.input.lease_seconds = 10.0
    fast.agent.input.foreground_refresh_seconds = 60.0  # the cache stays stale for the whole test
    fast.fake.foreground_app = ForegroundApp("notepad.exe", "doc", None, False, "1", 10)
    sid = (await start_input(fast, controller))["result"]["input_session_id"]
    # an elevated window comes to front: integrity unknown to us (OpenProcess denied) and the cache is stale
    fast.fake.foreground_app = ForegroundApp("unknown", "", None, None, "2", 20)
    fast.fake.input_restricted = True
    await asyncio.sleep(0.8)  # several lock/desktop polls
    session = fast.agent.input.current
    assert session is not None and session.live, "an elevated window must only restrict, never end the session"
    assert fast.agent.input.summary()["input_restricted"] is True
    # keyboard input is refused honestly (Windows UIPI would drop it silently), pointer input still runs
    await send_batch(fast, controller, sid, 1, [MOVE, {"type": "text", "text": "secret"}])
    await expect_error(fast, "INPUT_RESTRICTED")
    await wait_for(lambda: fast.agent.input.summary()["session"]["dropped_events"] == 1)
    assert fast.fake.input_count("move") == 1 and fast.fake.input_count("text") == 0
    assert fast.agent.input.summary()["session"]["accepted_events"] == 1


async def test_remote_disable_ends_session(harness: AgentHarness, controller: Controller) -> None:
    sid = (await start_input(harness, controller))["result"]["input_session_id"]
    await send_batch(harness, controller, sid, 1, [LEFT_DOWN])
    await wait_for(lambda: harness.fake.held_buttons == {"left"})
    await harness.agent.set_remote_enabled(False)
    ended = await expect_session_event(harness, "ended", "remote_disabled")
    assert ended["holds_released"] == 1
    await harness.agent.set_remote_enabled(True)
    await send_batch(harness, controller, sid, 2, [MOVE])
    await expect_error(harness, "INPUT_SESSION_EXPIRED")


async def test_relay_disconnect_ends_session_and_old_id_is_never_replayed(
    harness: AgentHarness, controller: Controller
) -> None:
    sid = (await start_input(harness, controller))["result"]["input_session_id"]
    await send_batch(harness, controller, sid, 1, [LEFT_DOWN])
    await wait_for(lambda: harness.fake.held_buttons == {"left"})
    await harness.relay.close(1012, "deploy")
    await wait_for(lambda: harness.agent.input.current is None and harness.fake.held_buttons == set())
    await harness.relay.wait_connected(timeout=15)
    await harness.wait_snapshot_applied()
    await send_batch(harness, controller, sid, 2, [MOVE])
    await expect_error(harness, "INPUT_SESSION_EXPIRED")
    assert harness.fake.input_count("move") == 0


# ----- target change -------------------------------------------------------------------------------------------------


async def test_target_change_stops_typing_until_the_customer_continues(
    harness: AgentHarness, controller: Controller
) -> None:
    harness.fake.foreground_app = ForegroundApp("notepad.exe", "doc", None, False, "1", 10)
    sid = (await start_input(harness, controller))["result"]["input_session_id"]
    await send_batch(harness, controller, sid, 1, [{"type": "text", "text": "hello"}])
    await wait_for(lambda: harness.fake.input_count("text") == 1)
    harness.fake.foreground_app = ForegroundApp("chrome.exe", "tab", "chrome", False, "2", 20)
    await send_batch(
        harness, controller, sid, 2, [MOVE, {"type": "text", "text": "x"}, {"type": "key", "key": "enter"}]
    )
    await expect_error(harness, "INPUT_TARGET_CHANGED")
    await wait_for(lambda: harness.fake.input_count("move") == 1)
    assert harness.fake.input_count("text") == 1 and harness.fake.input_count("key") == 0
    ack = await expect_ack(harness, 2)
    assert ack["dropped_events"] == 2 and ack["accepted_events"] == 2
    await expect_state(harness, lambda st: (st["foreground_app"] or {}).get("process_name") == "chrome.exe")
    # typing the phone sends right after (it could not know yet) stays blocked: never into the new window
    await send_batch(harness, controller, sid, 3, [{"type": "text", "text": "y"}, MOVE])
    await wait_for(lambda: harness.fake.input_count("move") == 2)
    assert harness.fake.input_count("text") == 1 and harness.agent.input.summary()["session"]["dropped_events"] == 3
    # a user-directed click re-captures the target: typing after it goes to the new window
    harness.fake.foreground_app = ForegroundApp("code.exe", "editor", None, False, "3", 30)
    await send_batch(harness, controller, sid, 4, [CLICK, {"type": "text", "text": "z"}])
    await wait_for(lambda: harness.fake.input_count("text") == 2)
    assert harness.fake.input_events[-1].args == ("z",)


async def test_keyboard_batches_queued_across_a_target_change_are_not_typed(
    harness: AgentHarness, controller: Controller
) -> None:
    """Review finding: the phone streams continuously; batches already in the agent's queue when the
    foreground changes must not type into the new window."""
    harness.fake.foreground_app = ForegroundApp("notepad.exe", "doc", None, False, "1", 10)
    sid = (await start_input(harness, controller))["result"]["input_session_id"]
    harness.fake.input_delay_seconds = 0.15  # the first batch is still being injected when the next ones queue
    await send_batch(harness, controller, sid, 1, [{"type": "text", "text": "a"}, MOVE])
    await wait_for(lambda: harness.fake.input_count("text") == 1)
    harness.fake.foreground_app = ForegroundApp("mail.exe", "inbox", None, False, "9", 90)
    await send_batch(harness, controller, sid, 2, [{"type": "text", "text": "b"}])
    await send_batch(harness, controller, sid, 3, [{"type": "text", "text": "c"}, {"type": "shortcut", "name": "ctrl_v"}])
    await expect_error(harness, "INPUT_TARGET_CHANGED")
    await wait_for(lambda: harness.agent.input.summary()["session"]["dropped_events"] == 3)
    await asyncio.sleep(0.2)
    assert [r.args for r in harness.fake.input_events if r.name in ("text", "shortcut")] == [("a",)]
    harness.fake.input_delay_seconds = 0.0


async def test_typing_issued_well_after_a_target_change_continues(
    harness: AgentHarness, controller: Controller
) -> None:
    """Keyboard-only phones cannot click: typing issued at least ``target_change_grace_seconds`` after the
    change (the phone has seen pc_state.foreground_app and paused live typing) is a deliberate continuation."""
    harness.agent.input.target_change_grace_seconds = 0.4
    harness.fake.foreground_app = ForegroundApp("notepad.exe", "doc", None, False, "1", 10)
    sid = (await start_input(harness, controller))["result"]["input_session_id"]
    harness.fake.foreground_app = ForegroundApp("chrome.exe", "tab", "chrome", False, "2", 20)
    await send_batch(harness, controller, sid, 1, [{"type": "text", "text": "x"}])
    await expect_error(harness, "INPUT_TARGET_CHANGED")
    await send_batch(harness, controller, sid, 2, [{"type": "text", "text": "y"}])
    await wait_for(lambda: harness.agent.input.summary()["session"]["dropped_events"] == 2)
    assert harness.fake.input_count("text") == 0
    await asyncio.sleep(0.5)
    await send_batch(harness, controller, sid, 3, [{"type": "text", "text": "z"}])
    await wait_for(lambda: harness.fake.input_count("text") == 1)
    assert harness.fake.input_events[-1].args == ("z",)


# ----- acks / backpressure ------------------------------------------------------------------------------------------


async def test_acks_at_most_four_per_second(harness: AgentHarness, controller: Controller) -> None:
    sid = (await start_input(harness, controller))["result"]["input_session_id"]
    harness.relay.drain()
    for seq in range(1, 41):
        await send_batch(harness, controller, sid, seq, [MOVE])
        await asyncio.sleep(0.025)
    await asyncio.sleep(0.4)
    acks = [f for f in harness.relay.drain() if f["type"] == "input_ack"]
    assert acks and acks[-1]["last_seq"] == 40 and acks[-1]["accepted_events"] == 40
    times = sorted(
        time.mktime(time.strptime(a["at"][:19], "%Y-%m-%dT%H:%M:%S")) + float("0." + a["at"][20:23]) for a in acks
    )
    for i, t in enumerate(times):
        assert sum(1 for u in times[i:] if u - t < 1.0) <= 4, times
    assert harness.fake.input_count("move") == 40


async def test_backpressure_suspends_and_discards(harness: AgentHarness, controller: Controller) -> None:
    harness.agent.input.age_budget_ms = 300
    sid = (await start_input(harness, controller))["result"]["input_session_id"]
    harness.fake.input_delay_seconds = 0.25
    for seq in range(1, 8):
        await send_batch(harness, controller, sid, seq, [LEFT_DOWN] if seq == 1 else [MOVE])
    suspended = await expect_session_event(harness, "suspended", "backpressure", timeout=8)
    assert suspended["holds_released"] == 1 and harness.fake.held_buttons == set()
    harness.fake.input_delay_seconds = 0.0
    moved = harness.fake.input_count("move")
    assert moved < 6  # the backlog was discarded, not played back late
    await send_batch(harness, controller, sid, 8, [MOVE])
    err = await expect_error(harness, "INPUT_SUSPENDED")
    assert err["error"]["retryable"] is True
    await asyncio.sleep(0.2)
    assert harness.fake.input_count("move") == moved
    res = await start_input(harness, controller)  # a fresh session is required and works
    assert res["state"] == "succeeded"


# ----- crash recovery ---------------------------------------------------------------------------------------------


async def test_crash_recovery_releases_holds_and_rejects_old_session(
    settings: Any, fake_api: Any, fake_relay: Any, fake_state: Any, controller: Controller
) -> None:
    from dome_agent.agent import Agent
    from dome_agent.store import Store
    from dome_agent.testing.fake_platform import build_fake_platform

    from .helpers import link_identity

    cred = fake_api.issue_credential()
    link_identity(
        settings.state_dir,
        fake_api.pc_id,
        fake_api.account_id,
        credential=cred,
        api_url=fake_api.url,
        relay_url=fake_relay.url,
    )
    st = Store(settings.db_path)
    st.set_remote_enabled(True)
    controller.grant_locally(st)
    st.close()
    old_sid = "C" * 22
    (settings.state_dir / HOLDS_FILENAME).write_text(
        json.dumps(
            {
                "input_session_id": old_sid,
                "controller_id": controller.controller_id,
                "held_buttons": ["left"],
                "held_keys": ["ctrl"],
            }
        )
    )
    fake_relay.controllers.append(controller.snapshot_entry())
    agent = Agent(settings, platform=build_fake_platform(fake_state))
    await agent.start()
    try:
        assert fake_state.input_events and fake_state.input_events[0].name == "release"
        assert fake_state.input_events[0].args == (("left",), ("ctrl",))
        assert not (settings.state_dir / HOLDS_FILENAME).exists()
        await fake_relay.wait_connected()
        frame = await fake_relay.expect("input_session")
        assert frame["event"] == "ended" and frame["reason"] == "agent_restart" and frame["holds_released"] == 2
        assert frame["input_session_id"] == old_sid
        await fake_relay.expect("state")
        env = controller.input_batch(old_sid, 5, [MOVE])
        await fake_relay.send(relay_input_batch_frame(env))
        err = await fake_relay.expect("error")
        assert err["error"]["code"] == "INPUT_SESSION_REQUIRED"  # never recovered or replayed from disk
        assert fake_state.input_count("move") == 0
        assert "input holds released after restart" in agent.status()["notes"]
    finally:
        await agent.stop()


async def test_agent_stop_ends_session_and_releases(harness: AgentHarness, controller: Controller) -> None:
    sid = (await start_input(harness, controller))["result"]["input_session_id"]
    await send_batch(harness, controller, sid, 1, [LEFT_DOWN])
    await wait_for(lambda: harness.fake.held_buttons == {"left"})
    await harness.agent.input.shutdown()
    assert harness.fake.held_buttons == set()
    ended = await expect_session_event(harness, "ended", "agent_restart")
    assert ended["holds_released"] == 1


# ----- grant_update delivery --------------------------------------------------------------------------------------


async def test_grant_update_is_sent_and_resent_offline(harness: AgentHarness, controller: Controller) -> None:
    row = await harness.agent.update_grant_capabilities(controller.controller_id, remove=["keyboard"])
    assert row is not None
    frame = await harness.relay.expect("grant_update")
    load_schemas().validate_frame("agent_to_relay", frame)
    assert frame == {
        "type": "grant_update",
        "controller_id": controller.controller_id,
        "kid": controller.kid,
        "capabilities": [c for c in controller.capabilities if c != "keyboard"],
    }
    assert harness.agent.status()["pending_grant_updates"] == []
    await harness.relay.close(1012, "deploy")
    await wait_for(lambda: harness.agent.relay is not None and not harness.agent.relay.connected)
    await harness.agent.update_grant_capabilities(controller.controller_id, add=["keyboard"])
    assert harness.agent.status()["pending_grant_updates"] == [controller.controller_id]
    await harness.relay.wait_connected(timeout=15)
    frame = await harness.relay.expect("grant_update", timeout=15)
    assert "keyboard" in frame["capabilities"]
    await harness.wait_snapshot_applied()
    assert harness.agent.status()["pending_grant_updates"] == []
    pointer_only = await second_phone(harness, controller, ("pointer",))
    with pytest.raises(Exception, match="at least one capability"):
        await harness.agent.update_grant_capabilities(pointer_only.controller_id, remove=["pointer"])
    with pytest.raises(Exception, match="only pointer/keyboard"):
        await harness.agent.update_grant_capabilities(controller.controller_id, remove=["status"])


async def test_grant_control_op_and_cli(
    harness: AgentHarness, controller: Controller, capsys: pytest.CaptureFixture[str]
) -> None:
    from dome_agent import cli

    rc = await asyncio.to_thread(
        cli.main,
        ["--state-dir", str(harness.settings.state_dir), "grant", controller.controller_id, "--remove-pointer"],
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "pointer" not in out.split("Permissions for")[1]
    frame = await harness.relay.expect("grant_update")
    assert "pointer" not in frame["capabilities"]
    rc = await asyncio.to_thread(
        cli.main, ["--state-dir", str(harness.settings.state_dir), "grant", controller.controller_id, "--pointer"]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "every app of the unlocked Windows session" in out  # the broad-scope explanation is shown
    rc = await asyncio.to_thread(
        cli.main, ["--state-dir", str(harness.settings.state_dir), "grant", controller.controller_id]
    )
    assert rc == 2


# ----- content hygiene ----------------------------------------------------------------------------------------------


async def test_text_content_never_appears_in_logs_or_persisted_state(
    harness: AgentHarness, controller: Controller, capsys: pytest.CaptureFixture[str]
) -> None:
    from dome_agent.diagnostics import build_bundle
    from dome_agent.logsetup import configure_logging

    sentinel = "ZEBRA-SENTINEL-7731-" + uuid.uuid4().hex[:6]
    configure_logging("DEBUG", harness.settings.log_path)
    try:
        sid = (await start_input(harness, controller))["result"]["input_session_id"]
        await send_batch(
            harness, controller, sid, 1, [{"type": "text", "text": sentinel}, {"type": "key", "key": "enter"}]
        )
        await wait_for(lambda: harness.fake.input_count("text") == 1)
        harness.fake.input_fail = "INPUT_INJECTION_FAILED"
        await send_batch(harness, controller, sid, 2, [{"type": "text", "text": sentinel + "-2"}])
        await expect_error(harness, "INPUT_INJECTION_FAILED")
        harness.fake.input_fail = None
        await stop_input(harness, controller, sid)
        for h in logging.getLogger().handlers:
            h.flush()
        log_text = harness.settings.log_path.read_text()
        assert "input session started" in log_text and sentinel not in log_text
        assert sentinel not in capsys.readouterr().out
        assert sentinel not in json.dumps(harness.agent.store.list_security_events(500))
        assert sentinel not in json.dumps(harness.agent.status())
        assert sentinel not in json.dumps(build_bundle(harness.settings, harness.agent.status()))
        assert harness.agent.store.journal_count() == 2  # only the two lifecycle commands, never a batch
        frames = harness.relay.drain()
        assert sentinel not in json.dumps(frames)
        assert sentinel not in json.dumps(
            [r for r in (harness.settings.state_dir).glob("*.json")]
            and [p.read_text() for p in harness.settings.state_dir.glob("*.json")]
        )
        assert sentinel not in harness.settings.db_path.read_bytes().decode("latin-1")
    finally:
        logging.getLogger().handlers.clear()
    # the only place the text ever went is the (fake) input adapter
    assert harness.fake.input_events[0].args == (sentinel,)
