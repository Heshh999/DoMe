"""The pointer stays on the watched screen (protocol 1.5, rules.screen_cursor (5), DECISIONS #107), end to end through
the agent with the fake relay, the fake platform (two monitors, a settable pointer) and the in-process fake worker:
while a connected Screen view of the batch's controller exists, an injected relative move that took the pointer from
inside the streamed monitor to outside it is pulled back to the nearest point inside, before the batch's next event.
A pointer that was already elsewhere (the person's own mouse) is never moved, no clip rectangle exists, and a failed
correction never fails the batch. Nothing here proves Windows' SendInput/SetCursorPos: the platform is a fake."""

from __future__ import annotations

from typing import Any

import pytest
from dome_protocol import ProtocolError

from dome_agent.platform.protocol import Monitor

from .conftest import AgentHarness
from .helpers import Controller
from .test_screen_session import expect_screen_event, live, second_phone, send_batch, start_input, wait_for

PRIMARY = Monitor(0, 0, 0, 1920, 1080, True)
SIDE = Monitor(1, 1920, 0, 1280, 1024, False)  # x 1920..3199, y 0..1023


@pytest.fixture
def two(harness: AgentHarness) -> AgentHarness:
    harness.fake.monitors = [PRIMARY, SIDE]
    harness.agent.screen.lock_poll_seconds = 0.1
    harness.agent.screen.indicator_poll_seconds = 0.1
    return harness


def injected(h: AgentHarness, since: int = 0) -> list[tuple[str, tuple[object, ...], tuple[int, int]]]:
    """Every injection after the first ``since`` records: (name, args, where the pointer was when it was made)."""
    return [(r.name, r.args, r.cursor) for r in h.fake.input_events[since:]]


async def watching_side(h: AgentHarness, controller: Controller) -> str:
    """An input session and a connected Screen view of monitor 1 for ``controller``; returns the input session id."""
    sid = await start_input(h, controller)
    res, _worker = await live(h, controller, surface=1)
    assert res["result"]["surface_index"] == 1
    assert h.agent.screen.streamed_monitor(controller.controller_id) == SIDE
    return sid


async def batch(h: AgentHarness, controller: Controller, sid: str, seq: int, events: list[dict[str, Any]]) -> None:
    accepted = h.agent.input.summary()["session"]["accepted_events"]
    await send_batch(h, controller, sid, seq, events)
    await wait_for(lambda: h.agent.input.summary()["session"]["accepted_events"] >= accepted + len(events))


async def test_a_move_past_the_far_edge_is_pulled_back_onto_the_watched_monitor(
    two: AgentHarness, controller: Controller
) -> None:
    sid = await watching_side(two, controller)
    two.fake.cursor = (3199, 500)
    mark = len(two.fake.input_events)
    await batch(two, controller, sid, 1, [{"type": "pointer_move", "dx": 400, "dy": 0}])
    assert injected(two, mark) == [("move", (400, 0), (3199, 500)), ("move_to", (3199, 500), (3599, 500))]
    assert two.fake.cursor == (3199, 500)
    session = two.agent.input.summary()["session"]
    assert (session["confined_moves"], session["confine_failures"], session["dropped_events"]) == (1, 0, 0)
    assert two.agent.status()["input"]["session"]["confined_moves"] == 1  # what the diagnostics bundle shows


async def test_a_move_towards_the_other_monitor_stops_at_the_shared_edge(
    two: AgentHarness, controller: Controller
) -> None:
    sid = await watching_side(two, controller)
    two.fake.cursor = (2000, 1000)
    mark = len(two.fake.input_events)
    await batch(two, controller, sid, 1, [{"type": "pointer_move", "dx": -300, "dy": 60}])
    assert injected(two, mark)[-1] == ("move_to", (1920, 1023), (1700, 1060))  # clamped on both axes
    assert two.fake.cursor == (1920, 1023)


