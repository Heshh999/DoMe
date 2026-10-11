"""Window actions (protocol 1.2): app.maximize, app.restore and app.set_fullscreen from a paired phone through
the real relay and the real agent process on the fake platform. They act only on a running approved app's
window, never start the app, and report the window state the agent read back; F11 is pressed only into apps
that use it for full screen; a phone that stamps one below the action's `since` is refused by the relay
before the PC sees it (rules.command_version_stamping). A browser window's title (after a YouTube search:
the query) never leaves the PC (rules.search_queries)."""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from conftest import REGISTRY, Browser, Env, RealAgent, run_command, wait_state

HERE = Path(__file__).resolve().parent
NOTEPAD = {"app_id": "notepad"}
EDITOR = {"app_id": "editor"}  # an editor whose full-screen key is F11
QUERY = "lofi hip hop radio 9417"
WINDOW_ACTIONS: list[tuple[str, dict[str, Any]]] = [
    ("app.maximize", {}),
    ("app.restore", {}),
    ("app.set_fullscreen", {"enabled": True}),
]


@pytest.fixture
async def agent(env: Env, alice: Browser) -> AsyncIterator[RealAgent]:
    """This module's PC: apps started with app.launch show a window (fake_window_agent.py), and the agent's
    temporary folder is its own, so an executable next to its state can be approved like one in Program Files."""
    a = RealAgent(env, alice, command=[sys.executable, str(HERE / "fake_window_agent.py")])
    (a.root / "tmp").mkdir()
    a.extra_env = {"TMPDIR": str(a.root / "tmp"), "DOME_TEST_BROWSER_TITLE": f"{QUERY} - YouTube - Microsoft Edge"}
    await a.link()
    await a.start()
    try:
        yield a
    finally:
        await a.stop()
        a.cleanup()


async def approve_app(agent: RealAgent, app_id: str, exe_name: str, display_name: str) -> None:
    exe = agent.root / "apps" / app_id / exe_name
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"MZ fake " + app_id.encode())
    approved = await agent.control("approve_app", app_id=app_id, exe_path=str(exe), display_name=display_name)
    assert approved["app_id"] == app_id and approved["display_name"] == display_name


async def approve_notepad(agent: RealAgent) -> None:
    await approve_app(agent, "notepad", "notepad.exe", "Notepad")


async def test_window_actions_never_start_the_app_then_read_back_its_window(agent: RealAgent, phone: Any) -> None:
    await approve_notepad(agent)
    for action, params in WINDOW_ACTIONS:
        res = await run_command(phone, agent.pc_id, action, params, NOTEPAD)
        assert res["origin"] == "agent" and res["state"] == "failed", (action, res)
        assert res["error"]["code"] == "APP_NOT_RUNNING" and res["error"]["retryable"] is False
    res = await run_command(phone, agent.pc_id, "app.list")
    assert res["result"]["apps"] == [{"app_id": "notepad", "display_name": "Notepad", "running": False, "windows": []}]

    res = await run_command(phone, agent.pc_id, "app.launch", {"app_id": "notepad"})
    assert res["state"] == "succeeded" and res["result"]["running"] is True, res

    res = await run_command(phone, agent.pc_id, "app.maximize", {}, NOTEPAD)
    assert res["origin"] == "agent" and res["state"] == "succeeded", res
    REGISTRY.validate_result("app.maximize", res["result"])
    window_id = res["result"]["window_id"]
    assert res["result"] == {
        "app_id": "notepad",
        "window_id": window_id,
        "state": "maximized",
        "fullscreen": False,
        "foreground": True,
    }
    window = {**NOTEPAD, "window_id": window_id}

    # Notepad does not use F11 for full screen, and in other apps F11 does something else (Excel adds a chart
    # sheet): nothing is pressed, and the window is left as it was
    res = await run_command(phone, agent.pc_id, "app.set_fullscreen", {"enabled": True}, window)
    assert res["state"] == "failed" and res["error"]["code"] == "FULLSCREEN_UNSUPPORTED", res
    assert res["result"] == {**NOTEPAD, "window_id": window_id, "state": "maximized", "fullscreen": False, "foreground": True}

    # an editor whose full-screen key is F11
    await approve_app(agent, "editor", "notepad++.exe", "Notepad++")
    res = await run_command(phone, agent.pc_id, "app.launch", EDITOR)
    assert res["state"] == "succeeded", res
    res = await run_command(phone, agent.pc_id, "app.maximize", {}, EDITOR)
    assert res["state"] == "succeeded" and res["result"]["state"] == "maximized", res
    editor = {**EDITOR, "window_id": res["result"]["window_id"]}
    res = await run_command(phone, agent.pc_id, "app.set_fullscreen", {"enabled": True}, editor)
    assert res["state"] == "succeeded" and res["result"]["fullscreen"] is True, res
    REGISTRY.validate_result("app.set_fullscreen", res["result"])
    res = await run_command(phone, agent.pc_id, "app.set_fullscreen", {"enabled": True}, editor)
    assert res["state"] == "succeeded" and res["result"]["fullscreen"] is True  # absolute: already there
    # minimised in full screen, then full screen again: Windows shows it again full screen, nothing is toggled
    res = await run_command(phone, agent.pc_id, "app.minimize", {}, editor)
    assert res["state"] == "succeeded", res
    res = await run_command(phone, agent.pc_id, "app.set_fullscreen", {"enabled": True}, editor)
    assert res["state"] == "succeeded" and res["result"]["fullscreen"] is True, res
    res = await run_command(phone, agent.pc_id, "app.set_fullscreen", {"enabled": False}, editor)
    assert res["state"] == "succeeded" and res["result"]["fullscreen"] is False
    assert res["result"]["state"] == "maximized"  # leaving full screen returns to the maximised window

    res = await run_command(phone, agent.pc_id, "app.restore", {}, window)
    assert res["state"] == "succeeded", res
    REGISTRY.validate_result("app.restore", res["result"])
    assert res["result"]["state"] == "normal" and res["result"]["fullscreen"] is False

    res = await run_command(phone, agent.pc_id, "app.maximize", {}, {**NOTEPAD, "window_id": "999"})
    assert res["state"] == "failed" and res["error"]["code"] == "TARGET_GONE"  # not one of the app's windows
    res = await run_command(phone, agent.pc_id, "app.maximize", {}, {"app_id": "wordpad"})
    assert res["state"] == "failed" and res["error"]["code"] == "APP_NOT_APPROVED"

    res = await run_command(phone, agent.pc_id, "app.list")
    apps = {a["app_id"]: a for a in res["result"]["apps"]}
    assert [w["window_id"] for w in apps["notepad"]["windows"]] == [window_id]

    # the relay kept lifecycle only, and the window actions appear under their own names
    rows = await phone.browser.get(f"/v1/commands?pc_id={agent.pc_id}&limit=20", schema="commands_response")
    actions = {r["action"] for r in rows["commands"]}
    assert {"app.maximize", "app.restore", "app.set_fullscreen"} <= actions


