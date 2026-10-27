"""Screen view sessions end to end through the agent with the fake relay, the fake platform, a fake UI that can create
the bars and the in-process fake worker (design §7, §4.2, §6.3, §8; rules.screen_sessions / local_permission_ui):
every start check in order, the PC-signed answer frame, the started / paused / resumed / ended frames with every
end reason, the fail-closed indicator, two-way liveness, consent (ask mode, cooldown, back-off, block, turn off),
the emergency stops and their order, allowance tickets as a dead-man switch, pointer_move_to / pointer_wheel / f11
and DoMe's own permission UI. Nothing here proves Windows capture: the worker is a fake."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
import structlog
from dome_protocol import (
    ProtocolError,
    format_rfc3339,
    load_registry,
    load_schemas,
    now_utc,
    verify_screen_answer_payload,
)

from dome_agent.screen.session import RECOVERY_FILENAME, parse_wall
from dome_agent.testing.fake_screen_worker import FakeScreenWorker

from .conftest import AgentHarness
from .helpers import Controller, payload_of, relay_input_batch_frame

FIXTURE = json.loads(
    (Path(__file__).resolve().parents[2] / "shared" / "protocol" / "fixtures" / "es256-python.json").read_text(
        encoding="utf-8"
    )
)
OFFER: str = FIXTURE["screen"]["offer_sdp"]
SENTINEL_FINGERPRINT = "01:02:03:04:05:06:07:08:09:0A"


async def wait_for(pred: Callable[[], bool], timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("condition not met in time")


def ticket(*, seconds: float = 60.0, unlimited: bool = False, stun: bool = True) -> dict[str, Any]:
    out: dict[str, Any] = {
        "granted_until": None if unlimited and seconds <= 0 else format_rfc3339(now_utc() + timedelta(seconds=seconds)),
        "unlimited": unlimited,
        "stun_urls": ["stun:stun.example.net:3478"] if stun else [],
    }
    return out


async def send_start(
    h: AgentHarness,
    controller: Controller,
    *,
    offer: str = OFFER,
    takeover: bool = False,
    relay_screen: dict[str, Any] | None | str = "default",
    surface: str | int = "primary",
) -> str:
    env = controller.command(
        "screen.session_start", {"offer_sdp": offer, "surface": surface, "takeover": takeover}, lifetime=60
    )
    cid: str = payload_of(env)["command_id"]
    relay: dict[str, Any] = {
        "received_at": format_rfc3339(now_utc()),
        "connection_id": h.agent.relay.connection_id
        if h.agent.relay and h.agent.relay.connection_id
        else str(uuid.uuid4()),
    }
    if relay_screen == "default":
        relay["screen"] = ticket()
    elif relay_screen is not None:
        relay["screen"] = relay_screen
    frame = {"type": "command", "envelope": env, "relay": relay}
    load_schemas().validate_frame("relay_to_agent", frame)
    await h.relay.send(frame)
    return cid


async def start_screen(h: AgentHarness, controller: Controller, **kw: Any) -> dict[str, Any]:
    cid = await send_start(h, controller, **kw)
    res = await h.result(cid, timeout=15)
    load_schemas().validate_frame("agent_to_relay", res)
    return res


async def expect_screen_event(
    h: AgentHarness, event: str, reason: str | None = None, timeout: float = 8.0
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while True:
        frame = await h.relay.expect("screen_session", timeout=max(0.1, deadline - time.monotonic()))
        if frame["event"] == event and (reason is None or frame["reason"] == reason):
            load_schemas().validate_frame("agent_to_relay", frame)
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


async def live(h: AgentHarness, controller: Controller, **kw: Any) -> tuple[dict[str, Any], FakeScreenWorker]:
    """A session that reached `started` (connected, first frame)."""
    res = await start_screen(h, controller, **kw)
    assert res["state"] == "succeeded", res
    await expect_screen_event(h, "started", "started")
    worker = h.screen_worker.current
    assert worker is not None
    return res, worker


async def send_batch(h: AgentHarness, controller: Controller, sid: str, seq: int, events: list[dict[str, Any]]) -> None:
    env = controller.input_batch(sid, seq, events)
    await h.relay.send(relay_input_batch_frame(env, h.agent.relay.connection_id if h.agent.relay else None))


async def start_input(h: AgentHarness, controller: Controller) -> str:
    env = controller.command("input.session_start", {})
    await h.send_command(env)
    res = await h.result(payload_of(env)["command_id"])
    assert res["state"] == "succeeded"
    return str(res["result"]["input_session_id"])


@pytest.fixture
def fast(harness: AgentHarness) -> AgentHarness:
    """Short budgets: every end reason within a test's patience, the real limits otherwise."""
    screen = harness.agent.screen
    screen.lock_poll_seconds = 0.1
    screen.indicator_poll_seconds = 0.1
    screen.usage_min_interval = 0.2
    return harness


# ----- start, answer, result --------------------------------------------------------------------------------------
async def test_start_answers_with_a_pc_signed_frame_and_a_content_free_result(
    fast: AgentHarness, controller: Controller
) -> None:
    h = fast
    cid = await send_start(h, controller)
    answer = await h.relay.expect("screen_answer", timeout=10)
    load_schemas().validate_frame("agent_to_relay", answer)
    assert answer["ref_controller_id"] == controller.controller_id
    verified = verify_screen_answer_payload(
        answer["envelope"],
        pinned_jwk=h.agent.identity.public_jwk,  # what the phone pinned at pairing
        account_id=controller.account_id,
        controller_id=controller.controller_id,
        pc_id=controller.pc_id,
        command_id=cid,
        offer_sdp=OFFER,
    )
    res = await h.result(cid)
    assert res["state"] == "succeeded", res
    r = res["result"]
    load_registry().validate_result("screen.session_start", r)
    assert r["screen_session_id"] == verified.screen_session_id and len(r["screen_session_id"]) == 22
    assert r["surfaces"] == [{"index": 0, "width": 1920, "height": 1080, "primary": True}]
    assert (r["surface_index"], r["gen"], r["audio"], r["lease_seconds"], r["consent"], r["encoder"]) == (
        0,
        1,
        False,
        8,
        "not_required",
        "h264",
    )
    assert r["allowance"] == {"unlimited": False, "remaining_seconds": None, "resets_at": None}
    assert "sdp" not in json.dumps(res).lower() and SENTINEL_FINGERPRINT not in json.dumps(res)
    row = h.agent.store.journal_get(cid)
    assert row is not None and SENTINEL_FINGERPRINT not in json.dumps(row, default=str)
    started = await expect_screen_event(h, "started", "started")
    assert started["path"] == "direct" and started["controller_id"] == controller.controller_id
    worker = h.screen_worker.current
    assert worker is not None and worker.start_message is not None
    msg = worker.start_message
    assert msg["offer_sdp"] == OFFER and msg["stun_urls"] == ["stun:stun.example.net:3478"]
    assert msg["source"]["kind"] == "fake" and msg["source"]["width"] == 1920 and msg["lease_seconds"] == 8
    assert msg["reserved"] == [{"x": 0, "y": 0, "w": 65535, "h": round(40 * 65535 / 1079)}]  # the bar, normalised
    assert h.ui.bars_shown and h.ui.bar_requests[-1].viewer_name == controller.display_name
    assert any(
        s.screen_session is not None and s.screen_session.phone_name == controller.display_name for s in h.ui.statuses
    )
    assert h.ui.notifications[-1][1].startswith(f"{controller.display_name} is viewing this screen")
    status = h.agent.status()["screen"]
    assert status["session"]["state"] == "live" and status["session"]["path"] == "direct"
    # the bar only closes once the worker has exited (end order)
    await h.agent.screen.end("stopped")
    assert worker.exit_code == 0 and h.ui.hidden == 1


async def test_bars_come_before_the_worker_and_the_worker_only_after_the_bars(
    fast: AgentHarness, controller: Controller
) -> None:
    order: list[str] = []
    original_show = fast.ui.show_screen_bars
    original_spawn = fast.screen_worker.spawn

    def show(request: Any) -> Any:
        order.append("bars")
        return original_show(request)

    async def spawn(**kw: Any) -> Any:
        order.append("worker")
        return await original_spawn(**kw)

    fast.ui.show_screen_bars = show  # type: ignore[method-assign]
    fast.screen_worker.spawn = spawn  # type: ignore[method-assign]
    await live(fast, controller)
    assert order == ["bars", "worker"]


# ----- the start checks, in order -----------------------------------------------------------------------------------
async def test_start_without_a_relay_ticket_or_with_a_bad_one_is_refused_before_any_worker(
    fast: AgentHarness, controller: Controller
) -> None:
    res = await start_screen(fast, controller, relay_screen=None)
    assert res["state"] == "failed" and res["error"]["code"] == "ACTION_UNAVAILABLE"
    res = await start_screen(
        fast, controller, relay_screen={"granted_until": None, "unlimited": False, "stun_urls": []}
    )
    assert res["error"]["code"] == "ACTION_UNAVAILABLE"
    assert fast.screen_worker.spawned == [] and not fast.ui.bar_requests


async def test_invalid_offer_is_screen_offer_invalid_with_no_worker(fast: AgentHarness, controller: Controller) -> None:
    res = await start_screen(fast, controller, offer=OFFER.replace("a=mid:0\r\na=recvonly", "a=mid:0\r\na=sendrecv"))
    assert res["state"] == "failed" and res["error"]["code"] == "SCREEN_OFFER_INVALID"
    assert res["error"]["detail"] == {"reason": "direction"} and "a=" not in res["error"]["message"]
    assert fast.screen_worker.spawned == [] and not fast.ui.bar_requests


async def test_locked_disabled_and_unsupported_refuse(fast: AgentHarness, controller: Controller) -> None:
    fast.fake.locked = True
    assert (await start_screen(fast, controller))["error"]["code"] == "PC_SESSION_LOCKED"
    fast.fake.locked = False
    fast.fake.secure_desktop = True
    assert (await start_screen(fast, controller))["error"]["code"] == "PC_SESSION_LOCKED"
    fast.fake.secure_desktop = False
    await fast.agent.set_remote_enabled(False)
    assert (await start_screen(fast, controller))["error"]["code"] == "PC_REMOTE_DISABLED"
    await fast.agent.set_remote_enabled(True)
    from dome_agent.platform.unsupported import build_unsupported_platform

    fast.agent.services.platform = build_unsupported_platform()
    assert (await start_screen(fast, controller))["error"]["code"] == "PLATFORM_UNSUPPORTED"
    assert fast.screen_worker.spawned == []


