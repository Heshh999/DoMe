"""*Full screen with F11*, the PC owner's per-app opt-in (round 3): F11 is pressed for full screen in an app on
the built-in F11 list or in one the owner turned it on for, never in any other app. The choice is stored with
the approval (a column added to older databases), changed only on the PC (the tray's Approved apps window,
``dome-agent app-option``, the control channel), kept when an app is re-approved, and the phone is told where
to turn it on when F11 was not pressed. The owner's own case: ZStream at C:\\zstream\\zstream-app.exe."""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Coroutine
from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from dome_agent import cli
from dome_agent.agent import Agent
from dome_agent.approved_apps import ApprovalError, ApprovedApps, f11_built_in
from dome_agent.control import ControlClient, ControlError
from dome_agent.platform.protocol import f11_not_enabled_message
from dome_agent.settings import Settings
from dome_agent.store import Store
from dome_agent.tray import (
    FULLSCREEN_F11_BUILT_IN_LABEL,
    FULLSCREEN_F11_LABEL,
    ApprovedAppsWindow,
    F11Checkbox,
    app_list_rows,
    f11_checkbox,
)

from .conftest import AgentHarness
from .helpers import Controller
from .test_handlers import run

ZSTREAM = {"app_id": "zstream"}
NOT_ON = (
    "Full screen is not turned on for ZStream. On the PC: DoMe tray > Approved apps > ZStream > Full screen "
    "with F11. Or use Maximise."
)
ITS_OWN = (
    "ZStream is in its own full screen, and Full screen with F11 is not turned on for it. Leave full screen on the "
    "PC, or turn it on: DoMe tray > Approved apps > ZStream > Full screen with F11."
)


def _program(root: Path, folder: str, name: str, content: bytes = b"MZ") -> Path:
    exe = root / "Programs" / folder / name
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_bytes(content)
    return exe


# ----- the store: a column added to databases written by older agents -------------------------------------------
_OLD_APPROVED_APPS = """CREATE TABLE approved_apps (
    app_id       TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    exe_path     TEXT NOT NULL,
    exe_sha256   TEXT NOT NULL,
    added_at     TEXT NOT NULL
)"""


def _columns(path: Path) -> list[str]:
    conn = sqlite3.connect(path)
    try:
        return [str(r[1]) for r in conn.execute("PRAGMA table_info(approved_apps)")]
    finally:
        conn.close()


def test_an_older_database_gets_the_column_and_keeps_its_apps(tmp_path: Path) -> None:
    db = tmp_path / "state.sqlite3"
    conn = sqlite3.connect(db)
    conn.execute(_OLD_APPROVED_APPS)
    conn.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute(
        "INSERT INTO approved_apps VALUES ('zstream', 'ZStream', 'C:\\zstream\\zstream-app.exe', ?, "
        "'2026-10-01T00:00:00Z')",
        ("a" * 64,),
    )
    conn.execute("INSERT INTO settings VALUES ('remote_enabled', '1')")
    conn.commit()
    conn.close()
    assert "fullscreen_f11" not in _columns(db)

    store = Store(db)
    try:
        row = store.get_approved_app("zstream")
        assert row is not None and row.fullscreen_f11 is False  # default: off
        assert (row.display_name, row.exe_path, row.exe_sha256) == ("ZStream", "C:\\zstream\\zstream-app.exe", "a" * 64)
        assert store.remote_enabled is True  # the rest of the database is untouched
        assert store.set_approved_app_fullscreen_f11("zstream", True) is not None
    finally:
        store.close()
    assert _columns(db)[-1] == "fullscreen_f11"

    # Opening again neither fails nor resets the choice.
    store = Store(db)
    try:
        assert store.list_approved_apps()[0].fullscreen_f11 is True
    finally:
        store.close()

    # An older agent (it names its columns) still writes and reads the table: the column takes its default.
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO approved_apps(app_id, display_name, exe_path, exe_sha256, added_at) VALUES (?,?,?,?,?)",
        ("tool", "Tool", "C:\\Tools\\tool.exe", "b" * 64, "2026-10-02T00:00:00Z"),
    )
    conn.commit()
    conn.close()
    store = Store(db)
    try:
        assert store.get_approved_app("tool") is not None
        assert store.get_approved_app("tool").fullscreen_f11 is False  # type: ignore[union-attr]
    finally:
        store.close()


