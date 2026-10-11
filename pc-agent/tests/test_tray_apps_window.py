"""The tray's Approved apps window on a real Tk: add, duplicate, collision, plain-words refusals, re-approve
after an update, find a moved program again, remove, and *Full screen with F11* (pre-ticked in the Add dialog
only for an app on the built-in F11 list; ticked per app in the window). The agent side is the real
``Agent.approve_app_local`` / ``remove_app_local`` / ``set_app_fullscreen_f11_local`` on its own event loop
thread, exactly as in the tray (Tk thread ↔ agent loop).

Needs tkinter and a display (Windows, or Linux under ``xvfb-run``); skipped with the reason otherwise. It runs
in CI on windows-latest and must never hang there:

* every wait for the Tk thread gives up after :data:`TK_WAIT` seconds, and Tk that cannot open a window skips;
* the fixture destroys every window the test opened, on the Tk thread, then quits the Tk loop and waits at most
  :data:`TK_WAIT` seconds for the thread (the root is quit, never destroyed: destroying it and freeing it from
  the test thread aborts Tcl with "Tcl_AsyncDelete: async handler deleted by the wrong thread");
* a watchdog ends the whole run (exit code 1, with every thread's traceback) if the test and its fixture take
  longer than :data:`WATCHDOG_SECONDS` (a normal run takes about 3 s)."""

from __future__ import annotations

import asyncio
import faulthandler
import os
import sys
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from dome_agent.agent import Agent
from dome_agent.approved_apps import ApprovedApps
from dome_agent.settings import Settings
from dome_agent.store import Store
from dome_agent.tray import FULLSCREEN_F11_BUILT_IN_LABEL, FULLSCREEN_F11_LABEL, TkThread, TrayUI

tkinter = pytest.importorskip("tkinter", reason="this Python has no tkinter")
if sys.platform != "win32" and not os.environ.get("DISPLAY"):
    pytest.skip("no display for Tk: DISPLAY is not set (run under xvfb-run)", allow_module_level=True)

TK_WAIT = 10.0
WATCHDOG_SECONDS = 120.0


class _Agent:
    """The agent's local app methods, on an ApprovedApps that accepts programs under tmp_path."""

    approve_app_local = Agent.approve_app_local
    remove_app_local = Agent.remove_app_local
    set_app_fullscreen_f11_local = Agent.set_app_fullscreen_f11_local

    def __init__(self, store: Store, temp: Path) -> None:
        self.apps = ApprovedApps(store, temp_dirs=(temp,))
        self.updates = 0
        self.state = SimpleNamespace(request_update=self._update)

    def _update(self) -> None:
        asyncio.get_running_loop()  # only meaningful on the agent loop
        self.updates += 1