async def test_a_phone_without_screen_gets_grant_missing(fast: AgentHarness, controller: Controller) -> None:
    plain = await second_phone(fast, controller, ("status", "pointer", "keyboard"))
    res = await start_screen(fast, plain)
    assert res["state"] == "failed" and res["error"]["code"] == "GRANT_MISSING"


async def test_monitor_enumeration_failure_and_bad_surface(fast: AgentHarness, controller: Controller) -> None:
    fast.fake.screen_fail = "PLATFORM_UNSUPPORTED"
    res = await start_screen(fast, controller)
    assert res["error"]["code"] == "SCREEN_CAPTURE_UNAVAILABLE" and res["error"]["detail"] == {
        "detail": "unsupported_display"
    }
    fast.fake.screen_fail = None
    res = await start_screen(fast, controller, surface=3)
    assert res["error"]["code"] == "INVALID_PARAMETERS"
    assert fast.screen_worker.spawned == []


# ----- the indicator, fail-closed ---------------------------------------------------------------------------------------
async def test_a_ui_that_cannot_create_a_bar_starts_nothing(fast: AgentHarness, controller: Controller) -> None:
    fast.ui.bars_ok = False
    res = await start_screen(fast, controller)
    assert res["state"] == "failed" and res["error"]["code"] == "SCREEN_CAPTURE_UNAVAILABLE"
    assert res["error"]["detail"] == {"detail": "indicator"}
    assert fast.screen_worker.spawned == [] and fast.agent.screen.current is None
    assert fast.agent.permission_ui.reserved_rects() == ()


async def test_a_frozen_ui_thread_counts_as_no_bar(fast: AgentHarness, controller: Controller) -> None:
    fast.agent.screen.indicator.confirm_timeout = 0.2
    fast.ui.bars_delay = 1.5
    res = await start_screen(fast, controller)
    assert res["error"]["code"] == "SCREEN_CAPTURE_UNAVAILABLE" and res["error"]["detail"] == {"detail": "indicator"}
    assert fast.screen_worker.spawned == []


async def test_a_start_abandoned_mid_way_leaves_no_bar_worker_or_session(
    fast: AgentHarness, controller: Controller
) -> None:
    """The executor cancels the handler (its 45 s timeout, or a revocation) while the worker is still answering:
    the bars close, the worker is stopped and the PC is free for the next start."""
    fast.screen_worker.answer_delay = 5.0
    cid = await send_start(fast, controller)
    await wait_for(lambda: fast.screen_worker.current is not None and fast.ui.bars_shown)
    running = fast.agent.executor._running  # noqa: SLF001
    assert running is not None and running.task is not None
    running.task.cancel()
    res = await fast.result(cid)
    assert res["state"] == "canceled"
    worker = fast.screen_worker.current
    assert worker is not None
    await wait_for(lambda: worker.exit_code is not None and not fast.ui.bars_shown)
    assert fast.agent.screen.current is None and fast.agent.permission_ui.reserved_rects() == ()
    assert fast.agent.screen.history.recent() == []  # a start that never completed records nothing
    fast.screen_worker.answer_delay = 0.0
    await live(fast, controller)


async def test_the_start_answers_within_the_command_timeout(fast: AgentHarness, controller: Controller) -> None:
    """Consent (30 s) + bars (3 s) + the worker must fit actions.json's 45 s: the worker's answer wait is cut to
    what is left of the start budget, and the phone hears SCREEN_CAPTURE_UNAVAILABLE rather than a generic
    timeout."""
    screen = fast.agent.screen
    assert screen.start_budget == pytest.approx(load_registry().get("screen.session_start").timeout_ms / 1000 - 3.0)
    screen.start_budget = 0.8
    fast.screen_worker.answer_delay = 5.0
    t0 = time.monotonic()
    res = await start_screen(fast, controller)
    assert time.monotonic() - t0 < 4.0
    assert res["state"] == "failed" and res["error"]["code"] == "SCREEN_CAPTURE_UNAVAILABLE"
    assert res["error"]["detail"] == {"detail": "driver"}
    worker = fast.screen_worker.current
    assert worker is not None and worker.exit_code is not None and not fast.ui.bars_shown


async def test_an_abandoned_start_takes_its_consent_window_down(fast: AgentHarness, controller: Controller) -> None:
    fast.agent.store.set_screen_grant_settings(controller.controller_id, ask_each_time=True, cooldown_seconds=600)
    cid = await send_start(fast, controller)
    await wait_for(lambda: bool(fast.ui.consent_requests))
    assert fast.agent.permission_ui.is_open
    running = fast.agent.executor._running  # noqa: SLF001
    assert running is not None and running.task is not None
    running.task.cancel()
    assert (await fast.result(cid))["state"] == "canceled"
    assert fast.ui.closed_consents == [fast.ui.consent_requests[-1].request_id]
    assert not fast.agent.permission_ui.is_open and fast.screen_worker.spawned == []


async def test_a_missing_bar_pauses_and_then_ends_capture(fast: AgentHarness, controller: Controller) -> None:
    fast.agent.screen.indicator_grace = 0.6
    _res, worker = await live(fast, controller)
    fast.ui.bars_present = False  # a monitor change took a bar away
    paused = await expect_screen_event(fast, "paused", "indicator")
    assert paused["reason"] == "indicator" and worker.paused
    fast.ui.bars_present = True  # the re-created bars are confirmed again
    resumed = await expect_screen_event(fast, "resumed", "indicator")
    assert resumed["path"] == "direct" and not worker.paused
    fast.ui.bars_present = False
    await expect_screen_event(fast, "paused", "indicator")
    ended = await expect_screen_event(fast, "ended", "capture_failed")
    assert ended["reason"] == "capture_failed" and worker.exit_code == 0


async def test_a_new_monitor_gets_a_bar_before_capture_resumes_and_a_resized_one_a_new_generation(
    fast: AgentHarness, controller: Controller
) -> None:
    """Design §7: the monitors are enumerated afresh on every check. A monitor plugged in has no bar: capture pauses,
    bars are made for the new set, capture resumes. The streamed monitor changing size makes the worker rebuild at a
    new `gen`; a tap aimed at the old picture is refused, never mapped onto the moved rectangle."""
    from dome_agent.platform.protocol import Monitor

    sid = await start_input(fast, controller)
    _res, worker = await live(fast, controller)
    second = Monitor(1, 1920, 0, 1280, 1024, False)
    fast.fake.monitors = [Monitor(0, 0, 0, 1920, 1080, True), second]
    await expect_screen_event(fast, "paused", "indicator")
    await expect_screen_event(fast, "resumed", "indicator")
    assert fast.ui.bar_requests[-1].monitors == (Monitor(0, 0, 0, 1920, 1080, True), second)
    assert fast.agent.screen.indicator.monitors == tuple(fast.fake.monitors) and not worker.paused
    assert worker.messages_of("rebuild") == [] and worker.messages_of("reserved") == []  # the streamed one is as it was
    session = fast.agent.screen.current
    assert session is not None and session.gen == 1
    tap = [{"type": "pointer_move_to", "x": 65535, "y": 65535, "gen": 1}]
    await send_batch(fast, controller, sid, 1, tap)
    await wait_for(lambda: fast.fake.input_count("move_to") == 1)
    # the primary is switched to 2560 x 1440: pause, bars again, the worker rebuilds at gen 2
    fast.fake.monitors = [Monitor(0, 0, 0, 2560, 1440, True), Monitor(1, 2560, 0, 1280, 1024, False)]
    await expect_screen_event(fast, "paused", "indicator")
    await expect_screen_event(fast, "resumed", "indicator")
    await wait_for(lambda: bool(worker.messages_of("rebuild")))
    rebuild = worker.messages_of("rebuild")[-1]
    assert rebuild["source"] == {
        "surface_index": 0,
        "primary": True,
        "left": 0,
        "top": 0,
        "width": 2560,
        "height": 1440,
        "rotation": 0,
    }
    assert rebuild["reserved"] == [{"x": 0, "y": 0, "w": 65535, "h": round(40 * 65535 / 1439)}]
    await wait_for(lambda: session.gen == 2 and not session.geometry_pending)
    await send_batch(fast, controller, sid, 2, tap)  # aimed at the old picture
    await expect_error(fast, "SCREEN_GEOMETRY_CHANGED")
    assert fast.fake.input_count("move_to") == 1
    await send_batch(fast, controller, sid, 3, [{"type": "pointer_move_to", "x": 65535, "y": 65535, "gen": 2}])
    await wait_for(lambda: fast.fake.input_count("move_to") == 2)
    assert fast.fake.input_events[-1].args == (2559, 1439)  # the new rectangle, in physical pixels


async def test_taps_wait_for_the_new_generation_and_a_rebuild_that_never_finishes_ends_the_session(
    fast: AgentHarness, controller: Controller
) -> None:
    from dome_agent.platform.protocol import Monitor

    primary, side = Monitor(0, 0, 0, 1920, 1080, True), Monitor(1, -1280, 0, 1280, 1024, False)
    fast.fake.monitors = [primary, side]
    fast.screen_worker.announce_geometry = False  # a worker slow to announce its new generation
    sid = await start_input(fast, controller)
    res, worker = await live(fast, controller, surface=1)
    assert res["result"]["surface_index"] == 1 and res["result"]["surfaces"][1]["width"] == 1280
    session = fast.agent.screen.current
    assert session is not None
    fast.fake.monitors = [primary, Monitor(1, -1280, 0, 1024, 768, False)]  # the viewed monitor changed mode
    await wait_for(lambda: bool(worker.messages_of("rebuild")))
    assert session.geometry_pending and session.gen == 1
    await send_batch(fast, controller, sid, 1, [{"type": "pointer_move_to", "x": 0, "y": 0, "gen": 1}])
    await expect_error(fast, "SCREEN_GEOMETRY_CHANGED")  # until the worker says which picture the phone sees
    assert fast.fake.input_count("move_to") == 0
    fast.agent.screen.indicator_grace = 0.5  # a worker that never announces the rebuilt picture ends the session
    ended = await expect_screen_event(fast, "ended", "capture_failed")
    assert ended["reason"] == "capture_failed" and worker.exit_code == 0 and not fast.ui.bars_shown


async def test_an_unplugged_viewed_monitor_ends_the_session(fast: AgentHarness, controller: Controller) -> None:
    from dome_agent.platform.protocol import Monitor

    primary, side = Monitor(0, 0, 0, 1920, 1080, True), Monitor(1, -1280, 0, 1280, 1024, False)
    fast.fake.monitors = [primary, side]
    _res, worker = await live(fast, controller, surface=1)
    fast.fake.monitors = [primary]
    ended = await expect_screen_event(fast, "ended", "capture_failed")
    assert ended["reason"] == "capture_failed" and worker.exit_code == 0 and not fast.ui.bars_shown
    assert worker.messages_of("rebuild") == []  # Phase 1 never switches to another monitor by itself