def test_a_new_database_has_the_column(store: Store) -> None:
    row = store.add_approved_app("zstream", "ZStream", "C:\\zstream\\zstream-app.exe", "a" * 64)
    assert row.fullscreen_f11 is False
    assert store.add_approved_app("zstream", "ZStream", "C:\\zstream\\zstream-app.exe", "b" * 64, fullscreen_f11=True)
    assert store.get_approved_app("zstream").fullscreen_f11 is True  # type: ignore[union-attr]
    # refreshed after an update (no choice given): the choice is kept
    assert store.add_approved_app("zstream", "ZStream", "C:\\zstream\\zstream-app.exe", "c" * 64).fullscreen_f11
    assert store.set_approved_app_fullscreen_f11("nope", True) is None


def test_approval_keeps_the_choice_across_re_approval_and_moves(store: Store, tmp_path: Path) -> None:
    apps = ApprovedApps(store, temp_dirs=(tmp_path / "Temp",))
    exe = _program(tmp_path, "zstream", "zstream-app.exe")
    assert apps.approve("zstream", str(exe), "ZStream").fullscreen_f11 is False
    assert apps.set_fullscreen_f11("zstream", True) is not None
    exe.write_bytes(b"MZ updated")  # an update: re-approved
    assert apps.approve("zstream", str(exe), "ZStream").fullscreen_f11 is True
    moved = _program(tmp_path, "zstream-2", "zstream-app.exe")  # Find it again…
    assert apps.approve("zstream", str(moved), "ZStream", replace=True).fullscreen_f11 is True
    assert apps.approve("zstream", str(moved), "ZStream", fullscreen_f11=False).fullscreen_f11 is False
    assert apps.set_fullscreen_f11("unknown", True) is None
    with pytest.raises(ApprovalError):
        apps.set_fullscreen_f11("Not An Id", True)
    assert f11_built_in("C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe")
    assert f11_built_in("C:/Users/me/AppData/Local/Programs/Microsoft VS Code/Code.exe")
    assert not f11_built_in("C:\\zstream\\zstream-app.exe") and not f11_built_in("C:\\Office\\EXCEL.EXE")