async def test_a_move_click_move_batch_is_clamped_before_the_click(two: AgentHarness, controller: Controller) -> None:
    """Coalescing never merges across a button: the click lands where the clamp put the pointer, on monitor 1."""
    sid = await watching_side(two, controller)
    two.fake.cursor = (3150, 500)
    mark = len(two.fake.input_events)
    await batch(
        two,
        controller,
        sid,
        1,
        [
            {"type": "pointer_move", "dx": 100, "dy": 0},
            {"type": "pointer_button", "button": "left", "action": "click"},
            {"type": "pointer_move", "dx": -50, "dy": 0},
        ],
    )
    assert injected(two, mark) == [
        ("move", (100, 0), (3150, 500)),
        ("move_to", (3199, 500), (3250, 500)),
        ("button", ("left", "click"), (3199, 500)),  # inside monitor 1
        ("move", (-50, 0), (3199, 500)),  # back inside: nothing to correct
    ]
    assert two.agent.input.summary()["session"]["confined_moves"] == 1


async def test_a_crossing_move_that_reads_as_not_applied_yet_is_read_again_before_deciding(
    two: AgentHarness, controller: Controller, monkeypatch: pytest.MonkeyPatch
) -> None:
    """KNOWN_ISSUES #43: whether GetCursorPos shows a relative SendInput the moment it returned is not verified on a
    device. When the pointer reads as not moved at all although the move would have left the monitor, the agent reads
    once more before deciding; a move that stays inside is read once, as before."""
    sid = await watching_side(two, controller)
    adapter = two.agent.platform.input
    real_move = adapter.move
    queued: list[tuple[int, int]] = []
    reads: list[tuple[int, int] | None] = []

    def queued_move(dx: int, dy: int) -> None:
        at = two.fake.cursor
        real_move(dx, dy)
        queued.append(two.fake.cursor)
        two.fake.cursor = at  # Windows has the move but has not applied it yet

    def position() -> tuple[int, int] | None:
        if queued and len(reads) % 3 == 2:  # the second read after the move sees it applied
            two.fake.cursor = queued.pop()
        reads.append(two.fake.cursor)
        return two.fake.cursor

    monkeypatch.setattr(adapter, "move", queued_move)
    monkeypatch.setattr(adapter, "cursor_position", position)
    two.fake.cursor = (3199, 500)
    mark = len(two.fake.input_events)
    await batch(two, controller, sid, 1, [{"type": "pointer_move", "dx": 400, "dy": 0}])
    assert reads == [(3199, 500), (3199, 500), (3599, 500)]  # before, after (not applied yet), once more
    assert injected(two, mark)[-1] == ("move_to", (3199, 500), (3599, 500))
    assert two.fake.cursor == (3199, 500) and two.agent.input.summary()["session"]["confined_moves"] == 1
    # a move that stays inside the monitor is never read twice, applied or not
    reads.clear()
    queued.clear()
    monkeypatch.setattr(adapter, "move", real_move)
    await batch(two, controller, sid, 2, [{"type": "pointer_move", "dx": -100, "dy": 0}])
    assert reads == [(3199, 500), (3099, 500)] and two.agent.input.summary()["session"]["confined_moves"] == 1


async def test_a_move_inside_the_monitor_or_from_elsewhere_is_left_alone(
    two: AgentHarness, controller: Controller
) -> None:
    sid = await watching_side(two, controller)
    two.fake.cursor = (2000, 500)
    mark = len(two.fake.input_events)
    await batch(two, controller, sid, 1, [{"type": "pointer_move", "dx": 100, "dy": 20}])
    assert [name for name, _a, _c in injected(two, mark)] == ["move"] and two.fake.cursor == (2100, 520)
    # the person's own mouse put the pointer on monitor 0: the phone's move is not "corrected" onto monitor 1
    two.fake.cursor = (500, 500)
    mark = len(two.fake.input_events)
    await batch(two, controller, sid, 2, [{"type": "pointer_move", "dx": 10, "dy": 0}])
    assert [name for name, _a, _c in injected(two, mark)] == ["move"] and two.fake.cursor == (510, 500)
    assert two.agent.input.summary()["session"]["confined_moves"] == 0