# ----- two-way liveness ---------------------------------------------------------------------------------------------------
async def test_stopped_agent_ticks_make_the_worker_exit(fast: AgentHarness, controller: Controller) -> None:
    fast.agent.screen.tick_timeout_seconds = 0.6
    fast.agent.screen.supervisor.tick_interval = 0.2
    _res, worker = await live(fast, controller)
    await wait_for(lambda: worker.ticks >= 2)
    fast.agent.screen.supervisor.tick_interval = 60.0  # the agent's ticker stopped (a stuck event loop): no more ticks
    if fast.agent.screen.supervisor._ticker is not None:  # noqa: SLF001
        fast.agent.screen.supervisor._ticker.cancel()  # noqa: SLF001
    t0 = time.monotonic()
    ended = await expect_screen_event(fast, "ended", "capture_failed")
    assert worker.exit_reason == "ticks_stopped" and time.monotonic() - t0 < 10 and ended["reason"] == "capture_failed"


async def test_lease_expiry_without_heartbeats_is_viewer_gone(fast: AgentHarness, controller: Controller) -> None:
    fast.screen_worker.hb_interval = None
    fast.agent.screen.lease_seconds = 0.5
    _res, worker = await live(fast, controller)
    ended = await expect_screen_event(fast, "ended", "viewer_gone")
    assert ended["reason"] == "viewer_gone" and worker.exit_code == 0 and fast.ui.hidden == 1


async def test_no_dtls_within_the_connect_timeout_is_connection_failed(
    fast: AgentHarness, controller: Controller
) -> None:
    fast.screen_worker.connect_delay = None
    fast.agent.screen.connect_timeout = 0.5
    res = await start_screen(fast, controller)
    assert res["state"] == "succeeded"
    ended = await expect_screen_event(fast, "ended", "connection_failed")
    assert ended["reason"] == "connection_failed" and ended["live_seconds"] == 0


async def test_a_worker_crash_is_capture_failed(fast: AgentHarness, controller: Controller) -> None:
    _res, worker = await live(fast, controller)
    await worker.crash(70)
    ended = await expect_screen_event(fast, "ended", "capture_failed")
    assert ended["reason"] == "capture_failed" and fast.ui.hidden == 1
    assert fast.agent.screen.history.recent()[0]["reason"] == "capture_failed"


async def test_a_capture_error_after_dtls_is_logged_with_its_reason(fast: AgentHarness, controller: Controller) -> None:
    """The field log of 2026-10-26 said only "SCREEN_CAPTURE_UNAVAILABLE": the agent's line now carries the worker's
    detail and message (the capture modules' own text, FFmpeg's or Windows' words sanitised there)."""
    _res, worker = await live(fast, controller)
    message = (
        "Windows.Graphics.Capture could not start: Exception: Capture session threw an exception: The Graphics "
        "Capture API is not supported on this platform.\n" + "x" * 300
    )
    with structlog.testing.capture_logs() as logs:
        await worker.emit(
            {"ev": "error", "code": "SCREEN_CAPTURE_UNAVAILABLE", "detail": "unsupported_display", "message": message}
        )
        ended = await expect_screen_event(fast, "ended", "capture_failed")
    assert ended["reason"] == "capture_failed"
    [line] = [e for e in logs if e["event"] == "screen worker reported an error"]
    assert line["code"] == "SCREEN_CAPTURE_UNAVAILABLE" and line["detail"] == "unsupported_display"
    assert line["message"].startswith("Windows.Graphics.Capture could not start: Exception: Capture session threw")
    assert "\n" not in line["message"] and len(line["message"]) == 200


async def test_a_worker_that_refuses_to_start_maps_its_error(fast: AgentHarness, controller: Controller) -> None:
    fast.screen_worker.fail_start = "SCREEN_ENCODER_UNAVAILABLE"
    res = await start_screen(fast, controller)
    assert (
        res["error"]["code"] == "SCREEN_ENCODER_UNAVAILABLE"
        and fast.ui.hidden == 1
        and fast.agent.screen.current is None
    )
    fast.screen_worker.fail_start = "SCREEN_CAPTURE_UNAVAILABLE"
    fast.screen_worker.fail_detail = "busy"
    res = await start_screen(fast, controller)
    assert res["error"]["code"] == "SCREEN_CAPTURE_UNAVAILABLE" and res["error"]["detail"] == {"detail": "busy"}


async def test_the_preflight_refuses_a_start_only_when_no_capture_path_could_serve_it(
    fast: AgentHarness, controller: Controller
) -> None:
    """KNOWN_ISSUES #31: a PC without a D3D11 device used to answer the start, show the bars, connect and end
    `capture_failed`. The DXGI probe before the bars refuses that with the honest detail. A duplication that is busy,
    unsupported for the display or refused in a remote session is NOT refused: the Windows.Graphics.Capture fallback
    exists for exactly those (DECISIONS #88), so the start goes on and the worker is told to skip Desktop Duplication."""
    from dome_agent.platform.protocol import DuplicationProbe
    from dome_agent.screen import preflight

    fast.fake.duplication_probe = DuplicationProbe(0x887A0005, stage="device", adapter="GPU A")
    with structlog.testing.capture_logs() as logs:
        res = await start_screen(fast, controller)
    assert res["state"] == "failed", res
    assert res["error"]["code"] == "SCREEN_CAPTURE_UNAVAILABLE" and res["error"]["detail"] == {"detail": "driver"}
    [line] = [e for e in logs if e["event"] == "capture pre-flight refused the start"]
    assert line["detail"] == "driver" and line["hresult"] == "0x887a0005" and line["stage"] == "device"
    assert line["surface_index"] == 0 and line["adapter"] == "GPU A"
    assert not fast.ui.bars_shown and fast.ui.hidden == 0 and fast.screen_worker.spawned == []
    assert fast.agent.screen.current is None and fast.agent.screen.history.recent() == []
    # Desktop Duplication's own refusals: the start goes on, bars, worker, answer; the worker's source spec carries the
    # verdict (detail and HRESULT name) so it skips Desktop Duplication and opens Windows.Graphics.Capture at once
    cases = [
        (DuplicationProbe(preflight.DXGI_ERROR_NOT_CURRENTLY_AVAILABLE, stage="duplicate", output_index=0), "busy"),
        (DuplicationProbe(preflight.DXGI_ERROR_UNSUPPORTED, stage="duplicate", adapter="GPU A"), "unsupported_display"),
        (DuplicationProbe(preflight.E_ACCESSDENIED, remote_session=True, stage="duplicate"), "remote_session"),
    ]
    for probe, detail in cases:
        fast.fake.duplication_probe = probe
        with structlog.testing.capture_logs() as logs:
            res, worker = await live(fast, controller)
        assert res["state"] == "succeeded" and fast.ui.bars_shown
        [line] = [e for e in logs if e["event"].startswith("capture pre-flight found Desktop Duplication failing")]
        assert line["detail"] == detail and line["hresult"] == f"0x{probe.hresult:08x}"
        assert worker.start_message is not None
        verdict = worker.start_message["source"]["preflight"]
        assert verdict["detail"] == detail and verdict["hresult"] == f"0x{probe.hresult:08x}"
        assert verdict["hresult_name"] == preflight.HRESULT_NAMES[probe.hresult] and verdict["stage"] == "duplicate"
        await fast.agent.screen.end("stopped")
        await expect_screen_event(fast, "ended", "stopped")
    assert fast.fake.count("duplication_probe") == len(cases) + 1
    # the probe passes: no verdict travels, the start goes on exactly as before
    fast.fake.duplication_probe = DuplicationProbe(preflight.S_OK, stage="duplicate", output_index=0)
    res, worker = await live(fast, controller)
    assert res["state"] == "succeeded" and worker.start_message is not None
    assert "preflight" not in worker.start_message["source"]


async def test_a_probe_that_passes_does_not_hide_a_real_capture_failure(
    fast: AgentHarness, controller: Controller
) -> None:
    """The probe is advice, not the only path: after a passing probe the real open (after DTLS) can still fail, and
    that is reported exactly as before."""
    from dome_agent.platform.protocol import DuplicationProbe
    from dome_agent.screen import preflight

    fast.fake.duplication_probe = DuplicationProbe(preflight.S_OK, stage="duplicate", output_index=0)
    fast.screen_worker.fail_start = "SCREEN_CAPTURE_UNAVAILABLE"
    fast.screen_worker.fail_detail = "unsupported_display"
    res = await start_screen(fast, controller)
    assert res["error"]["code"] == "SCREEN_CAPTURE_UNAVAILABLE"
    assert res["error"]["detail"] == {"detail": "unsupported_display"} and fast.ui.hidden == 1


async def test_a_slow_or_absent_probe_does_not_delay_or_refuse_the_start(
    fast: AgentHarness, controller: Controller
) -> None:
    from dome_agent.platform.protocol import DuplicationProbe

    fast.fake.probe_delay_seconds = 0.6
    fast.agent.screen.preflight_timeout = 0.2
    t0 = time.monotonic()
    res, worker = await live(fast, controller)
    assert res["state"] == "succeeded" and time.monotonic() - t0 < 1.8
    await fast.agent.screen.end("stopped")
    await expect_screen_event(fast, "ended", "stopped")
    fast.fake.probe_delay_seconds = 0.0
    await asyncio.sleep(0.6)  # the slow probe's own thread returns: no later start finds it still running
    # a probe is never run for a replacement or takeover: our own worker's duplication would count as "busy"
    res, worker = await live(fast, controller)
    probes = fast.fake.count("duplication_probe")
    fast.fake.duplication_probe = DuplicationProbe(0x887A0022, stage="duplicate")
    res = await start_screen(fast, controller)  # the same phone again: replaces its session, no probe
    assert res["state"] == "succeeded" and fast.fake.count("duplication_probe") == probes
    fast.fake.duplication_probe = None


async def test_a_worker_that_never_answers_is_capture_unavailable(fast: AgentHarness, controller: Controller) -> None:
    fast.screen_worker.answer_delay = 5.0
    fast.agent.screen.worker_start_timeout = 0.3
    res = await start_screen(fast, controller)
    assert res["error"]["code"] == "SCREEN_CAPTURE_UNAVAILABLE" and res["error"]["detail"] == {"detail": "driver"}
    worker = fast.screen_worker.current
    assert worker is not None and worker.exit_code is not None and fast.ui.hidden == 1


