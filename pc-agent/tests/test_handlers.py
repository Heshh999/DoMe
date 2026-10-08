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
    ext = await harness.connect_extension(tab)
    yt = {"browser_instance_id": ext.browser_instance_id, "tab_id": 11, "tab_token": tab.tab_token}
    harness.fake.add_session("Spotify.exe#0", title="Song")
    exe = tmp_path / "apps" / "note.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"MZ")
    harness.agent.apps._temp_dirs = ()  # noqa: SLF001
    harness.agent.apps.approve("note", str(exe), "Notepad")
    harness.fake.processes[str(exe.resolve())] = [700]
    harness.fake.add_window(700, "9001", "Doc - Notepad")
    return {"yt": yt, "tab": tab, "ext": ext, "exe": str(exe.resolve())}


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
    ("app.launch", {"app_id": "note"}, None),
    ("app.close", {}, "window"),
    ("power.cancel", {}, None),
    ("power.sleep", {"countdown_seconds": 0}, None),
    ("power.restart", {"countdown_seconds": 0}, None),
    ("power.shutdown", {"countdown_seconds": 0}, None),
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
    if action == "app.focus":
        assert res["result"]["focused"] is True
    if action == "windows.set_volume":
        assert res["result"] == {"value": 25, "muted": False}


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