class _Ui:
    def __init__(self, tray: TrayUI, tk_thread: TkThread) -> None:
        self.tray = tray
        self.tk = tk_thread

    def on_tk(self, fn: Callable[[], Any]) -> Any:
        done = threading.Event()
        box: dict[str, Any] = {}

        def run() -> None:
            try:
                box["value"] = fn()
            except BaseException as exc:  # noqa: BLE001 - re-raised in the test thread
                box["error"] = exc
            finally:
                done.set()

        self.tk.post(run)
        assert done.wait(TK_WAIT), "the Tk thread is stuck"
        if "error" in box:
            raise box["error"]
        return box.get("value")

    def wait_for(self, predicate: Callable[[], bool], what: str) -> None:
        """``predicate`` runs on the test thread (it reads widgets through ``on_tk``)."""
        deadline = time.monotonic() + TK_WAIT
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.05)
        raise AssertionError(f"timed out waiting for {what}; message: {self.message()!r}")

    @property
    def win(self) -> Any:
        window = self.tray._apps_window  # noqa: SLF001
        assert window is not None
        return window

    def message(self) -> str:
        return str(self.on_tk(lambda: self.win._message.cget("text")))  # noqa: SLF001

    def row(self, app_id: str) -> tuple[str, ...] | None:
        """(name, full screen, status, program file) as the list shows them."""
        tree = self.win._tree  # noqa: SLF001
        return self.on_tk(lambda: tuple(tree.item(app_id)["values"]) if tree.exists(app_id) else None)

    def dialog(self, title: str) -> Any:
        def find() -> Any:
            for child in self.win._win.winfo_children():  # noqa: SLF001
                if isinstance(child, tkinter.Toplevel) and child.title() == title:
                    return child
            return None

        return self.on_tk(find)

    def click(self, dialog: Any, text: str) -> None:
        def press() -> None:
            stack = list(dialog.winfo_children())
            while stack:
                widget = stack.pop()
                if isinstance(widget, tkinter.Button) and widget.cget("text") == text:
                    widget.invoke()
                    return
                stack.extend(widget.winfo_children())
            raise AssertionError(f"no button {text!r}")

        self.on_tk(press)

    def type_name(self, dialog: Any, name: str) -> None:
        def set_name() -> None:
            entry = next(w for w in dialog.winfo_children() if isinstance(w, tkinter.Entry))
            entry.delete(0, "end")
            entry.insert(0, name)

        self.on_tk(set_name)

    def checkbox(self, widget: Any) -> tuple[str, str, bool]:
        """A Checkbutton's (text, state, ticked)."""

        def read() -> tuple[str, str, bool]:
            ticked = bool(widget.tk.getboolean(widget.getvar(str(widget.cget("variable")))))
            return str(widget.cget("text")), str(widget.cget("state")), ticked

        return self.on_tk(read)

    def dialog_checkbox(self, dialog: Any) -> Any:
        return self.on_tk(lambda: next(w for w in dialog.winfo_children() if isinstance(w, tkinter.Checkbutton)))

    def window_checkbox(self) -> tuple[str, str, bool]:
        return self.checkbox(self.win._f11_check)  # noqa: SLF001


def _watchdog(config: pytest.Config) -> threading.Timer:
    """After :data:`WATCHDOG_SECONDS`: show every thread's traceback on the real stderr (pytest's capture is
    suspended first, as pytest-timeout does) and end the run, instead of hanging CI."""

    def expire() -> None:
        capman = config.pluginmanager.getplugin("capturemanager")
        if capman is not None:
            capman.suspend_global_capture(in_=True)
        sys.stderr.write(f"\ntest_tray_apps_window.py: still running after {WATCHDOG_SECONDS:.0f} s; threads:\n")
        sys.stderr.flush()
        faulthandler.dump_traceback(file=sys.stderr, all_threads=True)
        os._exit(1)

    timer = threading.Timer(WATCHDOG_SECONDS, expire)
    timer.daemon = True
    timer.start()
    return timer


def _close_windows(tk_thread: TkThread) -> None:
    """On the Tk thread: destroy every window the test opened, then leave the Tk loop (the root is quit, not
    destroyed; see the module docstring). Waits at most :data:`TK_WAIT` seconds for the thread to end."""

    def close() -> None:
        root = tk_thread.root
        for child in list(root.winfo_children()):
            child.destroy()
        root.quit()

    tk_thread.post(close)
    tk_thread._thread.join(TK_WAIT)  # noqa: SLF001
    assert not tk_thread._thread.is_alive(), "the Tk thread did not stop"  # noqa: SLF001


@pytest.fixture
def ui(request: pytest.FixtureRequest, settings: Settings, tmp_path: Path) -> Iterator[tuple[_Ui, _Agent]]:
    watchdog = _watchdog(request.config)
    try:
        tk_thread = TkThread()
        tk_thread.start()
        if tk_thread.root is None:
            pytest.skip("Tk could not open a window here (no display or desktop session)")
        store = Store(tmp_path / "apps.sqlite")
        agent = _Agent(store, tmp_path / "Temp")
        loop = asyncio.new_event_loop()
        loop_thread = threading.Thread(target=loop.run_forever, name="dome-agent-loop", daemon=True)
        loop_thread.start()
        tray = TrayUI(settings)
        tray._tk = tk_thread  # noqa: SLF001
        tray.bind(agent, loop)
        try:
            yield _Ui(tray, tk_thread), agent
        finally:
            try:
                _close_windows(tk_thread)  # nothing may touch the store once it is closed
            finally:
                loop.call_soon_threadsafe(loop.stop)
                loop_thread.join(TK_WAIT)
                loop.close()
                store.close()
    finally:
        watchdog.cancel()