# ----- stops from the phone ----------------------------------------------------------------------------------------------
async def test_session_stop_is_idempotent_and_owner_only(fast: AgentHarness, controller: Controller) -> None:
    res, worker = await live(fast, controller)
    sid = res["result"]["screen_session_id"]
    await asyncio.sleep(0.3)
    other = await second_phone(fast, controller, ("status", "screen"))
    env = other.command("screen.session_stop", {"screen_session_id": sid})
    await fast.send_command(env)
    stolen = await fast.result(payload_of(env)["command_id"])
    assert stolen["result"] == {"stopped": False, "live_seconds": 0} and fast.agent.screen.current is not None
    env = controller.command("screen.session_stop", {"screen_session_id": sid})
    await fast.send_command(env)
    stopped = await fast.result(payload_of(env)["command_id"])
    load_registry().validate_result("screen.session_stop", stopped["result"])
    assert stopped["result"]["stopped"] is True
    ended = await expect_screen_event(fast, "ended", "stopped")
    assert worker.messages_of("stop") and worker.exit_code == 0 and ended["live_seconds"] >= 0
    env = controller.command("screen.session_stop", {"screen_session_id": sid})
    await fast.send_command(env)
    again = await fast.result(payload_of(env)["command_id"])
    assert again["result"]["stopped"] is False
    assert fast.ui.notifications[-1][0] == "Screen view ended"
    history = fast.agent.screen.history.recent()
    assert history[0]["reason"] == "stopped" and history[0]["display_name"] == controller.display_name
    assert "sdp" not in json.dumps(history).lower()


async def test_bye_on_the_data_channel_ends_the_session(fast: AgentHarness, controller: Controller) -> None:
    _res, worker = await live(fast, controller)
    await worker.emit({"ev": "bye"})
    ended = await expect_screen_event(fast, "ended", "stopped")
    assert ended["reason"] == "stopped"
    await worker.emit({"ev": "hb"})  # late data-channel messages after the end are dropped
    assert fast.agent.screen.current is None


# ----- takeover and restart -----------------------------------------------------------------------------------------------
async def test_second_phone_needs_takeover_and_the_same_phone_replaces_itself(
    fast: AgentHarness, controller: Controller
) -> None:
    _res, first = await live(fast, controller)
    other = await second_phone(fast, controller, ("status", "screen"))
    owned = await start_screen(fast, other)
    assert owned["state"] == "failed" and owned["error"]["code"] == "SCREEN_SESSION_OWNED"
    assert first.alive
    taken = await start_screen(fast, other, takeover=True)
    assert taken["state"] == "succeeded"
    ended = await expect_screen_event(fast, "ended", "takeover")
    assert ended["controller_id"] == controller.controller_id and first.exit_code == 0
    await expect_screen_event(fast, "started", "started")
    assert fast.ui.bar_requests[-1].viewer_name == "Other phone"
    replaced = await start_screen(fast, other)
    assert replaced["state"] == "succeeded"
    ended = await expect_screen_event(fast, "ended", "replaced")
    assert ended["controller_id"] == other.controller_id
    await expect_screen_event(fast, "started", "started")


# ----- emergency stops --------------------------------------------------------------------------------------------------------
async def test_disable_remote_control_ends_the_screen_before_the_input_session(
    fast: AgentHarness, controller: Controller
) -> None:
    """Design §7: the Screen view session is retired at once (no tap maps, the worker is told to stop), held input
    is released while the worker is still exiting (here it ignores `stop` and takes the whole 2 s grace), and both
    have ended, bars closed, when set_remote_enabled returns."""
    fast.screen_worker.exit_on_stop = False
    _res, worker = await live(fast, controller)
    sid = await start_input(fast, controller)
    press = [
        {"type": "pointer_move_to", "x": 32768, "y": 32768, "gen": 1},  # off DoMe's bar, where presses are refused
        {"type": "pointer_button", "button": "left", "action": "down"},
    ]
    await send_batch(fast, controller, sid, 1, press)
    await wait_for(lambda: fast.fake.held_buttons == {"left"})
    fast.relay.drain()
    t0 = time.monotonic()
    await fast.agent.set_remote_enabled(False)
    took = time.monotonic() - t0
    frames: list[dict[str, Any]] = []

    def both_ended() -> bool:
        frames.extend(fast.relay.drain())  # the fake relay reads its socket on its own task
        return {"screen_session", "input_session"} <= {f["type"] for f in frames}

    await wait_for(both_ended)
    kinds = [(f["type"], f["event"], f["reason"]) for f in frames if f["type"] in ("screen_session", "input_session")]
    assert ("screen_session", "ended", "remote_disabled") in kinds and (
        "input_session",
        "ended",
        "remote_disabled",
    ) in kinds
    release = next(r for r in fast.fake.input_events if r.name == "release")
    assert release.args == (("left",), ()) and release.at - t0 < 1.0  # released long before the worker's grace ran out
    assert kinds.index(("input_session", "ended", "remote_disabled")) < kinds.index(
        ("screen_session", "ended", "remote_disabled")
    )
    assert took >= fast.agent.screen.supervisor.stop_grace  # ... and the call waited for the screen to end
    assert worker.terminated and worker.exit_code is not None and fast.ui.hidden == 1
    assert fast.agent.screen.current is None and fast.fake.held_buttons == set()
    await fast.agent.set_remote_enabled(True)


async def test_revocation_narrowing_and_plan_changes_end_the_session(
    fast: AgentHarness, controller: Controller
) -> None:
    await live(fast, controller)
    await fast.agent.update_grant_capabilities(controller.controller_id, remove=["screen_audio"])  # keeps screen
    await asyncio.sleep(0.2)
    assert fast.agent.screen.current is not None
    await fast.agent.update_grant_capabilities(controller.controller_id, remove=["screen"])
    ended = await expect_screen_event(fast, "ended", "grant_removed")
    assert ended["reason"] == "grant_removed"
    grant_update = await fast.relay.expect("grant_update")
    assert "screen" not in grant_update["capabilities"] and "screen_audio" not in grant_update["capabilities"]
    # back on (the fake platform may widen without the window: tests), then a snapshot that drops the phone
    await fast.agent.update_grant_capabilities(controller.controller_id, add=["screen"])
    await fast.relay.expect("grant_update")
    await live(fast, controller)
    fast.relay.controllers.clear()
    await fast.relay.send_snapshot()
    ended = await expect_screen_event(fast, "ended", "controller_revoked")
    assert ended["reason"] == "controller_revoked"
    fast.relay.controllers.append(controller.snapshot_entry())
    controller.grant_locally(fast.agent.store)
    await fast.relay.send_snapshot()
    await fast.relay.expect("state")
    await live(fast, controller)
    fast.relay.pc_enabled = False
    await fast.relay.send_snapshot()
    ended = await expect_screen_event(fast, "ended", "plan_disabled")
    assert ended["reason"] == "plan_disabled"
    fast.relay.pc_enabled = True
    await fast.relay.send_snapshot()
    await fast.relay.expect("state")
    await live(fast, controller)
    await fast.agent.revoke_controller_locally(controller.controller_id)
    ended = await expect_screen_event(fast, "ended", "controller_revoked")
    assert ended["reason"] == "controller_revoked"


async def test_lock_ends_and_a_secure_desktop_pauses_then_ends(fast: AgentHarness, controller: Controller) -> None:
    _res, worker = await live(fast, controller)
    fast.fake.locked = True
    ended = await expect_screen_event(fast, "ended", "session_locked")
    assert ended["reason"] == "session_locked" and worker.exit_code == 0
    fast.fake.locked = False
    fast.agent.screen.secure_pause_max = 0.6
    _res, worker = await live(fast, controller)
    fast.fake.secure_desktop = True  # a UAC prompt
    paused = await expect_screen_event(fast, "paused", "secure_desktop")
    assert paused["reason"] == "secure_desktop" and worker.messages_of("pause")[-1]["reason"] == "secure_desktop"
    fast.fake.secure_desktop = False
    resumed = await expect_screen_event(fast, "resumed", "secure_desktop")
    assert resumed["path"] == "direct" and worker.messages_of("resume")
    fast.fake.secure_desktop = True
    await expect_screen_event(fast, "paused", "secure_desktop")
    ended = await expect_screen_event(fast, "ended", "secure_desktop")
    assert ended["reason"] == "secure_desktop"
    fast.fake.secure_desktop = False


async def test_max_duration_ends_the_session(fast: AgentHarness, controller: Controller) -> None:
    fast.agent.screen.max_seconds = 0.6
    await live(fast, controller)
    ended = await expect_screen_event(fast, "ended", "max_duration")
    assert ended["reason"] == "max_duration"


async def test_agent_shutdown_ends_as_agent_restart_and_recovery_reports_it(
    fast: AgentHarness, controller: Controller
) -> None:
    res, worker = await live(fast, controller)
    recovery = fast.settings.state_dir / RECOVERY_FILENAME
    data = json.loads(recovery.read_text())
    assert data == {
        "screen_session_id": res["result"]["screen_session_id"],
        "controller_id": controller.controller_id,
        "display_name": controller.display_name,
        "started_at": data["started_at"],
    }
    assert "sdp" not in recovery.read_text().lower()
    # a crashed previous process: the file survives, the next start-up records agent_restart
    recovery.write_text(json.dumps(data))
    fast.agent.screen._delete_recovery = lambda: None  # type: ignore[method-assign]  # noqa: SLF001
    assert await fast.agent.screen.recover_after_restart() is True
    await fast.agent.screen.on_snapshot_applied()
    frame = await expect_screen_event(fast, "ended", "agent_restart")
    assert frame["screen_session_id"] == res["result"]["screen_session_id"]
    assert fast.agent.screen.history.recent()[0]["reason"] == "agent_restart"
    await fast.agent.screen.shutdown()
    ended = await expect_screen_event(fast, "ended", "agent_restart")
    assert ended["reason"] == "agent_restart" and worker.exit_code == 0


# ----- allowance tickets (the dead-man switch) ------------------------------------------------------------------------------
async def test_the_agent_asks_for_tickets_and_ends_when_none_comes(fast: AgentHarness, controller: Controller) -> None:
    fast.agent.screen.renew_before = 1.0
    res, worker = await live(fast, controller, relay_screen=ticket(seconds=1.6))
    sid = res["result"]["screen_session_id"]
    usage = await fast.relay.expect("screen_usage", timeout=5)
    load_schemas().validate_frame("agent_to_relay", usage)
    assert usage["screen_session_id"] == sid and usage["relayed"] is False and usage["relayed_bytes"] == 0
    ended = await expect_screen_event(fast, "ended", "allowance_unconfirmed")  # the relay never answered
    assert ended["reason"] == "allowance_unconfirmed" and worker.exit_code == 0


