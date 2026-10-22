"""The Screen view policy and UI models without a display (design §6.3, §6.4, §7, §2.1; WP1.3/WP1.5 fake-UI tests):
the consent gate (ask mode, the PC-wide cooldown, one window per minute, the escalating back-off, the 1-hour block),
the permission-UI state the input dispatcher reads, the grant / consent window models (5 s and 1 s holds), the
indicator's reserved-rectangle normalisation and the fail-closed indicator controller with a UI that cannot create
a window."""

from __future__ import annotations

import asyncio
from concurrent.futures import Future

import pytest
from dome_protocol import ProtocolError

from dome_agent.platform.protocol import Monitor
from dome_agent.screen.consent import (
    BACKOFF_SECONDS,
    ConsentBroker,
    ConsentGate,
    ConsentWindowModel,
    GrantWindowModel,
    PermissionUiState,
)
from dome_agent.screen.indicator import IndicatorController, normalise_rects
from dome_agent.store import Store
from dome_agent.testing.fake_ui import FakeScreenUI
from dome_agent.ui import NullUI, ScreenBarRequest, ScreenBars

PHONE_A = "0f6d2c1e-1111-4222-8333-444455556666"
PHONE_B = "0f6d2c1e-2222-4222-8333-444455556666"


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


# ----- the gate -------------------------------------------------------------------------------------------------
def test_no_ask_mode_and_no_cooldown_needs_no_consent(store: Store) -> None:
    gate = ConsentGate(store, now=Clock())
    assert gate.decide(PHONE_A).kind == "not_required"


def test_ask_mode_is_per_grant_and_set_only_through_settings(store: Store) -> None:
    gate = ConsentGate(store, now=Clock())
    store.set_screen_grant_settings(PHONE_A, ask_each_time=True, cooldown_seconds=600)
    assert gate.decide(PHONE_A) == gate.decide(PHONE_A)
    assert gate.decide(PHONE_A).kind == "ask" and gate.decide(PHONE_A).cause == "ask_mode"
    assert gate.decide(PHONE_B).kind == "not_required"


def test_a_stop_at_the_pc_asks_every_phone_for_the_longest_setting(store: Store) -> None:
    gate = ConsentGate(store, now=Clock())
    store.set_screen_grant_settings(PHONE_A, ask_each_time=False, cooldown_seconds=0)  # this phone's own setting: off
    length = gate.note_stop_on_pc(PHONE_A, [PHONE_A, PHONE_B])  # PHONE_B keeps the default 600
    assert length == 600 and gate.cooldown_active()
    # a second controller's start during another phone's cooldown is asked (design §6.3 unit test)
    assert gate.decide(PHONE_B) == gate.decide(PHONE_B)
    assert gate.decide(PHONE_B).kind == "ask" and gate.decide(PHONE_B).cause == "cooldown"
    assert gate.decide(PHONE_A).kind == "ask"  # its own "off" does not exempt it from another phone's cooldown
    store.set_screen_grant_settings(PHONE_B, ask_each_time=False, cooldown_seconds=0)
    assert gate.note_stop_on_pc(PHONE_A, [PHONE_A, PHONE_B]) == 0 and not gate.cooldown_active()


def test_one_window_per_minute_then_backoff_escalates_and_allow_resets(store: Store) -> None:
    clock = Clock()
    gate = ConsentGate(store, now=clock)
    store.set_screen_grant_settings(PHONE_A, ask_each_time=True, cooldown_seconds=600)
    store.set_screen_grant_settings(PHONE_B, ask_each_time=True, cooldown_seconds=600)
    assert gate.decide(PHONE_A).kind == "ask"
    gate.note_window_opened()
    gate.note_outcome(PHONE_A, "deny")
    # the next start from ANY controller is refused without a window for 60 s
    assert gate.decide(PHONE_B) == gate.decide(PHONE_B)
    assert gate.decide(PHONE_B).kind == "refused" and gate.decide(PHONE_B).refusal == "backoff"
    clock.t += BACKOFF_SECONDS[0] + 0.1
    assert gate.decide(PHONE_B).kind == "ask"
    gate.note_window_opened()
    gate.note_outcome(PHONE_B, "timeout")  # the second consecutive refusal: 120 s
    clock.t += BACKOFF_SECONDS[0] + 0.1
    assert gate.decide(PHONE_A).refusal == "backoff"
    clock.t += BACKOFF_SECONDS[1]
    assert gate.decide(PHONE_A).kind == "ask"
    gate.note_window_opened()
    gate.note_outcome(PHONE_A, "allow")  # reset
    clock.t += 61
    assert gate.decide(PHONE_B).kind == "ask"
    gate.note_window_opened()
    clock.t += 5
    # a third start within the same minute opens no window and is refused at once
    assert gate.decide(PHONE_A).kind == "refused" and gate.decide(PHONE_A).refusal == "backoff"