async def test_a_window_action_stamped_below_its_since_is_refused_by_the_relay(agent: RealAgent, phone: Any) -> None:
    await approve_notepad(agent)
    for action, params in WINDOW_ACTIONS:
        res = await run_command(phone, agent.pc_id, action, params, NOTEPAD, protocol_version="1.1")
        assert res["origin"] == "relay" and res["state"] == "failed", (action, res)
        assert res["error"]["code"] == "PROTOCOL_INCOMPATIBLE"
    # the PC never received them: nothing is in its journal
    store = agent.store()
    try:
        assert store.journal_count() == 0
    finally:
        store.close()
    # an action that 1.1 defines keeps working with the same phone's 1.1 stamp
    res = await run_command(phone, agent.pc_id, "app.list", protocol_version="1.1")
    assert res["origin"] == "agent" and res["state"] == "succeeded"


async def test_a_browser_window_title_never_leaves_the_pc(agent: RealAgent, phone: Any) -> None:
    """After a YouTube search the browser window's own title is "<query> - YouTube - Microsoft Edge". app.list
    names the window "Microsoft Edge", pc_state's foreground_app carries no title, and nothing on the PC keeps
    it; another app's window title reaches the phone but is not journaled either."""
    await approve_app(agent, "edge", "msedge.exe", "Microsoft Edge")
    await approve_notepad(agent)
    for app in ("notepad", "edge"):  # Edge last: it is in front
        res = await run_command(phone, agent.pc_id, "app.launch", {"app_id": app})
        assert res["state"] == "succeeded", res
    res = await run_command(phone, agent.pc_id, "app.list")
    assert res["state"] == "succeeded" and QUERY not in repr(res), res
    apps = {a["app_id"]: a for a in res["result"]["apps"]}
    assert [w["title"] for w in apps["edge"]["windows"]] == ["Microsoft Edge"]
    assert [w["title"] for w in apps["notepad"]["windows"]] == ["Untitled - Notepad"]
    state = await wait_state(phone, agent.pc_id, lambda s: (s.get("foreground_app") or {}).get("browser") == "edge")
    assert state["state"]["foreground_app"] == {"process_name": "msedge.exe", "browser": "edge", "elevated": False}
    assert QUERY not in repr(state)
    for path in [agent.log_path, *agent.state_dir.rglob("*")]:
        if path.is_file():
            data = path.read_bytes()
            assert QUERY.encode() not in data, f"the query found in {path}"
            if path.name.startswith("state.sqlite3"):
                assert b"Untitled - Notepad" not in data, f"a window title journaled in {path}"