async def test_tickets_renew_and_final_reasons_map(fast: AgentHarness, controller: Controller) -> None:
    fast.agent.screen.renew_before = 1.0
    res, worker = await live(fast, controller, relay_screen=ticket(seconds=1.5))
    sid = res["result"]["screen_session_id"]
    await fast.relay.expect("screen_usage", timeout=5)
    await fast.relay.send(
        {
            "type": "screen_allowance",
            "screen_session_id": sid,
            "granted_until": format_rfc3339(now_utc() + timedelta(seconds=1.5)),
            "unlimited": False,
            "final": False,
        }
    )
    await wait_for(lambda: any(m.get("op") == "allowance" for m in worker.messages))
    forwarded = worker.messages_of("allowance")[-1]
    assert forwarded["unlimited"] is False and forwarded["remaining_seconds"] is None
    await fast.relay.expect("screen_usage", timeout=5)  # the next chunk is asked for again
    await fast.relay.send(
        {
            "type": "screen_allowance",
            "screen_session_id": sid,
            "granted_until": format_rfc3339(now_utc() + timedelta(seconds=0.8)),
            "unlimited": False,
            "final": True,
            "resets_at": format_rfc3339(now_utc() + timedelta(hours=4)),
        }
    )
    ended = await expect_screen_event(fast, "ended", "allowance_exhausted")
    assert ended["reason"] == "allowance_exhausted"


async def test_controller_offline_ends_within_one_ticket_at_once(fast: AgentHarness, controller: Controller) -> None:
    res, _worker = await live(fast, controller)
    sid = res["result"]["screen_session_id"]
    await fast.relay.send(
        {
            "type": "screen_allowance",
            "screen_session_id": sid,
            "granted_until": None,
            "unlimited": False,
            "final": True,
            "reason": "controller_offline",
        }
    )
    ended = await expect_screen_event(fast, "ended", "controller_offline")
    assert ended["reason"] == "controller_offline"
    await live(fast, controller)
    sid2 = fast.agent.screen.current.screen_session_id  # type: ignore[union-attr]
    await fast.relay.send(
        {
            "type": "screen_allowance",
            "screen_session_id": sid,
            "granted_until": None,
            "unlimited": False,
            "final": True,
            "reason": "plan_disabled",
        }
    )
    await asyncio.sleep(0.2)
    assert fast.agent.screen.current is not None  # a ticket for an ended session is dropped
    await fast.relay.send(
        {
            "type": "screen_allowance",
            "screen_session_id": sid2,
            "granted_until": None,
            "unlimited": False,
            "final": True,
            "reason": "plan_disabled",
        }
    )
    assert (await expect_screen_event(fast, "ended", "plan_disabled"))["reason"] == "plan_disabled"


async def test_unlimited_plans_still_ask_in_chunks(fast: AgentHarness, controller: Controller) -> None:
    fast.agent.screen.chunk_seconds = 1.2
    fast.agent.screen.renew_before = 0.8
    await live(fast, controller, relay_screen={"granted_until": None, "unlimited": True, "stun_urls": []})
    usage = await fast.relay.expect("screen_usage", timeout=5)
    assert usage["live_seconds"] >= 0
    ended = await expect_screen_event(fast, "ended", "allowance_unconfirmed")
    assert ended["reason"] == "allowance_unconfirmed"


async def test_tickets_are_not_requested_before_the_snapshot_is_back(
    fast: AgentHarness, controller: Controller
) -> None:
    fast.agent.screen.renew_before = 1.0
    await live(fast, controller, relay_screen=ticket(seconds=1.5))
    fast.agent._snapshot_received = False  # noqa: SLF001  # reconnecting: the snapshot must be applied first
    fast.relay.drain()
    await asyncio.sleep(0.6)
    assert not any(f["type"] == "screen_usage" for f in fast.relay.drain())
    fast.agent._snapshot_received = True  # noqa: SLF001
    await fast.relay.expect("screen_usage", timeout=3)


# ----- consent ------------------------------------------------------------------------------------------------------------
async def test_ask_mode_shows_the_consent_window_and_allow_starts(fast: AgentHarness, controller: Controller) -> None:
    fast.agent.store.set_screen_grant_settings(controller.controller_id, ask_each_time=True, cooldown_seconds=600)
    cid = await send_start(fast, controller)
    await wait_for(lambda: bool(fast.ui.consent_requests))
    req = fast.ui.consent_requests[-1]
    assert req.cause == "ask_mode" and req.display_name == controller.display_name and req.timeout_seconds == 30
    assert fast.agent.permission_ui.is_open and fast.screen_worker.spawned == []  # no worker while asking
    fast.ui.answer_consent("allow")
    res = await fast.result(cid)
    assert res["state"] == "succeeded" and res["result"]["consent"] == "granted"
    assert not fast.agent.permission_ui.is_open


async def test_deny_timeout_block_and_turn_off(fast: AgentHarness, controller: Controller) -> None:
    fast.agent.store.set_screen_grant_settings(controller.controller_id, ask_each_time=True, cooldown_seconds=600)
    gate = fast.agent.screen.gate
    gate.window_interval = 0.0
    gate.backoff = (0.0, 0.0, 0.0, 0.0)
    cid = await send_start(fast, controller)
    await wait_for(lambda: bool(fast.ui.consent_requests))
    fast.ui.answer_consent("deny")
    res = await fast.result(cid)
    assert res["error"]["code"] == "SCREEN_CONSENT_DECLINED" and res["error"]["message"] == "Not allowed at the PC."
    assert fast.agent.screen.history.recent()[0] == {
        **fast.agent.screen.history.recent()[0],
        "kind": "refused",
        "reason": "denied",
    }
    assert any(
        e["kind"] == "screen_consent_declined" and e["detail"] == {"reason": "denied"}
        for e in fast.agent.store.list_security_events()
    )
    fast.agent.screen.broker.timeout_seconds = 0.3
    cid = await send_start(fast, controller)
    res = await fast.result(cid)
    assert res["error"]["code"] == "SCREEN_CONSENT_TIMEOUT" and fast.ui.closed_consents
    assert fast.agent.screen.history.recent()[0]["reason"] == "timeout"
    cid = await send_start(fast, controller)
    await wait_for(lambda: len(fast.ui.consent_requests) == 3)
    fast.ui.answer_consent("block")
    res = await fast.result(cid)
    assert (
        res["error"]["code"] == "SCREEN_CONSENT_DECLINED"
        and fast.agent.screen.history.recent()[0]["reason"] == "blocked"
    )
    res = await start_screen(fast, controller)  # blocked for an hour: refused at once, no window
    assert res["error"]["code"] == "SCREEN_CONSENT_DECLINED" and len(fast.ui.consent_requests) == 3
    gate._blocks.clear()  # noqa: SLF001
    cid = await send_start(fast, controller)
    await wait_for(lambda: len(fast.ui.consent_requests) == 4)
    fast.ui.answer_consent("turn_off")
    res = await fast.result(cid)
    assert res["error"]["code"] == "SCREEN_CONSENT_DECLINED"
    update = await fast.relay.expect("grant_update")
    assert "screen" not in update["capabilities"] and fast.screen_worker.spawned == []


async def test_a_stop_at_the_pc_asks_every_phone_and_the_third_refusal_opens_no_window(
    fast: AgentHarness, controller: Controller
) -> None:
    _res, worker = await live(fast, controller)
    fast.ui.press_stop_on_bar()
    ended = await expect_screen_event(fast, "ended", "stopped_on_pc")
    assert ended["reason"] == "stopped_on_pc" and worker.exit_code == 0
    until, by = fast.agent.store.get_screen_cooldown()
    assert until is not None and by == controller.controller_id
    other = await second_phone(fast, controller, ("status", "screen"))  # a second installation of the same owner
    cid = await send_start(fast, other, takeover=True)
    await wait_for(lambda: bool(fast.ui.consent_requests))
    assert (
        fast.ui.consent_requests[-1].cause == "cooldown"
        and fast.ui.consent_requests[-1].controller_id == other.controller_id
    )
    fast.ui.answer_consent("deny")
    assert (await fast.result(cid))["error"]["code"] == "SCREEN_CONSENT_DECLINED"
    res = await start_screen(fast, controller)  # within the back-off: refused at once, no window
    assert res["error"]["code"] == "SCREEN_CONSENT_DECLINED" and len(fast.ui.consent_requests) == 1
    assert fast.agent.screen.history.recent()[0]["reason"] == "backoff"
    fast.agent.screen.gate._backoff_until = 0.0  # noqa: SLF001
    res = await start_screen(fast, controller)  # still inside the one-window-per-minute rule
    assert res["error"]["code"] == "SCREEN_CONSENT_DECLINED" and len(fast.ui.consent_requests) == 1


async def test_stop_from_the_tray_and_the_hotkey(fast: AgentHarness, controller: Controller) -> None:
    from dome_agent.control import ControlClient

    await live(fast, controller)
    ctl = ControlClient(fast.settings.state_dir)
    assert (await asyncio.to_thread(ctl.call, "screen_stop")) == {"stopped": True}
    assert (await expect_screen_event(fast, "ended", "stopped_on_pc"))["reason"] == "stopped_on_pc"
    fast.agent.store.set_screen_cooldown(None, None)
    await live(fast, controller)
    fast.ui.press_hotkey()
    assert (await expect_screen_event(fast, "ended", "stopped_on_pc"))["reason"] == "stopped_on_pc"
    assert (await asyncio.to_thread(ctl.call, "screen_stop")) == {"stopped": False}
    history = await asyncio.to_thread(ctl.call, "screen_history")
    assert len(history["history"]) == 2 and all(r["reason"] == "stopped_on_pc" for r in history["history"])
    assert (await asyncio.to_thread(ctl.call, "screen_history", clear=True))["cleared"] == 2


async def test_stop_and_turn_off_from_the_bar(fast: AgentHarness, controller: Controller) -> None:
    await live(fast, controller)
    fast.ui.press_turn_off_on_bar()
    ended = await expect_screen_event(fast, "ended", "grant_removed")
    assert ended["reason"] == "grant_removed"
    update = await fast.relay.expect("grant_update")
    assert set(update["capabilities"]).isdisjoint({"screen", "screen_audio"})