def _program(root: Path, folder: str, name: str, content: bytes = b"MZ") -> Path:
    exe = root / "Programs" / folder / name
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_bytes(content)
    return exe


def test_approved_apps_window_end_to_end(
    ui: tuple[_Ui, _Agent], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tkinter import filedialog, messagebox

    u, agent = ui
    picks: list[str] = []
    asked: list[dict[str, Any]] = []

    def pick(**kwargs: Any) -> str:
        asked.append(kwargs)
        return picks.pop(0) if picks else ""

    monkeypatch.setattr(filedialog, "askopenfilename", pick)
    monkeypatch.setattr(messagebox, "askyesno", lambda *a, **k: True)

    # The menu's "Add an app…": the window opens and the file picker follows (cancelled here).
    u.on_tk(lambda: u.tray._open_apps_window(add=True))  # noqa: SLF001
    u.wait_for(lambda: bool(asked), "the file picker")
    assert asked[0]["filetypes"] == [("Programs", "*.exe")] and asked[0]["parent"] is u.win._win  # noqa: SLF001
    assert "No apps yet" in u.message()
    assert u.window_checkbox() == (FULLSCREEN_F11_LABEL, "disabled", False)  # nothing selected

    # Add (Tk's forward slashes are normalised), named from the file, re-checked as "Ready". Spotify is not on
    # the built-in F11 list, so "Full screen with F11" starts unticked.
    spotify = _program(tmp_path, "Spotify", "spotify.exe", b"MZ v1")
    picks.append(str(spotify.parent) + "//./spotify.exe")  # normalised like Tk's C:/forward/slashes
    u.on_tk(lambda: u.win._add_btn.invoke())  # noqa: SLF001
    name_dialog = u.dialog("Add an app")
    assert name_dialog is not None
    assert u.checkbox(u.dialog_checkbox(name_dialog)) == (FULLSCREEN_F11_LABEL, "normal", False)
    u.click(name_dialog, "Add")
    u.wait_for(lambda: (u.row("spotify") or ("", "", ""))[2] == "Ready", "spotify ready")
    assert u.row("spotify") == ("Spotify", "Off", "Ready", str(spotify.resolve()))
    assert "Added “Spotify”. It now shows in the Apps list on your phone." in u.message()
    assert agent.apps.get("spotify").fullscreen_f11 is False  # type: ignore[union-attr]

    # The same file again: no dialog, the row is selected.
    picks.append(str(spotify))
    u.on_tk(lambda: u.win._add_btn.invoke())  # noqa: SLF001
    assert "“Spotify” is already in the list." in u.message() and u.dialog("Add an app") is None

    # Another spotify.exe: the same name is refused, a new one is taken, the id becomes spotify-2.
    other = _program(tmp_path, "Other", "spotify.exe", b"MZ other")
    picks.append(str(other))
    u.on_tk(lambda: u.win._add_btn.invoke())  # noqa: SLF001
    name_dialog = u.dialog("Add an app")
    u.click(name_dialog, "Add")
    assert u.dialog("Add an app") is not None  # still open: "Another app is already called “Spotify”"
    u.type_name(name_dialog, "Spotify 2")
    u.click(name_dialog, "Add")
    u.wait_for(lambda: u.row("spotify-2") is not None, "spotify-2")

    # Edge is on the built-in F11 list: the Add dialog shows the box ticked, and it cannot be unticked.
    edge = _program(tmp_path, "Edge", "msedge.exe")
    picks.append(str(edge))
    u.on_tk(lambda: u.win._add_btn.invoke())  # noqa: SLF001
    name_dialog = u.dialog("Add an app")
    assert u.checkbox(u.dialog_checkbox(name_dialog)) == (FULLSCREEN_F11_BUILT_IN_LABEL, "disabled", True)
    u.click(name_dialog, "Add")
    u.wait_for(lambda: u.row("msedge") is not None, "edge added")
    assert (u.row("msedge") or ())[1] == "F11" and agent.apps.get("msedge").fullscreen_f11 is True  # type: ignore[union-attr]
    u.on_tk(lambda: u.win.refresh(select="msedge"))
    assert u.window_checkbox() == (FULLSCREEN_F11_BUILT_IN_LABEL, "disabled", True)

    # Full screen with F11 for Spotify: select it, tick the box.
    u.on_tk(lambda: u.win.refresh(select="spotify"))
    assert u.window_checkbox() == (FULLSCREEN_F11_LABEL, "normal", False)
    u.on_tk(lambda: u.win._f11_check.invoke())  # noqa: SLF001
    u.wait_for(lambda: "Full screen with F11 is now on for “Spotify”." in u.message(), "full screen turned on")
    assert agent.apps.get("spotify").fullscreen_f11 is True  # type: ignore[union-attr]
    assert (u.row("spotify") or ())[1] == "F11" and u.window_checkbox() == (FULLSCREEN_F11_LABEL, "normal", True)

    # Plain-words refusals: an apostrophe in the folder, a Microsoft Store app.
    civ = _program(tmp_path, "Sid Meier's Civ", "civ.exe")
    picks.append(str(civ))
    u.on_tk(lambda: u.win._add_btn.invoke())  # noqa: SLF001
    u.click(u.dialog("Add an app"), "Add")
    u.wait_for(lambda: "an apostrophe (')" in u.message(), "apostrophe explained")
    store_app = _program(tmp_path, "WindowsApps/SpotifyAB.Spotify_1.2", "Spotify.exe")
    picks.append(str(store_app))
    u.on_tk(lambda: u.win._add_btn.invoke())  # noqa: SLF001
    assert "Microsoft Store app" in u.message() and u.dialog("Add an app") is None

    # An update changes the file: "Changed…", Re-approve keeps the id (and Full screen with F11).
    spotify.write_bytes(b"MZ v2 (updated)")
    u.on_tk(lambda: u.win.refresh(select="spotify"))
    u.wait_for(lambda: "Changed (an update?)" in str(u.row("spotify")), "changed status")
    assert u.on_tk(lambda: str(u.win._reapprove_btn.cget("state"))) == "normal"  # noqa: SLF001
    u.on_tk(lambda: u.win._reapprove_btn.invoke())  # noqa: SLF001
    u.wait_for(lambda: "is ready again" in u.message(), "re-approved")
    u.wait_for(lambda: (u.row("spotify") or ("", "", ""))[2] == "Ready", "ready after re-approve")

    # The program moved: "Find it again…" points the same id at the new file.
    moved = _program(tmp_path, "Spotify-new", "spotify.exe", b"MZ moved")
    spotify.unlink()
    u.on_tk(lambda: u.win.refresh(select="spotify"))
    u.wait_for(lambda: "Not found: click Find it again" in str(u.row("spotify")), "missing status")
    assert u.on_tk(lambda: u.win._reapprove_btn.cget("text")) == "Find it again…"  # noqa: SLF001
    picks.append(str(moved))
    u.on_tk(lambda: u.win._reapprove_btn.invoke())  # noqa: SLF001
    u.wait_for(lambda: (u.row("spotify") or ("", "", "", ""))[3] == str(moved.resolve()), "found again")
    assert (u.row("spotify") or ())[1] == "F11"  # the owner's choice survived both

    # Remove (after the confirmation).
    u.on_tk(lambda: u.win.refresh(select="spotify-2"))
    u.on_tk(lambda: u.win._remove_btn.invoke())  # noqa: SLF001
    u.wait_for(lambda: u.row("spotify-2") is None, "removed")
    assert [r.app_id for r in agent.apps.list()] == ["msedge", "spotify"]
    assert agent.updates == 7  # three adds, full screen on, re-approve, find again, remove

    u.on_tk(lambda: u.win.close())
    assert u.tray._apps_window is None  # noqa: SLF001