# ----- the window actions -----------------------------------------------------------------------------------------
@pytest.fixture
def zstream(harness: AgentHarness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[bool]:
    """ZStream approved (off the F11 list, not opted in) and running with one window; returns the record of
    ``state.request_update`` calls made from the agent loop."""
    exe = _program(tmp_path, "zstream", "zstream-app.exe")
    harness.agent.apps._temp_dirs = ()  # noqa: SLF001
    harness.agent.apps.approve("zstream", str(exe), "ZStream")
    harness.fake.processes[str(exe.resolve())] = [800]
    harness.fake.add_window(800, "9801", "Movie night - ZStream")
    updates: list[bool] = []
    real = harness.agent.state.request_update

    def recording() -> None:
        asyncio.get_running_loop()  # a no-op off the loop: it must be called there
        updates.append(True)
        real()

    monkeypatch.setattr(harness.agent.state, "request_update", recording)
    return updates


async def test_full_screen_zstream_needs_the_owner_to_turn_it_on(
    harness: AgentHarness, controller: Controller, zstream: list[bool]
) -> None:
    fake = harness.fake
    res = await run(harness, controller, "app.set_fullscreen", {"enabled": True}, ZSTREAM)
    assert res["state"] == "failed" and res["error"]["code"] == "FULLSCREEN_UNSUPPORTED"
    assert res["error"]["message"] == NOT_ON and "Movie night" not in str(res)  # no window title
    assert res["result"] == {
        **ZSTREAM,
        "window_id": "9801",
        "state": "normal",
        "fullscreen": False,
        "foreground": False,
    }
    assert fake.count("f11") == 0 and fake.foreground is None  # nothing pressed, nothing moved

    # The owner ticks "Full screen with F11" for ZStream (tray or `dome-agent app-option`).
    zstream.clear()
    row = await harness.agent.set_app_fullscreen_f11_local("zstream", True)
    assert row is not None and row.fullscreen_f11 and zstream == [True]

    res = await run(harness, controller, "app.set_fullscreen", {"enabled": True}, ZSTREAM)
    assert res["state"] == "succeeded" and res["result"]["fullscreen"] is True and fake.count("f11") == 1
    res = await run(harness, controller, "app.set_fullscreen", {"enabled": False}, ZSTREAM)  # "exit full screen"
    assert res["state"] == "succeeded" and res["result"]["fullscreen"] is False and fake.count("f11") == 2
    res = await run(harness, controller, "app.set_fullscreen", {"enabled": True}, ZSTREAM)
    res = await run(harness, controller, "app.maximize", {}, ZSTREAM)  # leaves full screen with F11 first
    assert res["state"] == "succeeded" and res["result"]["state"] == "maximized" and not res["result"]["fullscreen"]
    assert fake.count("f11") == 4

    # Turned off again: ZStream in its own full screen is left alone, and the phone is told why.
    assert (await harness.agent.set_app_fullscreen_f11_local("zstream", False)) is not None
    fake.add_window(800, "9801", "Movie night - ZStream", fullscreen=True)
    for action, params in (("app.restore", {}), ("app.maximize", {}), ("app.set_fullscreen", {"enabled": False})):
        res = await run(harness, controller, action, params, ZSTREAM)
        assert res["state"] == "failed" and res["error"]["code"] == "WINDOW_STATE_UNCHANGED", action
        assert res["error"]["message"] == ITS_OWN and res["result"]["fullscreen"] is True
    assert fake.count("f11") == 4


async def test_an_app_on_the_built_in_list_gets_f11_whatever_its_choice(
    harness: AgentHarness, controller: Controller, tmp_path: Path
) -> None:
    edge = _program(tmp_path, "Edge", "msedge.exe")
    harness.agent.apps._temp_dirs = ()  # noqa: SLF001
    assert harness.agent.apps.approve("edge", str(edge), "Microsoft Edge").fullscreen_f11 is False
    harness.fake.processes[str(edge.resolve())] = [900]
    harness.fake.add_window(900, "9901", "YouTube - Microsoft Edge")
    res = await run(harness, controller, "app.set_fullscreen", {"enabled": True}, {"app_id": "edge"})
    assert res["state"] == "succeeded" and res["result"]["fullscreen"] is True
    assert harness.fake.count("f11") == 1


def test_the_message_names_the_app_and_the_tray_path() -> None:
    assert f11_not_enabled_message("ZStream", entering=True) == NOT_ON
    assert f11_not_enabled_message("ZStream", entering=False) == ITS_OWN
    assert len(f11_not_enabled_message("Z" * 64, entering=False)) <= 512  # error.message maxLength


# ----- the control channel and the CLI ------------------------------------------------------------------------
async def test_control_channel_approve_and_app_option(
    harness: AgentHarness, tmp_path: Path, zstream: list[bool]
) -> None:
    ctl = ControlClient(harness.settings.state_dir)
    exe = _program(tmp_path, "zstream-2", "zstream-app.exe", b"MZ 2")
    harness.agent.apps._temp_dirs = (tmp_path / "Temp",)  # noqa: SLF001
    zstream.clear()
    row = await asyncio.to_thread(
        ctl.call, "approve_app", app_id="zs2", exe_path=str(exe), display_name="ZStream 2", fullscreen_f11=True
    )
    assert (row["fullscreen_f11"], row["f11_built_in"]) == (True, False) and zstream == [True]
    row = await asyncio.to_thread(ctl.call, "approve_app", app_id="zs2", exe_path=str(exe), display_name="ZStream 2")
    assert row["fullscreen_f11"] is True  # re-approved without the flag: kept

    row = await asyncio.to_thread(ctl.call, "app_option", app_id="zs2", fullscreen_f11=False)
    assert row == {"app_id": "zs2", "display_name": "ZStream 2", "fullscreen_f11": False, "f11_built_in": False}
    assert harness.agent.apps.get("zs2").fullscreen_f11 is False and zstream == [True, True, True]  # type: ignore[union-attr]

    for args, code in (
        ({"app_id": "nope", "fullscreen_f11": True}, "UNKNOWN_APP"),
        ({"app_id": "Not An Id", "fullscreen_f11": True}, "INVALID_APP"),
        ({"app_id": "zs2", "fullscreen_f11": "on"}, "INVALID_PARAMETERS"),
        ({"app_id": "zs2"}, "INVALID_PARAMETERS"),
    ):
        with pytest.raises(ControlError) as ei:
            await asyncio.to_thread(ctl.call, "app_option", **args)
        assert ei.value.code == code, args
    with pytest.raises(ControlError) as ei:
        await asyncio.to_thread(
            ctl.call, "approve_app", app_id="zs3", exe_path=str(exe), fullscreen_f11="yes", replace=True
        )
    assert ei.value.code == "INVALID_PARAMETERS" and harness.agent.apps.get("zs3") is None


async def test_cli_app_option_reaches_the_running_agent(
    harness: AgentHarness, controller: Controller, zstream: list[bool], capsys: pytest.CaptureFixture[str]
) -> None:
    zstream.clear()
    base = ["--state-dir", str(harness.settings.state_dir)]
    assert await asyncio.to_thread(cli.main, [*base, "app-option", "zstream", "--fullscreen-f11", "on"]) == 0
    assert "Full screen with F11: on for ZStream" in capsys.readouterr().out
    assert harness.agent.apps.get("zstream").fullscreen_f11 is True and zstream == [True]  # type: ignore[union-attr]
    res = await run(harness, controller, "app.set_fullscreen", {"enabled": True}, ZSTREAM)
    assert res["state"] == "succeeded" and harness.fake.count("f11") == 1
    assert await asyncio.to_thread(cli.main, [*base, "app-option", "nope", "--fullscreen-f11", "on"]) == 1
    assert "No approved app has that id." in capsys.readouterr().err


def test_cli_without_agent(
    settings: Settings, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("dome_agent.approved_apps.default_temp_dirs", lambda: (tmp_path / "Temp",))
    exe = _program(tmp_path, "zstream", "zstream-app.exe")
    edge = _program(tmp_path, "Edge", "msedge.exe")
    base = ["--state-dir", str(settings.state_dir)]

    def stored(app_id: str) -> bool:
        store = Store(settings.db_path)
        try:
            row = store.get_approved_app(app_id)
            assert row is not None
            return row.fullscreen_f11
        finally:
            store.close()

    assert cli.main([*base, "approve-app", "zstream", str(exe), "--name", "ZStream"]) == 0
    out = capsys.readouterr().out
    assert "Full screen with F11: off for ZStream" in out and "app-option zstream --fullscreen-f11 on" in out
    assert stored("zstream") is False
    assert cli.main([*base, "app-option", "zstream", "--fullscreen-f11", "on"]) == 0
    assert "on for ZStream" in capsys.readouterr().out and stored("zstream") is True
    assert cli.main([*base, "app-option", "zstream", "--fullscreen-f11", "off"]) == 0
    assert stored("zstream") is False
    assert cli.main([*base, "approve-app", "zstream", str(exe), "--name", "ZStream", "--fullscreen-f11"]) == 0
    assert stored("zstream") is True
    assert cli.main([*base, "approve-app", "zstream", str(exe), "--name", "ZStream"]) == 0  # re-approve: kept
    assert stored("zstream") is True
    assert cli.main([*base, "approve-app", "edge", str(edge), "--name", "Edge"]) == 0
    assert "always on for Edge" in capsys.readouterr().out
    assert cli.main([*base, "app-option", "nope", "--fullscreen-f11", "on"]) == 1
    assert "No approved app has that id." in capsys.readouterr().err
    with pytest.raises(SystemExit) as ei:
        cli.main([*base, "app-option", "zstream", "--fullscreen-f11", "yes"])
    assert ei.value.code == 2


# ----- the tray's Approved apps window, on stand-in widgets (the real Tk run is test_tray_apps_window.py) ------
def test_checkbox_and_column_for_each_kind_of_app() -> None:
    zs, edge = "C:\\zstream\\zstream-app.exe", "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"
    # Add an app…: pre-ticked only for an app on the built-in list (which cannot be unticked)
    assert f11_checkbox(zs) == F11Checkbox(False, True, FULLSCREEN_F11_LABEL)
    assert f11_checkbox(edge) == F11Checkbox(True, False, FULLSCREEN_F11_BUILT_IN_LABEL)
    assert f11_checkbox(zs, True) == F11Checkbox(True, True, FULLSCREEN_F11_LABEL)
    assert f11_checkbox(zs, True, busy=True).enabled is False
    assert f11_checkbox(None) == F11Checkbox(False, False, FULLSCREEN_F11_LABEL)  # nothing selected

    from dome_agent.store import ApprovedAppRow

    rows = app_list_rows(
        [
            ApprovedAppRow("zstream", "ZStream", zs, "0" * 64, "2026-10-10T00:00:00Z"),
            ApprovedAppRow("chrome", "Chrome", edge, "0" * 64, "2026-10-10T00:00:00Z"),
            ApprovedAppRow("vlc", "VLC", "C:\\VLC\\vlc.exe", "0" * 64, "2026-10-10T00:00:00Z", fullscreen_f11=True),
        ],
        {},
    )
    assert [(r.name, r.fullscreen) for r in rows] == [("Chrome", "F11"), ("VLC", "F11"), ("ZStream", "Off")]


class _Var:
    def __init__(self) -> None:
        self.value = False

    def get(self) -> bool:
        return self.value

    def set(self, value: bool) -> None:
        self.value = bool(value)


class _Widget:
    def __init__(self) -> None:
        self.options: dict[str, Any] = {}

    def configure(self, **options: Any) -> None:
        self.options.update(options)

    def winfo_exists(self) -> bool:
        return True


class _Tree:
    def __init__(self) -> None:
        self.rows: dict[str, tuple[Any, ...]] = {}
        self.selected: tuple[str, ...] = ()

    def get_children(self) -> tuple[str, ...]:
        return tuple(self.rows)

    def delete(self, *iids: str) -> None:
        for iid in iids:
            self.rows.pop(iid, None)

    def insert(self, parent: str, index: str, *, iid: str, values: tuple[Any, ...]) -> None:
        self.rows[iid] = tuple(values)

    def exists(self, iid: str) -> bool:
        return iid in self.rows

    def selection_set(self, iid: str) -> None:
        self.selected = (iid,)

    def selection(self) -> tuple[str, ...]:
        return self.selected

    def see(self, iid: str) -> None:
        pass


def _stand_in_widgets(window: ApprovedAppsWindow) -> None:
    window._win = _Widget()  # noqa: SLF001
    window._tree = _Tree()  # noqa: SLF001
    window._message = _Widget()  # noqa: SLF001
    window._add_btn = _Widget()  # noqa: SLF001
    window._reapprove_btn = _Widget()  # noqa: SLF001
    window._remove_btn = _Widget()  # noqa: SLF001
    window._f11_var = _Var()  # noqa: SLF001
    window._f11_check = _Widget()  # noqa: SLF001


class _TrayAgent:
    """The agent's local app methods on a real store; ``state.request_update`` recorded on the loop."""

    approve_app_local = Agent.approve_app_local
    set_app_fullscreen_f11_local = Agent.set_app_fullscreen_f11_local

    def __init__(self, store: Store, temp: Path) -> None:
        self.apps = ApprovedApps(store, temp_dirs=(temp,))
        self.updates = 0
        self.state = SimpleNamespace(request_update=self._update)

    def _update(self) -> None:
        asyncio.get_running_loop()
        self.updates += 1


def _submit_now(coro: Coroutine[Any, Any, Any]) -> Future[Any]:
    """The agent loop, collapsed: run the coroutine to the end and hand back its finished future."""
    fut: Future[Any] = Future()
    try:
        fut.set_result(asyncio.run(coro))
    except BaseException as exc:  # noqa: BLE001 - delivered through the future, as on the real loop
        fut.set_exception(exc)
    return fut


def test_tray_checkbox_turns_the_choice_on_and_off(
    store: Store, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ApprovedAppsWindow, "_build", _stand_in_widgets)
    agent = _TrayAgent(store, tmp_path / "Temp")
    zs = _program(tmp_path, "zstream", "zstream-app.exe")
    edge = _program(tmp_path, "Edge", "msedge.exe")
    agent.apps.approve("zstream", str(zs), "ZStream")
    agent.apps.approve("edge", str(edge), "Microsoft Edge")
    window = ApprovedAppsWindow(
        root=None, agent=agent, submit=_submit_now, post=lambda fn: fn(), on_closed=lambda: None
    )
    tree: _Tree = window._tree  # noqa: SLF001
    var: _Var = window._f11_var  # noqa: SLF001
    check: _Widget = window._f11_check  # noqa: SLF001
    message: _Widget = window._message  # noqa: SLF001
    assert tree.rows["zstream"] == ("ZStream", "Off", "Ready", str(zs.resolve()))
    assert tree.rows["edge"][:2] == ("Microsoft Edge", "F11")
    assert check.options["state"] == "disabled"  # nothing selected yet

    tree.selection_set("zstream")
    window._update_buttons()  # noqa: SLF001  # <<TreeviewSelect>>
    assert (var.get(), check.options["state"], check.options["text"]) == (False, "normal", FULLSCREEN_F11_LABEL)

    var.set(True)  # a click: Tk flips the variable, then runs the command
    window._toggle_f11()  # noqa: SLF001
    assert agent.apps.get("zstream").fullscreen_f11 is True and agent.updates == 1  # type: ignore[union-attr]
    assert tree.rows["zstream"][1] == "F11" and var.get() is True
    assert message.options["text"] == "Full screen with F11 is now on for “ZStream”."

    var.set(False)
    window._toggle_f11()  # noqa: SLF001
    assert agent.apps.get("zstream").fullscreen_f11 is False and agent.updates == 2  # type: ignore[union-attr]
    assert tree.rows["zstream"][1] == "Off" and "now off for “ZStream”" in message.options["text"]

    # Edge is on the built-in list: ticked, cannot be changed, and a click changes nothing.
    tree.selection_set("edge")
    window._update_buttons()  # noqa: SLF001
    assert (var.get(), check.options["state"], check.options["text"]) == (
        True,
        "disabled",
        FULLSCREEN_F11_BUILT_IN_LABEL,
    )
    var.set(False)
    window._toggle_f11()  # noqa: SLF001
    assert var.get() is True and agent.updates == 2 and agent.apps.get("edge").fullscreen_f11 is False  # type: ignore[union-attr]

    # Removed meanwhile (dome-agent remove-app): said so, and the list is read again.
    agent.apps.remove("zstream")
    tree.selection_set("zstream")
    var.set(True)
    window._toggle_f11()  # noqa: SLF001
    assert "no longer in the list" in message.options["text"] and "zstream" not in tree.rows