# ----- grants only through the window -----------------------------------------------------------------------------------------
async def test_screen_grant_goes_through_the_window_and_screen_audio_needs_screen(
    fast: AgentHarness, controller: Controller
) -> None:
    agent = fast.agent
    await agent.update_grant_capabilities(controller.controller_id, remove=["screen", "screen_audio"])
    await fast.relay.expect("grant_update")
    agent.platform.name = "windows"  # a real PC: a bare flag may not widen (rules.local_permission_ui)
    try:
        with pytest.raises(ProtocolError) as exc:
            await agent.update_grant_capabilities(controller.controller_id, add=["screen"])
        assert exc.value.code == "INVALID_PARAMETERS"
        with pytest.raises(ProtocolError):
            await agent.update_grant_capabilities(
                controller.controller_id, add=["screen_audio"], via_window=True
            )  # audio without screen
        opened = await agent.request_screen_grant(controller.controller_id)
        assert opened is not None and opened["window_opened"] and agent.permission_ui.is_open
        req = fast.ui.grant_requests[-1]
        assert (req.screen, req.screen_audio, req.ask_each_time, req.cooldown_seconds) == (False, False, False, 600)
        fast.ui.answer_grant(None)  # Cancel
        await wait_for(lambda: not agent.permission_ui.is_open)
        assert "screen" not in agent.store.get_grant(controller.controller_id).capabilities  # type: ignore[union-attr]
        await agent.request_screen_grant(controller.controller_id)
        from dome_agent.ui import ScreenGrantDecision

        fast.ui.answer_grant(ScreenGrantDecision(screen_audio=True, ask_each_time=True, cooldown_seconds=60))
        update = await fast.relay.expect("grant_update")
        assert {"screen", "screen_audio"} <= set(update["capabilities"])
        settings = agent.store.get_screen_grant_settings(controller.controller_id)
        assert settings.ask_each_time is True and settings.cooldown_seconds == 60
        assert await agent.request_screen_grant(str(uuid.uuid4())) is None  # an unknown phone opens nothing
    finally:
        agent.platform.name = "fake"


async def test_cli_grant_screen_opens_the_window_through_the_control_channel(
    fast: AgentHarness, controller: Controller
) -> None:
    from dome_agent.control import ControlClient

    fast.agent.platform.name = "windows"
    try:
        result = await asyncio.to_thread(
            ControlClient(fast.settings.state_dir).call,
            "grant",
            controller_id=controller.controller_id,
            add=["screen"],
            remove=[],
        )
        assert result["window_opened"] is True and fast.ui.grant_requests
        assert fast.agent.status()["screen_grant_windows"] == [result["request_id"]]
        fast.ui.answer_grant(None)
        await wait_for(lambda: fast.agent.status()["screen_grant_windows"] == [])
    finally:
        fast.agent.platform.name = "fake"


async def test_cli_grant_screen_waits_for_the_answer_in_the_window(
    fast: AgentHarness, controller: Controller, capsys: pytest.CaptureFixture[str]
) -> None:
    """`DoMe.exe grant <id> --screen` opens the window and reports only what the person at the PC answered there,
    also for a phone that already has Screen view (the window then only changes its options)."""
    from dome_agent import cli
    from dome_agent.ui import ScreenGrantDecision

    agent = fast.agent
    await agent.update_grant_capabilities(controller.controller_id, remove=["screen", "screen_audio"])
    agent.platform.name = "windows"
    argv = [
        "--state-dir",
        str(fast.settings.state_dir),
        "grant",
        controller.controller_id,
        "--screen",
        "--timeout",
        "15",
    ]
    try:
        run = asyncio.ensure_future(asyncio.to_thread(cli.main, argv))
        await wait_for(lambda: len(fast.ui.grant_requests) == 1)
        await asyncio.sleep(1.2)  # the CLI keeps waiting while the window is up
        assert not run.done()
        fast.ui.answer_grant(ScreenGrantDecision(ask_each_time=True, cooldown_seconds=60))
        assert await asyncio.wait_for(run, 10) == 0
        assert "Screen view is allowed" in capsys.readouterr().out
        # already granted: the window opens for the options, and the CLI still answers only once it was answered
        run = asyncio.ensure_future(asyncio.to_thread(cli.main, argv))
        await wait_for(lambda: len(fast.ui.grant_requests) == 2)
        assert fast.ui.grant_requests[-1].screen is True
        await asyncio.sleep(1.2)
        assert not run.done()
        fast.ui.answer_grant(None)
        assert await asyncio.wait_for(run, 10) == 0  # Cancel took nothing away: still allowed
        settings = agent.store.get_screen_grant_settings(controller.controller_id)
        assert settings.ask_each_time is True and settings.cooldown_seconds == 60
    finally:
        agent.platform.name = "fake"


# ----- the absolute pointer and DoMe's own permission UI ------------------------------------------------------------------------------
async def test_pointer_move_to_needs_a_connected_session_at_the_current_gen(
    fast: AgentHarness, controller: Controller
) -> None:
    sid = await start_input(fast, controller)
    tap = [
        {"type": "pointer_move_to", "x": 65535, "y": 65535, "gen": 1},
        {"type": "pointer_button", "button": "left", "action": "click"},
    ]
    await send_batch(fast, controller, sid, 1, tap)
    err = await expect_error(fast, "SCREEN_SESSION_REQUIRED")
    assert err["ref_controller_id"] == controller.controller_id and err["ref_input_session_id"] == sid
    assert fast.fake.input_count("move_to") == 0 and fast.fake.input_count("button") == 0  # dropped whole
    assert fast.agent.input.summary()["session"]["dropped_batches"] == 1
    await live(fast, controller)
    await send_batch(fast, controller, sid, 2, [{"type": "pointer_move_to", "x": 65535, "y": 65535, "gen": 7}])
    await expect_error(fast, "SCREEN_GEOMETRY_CHANGED")
    await send_batch(fast, controller, sid, 3, tap)
    await wait_for(lambda: fast.fake.input_count("button") == 1)
    assert [r.args for r in fast.fake.input_events if r.name == "move_to"] == [(1919, 1079)]  # mapped pixels
    await send_batch(
        fast,
        controller,
        sid,
        4,
        [
            {"type": "pointer_move_to", "x": 100, "y": 100, "gen": 1},
            {"type": "pointer_move_to", "x": 32768, "y": 32768, "gen": 1},
        ],
    )
    await wait_for(lambda: fast.fake.input_count("move_to") == 2)
    assert fast.fake.input_events[-1].args == (960, 540)  # last position wins, never summed
    await send_batch(
        fast, controller, sid, 5, [{"type": "pointer_wheel", "dx": 0, "dy": -240}, {"type": "key", "key": "f11"}]
    )
    await wait_for(lambda: fast.fake.input_count("key") == 1)
    assert fast.fake.input_events[-2].args == (0, -240) and fast.fake.input_events[-1].args == ("f11",)
    other = await second_phone(fast, controller, ("status", "screen", "pointer"))
    # the input session belongs to the controller; the screen session too: another phone's batch is not its session
    env = other.command("input.session_start", {"takeover": True})
    await fast.send_command(env)
    other_sid = (await fast.result(payload_of(env)["command_id"]))["result"]["input_session_id"]
    await send_batch(fast, other, other_sid, 1, [{"type": "pointer_move_to", "x": 1, "y": 1, "gen": 1}])
    assert (await expect_error(fast, "SCREEN_SESSION_REQUIRED"))["ref_controller_id"] == other.controller_id


async def test_presses_are_dropped_while_a_permission_window_is_open_and_on_dome_ui(
    fast: AgentHarness, controller: Controller
) -> None:
    sid = await start_input(fast, controller)
    await live(fast, controller)
    token = fast.agent.permission_ui.open("consent_window")
    await send_batch(
        fast,
        controller,
        sid,
        1,
        [
            {"type": "pointer_move", "dx": 3, "dy": 3},
            {"type": "pointer_button", "button": "left", "action": "click"},
            {"type": "text", "text": "allow"},
        ],
    )
    err = await expect_error(fast, "INPUT_PAUSED_ON_PC")
    assert err["error"]["retryable"] is True
    await wait_for(lambda: fast.fake.input_count("move") == 1)
    assert fast.fake.input_count("button") == 0 and fast.fake.input_count("text") == 0
    fast.agent.permission_ui.close(token)
    # a press that would land on the bar (the top 40 px of the primary monitor) is never injected
    await send_batch(
        fast,
        controller,
        sid,
        2,
        [
            {"type": "pointer_move_to", "x": 1000, "y": 0, "gen": 1},
            {"type": "pointer_button", "button": "left", "action": "click"},
        ],
    )
    err = await expect_error(fast, "INPUT_TARGET_RESERVED")
    assert err["error"]["retryable"] is False and fast.fake.input_count("button") == 0
    await send_batch(
        fast,
        controller,
        sid,
        3,
        [
            {"type": "pointer_move_to", "x": 1000, "y": 60000, "gen": 1},
            {"type": "pointer_button", "button": "left", "action": "click"},
        ],
    )
    await wait_for(lambda: fast.fake.input_count("button") == 1)
    # the three dropped events (button and text behind the window, the press on the bar) are counted in the acks
    summary = fast.agent.input.summary()["session"]
    assert summary is not None and summary["dropped_events"] == 3 and summary["accepted_events"] == 4
    while True:
        ack = await fast.relay.expect("input_ack", timeout=5)
        if ack["dropped_events"] >= 3:
            break


async def test_restricted_input_is_told_to_the_phone_over_the_worker(
    fast: AgentHarness, controller: Controller
) -> None:
    _res, worker = await live(fast, controller)
    await start_input(fast, controller)
    fast.fake.input_restricted = True
    await wait_for(lambda: any(m.get("op") == "restricted" and m["restricted"] is True for m in worker.messages))
    fast.fake.input_restricted = False


# ----- content hygiene ---------------------------------------------------------------------------------------------------------
async def test_no_sdp_or_address_reaches_logs_journal_bundle_or_state(
    fast: AgentHarness, controller: Controller, capsys: pytest.CaptureFixture[str]
) -> None:
    from dome_agent.diagnostics import build_bundle
    from dome_agent.logsetup import configure_logging

    configure_logging("DEBUG", fast.settings.log_path)
    try:
        res, worker = await live(fast, controller)
        worker.stderr(
            "aioice Check (host 127.0.0.1:50000 -> 10.1.2.3:3478) and a=fingerprint:sha-256 " + SENTINEL_FINGERPRINT
        )
        await fast.agent.screen.end("stopped")
        await expect_screen_event(fast, "ended", "stopped")
        log_text = fast.settings.log_path.read_text(encoding="utf-8")
        assert "screen session starting" in log_text and "screen worker" in log_text
        captured = capsys.readouterr()  # read once: a second read returns only what was written since
        console = captured.out + captured.err
        assert "screen session ended" in console  # the console capture is not empty: the check below is real
        for needle in (SENTINEL_FINGERPRINT, "127.0.0.1", "10.1.2.3", "a=ice-pwd", "phonephone"):
            assert needle not in log_text, needle
            assert needle not in console, needle
            assert needle not in json.dumps(fast.agent.store.list_security_events(500))
            assert needle not in json.dumps(fast.agent.status())
            assert needle not in json.dumps(build_bundle(fast.settings, fast.agent.status()))
            assert needle not in fast.settings.db_path.read_bytes().decode("latin-1")
        frames = fast.relay.drain()
        assert all(SENTINEL_FINGERPRINT not in json.dumps(f) for f in frames if f["type"] != "screen_answer")
    finally:
        configure_logging("INFO", None)