async def test_absolute_moves_and_zero_moves_never_confine(two: AgentHarness, controller: Controller) -> None:
    sid = await watching_side(two, controller)
    two.fake.cursor = (3199, 500)
    mark = len(two.fake.input_events)
    await batch(
        two,
        controller,
        sid,
        1,
        [{"type": "pointer_move_to", "x": 65535, "y": 0, "gen": 1}, {"type": "pointer_move", "dx": 0, "dy": 0}],
    )
    assert injected(two, mark) == [("move_to", (3199, 0), (3199, 500))]  # mapped onto monitor 1 already
    assert two.agent.input.summary()["session"]["confined_moves"] == 0


async def test_no_screen_session_or_another_phones_session_confines_nothing(
    two: AgentHarness, controller: Controller
) -> None:
    sid = await start_input(two, controller)
    two.fake.cursor = (3199, 500)
    mark = len(two.fake.input_events)
    await batch(two, controller, sid, 1, [{"type": "pointer_move", "dx": 400, "dy": 0}])
    assert [name for name, _a, _c in injected(two, mark)] == ["move"] and two.fake.cursor == (3599, 500)
    # another phone watches monitor 1: this phone's moves are its own business
    other = await second_phone(two, controller, ("status", "screen"))
    await live(two, other, surface=1)
    assert two.agent.screen.streamed_monitor(controller.controller_id) is None
    two.fake.cursor = (3199, 500)
    mark = len(two.fake.input_events)
    await batch(two, controller, sid, 2, [{"type": "pointer_move", "dx": 400, "dy": 0}])
    assert [name for name, _a, _c in injected(two, mark)] == ["move"]
    # and once the Screen view has ended nothing is confined either
    await two.agent.screen.end("stopped")
    await expect_screen_event(two, "ended", "stopped")
    assert two.agent.input.summary()["session"]["confined_moves"] == 0


async def test_a_failed_correction_is_counted_and_never_fails_the_batch(
    two: AgentHarness, controller: Controller, monkeypatch: pytest.MonkeyPatch
) -> None:
    sid = await watching_side(two, controller)
    adapter = two.agent.platform.input

    def refused(x: int, y: int) -> None:
        raise ProtocolError("INPUT_INJECTION_FAILED", "Windows refused SetCursorPos (error 5).")

    monkeypatch.setattr(adapter, "move_to", refused)
    two.fake.cursor = (3199, 500)
    await batch(two, controller, sid, 1, [{"type": "pointer_move", "dx": 400, "dy": 0}])
    session = two.agent.input.summary()["session"]
    assert (session["accepted_events"], session["dropped_events"]) == (1, 0)
    assert (session["confined_moves"], session["confine_failures"]) == (0, 1)
    ack = await two.relay.expect("input_ack")
    assert ack["dropped_events"] == 0
    # an unreadable position before the move confines nothing (and fails nothing)
    monkeypatch.setattr(adapter, "cursor_position", lambda: None)
    await batch(two, controller, sid, 2, [{"type": "pointer_move", "dx": 400, "dy": 0}])
    assert two.agent.input.summary()["session"]["confine_failures"] == 1


async def test_the_confinement_counters_outlive_the_input_session(two: AgentHarness, controller: Controller) -> None:
    sid = await watching_side(two, controller)
    two.fake.cursor = (3199, 500)
    await batch(two, controller, sid, 1, [{"type": "pointer_move", "dx": 400, "dy": 0}])
    await two.agent.input.end_for_controller(controller.controller_id, "stopped")
    last = two.agent.input.summary()["last_session"]
    assert last is not None and (last["confined_moves"], last["confine_failures"]) == (1, 0)