def test_block_this_phone_for_an_hour_is_per_controller(store: Store) -> None:
    clock = Clock()
    gate = ConsentGate(store, now=clock)
    store.set_screen_grant_settings(PHONE_A, ask_each_time=True, cooldown_seconds=600)
    gate.note_window_opened()
    gate.note_outcome(PHONE_A, "block")
    assert gate.decide(PHONE_A) == gate.decide(PHONE_A)
    assert gate.decide(PHONE_A).refusal == "blocked"
    assert gate.decide(PHONE_B).kind == "not_required"  # no ask mode, no cooldown, no back-off from a block
    clock.t += 3600.1
    assert gate.decide(PHONE_A).kind in ("ask", "refused")  # the block is over; the window rate rule may still apply
    assert gate.blocked_until(PHONE_A) is None
    assert gate.summary()["blocked_controllers"] == []


def test_cooldown_ending_resets_the_backoff(store: Store) -> None:
    clock = Clock()
    gate = ConsentGate(store, now=clock)
    gate.note_stop_on_pc(PHONE_A, [PHONE_A])
    gate.note_window_opened()
    gate.note_outcome(PHONE_B, "deny")
    assert gate.decide(PHONE_B).refusal == "backoff"
    from datetime import timedelta

    from dome_protocol import format_rfc3339, now_utc

    store.set_screen_cooldown(format_rfc3339(now_utc() - timedelta(seconds=1)), PHONE_A)  # the cooldown passed
    assert gate.decide(PHONE_B).kind == "not_required" and gate.summary()["consecutive_refusals"] == 0


# ----- the permission UI state ------------------------------------------------------------------------------------
def test_permission_ui_state_counts_windows_and_reserved_rects() -> None:
    state = PermissionUiState()
    assert not state.is_open and not state.hit(5, 5)
    a = state.open("grant_window")
    b = state.open("consent_window")
    assert state.is_open and set(state.open_kinds()) == {"grant_window", "consent_window"}
    state.close(a)
    assert state.is_open
    state.close(b)
    assert not state.is_open
    state.set_reserved("screen_bars", [(0, 0, 1920, 56), (1920, 0, 3840, 56)])
    assert state.hit(10, 10) and state.hit(2000, 55) and not state.hit(10, 56)
    state.clear_reserved("screen_bars")
    assert state.reserved_rects() == ()


def test_a_probed_window_counts_while_its_owner_says_so_and_a_failing_probe_counts_as_open() -> None:
    """The pairing approval's life belongs to the pairing manager: a probe, asked each time; fail closed."""
    state = PermissionUiState()
    waiting = [False]
    state.register_probe("pairing_approval", lambda: waiting[0])
    assert not state.is_open and state.open_kinds() == ()
    waiting[0] = True
    assert state.is_open and state.open_kinds() == ("pairing_approval",)
    token = state.open("grant_window")
    assert state.open_kinds() == ("grant_window", "pairing_approval")
    state.close(token)
    waiting[0] = False
    assert not state.is_open

    def broken() -> bool:
        raise RuntimeError("unreadable")

    state.register_probe("pairing_approval", broken)
    assert state.is_open  # a phone press is dropped rather than injected into a DoMe window


# ----- window models -----------------------------------------------------------------------------------------------
def test_grant_window_allow_unlocks_after_five_seconds() -> None:
    model = GrantWindowModel(opened_at=100.0)
    assert not model.allow_enabled(104.9) and model.hold_remaining(102.0) == 3
    with pytest.raises(ValueError):
        model.decide(104.0, screen_audio=False, ask_each_time=False, cooldown_seconds=600)
    assert model.allow_enabled(105.0)
    decision = model.decide(105.0, screen_audio=True, ask_each_time=True, cooldown_seconds=60)
    assert (decision.screen_audio, decision.ask_each_time, decision.cooldown_seconds) == (True, True, 60)
    with pytest.raises(ValueError):
        model.decide(105.0, screen_audio=False, ask_each_time=False, cooldown_seconds=42)