# ----- review round (2026-10-22): ends from worker events, pause causes, cooldown, emergency stops ------------------------
@pytest.mark.parametrize(
    ("event", "reason"), [("bye", "stopped"), ("error", "capture_failed"), ("disconnected", "viewer_gone")]
)
async def test_a_worker_event_ends_the_session_through_the_real_launcher(
    fast: AgentHarness, controller: Controller, event: str, reason: str
) -> None:
    """The real ProcessWorkerLauncher delivers worker events on its own pipe-reader task, and the end's stop waits for
    that task: the end must run on a task of its own. Before the fix `bye` (every phone Stop and backgrounding) and
    `disconnected` (every Wi-Fi/cellular switch) wedged the PC: no `ended`, bars up, no history, next start refused."""
    import os
    import sys

    from dome_agent.screen.supervisor import ProcessWorkerLauncher

    script = Path(__file__).with_name("scripted_screen_worker.py")
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        p for p in (str(Path(__file__).resolve().parents[1]), env.get("PYTHONPATH")) if p
    )
    fast.agent.screen.supervisor._launcher = ProcessWorkerLauncher(  # noqa: SLF001
        env=env, command=[sys.executable, str(script), event]
    )
    res = await start_screen(fast, controller)
    assert res["state"] == "succeeded", res
    await expect_screen_event(fast, "started", "started", timeout=15)
    recovery = fast.settings.state_dir / RECOVERY_FILENAME
    assert recovery.exists()
    ended = await expect_screen_event(fast, "ended", reason, timeout=15)
    assert ended["screen_session_id"] == res["result"]["screen_session_id"]
    await wait_for(lambda: fast.ui.hidden == 1 and not fast.ui.bars_shown, timeout=10)
    assert fast.agent.screen.history.recent()[0]["reason"] == reason
    assert not recovery.exists() and not fast.agent.screen.supervisor.running
    assert fast.agent.screen.supervisor.process is None and not fast.agent.screen._end_lock.locked()  # noqa: SLF001
    again = await start_screen(fast, controller)  # the PC is free for the next start
    assert again["state"] == "succeeded", again
    await expect_screen_event(fast, "started", "started", timeout=15)
    assert await fast.agent.screen.stop_on_pc("cli") is True
    await expect_screen_event(fast, "ended", "stopped_on_pc", timeout=15)


async def test_late_events_after_a_scheduled_end_are_dropped_and_a_new_start_waits_for_it(
    fast: AgentHarness, controller: Controller
) -> None:
    _res, worker = await live(fast, controller)
    await worker.emit({"ev": "bye"})
    assert fast.agent.screen.current is None  # retired synchronously, before the end task has run
    await worker.emit({"ev": "first_frame"})
    await worker.emit({"ev": "disconnected"})
    ended = await expect_screen_event(fast, "ended", "stopped")
    assert ended["reason"] == "stopped"
    await fast.agent.screen.drain_ends()
    assert [f for f in fast.relay.drain() if f["type"] == "screen_session"] == []  # one `ended`, nothing after it
    await live(fast, controller)
    assert fast.ui.bars_shown


async def test_uac_then_a_missing_bar_keeps_capture_paused_until_both_are_gone(
    fast: AgentHarness, controller: Controller
) -> None:
    """Pause causes are a set (design §7): a UAC prompt answered while a bar is still missing must not resume
    capture, and the secure-desktop limit keeps running while a bar is missing."""
    fast.agent.screen.indicator_grace = 30.0
    _res, worker = await live(fast, controller)
    fast.fake.secure_desktop = True
    await expect_screen_event(fast, "paused", "secure_desktop")
    assert worker.paused
    fast.ui.bars_present = False
    await expect_screen_event(fast, "paused", "indicator")
    fast.fake.secure_desktop = False
    await expect_screen_event(fast, "resumed", "secure_desktop")
    await asyncio.sleep(0.4)
    assert worker.paused and worker.messages_of("resume") == []  # a bar is still missing: capture stays paused
    assert fast.agent.screen.summary()["session"]["paused_causes"] == ["indicator"]
    fast.ui.bars_present = True
    await expect_screen_event(fast, "resumed", "indicator")
    await wait_for(lambda: not worker.paused)
    assert len(worker.messages_of("pause")) == 1 and len(worker.messages_of("resume")) == 1


async def test_a_missing_bar_then_uac_starts_the_secure_desktop_limit_and_keeps_capture_paused(
    fast: AgentHarness, controller: Controller
) -> None:
    fast.agent.screen.indicator_grace = 30.0
    fast.agent.screen.secure_pause_max = 0.8
    _res, worker = await live(fast, controller)
    fast.ui.bars_present = False
    await expect_screen_event(fast, "paused", "indicator")
    fast.fake.secure_desktop = True
    await expect_screen_event(fast, "paused", "secure_desktop")  # timed although capture was already paused
    fast.ui.bars_present = True
    await expect_screen_event(fast, "resumed", "indicator")
    await asyncio.sleep(0.3)
    assert worker.paused and worker.messages_of("resume") == []  # the secure desktop still pauses capture
    ended = await expect_screen_event(fast, "ended", "secure_desktop")
    assert ended["reason"] == "secure_desktop"
    fast.fake.secure_desktop = False


async def test_stop_and_turn_off_on_the_bar_starts_the_cooldown_for_every_installation(
    fast: AgentHarness, controller: Controller
) -> None:
    """Design §6.3: the bar's "Stop and turn off Screen view for this phone" is the strongest "don't watch me" the
    desk can give; a second installation (the Safari tab and the home-screen app) must then meet a consent window."""
    other = await second_phone(fast, controller, ("status", "screen"))
    await live(fast, controller)
    fast.ui.press_turn_off_on_bar()
    await expect_screen_event(fast, "ended", "grant_removed")
    until, by = fast.agent.store.get_screen_cooldown()
    assert until is not None and by == controller.controller_id
    event = next(e for e in fast.agent.store.list_security_events() if e["kind"] == "screen_stopped_on_pc")
    assert event["detail"] == {"source": "bar", "cooldown_seconds": 600, "turned_off": True}
    cid = await send_start(fast, other, takeover=True)
    await wait_for(lambda: bool(fast.ui.consent_requests))
    assert fast.ui.consent_requests[-1].cause == "cooldown" and fast.ui.consent_requests[-1].controller_id == (
        other.controller_id
    )
    fast.ui.answer_consent("deny")
    assert (await fast.result(cid))["error"]["code"] == "SCREEN_CONSENT_DECLINED"


async def test_the_trays_turn_off_while_that_phone_watches_starts_the_cooldown_too(
    fast: AgentHarness, controller: Controller
) -> None:
    fast.agent.store.set_screen_grant_settings(controller.controller_id, ask_each_time=False, cooldown_seconds=60)
    await live(fast, controller)
    assert await fast.agent.screen.turn_off_on_pc(controller.controller_id, "tray") is True
    await expect_screen_event(fast, "ended", "grant_removed")
    until, _by = fast.agent.store.get_screen_cooldown()
    assert until is not None  # the stopped phone's own (1 minute) setting was read before its grant narrowed
    assert parse_wall(until) - now_utc() <= timedelta(seconds=61)
    update = await fast.relay.expect("grant_update")
    assert "screen" not in update["capabilities"]
    # not watching: turning Screen view off for a phone only narrows its grant
    fast.agent.store.set_screen_cooldown(None, None)
    await fast.agent.update_grant_capabilities(controller.controller_id, add=["screen"])
    await fast.relay.expect("grant_update")
    assert await fast.agent.screen.turn_off_on_pc(controller.controller_id, "tray") is False
    assert fast.agent.store.get_screen_cooldown() == (None, None)


async def test_disable_remote_control_during_the_consent_window_refuses_the_start(
    fast: AgentHarness, controller: Controller
) -> None:
    fast.agent.store.set_screen_grant_settings(controller.controller_id, ask_each_time=True, cooldown_seconds=600)
    cid = await send_start(fast, controller)
    await wait_for(lambda: bool(fast.ui.consent_requests))
    await fast.agent.set_remote_enabled(False)
    res = await fast.result(cid)
    assert res["state"] == "failed" and res["error"]["code"] == "PC_REMOTE_DISABLED"
    assert fast.ui.closed_consents == [fast.ui.consent_requests[-1].request_id]  # the window is withdrawn
    assert not fast.agent.permission_ui.is_open
    assert fast.screen_worker.spawned == [] and fast.ui.bar_requests == []
    assert fast.agent.screen.gate.summary()["consecutive_refusals"] == 0  # nobody refused: no back-off
    fast.ui.answer_consent("allow")  # a late Allow on the closed window changes nothing
    await asyncio.sleep(0.1)
    assert fast.screen_worker.spawned == []
    await fast.agent.set_remote_enabled(True)


async def test_a_lock_or_uac_prompt_during_the_consent_window_refuses_the_start(
    fast: AgentHarness, controller: Controller
) -> None:
    fast.agent.store.set_screen_grant_settings(controller.controller_id, ask_each_time=True, cooldown_seconds=600)
    fast.agent.screen.gate.window_interval = 0.0
    cid = await send_start(fast, controller)
    await wait_for(lambda: bool(fast.ui.consent_requests))
    fast.fake.locked = True
    fast.ui.answer_consent("allow")
    res = await fast.result(cid)
    assert res["error"]["code"] == "PC_SESSION_LOCKED" and fast.screen_worker.spawned == []
    fast.fake.locked = False
    cid = await send_start(fast, controller)
    await wait_for(lambda: len(fast.ui.consent_requests) == 2)
    fast.fake.secure_desktop = True
    fast.ui.answer_consent("allow")
    res = await fast.result(cid)
    assert res["error"]["code"] == "PC_SESSION_LOCKED" and fast.screen_worker.spawned == []
    fast.fake.secure_desktop = False


