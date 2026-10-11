"""Every registry action has a handler, and every handler's success result validates against its
result schema — exercised end to end through the agent with the fake platform + fake extension."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from dome_protocol import load_registry, load_schemas

from dome_agent.actions import all_handlers
from dome_agent.testing.fake_extension import FakeTab

from .conftest import AgentHarness
from .helpers import Controller, payload_of


def test_every_registry_action_has_exactly_one_handler() -> None:
    registry = load_registry()
    handlers = all_handlers()
    assert set(handlers) == set(registry.actions)


async def run(
    h: AgentHarness,
    controller: Controller,
    action: str,
    params: dict[str, Any] | None = None,
    target: dict[str, Any] | None = None,
    *,
    confirm: bool = False,
) -> dict[str, Any]:
    env = controller.command(action, params, target, lifetime=90 if confirm else 30)
    cid = payload_of(env)["command_id"]
    await h.send_command(env)
    if confirm:
        req = await h.relay.expect("confirmation_required", command_id=cid)
        await h.send_confirmation(controller.confirmation(cid, req["challenge_text"]))
    res = await h.result(cid)
    load_schemas().validate_frame("agent_to_relay", res)
    return res


@pytest.fixture
async def setup(harness: AgentHarness, tmp_path: Path) -> dict[str, Any]:
    tab = FakeTab(tab_id=11, has_previous=True)
    results_tab = FakeTab.results_page(12)
    ext = await harness.connect_extension(tab, results_tab)
    yt = {"browser_instance_id": ext.browser_instance_id, "tab_id": 11, "tab_token": tab.tab_token}
    results = {"browser_instance_id": ext.browser_instance_id, "tab_id": 12, "tab_token": results_tab.tab_token}
    harness.fake.add_session("Spotify.exe#0", title="Song")
    exe = tmp_path / "apps" / "notepad++.exe"  # an editor on the F11 list (F11 is its full-screen toggle)
    exe.parent.mkdir()
    exe.write_bytes(b"MZ")
    harness.agent.apps._temp_dirs = ()  # noqa: SLF001
    harness.agent.apps.approve("note", str(exe), "Notepad++")
    harness.fake.processes[str(exe.resolve())] = [700]
    harness.fake.add_window(700, "9001", "Doc - Notepad")
    return {"yt": yt, "results": results, "tab": tab, "ext": ext, "exe": str(exe.resolve())}


CASES: list[tuple[str, dict[str, Any], str | None]] = [
    ("system.ping", {}, None),
    ("system.get_status", {}, None),
    ("youtube.list_tabs", {}, None),
    ("youtube.get_state", {}, "yt"),
    ("youtube.set_paused", {"paused": True}, "yt"),
    ("youtube.next", {}, "yt"),
    ("youtube.previous", {}, "yt"),
    ("youtube.seek_relative", {"seconds": -10}, "yt"),
    ("youtube.seek_to", {"position_seconds": 42.5}, "yt"),
    ("youtube.set_muted", {"muted": True}, "yt"),
    ("youtube.set_volume", {"value": 33}, "yt"),
    ("youtube.set_theater", {"enabled": True}, "yt"),
    ("youtube.search", {"query": "lofi beats"}, "yt"),
    ("youtube.open_result", {"index": 2, "video_id": "result00002"}, "results"),
    ("media.get_sessions", {}, None),
    ("media.set_paused", {"paused": True}, "media"),
    ("media.next", {}, "media"),
    ("media.previous", {}, "media"),
    ("windows.get_volume", {}, None),
    ("windows.set_volume", {"value": 25}, None),
    ("windows.set_muted", {"muted": True}, None),
    ("windows.lock", {}, None),
    ("app.list", {}, None),
    ("app.focus", {}, "window"),
    ("app.minimize", {}, "window"),
    ("app.maximize", {}, "window"),
    ("app.restore", {}, "window"),
    ("app.set_fullscreen", {"enabled": True}, "window"),
    ("app.launch", {"app_id": "note"}, None),
    ("app.close", {}, "window"),
    ("power.cancel", {}, None),
    ("power.sleep", {"countdown_seconds": 0}, None),
    ("power.restart", {"countdown_seconds": 0}, None),
    ("power.shutdown", {"countdown_seconds": 0}, None),
    ("input.session_start", {}, None),
    ("input.session_stop", {"input_session_id": "A" * 22}, None),
]


@pytest.mark.parametrize(("action", "params", "target_kind"), CASES, ids=[c[0] for c in CASES])
async def test_handler_success_result_validates(
    harness: AgentHarness,
    controller: Controller,
    setup: dict[str, Any],
    action: str,
    params: dict[str, Any],
    target_kind: str | None,
) -> None:
    registry = load_registry()
    spec = registry.get(action)
    target: dict[str, Any] | None = None
    if target_kind == "yt":
        target = setup["yt"]
    elif target_kind == "results":
        target = setup["results"]
    elif target_kind == "media":
        target = {"session_id": "Spotify.exe#0"}
    elif target_kind == "window":
        target = {"app_id": "note", "window_id": "9001"}
    if action == "windows.lock":
        harness.fake.locked = False
    res = await run(harness, controller, action, params, target, confirm=spec.requires_confirmation)
    assert res["state"] == "succeeded", res
    registry.validate_result(action, res["result"])  # agent-side validation is also re-applied here
    if action == "power.sleep":
        assert harness.fake.count("power_sleep") == 1
    if action == "youtube.next":
        assert res["result"]["previous_video_id"] == "dQw4w9WgXcQ" and res["result"]["tab"]["video_id"] != "dQw4w9WgXcQ"
    if action == "youtube.search":
        assert res["result"]["tab"]["tab_id"] == 11 and res["result"]["tab"]["tab_token"] != setup["yt"]["tab_token"]
        assert [r["index"] for r in res["result"]["results"]] == [1, 2, 3, 4, 5]
    if action == "youtube.open_result":
        assert res["result"]["tab"]["video_id"] == "result00002" and res["result"]["tab"]["context"] == "watch"
    if action == "app.focus":
        assert res["result"]["focused"] is True
    if action == "app.maximize":
        assert res["result"] == {
            "app_id": "note",
            "window_id": "9001",
            "state": "maximized",
            "fullscreen": False,
            "foreground": True,
        }
    if action == "app.restore":
        assert res["result"]["state"] == "normal" and res["result"]["fullscreen"] is False
    if action == "app.set_fullscreen":
        assert res["result"]["fullscreen"] is True and res["result"]["foreground"] is True
        assert harness.fake.count("f11") == 1
    if action == "windows.set_volume":
        assert res["result"] == {"value": 25, "muted": False}
    if action == "input.session_start":
        assert res["result"]["pointer"] and res["result"]["keyboard"] and len(res["result"]["input_session_id"]) == 22
    if action == "input.session_stop":
        assert res["result"] == {"stopped": False, "released_holds": 0}  # unknown id: idempotent no-op


def test_input_actions_are_human_only() -> None:
    registry = load_registry()
    for name in ("input.session_start", "input.session_stop"):
        spec = registry.get(name)
        assert spec.ai_eligible is False and spec.routine_allowed is False
        assert (
            spec.satisfied_by(("keyboard",)) and spec.satisfied_by(("pointer",)) and not spec.satisfied_by(("status",))
        )


async def test_youtube_request_fullscreen_reports_activation_required(
    harness: AgentHarness, controller: Controller, setup: dict[str, Any]
) -> None:
    res = await run(harness, controller, "youtube.request_fullscreen", {}, setup["yt"])
    assert res["state"] == "failed" and res["error"]["code"] == "ACTIVATION_REQUIRED"
    assert setup["tab"].fullscreen is False  # nothing was faked into success
    setup["tab"].fullscreen_allowed = True
    res = await run(harness, controller, "youtube.request_fullscreen", {}, setup["yt"])
    assert res["state"] == "succeeded" and res["result"]["tab"]["fullscreen"] is True


async def test_youtube_next_without_next_video(
    harness: AgentHarness, controller: Controller, setup: dict[str, Any]
) -> None:
    setup["tab"].next_videos = []
    res = await run(harness, controller, "youtube.next", {}, setup["yt"])
    assert res["state"] == "failed" and res["error"]["code"] == "NO_NEXT_VIDEO"
    setup["tab"].ad_showing = True
    setup["tab"].next_videos = ["abc123"]
    res = await run(harness, controller, "youtube.next", {}, setup["yt"])
    assert res["state"] == "failed" and res["error"]["code"] == "UNSUPPORTED_CONTEXT"


async def test_youtube_expected_video_mismatch_is_target_changed(
    harness: AgentHarness, controller: Controller, setup: dict[str, Any]
) -> None:
    res = await run(harness, controller, "youtube.next", {}, {**setup["yt"], "expected_video_id": "someOther11"})
    assert res["state"] == "failed" and res["error"]["code"] == "TARGET_CHANGED"
    assert setup["tab"].video_id == "dQw4w9WgXcQ"  # nothing happened


async def test_focus_denied_and_app_not_running(
    harness: AgentHarness, controller: Controller, setup: dict[str, Any]
) -> None:
    harness.fake.refuse_focus = True
    res = await run(harness, controller, "app.focus", {}, {"app_id": "note"})
    assert res["state"] == "failed" and res["error"]["code"] == "FOCUS_DENIED" and res["result"]["focused"] is False
    harness.fake.processes.clear()
    res = await run(harness, controller, "app.focus", {}, {"app_id": "note"})
    assert res["error"]["code"] == "APP_NOT_RUNNING"
    res = await run(harness, controller, "app.list")
    assert res["state"] == "succeeded" and res["result"]["apps"][0]["running"] is False


async def test_launch_refuses_changed_executable(
    harness: AgentHarness, controller: Controller, setup: dict[str, Any]
) -> None:
    Path(setup["exe"]).write_bytes(b"MZ tampered")
    res = await run(harness, controller, "app.launch", {"app_id": "note"})
    assert res["state"] == "failed" and res["error"]["code"] == "APP_NOT_APPROVED"
    assert harness.fake.count("launch") == 0


async def test_media_session_gone_and_unsupported_control(
    harness: AgentHarness, controller: Controller, setup: dict[str, Any]
) -> None:
    res = await run(harness, controller, "media.set_paused", {"paused": True}, {"session_id": "nope#9"})
    assert res["state"] == "failed" and res["error"]["code"] == "MEDIA_SESSION_GONE"
    harness.fake.add_session("Radio#0", controls=("play", "pause"))
    res = await run(harness, controller, "media.next", {}, {"session_id": "Radio#0"})
    assert res["error"]["code"] == "ACTION_UNAVAILABLE" and harness.fake.count("media_next") == 0


async def test_windows_actions_unsupported_on_this_platform(harness: AgentHarness, controller: Controller) -> None:
    from dome_agent.platform.unsupported import build_unsupported_platform

    harness.agent.services.platform = build_unsupported_platform()
    for action, params in (
        ("windows.get_volume", {}),
        ("media.get_sessions", {}),
        ("app.list", {}),
        ("windows.lock", {}),
    ):
        res = await run(harness, controller, action, params)
        assert res["state"] == "failed" and res["error"]["code"] == "PLATFORM_UNSUPPORTED", action
    # YouTube still works without Windows adapters
    tab = FakeTab(tab_id=3)
    ext = await harness.connect_extension(tab)
    res = await run(
        harness,
        controller,
        "youtube.set_paused",
        {"paused": True},
        {"browser_instance_id": ext.browser_instance_id, "tab_id": 3, "tab_token": tab.tab_token},
    )
    assert res["state"] == "succeeded"


async def test_youtube_next_lost_response_is_outcome_unknown(harness: AgentHarness, controller: Controller) -> None:
    """The op reached the browser but the answer is late/lost: never a clean retryable failure (spec §8)."""
    tab = FakeTab(tab_id=21)
    ext = await harness.connect_extension(tab, op_delay=2.0)  # answers long after the bridge timeout
    yt = {"browser_instance_id": ext.browser_instance_id, "tab_id": 21, "tab_token": tab.tab_token}
    spec = load_registry().get("youtube.next")
    object.__setattr__(spec, "timeout_ms", 1500)  # bridge timeout = 1000 ms
    try:
        res = await run(harness, controller, "youtube.next", {}, yt)
    finally:
        object.__setattr__(spec, "timeout_ms", 10000)
    assert res["state"] == "outcome_unknown"
    assert res["error"]["code"] == "OUTCOME_UNKNOWN" and res["error"]["retryable"] is False
    assert res["warning"]
    import asyncio

    for _ in range(100):
        if any(r["op"] == "next" for r in ext.requests):
            break
        await asyncio.sleep(0.05)
    assert [r["op"] for r in ext.requests] == ["next"]  # exactly one skip reached the browser, no retry
    assert harness.agent.store.journal_get(payload_of(controller.command("system.ping"))["command_id"]) is None


async def test_youtube_idempotent_lost_response_stays_retryable_failure(
    harness: AgentHarness, controller: Controller
) -> None:
    tab = FakeTab(tab_id=22)
    ext = await harness.connect_extension(tab, op_delay=2.0)
    yt = {"browser_instance_id": ext.browser_instance_id, "tab_id": 22, "tab_token": tab.tab_token}
    spec = load_registry().get("youtube.set_paused")
    object.__setattr__(spec, "timeout_ms", 1500)
    try:
        res = await run(harness, controller, "youtube.set_paused", {"paused": True}, yt)
    finally:
        object.__setattr__(spec, "timeout_ms", 8000)
    assert res["state"] == "failed" and res["error"]["code"] == "EXTENSION_DISCONNECTED"
    assert res["error"]["retryable"] is True


# ----- window actions (protocol 1.2) ---------------------------------------------------------------------

NOTE = {"app_id": "note"}


async def test_maximize_restore_set_fullscreen_read_back_state(
    harness: AgentHarness, controller: Controller, setup: dict[str, Any]
) -> None:
    fake = harness.fake
    res = await run(harness, controller, "app.maximize", {}, NOTE)
    assert res["state"] == "succeeded" and res["result"]["state"] == "maximized"
    assert fake.window_flags["9001"].maximized and fake.foreground == "9001"
    res = await run(harness, controller, "app.set_fullscreen", {"enabled": True}, NOTE)
    assert res["result"]["fullscreen"] is True and res["result"]["state"] == "maximized"
    # restore leaves full screen first (one more F11), then shows the window at normal size
    res = await run(harness, controller, "app.restore", {}, NOTE)
    assert res["state"] == "succeeded" and res["result"]["state"] == "normal" and res["result"]["fullscreen"] is False
    assert fake.count("f11") == 2
    # a window minimised while maximised: restore ends at normal size
    fake.add_window(700, "9001", "Doc - Notepad", minimized=True, maximized=True)
    res = await run(harness, controller, "app.restore", {}, NOTE)
    assert res["result"]["state"] == "normal" and not fake.windows["9001"].minimized
    # app.list still reports app_window exactly as in 1.1: no placement fields leak into it
    res = await run(harness, controller, "app.list")
    assert set(res["result"]["apps"][0]["windows"][0]) == {"window_id", "title", "minimized", "foreground"}


async def test_window_actions_never_launch_the_app(
    harness: AgentHarness, controller: Controller, setup: dict[str, Any]
) -> None:
    harness.fake.processes.clear()
    for action, params in (("app.maximize", {}), ("app.restore", {}), ("app.set_fullscreen", {"enabled": True})):
        res = await run(harness, controller, action, params, NOTE)
        assert res["state"] == "failed" and res["error"]["code"] == "APP_NOT_RUNNING", action
    assert harness.fake.count("launch") == 0 and harness.fake.count("f11") == 0
    assert harness.fake.count("maximize") == 0 and harness.fake.count("restore") == 0


async def test_window_action_waits_for_the_first_window_then_target_gone(
    harness: AgentHarness, controller: Controller, setup: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    from dome_agent.actions import apps as apps_actions

    fake = harness.fake
    del fake.windows["9001"]  # the app is starting: its process runs, no window yet

    async def window_appears() -> None:
        await asyncio.sleep(0.6)
        fake.add_window(700, "9002", "Untitled - Notepad")

    task = asyncio.get_running_loop().create_task(window_appears())
    res = await run(harness, controller, "app.maximize", {}, NOTE)
    await task
    assert res["state"] == "succeeded" and res["result"]["window_id"] == "9002", res
    # no window ever appears: TARGET_GONE once the wait is over (shortened here)
    monkeypatch.setattr(apps_actions, "FIRST_WINDOW_WAIT", 0.5)
    del fake.windows["9002"]
    res = await run(harness, controller, "app.restore", {}, NOTE)
    assert res["state"] == "failed" and res["error"]["code"] == "TARGET_GONE"
    # the process exits while we wait: the app is not running (and nothing is started)
    monkeypatch.setattr(apps_actions, "FIRST_WINDOW_WAIT", 2.0)

    async def process_exits() -> None:
        await asyncio.sleep(0.4)
        fake.processes.clear()

    task = asyncio.get_running_loop().create_task(process_exits())
    res = await run(harness, controller, "app.restore", {}, NOTE)
    await task
    assert res["error"]["code"] == "APP_NOT_RUNNING" and fake.count("launch") == 0


async def test_maximize_fixed_size_window_is_unchanged(
    harness: AgentHarness, controller: Controller, setup: dict[str, Any]
) -> None:
    harness.fake.add_window(700, "9001", "Doc - Notepad", can_maximize=False)
    res = await run(harness, controller, "app.maximize", {}, NOTE)
    assert res["state"] == "failed" and res["error"]["code"] == "WINDOW_STATE_UNCHANGED"
    assert res["result"] == {
        "app_id": "note",
        "window_id": "9001",
        "state": "normal",
        "fullscreen": False,
        "foreground": False,
    }


async def test_set_fullscreen_already_in_that_state_presses_nothing(
    harness: AgentHarness, controller: Controller, setup: dict[str, Any]
) -> None:
    harness.fake.add_window(700, "9001", "Doc - Notepad", fullscreen=True)
    res = await run(harness, controller, "app.set_fullscreen", {"enabled": True}, NOTE)
    assert res["state"] == "succeeded" and res["result"]["fullscreen"] is True
    res = await run(harness, controller, "app.set_fullscreen", {"enabled": False}, NOTE)
    assert res["state"] == "succeeded" and res["result"]["fullscreen"] is False
    res = await run(harness, controller, "app.set_fullscreen", {"enabled": False}, NOTE)
    assert res["state"] == "succeeded" and res["result"]["fullscreen"] is False
    assert harness.fake.count("f11") == 1  # only the one change that was needed


async def test_set_fullscreen_focus_denied_or_modifier_held_presses_nothing(
    harness: AgentHarness, controller: Controller, setup: dict[str, Any]
) -> None:
    harness.fake.refuse_focus = True
    res = await run(harness, controller, "app.set_fullscreen", {"enabled": True}, NOTE)
    assert res["state"] == "failed" and res["error"]["code"] == "FOCUS_DENIED"
    assert res["result"]["foreground"] is False and res["result"]["fullscreen"] is False
    harness.fake.refuse_focus = False
    harness.fake.held_modifier = True
    res = await run(harness, controller, "app.set_fullscreen", {"enabled": True}, NOTE)
    assert res["state"] == "failed" and res["error"]["code"] == "ACTION_UNAVAILABLE"
    assert harness.fake.count("f11") == 0


async def test_set_fullscreen_unsupported_reports_best_known_state_and_never_maximises(
    harness: AgentHarness, controller: Controller, setup: dict[str, Any]
) -> None:
    harness.fake.refuse_fullscreen = True
    res = await run(harness, controller, "app.set_fullscreen", {"enabled": True}, NOTE)
    assert res["state"] == "failed" and res["error"]["code"] == "FULLSCREEN_UNSUPPORTED"
    assert res["error"]["retryable"] is False
    assert res["result"] == {
        "app_id": "note",
        "window_id": "9001",
        "state": "normal",
        "fullscreen": False,
        "foreground": True,
    }
    assert harness.fake.count("f11") == 1 and harness.fake.count("maximize") == 0
    assert not harness.fake.window_flags["9001"].maximized


async def test_set_fullscreen_refused_while_the_input_session_holds_keys(
    harness: AgentHarness, controller: Controller, setup: dict[str, Any]
) -> None:
    import asyncio

    start = controller.command("input.session_start", {})
    await harness.send_command(start)
    sid = (await harness.result(payload_of(start)["command_id"]))["result"]["input_session_id"]
    # a shortcut whose injection AND recovery release failed: CTRL may still be down from the phone
    harness.fake.input_fail, harness.fake.input_fail_remaining = "INPUT_INJECTION_FAILED", 1
    harness.fake.input_fail_stuck = True
    from .helpers import relay_input_batch_frame

    batch = controller.input_batch(sid, 1, [{"type": "shortcut", "name": "ctrl_v"}])
    await harness.relay.send(
        relay_input_batch_frame(batch, harness.agent.relay.connection_id if harness.agent.relay else None)
    )
    for _ in range(150):
        session = harness.agent.input.current
        if session is not None and session.held_keys:
            break
        await asyncio.sleep(0.02)
    res = await run(harness, controller, "app.set_fullscreen", {"enabled": True}, NOTE)
    assert res["state"] == "failed" and res["error"]["code"] == "ACTION_UNAVAILABLE"
    assert "Release the keys" in res["error"]["message"] and res["result"]["fullscreen"] is False
    assert harness.fake.count("set_fullscreen") == 0 and harness.fake.count("f11") == 0
    # maximise needs no key press: it is not blocked by the held keys
    res = await run(harness, controller, "app.maximize", {}, NOTE)
    assert res["state"] == "succeeded"
    # ... unless the window is in full screen: leaving it first takes an F11, refused like set_fullscreen's
    harness.fake.add_window(700, "9001", "Doc - Notepad", fullscreen=True)
    for action in ("app.maximize", "app.restore"):
        res = await run(harness, controller, action, {}, NOTE)
        assert res["state"] == "failed" and res["error"]["code"] == "ACTION_UNAVAILABLE", action
        assert "Release the keys" in res["error"]["message"] and res["result"]["fullscreen"] is True
    assert harness.fake.count("f11") == 0
    harness.fake.add_window(700, "9001", "Doc - Notepad")
    harness.fake.input_fail_stuck = False
    stop = controller.command("input.session_stop", {"input_session_id": sid})
    await harness.send_command(stop)
    assert (await harness.result(payload_of(stop)["command_id"]))["result"]["released_holds"] == 2
    res = await run(harness, controller, "app.set_fullscreen", {"enabled": True}, NOTE)
    assert res["state"] == "succeeded" and harness.fake.count("f11") == 1


async def test_window_actions_on_an_elevated_app(
    harness: AgentHarness, controller: Controller, setup: dict[str, Any]
) -> None:
    harness.fake.add_window(700, "9001", "Task Manager", elevated=True)
    for action, params in (("app.maximize", {}), ("app.restore", {}), ("app.set_fullscreen", {"enabled": True})):
        res = await run(harness, controller, action, params, NOTE)
        assert res["state"] == "failed" and res["error"]["code"] == "APP_ELEVATED", action
        assert res["result"]["window_id"] == "9001"
    assert harness.fake.count("f11") == 0 and not harness.fake.window_flags["9001"].maximized


async def test_window_action_on_a_vanished_window_is_target_gone(
    harness: AgentHarness, controller: Controller, setup: dict[str, Any]
) -> None:
    res = await run(harness, controller, "app.maximize", {}, {"app_id": "note", "window_id": "4040"})
    assert res["state"] == "failed" and res["error"]["code"] == "TARGET_GONE" and "result" not in res


def _approve(harness: AgentHarness, tmp_path: Path, app_id: str, exe_name: str, pid: int) -> None:
    exe = tmp_path / "more-apps" / app_id / exe_name
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"MZ " + app_id.encode())
    harness.agent.apps.approve(app_id, str(exe))
    harness.fake.processes[str(exe.resolve())] = [pid]


async def test_set_fullscreen_never_presses_f11_into_an_app_off_the_list(
    harness: AgentHarness, controller: Controller, setup: dict[str, Any], tmp_path: Path
) -> None:
    """In Excel F11 adds a chart sheet to the open workbook; in Visual Studio it starts debugging."""
    _approve(harness, tmp_path, "excel", "EXCEL.EXE", 800)
    harness.fake.add_window(800, "9801", "Budget.xlsx - Excel")
    res = await run(harness, controller, "app.set_fullscreen", {"enabled": True}, {"app_id": "excel"})
    assert res["state"] == "failed" and res["error"]["code"] == "FULLSCREEN_UNSUPPORTED"
    assert res["result"] == {
        "app_id": "excel",
        "window_id": "9801",
        "state": "normal",
        "fullscreen": False,
        "foreground": False,
    }
    assert harness.fake.count("f11") == 0 and harness.fake.foreground is None  # nothing pressed, nothing moved
    # an app off the list in its own full screen is not taken out of it with F11 either
    harness.fake.add_window(800, "9801", "Budget.xlsx - Excel", fullscreen=True)
    for action, params in (("app.set_fullscreen", {"enabled": False}), ("app.maximize", {}), ("app.restore", {})):
        res = await run(harness, controller, action, params, {"app_id": "excel"})
        assert res["state"] == "failed" and res["error"]["code"] == "WINDOW_STATE_UNCHANGED", action
        assert res["result"]["fullscreen"] is True
    assert harness.fake.count("f11") == 0


async def test_window_minimised_in_full_screen(
    harness: AgentHarness, controller: Controller, setup: dict[str, Any]
) -> None:
    """Full screen, Minimise from the Apps page, then full screen again: Windows shows the window again in
    full screen, so an F11 decided on the minimised read ("not full screen") would take it OUT of it."""
    fake = harness.fake

    async def full_screen_then_minimised() -> None:
        fake.add_window(700, "9001", "Doc - Notepad", fullscreen=True)
        res = await run(harness, controller, "app.minimize", {}, NOTE)
        assert res["state"] == "succeeded" and fake.windows["9001"].minimized
        fake.calls.clear()

    await full_screen_then_minimised()
    res = await run(harness, controller, "app.set_fullscreen", {"enabled": True}, NOTE)
    assert res["state"] == "succeeded", res
    assert res["result"]["fullscreen"] is True and res["result"]["state"] == "normal" and fake.count("f11") == 0

    await full_screen_then_minimised()
    res = await run(harness, controller, "app.set_fullscreen", {"enabled": False}, NOTE)
    assert res["state"] == "succeeded" and res["result"]["fullscreen"] is False and fake.count("f11") == 1
    assert not fake.window_flags["9001"].fullscreen  # really left, not just reported

    for action, placement in (("app.restore", "normal"), ("app.maximize", "maximized")):
        await full_screen_then_minimised()
        res = await run(harness, controller, action, {}, NOTE)
        assert res["state"] == "succeeded", (action, res)
        assert res["result"]["state"] == placement and res["result"]["fullscreen"] is False
        assert fake.count("f11") == 1 and not fake.window_flags["9001"].fullscreen


@pytest.mark.parametrize("action", ["app.maximize", "app.restore"])
async def test_placement_reported_while_still_in_full_screen_is_not_a_success(
    harness: AgentHarness, controller: Controller, setup: dict[str, Any], action: str
) -> None:
    """The contract: maximise and restore leave full screen first. An adapter reading back the right placement
    while the window is still full screen did not do what was asked."""
    from dome_agent.platform.protocol import WindowState

    placement = WindowState(maximized=action == "app.maximize", fullscreen=True, foreground=True)
    setattr(harness.agent.services.platform.apps, action.split(".")[1], lambda window_id, **_: placement)
    res = await run(harness, controller, action, {}, NOTE)
    assert res["state"] == "failed" and res["error"]["code"] == "WINDOW_STATE_UNCHANGED"
    assert res["result"]["fullscreen"] is True


async def test_leaving_full_screen_targets_the_full_screen_window(
    harness: AgentHarness, controller: Controller, setup: dict[str, Any]
) -> None:
    """Two windows: the front one normal, the other in full screen. "Exit full screen" without a window_id is
    about the full-screen one; entering keeps the front-most window."""
    fake = harness.fake
    fake.add_window(700, "9002", "Other doc - Notepad", fullscreen=True)
    res = await run(harness, controller, "app.set_fullscreen", {"enabled": False}, NOTE)
    assert res["state"] == "succeeded" and res["result"]["window_id"] == "9002", res
    assert res["result"]["fullscreen"] is False and not fake.window_flags["9002"].fullscreen
    assert fake.count("f11") == 1
    res = await run(harness, controller, "app.set_fullscreen", {"enabled": True}, NOTE)
    assert res["state"] == "succeeded" and res["result"]["window_id"] == "9001"
    # nothing in full screen: leaving falls back to the front-most window, with nothing to press
    fake.add_window(700, "9001", "Doc - Notepad")
    res = await run(harness, controller, "app.set_fullscreen", {"enabled": False}, NOTE)
    assert res["state"] == "succeeded" and res["result"]["window_id"] == "9001" and fake.count("f11") == 2


async def test_app_list_names_browser_windows_and_journals_no_title(
    harness: AgentHarness, controller: Controller, setup: dict[str, Any], tmp_path: Path
) -> None:
    """A browser window's title is its tab's page title; on a YouTube results page that is the search query
    (rules.search_queries). Other windows' titles go to the phone once but are never journaled."""
    _approve(harness, tmp_path, "edge", "msedge.exe", 900)
    harness.fake.add_window(900, "9901", "ZEBRA QUERY 4471 - YouTube and 2 more pages - Personal - Microsoft Edge")
    env = controller.command("app.list", {})
    cid = payload_of(env)["command_id"]
    await harness.send_command(env)
    res = await harness.result(cid)
    apps = {a["app_id"]: a for a in res["result"]["apps"]}
    assert apps["edge"]["windows"] == [
        {"window_id": "9901", "title": "Microsoft Edge", "minimized": False, "foreground": False}
    ]
    assert apps["note"]["windows"][0]["title"] == "Doc - Notepad"  # shown to the phone...
    row = harness.agent.store.journal_get(cid)
    assert row is not None and row.frame is not None and row.state == "succeeded"
    journaled = {a["app_id"]: a for a in row.frame["result"]["apps"]}
    assert journaled["note"]["windows"][0] == {**apps["note"]["windows"][0], "title": ""}  # ...never kept
    load_registry().validate_result("app.list", row.frame["result"])
    db = b"".join(p.read_bytes() for p in harness.settings.db_path.parent.glob(harness.settings.db_path.name + "*"))
    assert b"ZEBRA QUERY 4471" not in db and b"Doc - Notepad" not in db
    assert "ZEBRA QUERY 4471" not in repr(res) and "ZEBRA QUERY 4471" not in repr(harness.relay.drain())