def test_consent_window_allow_after_one_second_and_expiry() -> None:
    model = ConsentWindowModel(opened_at=10.0, timeout_seconds=30.0)
    assert not model.allow_enabled(10.5) and model.allow_enabled(11.0)
    assert model.remaining(20.0) == 20 and not model.expired(39.9) and model.expired(40.0)


async def test_consent_broker_answers_and_times_out(store: Store) -> None:
    ui = FakeScreenUI()
    state = PermissionUiState()
    broker = ConsentBroker(ui, state, timeout_seconds=0.3)

    async def ask() -> str:
        return await broker.ask(controller_id=PHONE_A, display_name="Maya's iPhone", cause="ask_mode")

    task = asyncio.ensure_future(ask())
    await asyncio.sleep(0.05)
    assert state.is_open and ui.consent_requests[-1].cause == "ask_mode"  # DoMe injects no input meanwhile (§6.4)
    ui.answer_consent("allow")
    assert await task == "allow" and not state.is_open
    outcome = await broker.ask(controller_id=PHONE_A, display_name="Maya's iPhone", cause="cooldown")
    assert outcome == "timeout" and ui.closed_consents == [ui.consent_requests[-1].request_id]
    assert not state.is_open


# ----- the indicator ----------------------------------------------------------------------------------------------
def test_reserved_rectangles_are_normalised_to_the_streamed_monitor() -> None:
    primary = Monitor(0, 0, 0, 1920, 1080, True)
    second = Monitor(1, 1920, 0, 2560, 1440, False)
    bars = [(0, 0, 1920, 56), (1920, 0, 4480, 56)]
    assert normalise_rects(bars, primary) == [{"x": 0, "y": 0, "w": 65535, "h": round(56 * 65535 / 1079)}]
    assert normalise_rects(bars, second) == [{"x": 0, "y": 0, "w": 65535, "h": round(56 * 65535 / 1439)}]
    assert normalise_rects([(5000, 5000, 5100, 5100)], primary) == []  # off this monitor


async def test_indicator_fails_closed_when_the_ui_cannot_create_a_window() -> None:
    state = PermissionUiState()
    monitors = [Monitor(0, 0, 0, 1920, 1080, True)]
    for ui in (NullUI(), FakeScreenUI(bars_ok=False)):
        controller = IndicatorController(ui, state)
        with pytest.raises(ProtocolError) as exc:
            await controller.arm(
                viewer_name="Maya's iPhone",
                audio=False,
                monitors=monitors,
                on_stop=lambda s: None,
                on_turn_off=lambda: None,
            )
        assert exc.value.code == "SCREEN_CAPTURE_UNAVAILABLE" and exc.value.detail == {"detail": "indicator"}
        assert not controller.armed and state.reserved_rects() == ()


async def test_indicator_times_out_on_a_frozen_ui_and_arms_on_confirmation() -> None:
    state = PermissionUiState()
    monitors = [Monitor(0, 0, 0, 1920, 1080, True)]

    class Frozen(FakeScreenUI):
        def show_screen_bars(self, request: ScreenBarRequest) -> Future[ScreenBars]:
            return Future()  # never resolves

    controller = IndicatorController(Frozen(), state, confirm_timeout=0.2)
    with pytest.raises(ProtocolError) as exc:
        await controller.arm(
            viewer_name="x", audio=False, monitors=monitors, on_stop=lambda s: None, on_turn_off=lambda: None
        )
    assert exc.value.detail == {"detail": "indicator"}
    ui = FakeScreenUI()
    controller = IndicatorController(ui, state)
    bars = await controller.arm(
        viewer_name="x", audio=False, monitors=monitors, on_stop=lambda s: None, on_turn_off=lambda: None
    )
    assert bars.created and controller.armed and controller.present()
    assert state.reserved_rects() == ((0, 0, 1920, 40),) and controller.reserved_for(monitors[0])[0]["w"] == 65535
    ui.bars_present = False
    assert not controller.present()
    controller.disarm()
    assert ui.hidden == 1 and state.reserved_rects() == ()