async def test_a_frozen_ui_thread_pauses_capture_withholds_ticks_and_ends_the_session(
    fast: AgentHarness, controller: Controller
) -> None:
    """Design §7: a frozen UI thread cannot leave an unattended stream behind bars that no longer repaint. Its
    heartbeat goes stale: the bars no longer count as present (capture pauses `indicator`, then `capture_failed`)
    and the supervisor withholds the worker's ticks, so the worker stops by its own timeout as well."""
    screen = fast.agent.screen
    screen.indicator_grace = 1.0
    screen.indicator.confirm_timeout = 0.2
    screen.tick_timeout_seconds = 1.5
    screen.supervisor.tick_interval = 0.2
    _res, worker = await live(fast, controller)
    await wait_for(lambda: worker.ticks >= 2)
    fast.ui.ui_frozen = True
    paused = await expect_screen_event(fast, "paused", "indicator")
    assert paused["reason"] == "indicator" and worker.paused
    ticks = worker.ticks
    ended = await expect_screen_event(fast, "ended", "capture_failed")
    assert ended["reason"] == "capture_failed" and worker.exit_code is not None
    assert worker.ticks <= ticks + 1 and screen.supervisor.ticks_withheld >= 1  # no tick while the UI was frozen
    assert fast.agent.screen.current is None
    fast.ui.ui_frozen = False


async def test_a_frozen_ui_thread_makes_the_worker_exit_by_its_tick_timeout(
    fast: AgentHarness, controller: Controller
) -> None:
    screen = fast.agent.screen
    screen.indicator_grace = 30.0  # the agent's own grace is long here: the worker's tick timeout ends it first
    screen.indicator.confirm_timeout = 0.2
    screen.tick_timeout_seconds = 0.8
    screen.supervisor.tick_interval = 0.2
    _res, worker = await live(fast, controller)
    await wait_for(lambda: worker.ticks >= 2)
    fast.ui.ui_frozen = True
    ended = await expect_screen_event(fast, "ended", "capture_failed")
    assert worker.exit_reason == "ticks_stopped" and ended["reason"] == "capture_failed"
    fast.ui.ui_frozen = False


async def test_a_bar_that_is_not_on_its_monitor_starts_nothing_and_a_live_one_pauses_capture(
    fast: AgentHarness, controller: Controller
) -> None:
    fast.ui.bars_misplaced = True
    res = await start_screen(fast, controller)
    assert res["error"]["code"] == "SCREEN_CAPTURE_UNAVAILABLE" and res["error"]["detail"] == {"detail": "indicator"}
    assert fast.screen_worker.spawned == []
    fast.ui.bars_misplaced = False
    fast.agent.screen.indicator_grace = 30.0
    _res, worker = await live(fast, controller)
    fast.ui.bars_misplaced = True  # a scaling change moved a bar off its monitor
    await expect_screen_event(fast, "paused", "indicator")
    assert worker.paused
    fast.ui.bars_misplaced = False
    await expect_screen_event(fast, "resumed", "indicator")


async def test_a_build_without_the_media_stack_refuses_honestly_before_any_bar(
    fast: AgentHarness, controller: Controller, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every Windows build so far is made without the `screen` extra: it still speaks 1.4 (pairing needs
    pc_key_mac) but must not blame the display. The start is refused ACTION_UNAVAILABLE{reason:
    screen_view_not_included} before any consent window, bar or worker."""
    import importlib.util

    from dome_agent import screen as screen_pkg

    real_find_spec = importlib.util.find_spec

    def without_media(name: str, *args: Any, **kw: Any) -> Any:
        return None if name in ("aiortc", "av") else real_find_spec(name, *args, **kw)

    screen_pkg.media_stack_available.cache_clear()
    monkeypatch.setattr(importlib.util, "find_spec", without_media)
    try:
        assert screen_pkg.media_stack_available() is False
        fast.agent.screen._media_available = screen_pkg.media_stack_available  # noqa: SLF001
        fast.agent.store.set_screen_grant_settings(controller.controller_id, ask_each_time=True, cooldown_seconds=600)
        res = await start_screen(fast, controller)
        assert res["state"] == "failed" and res["error"]["code"] == "ACTION_UNAVAILABLE", res
        assert res["error"]["detail"] == {"reason": "screen_view_not_included"}
        assert "doesn't include Screen view" in res["error"]["message"]
        assert fast.ui.consent_requests == [] and fast.ui.bar_requests == [] and fast.screen_worker.spawned == []
        assert fast.agent.status()["screen"]["included"] is False
    finally:
        monkeypatch.undo()
        screen_pkg.media_stack_available.cache_clear()
    assert screen_pkg.media_stack_available() is True  # this development environment has the PyPI wheels


# ----- device round 2 (2026-10-27): what the media path did, and VP8 first after an H.264 stall -----------------------------
H264_ONLY_OFFER = OFFER.replace("UDP/TLS/RTP/SAVPF 96 97\r\n", "UDP/TLS/RTP/SAVPF 96\r\n").replace(
    "a=rtpmap:97 VP8/90000\r\n", ""
)


async def test_media_stats_are_whitelisted_and_outlive_the_session_in_the_status(
    fast: AgentHarness, controller: Controller
) -> None:
    """The worker's 2 s stats are kept as `session.media` (numbers, flags and short words from a fixed list; nothing
    else, never an address), shown in `status.screen.session.media`, kept as `last_session_media` once the session has
    ended (a bundle downloaded after Stop still has it), and summarised in the `screen session ended` line."""
    _res, worker = await live(fast, controller)
    await worker.emit(
        {
            "ev": "stats",
            "relayed_bytes": 0,
            "bytes_sent": 45000,
            "captured": 3,
            "pumped": 7,
            "encoded": 10,
            "aus": 7,
            "idr": 1,
            "rtp_packets": 52,
            "rtp_bytes": 45000,
            "rr": True,
            "pli": 2,
            "first_kf_ms": 120,
            "loss": 0.0123456,
            "encoder_label": "h264_mf\nhw " + "x" * 64,
            "stalled": None,
            "address": "192.168.1.5",  # not on the list: dropped
            "candidate": "candidate:1 1 udp 2122260223 10.1.2.3 50000 typ host",
            "nested": {"a": 1},
        }
    )
    media = fast.agent.screen.summary()["session"]["media"]
    assert media["captured"] == 3 and media["aus"] == 7 and media["rr"] is True and media["first_kf_ms"] == 120
    assert media["loss"] == 0.012 and media["stalled"] is None and len(media["encoder_label"]) == 32
    assert media["encoder_label"].startswith("h264_mf hw x")  # one printable line
    from dome_agent.screen.session import media_from_stats

    assert media_from_stats({"encoder_label": "h264\x1b[31m\x00mf", "stalled": "\x07\x00"}) == {
        "encoder_label": "h264 [31m mf"  # control characters become spaces; a value of nothing but them is dropped
    }
    assert not {"address", "candidate", "nested", "bytes_sent"} & set(media)
    assert "192.168" not in json.dumps(fast.agent.status()) and "10.1.2.3" not in json.dumps(fast.agent.status())
    with structlog.testing.capture_logs() as logs:
        await fast.agent.screen.end("stopped")
        await expect_screen_event(fast, "ended", "stopped")
    [ended] = [e for e in logs if e["event"] == "screen session ended"]
    assert (ended["aus"], ended["idr"], ended["rtp_packets"], ended["rtp_kb"], ended["rr"], ended["pli"]) == (
        7,
        1,
        52,
        43,
        True,
        2,
    )
    assert ended["encoded"] == 10 and ended["first_kf_ms"] == 120
    summary = fast.agent.screen.summary()
    assert summary["session"] is None
    last = summary["last_session_media"]
    assert last["reason"] == "stopped" and last["captured"] == 3 and last["rtp_packets"] == 52
    assert fast.agent.status()["screen"]["last_session_media"]["aus"] == 7
    # the diagnostics bundle (redacted by key name) keeps every media field: no key contains a redacted word
    from dome_agent.diagnostics import build_bundle

    bundled = build_bundle(fast.settings, fast.agent.status())["status"]["screen"]["last_session_media"]
    assert bundled == last and "[redacted]" not in json.dumps(bundled)


async def test_an_h264_stall_keeps_the_session_and_makes_the_next_start_ask_for_vp8_first(
    fast: AgentHarness, controller: Controller
) -> None:
    """Design §5.3 "A PC where H.264 failed ... offers VP8 first next time": the stall is logged and kept in the media
    facts, the session is not ended (the phone's no-picture watchdog starts a fresh one; 1.4 has no renegotiation),
    and for 24 hours starts whose offer has both codecs ask the worker for VP8 first."""
    _res, worker = await live(fast, controller)
    assert worker.start_message is not None and worker.start_message["encoder_preference"] == ["h264", "vp8"]
    with structlog.testing.capture_logs() as logs:
        await worker.emit({"ev": "encoder_stalled", "encoder": "h264", "reason": "no_keyframe", "encoded": 9, "aus": 0})
    [warning] = [e for e in logs if e["event"].startswith("screen H.264 encoder stalled")]
    assert warning["log_level"] == "warning" and (warning["reason"], warning["encoded"], warning["aus"]) == (
        "no_keyframe",
        9,
        0,
    )
    session = fast.agent.screen.current
    assert session is not None and session.state == "live" and session.media["stalled"] == "no_keyframe"
    assert fast.agent.screen.summary()["vp8_first"] is True
    await fast.agent.screen.end("stopped")
    await expect_screen_event(fast, "ended", "stopped")
    with structlog.testing.capture_logs() as logs:
        _res, again = await live(fast, controller)
    assert again.start_message is not None and again.start_message["encoder_preference"] == ["vp8", "h264"]
    [pref] = [e for e in logs if e["event"] == "screen encoder preference"]
    assert pref["order"] == "vp8,h264" and pref["reason"] == "h264_stalled"
    await fast.agent.screen.end("stopped")
    await expect_screen_event(fast, "ended", "stopped")
    # an offer without VP8 keeps H.264: there is nothing else to fall back to
    _res, h264_only = await live(fast, controller, offer=H264_ONLY_OFFER)
    assert h264_only.start_message is not None and h264_only.start_message["encoder_preference"] == ["h264"]
    await fast.agent.screen.end("stopped")
    await expect_screen_event(fast, "ended", "stopped")
    # the preference expires after 24 hours: H.264 first again
    assert fast.agent.screen.vp8_first_active(time.monotonic() + 24 * 3600 + 1) is False
    _res, later = await live(fast, controller)
    assert later.start_message is not None and later.start_message["encoder_preference"] == ["h264", "vp8"]
    assert fast.agent.screen.summary()["vp8_first"] is False


async def test_a_vp8_stall_does_not_change_the_preference(fast: AgentHarness, controller: Controller) -> None:
    _res, worker = await live(fast, controller)
    await worker.emit({"ev": "encoder_stalled", "encoder": "vp8", "reason": "no_keyframe", "encoded": 3, "aus": 0})
    assert fast.agent.screen.vp8_first_active() is False
    assert fast.agent.screen.summary()["session"]["media"]["stalled"] == "no_keyframe"
