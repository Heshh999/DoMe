"""The tray menu is rebuilt on every status update; the paired-phones submenu must follow the store."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from dome_agent.settings import Settings
from dome_agent.store import Store
from dome_agent.tray import TrayUI


def _submenu(menu: Any, text: str) -> Any:
    item = next(i for i in menu if i.text == text)
    return item.submenu


@pytest.fixture
def pystray(monkeypatch: pytest.MonkeyPatch) -> Any:
    # The dummy backend needs no display; only Menu/MenuItem are exercised here.
    monkeypatch.setenv("PYSTRAY_BACKEND", "dummy")
    return pytest.importorskip("pystray")


def test_phones_paired_after_the_menu_was_built_appear_in_it(pystray: Any, settings: Settings, store: Store) -> None:
    tray = TrayUI(settings)
    # The icon (and its menu) is created before the agent thread binds.
    menu = tray._menu()  # noqa: SLF001
    assert [i.text for i in _submenu(menu, "Paired phones")] == ["(starting…)"]

    tray.agent = SimpleNamespace(store=store)
    assert [i.text for i in _submenu(menu, "Paired phones")] == ["(no paired phones)"]

    store.add_grant(
        controller_id="0f6d2c1e-1111-4222-8333-444455556666",
        kid="kid-1",
        public_jwk={"kty": "EC"},
        capabilities=["status", "media", "volume"],
        display_name="iPhone",
    )
    phones = list(_submenu(menu, "Paired phones"))
    assert [i.text for i in phones] == ["iPhone (0f6d2c1e…)"]
    toggles = [i.text for i in phones[0].submenu]
    assert toggles[:2] == ["Allow touchpad", "Allow keyboard"]
    assert phones[0].submenu.items[0].checked is False


# ----- Add an app… / Approved apps… -------------------------------------------------------------------------------
def _item(menu: Any, text: str) -> Any:
    return next(i for i in menu if i.text == text)


def _drain(tray: TrayUI) -> list[Any]:
    posted = []
    while not tray._tk._queue.empty():  # noqa: SLF001
        posted.append(tray._tk._queue.get_nowait())  # noqa: SLF001
    return posted


def test_menu_offers_add_an_app_next_to_approved_apps(pystray: Any, settings: Settings) -> None:
    texts = [i.text for i in TrayUI(settings)._menu()]  # noqa: SLF001
    at = texts.index("Pair a phone…")
    assert texts[at + 1 : at + 3] == ["Add an app…", "Approved apps…"]


def test_app_items_open_the_window_on_the_tk_thread(pystray: Any, settings: Settings, store: Store) -> None:
    tray = TrayUI(settings)
    notes: list[str] = []
    tray.notify = lambda title, message: notes.append(message)  # type: ignore[method-assign]
    menu = tray._menu()  # noqa: SLF001

    _item(menu, "Add an app…")(None)  # agent thread not bound yet
    assert "still starting" in notes[-1] and _drain(tray) == []

    tray.agent = SimpleNamespace(store=store)
    _item(menu, "Approved apps…")(None)  # no tkinter: nothing posted would ever run
    assert "dome-agent approve-app" in notes[-1] and _drain(tray) == []

    tray._tk._root = object()  # noqa: SLF001  # the Tk thread is up
    opened: list[bool] = []
    tray._open_apps_window = lambda add=False: opened.append(add)  # type: ignore[method-assign]
    _item(menu, "Add an app…")(None)
    _item(menu, "Approved apps…")(None)
    posted = _drain(tray)
    assert len(posted) == 2 and opened == []  # the tray thread only posts; widgets are built on the Tk thread
    for fn in posted:
        fn()
    assert opened == [True, False]


class _FakeWindow:
    instances: list[_FakeWindow] = []

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.calls: list[str] = []
        self.open = True
        _FakeWindow.instances.append(self)

    def alive(self) -> bool:
        return self.open

    def refresh(self) -> None:
        self.calls.append("refresh")

    def show(self) -> None:
        self.calls.append("show")

    def add_soon(self) -> None:
        self.calls.append("add")


def test_one_apps_window_is_reused_until_closed(
    settings: Settings, store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("dome_agent.tray.ApprovedAppsWindow", _FakeWindow)
    _FakeWindow.instances.clear()
    tray = TrayUI(settings)
    tray.agent = SimpleNamespace(store=store)
    tray._tk._root = object()  # noqa: SLF001
    tray._open_apps_window()  # noqa: SLF001
    first = _FakeWindow.instances[-1]
    assert first.calls == ["show"] and first.kwargs["submit"] == tray._submit  # noqa: SLF001
    tray._open_apps_window(add=True)  # noqa: SLF001
    assert len(_FakeWindow.instances) == 1 and first.calls == ["show", "refresh", "show", "add"]
    first.kwargs["on_closed"]()
    first.open = False
    tray._open_apps_window()  # noqa: SLF001
    assert len(_FakeWindow.instances) == 2


async def _answer() -> int:
    return 42


async def _boom() -> None:
    raise RuntimeError("C:\\Users\\someone\\secret.exe")


def test_submit_without_a_loop_closes_the_coroutine(settings: Settings) -> None:
    import inspect

    tray = TrayUI(settings)
    coro = _answer()
    assert tray._submit(coro) is None  # noqa: SLF001
    assert inspect.getcoroutinestate(coro) == inspect.CORO_CLOSED  # no "never awaited" warning


def test_submit_returns_the_future_and_call_logs_failures(settings: Settings) -> None:
    import asyncio
    import threading
    import time

    import structlog.testing

    tray = TrayUI(settings)
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    try:
        tray.bind(SimpleNamespace(), loop)
        fut = tray._submit(_answer())  # noqa: SLF001
        assert fut is not None and fut.result(5) == 42
        with structlog.testing.capture_logs() as logs:
            tray._call(_boom())  # noqa: SLF001
            deadline = time.monotonic() + 5
            while not logs and time.monotonic() < deadline:
                time.sleep(0.01)
        assert [(e["event"], e["error"], e["action"]) for e in logs] == [
            ("tray action failed", "RuntimeError", "_boom")
        ]
        assert "secret" not in repr(logs)  # class name only, never the exception text
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(5)
        loop.close()
    coro = _answer()
    assert tray._submit(coro) is None  # noqa: SLF001  # closed loop (DoMe quitting)


def test_pump_reschedules_before_running_posted_functions() -> None:
    from dome_agent.tray import TkThread

    class _Root:
        def __init__(self) -> None:
            self.scheduled: list[Any] = []

        def after(self, ms: int, fn: Any) -> None:
            self.scheduled.append(fn)

    tk = TkThread()
    root = _Root()
    tk._root = root  # noqa: SLF001
    seen: list[int] = []
    tk.post(lambda: seen.append(len(root.scheduled)))  # e.g. a function that opens a modal dialog
    tk._pump()  # noqa: SLF001
    assert seen == [1] and len(root.scheduled) == 1  # the next pump was already queued


def test_tk_that_cannot_open_a_window_does_not_stall_start_up(monkeypatch: pytest.MonkeyPatch) -> None:
    """No display or desktop (tkinter.TclError from Tk()): the Tk thread says so at once and ends, instead of
    dying with the start-up wait running out (5 s) — the tray then offers the CLI, and the Tk window test
    skips with that reason."""
    import sys
    import time

    from dome_agent.tray import TkThread

    def no_display() -> Any:
        raise RuntimeError("no display name and no $DISPLAY environment variable")  # stands in for TclError

    monkeypatch.setitem(sys.modules, "tkinter", SimpleNamespace(Tk=no_display))
    tk = TkThread()
    started = time.monotonic()
    tk.start()
    assert tk.root is None and time.monotonic() - started < 2
    tk._thread.join(2)  # noqa: SLF001
    assert not tk._thread.is_alive()  # noqa: SLF001


def test_app_list_rows_show_which_apps_need_re_approval() -> None:
    from dome_agent.store import ApprovedAppRow
    from dome_agent.tray import app_list_rows

    def row(app_id: str, name: str) -> ApprovedAppRow:
        return ApprovedAppRow(app_id, name, f"C:\\Apps\\{app_id}.exe", "0" * 64, "2026-10-10T00:00:00Z")

    rows = app_list_rows([row("zed", "zed"), row("edge", "Microsoft Edge"), row("b", "Brave")], {"edge": "changed"})
    assert [(r.name, r.status) for r in rows] == [
        ("Brave", "checking"),
        ("Microsoft Edge", "changed"),
        ("zed", "checking"),
    ]
    assert rows[1].status_text == "Changed (an update?): click Re-approve…"
    assert rows[0].status_text == "Checking…" and rows[1].path == "C:\\Apps\\edge.exe"


# ----- Diagnostics -------------------------------------------------------------------------------------------------
def _status(**extra: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "agent_version": "1.2.0",
        "identity": {"pc_name": "DESK", "linked": True},
        "connection": "connected",
        "store": {"remote_enabled": True},
        "extension_connected": False,
        "grants": [],
        "input": {},
        "bridge_unavailable_reason": "",
        "control_unavailable_reason": "",
        "read_failures": {},
    }
    base.update(extra)
    return base


def test_status_text_explains_problems_in_plain_words() -> None:
    from dome_agent.tray import _status_text

    healthy = _status_text(SimpleNamespace(status=lambda: _status()))
    assert "No problems found." in healthy and "Problems DoMe found" not in healthy

    status = _status(
        bridge_unavailable_reason="the bridge pipe \\\\.\\pipe\\dome-bridge is held by another process (Access is denied.)",
        control_unavailable_reason="the control channel is not started",
        read_failures={
            "volume": {
                "code": "OS_ERROR",
                "message": "No audio endpoint",
                "failures": 3,
                "since": "2026-10-11T08:00:00Z",
            },
            "session": {"error": "OSError", "os_error": 5, "failures": 1, "since": "2026-10-11T08:01:00Z"},
            "windows": {"error": "TimeoutError", "failures": 2, "since": "2026-10-11T08:02:00Z"},
        },
    )
    text = _status_text(SimpleNamespace(status=lambda: status))
    assert "Problems DoMe found:" in text and "No problems found." not in text
    assert "YouTube: the browser extension can't connect to DoMe right now" in text
    assert "detail: the bridge pipe \\\\.\\pipe\\dome-bridge is held by another process" in text
    assert "Commands: `dome-agent` commands on this PC" in text and "detail: the control channel is not started" in text
    assert "Volume: DoMe can't read this PC's volume, so your phone can't show or change it." in text
    assert "failing since 2026-10-11T08:00:00Z (3 tries): OS_ERROR: No audio endpoint" in text
    assert "Lock screen: DoMe can't tell whether this PC is locked." in text and "OSError: OS error 5" in text
    assert "windows: DoMe can't read this right now." in text and "(2 tries): TimeoutError" in text


def test_diagnostics_item_saves_the_bundle_and_shows_the_status(
    pystray: Any, settings: Settings, store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pathlib import Path

    saved = Path("C:/DoMe/diagnostics/dome-diagnostics.json")
    outcome: dict[str, Any] = {"result": saved}

    def write_bundle(_settings: Any, _status: Any) -> Path:
        if isinstance(outcome["result"], Exception):
            raise outcome["result"]
        return saved

    monkeypatch.setattr("dome_agent.diagnostics.write_bundle", write_bundle)
    tray = TrayUI(settings)
    notes: list[str] = []
    shown: list[tuple[str, str]] = []
    tray.notify = lambda title, message: notes.append(message)  # type: ignore[method-assign]
    tray._show_text_window = lambda title, text: shown.append((title, text))  # type: ignore[method-assign]
    tray.agent = SimpleNamespace(
        store=store, status=lambda: _status(bridge_unavailable_reason="held by another process")
    )
    tray._tk._root = object()  # noqa: SLF001
    item = _item(tray._menu(), "Diagnostics…")  # noqa: SLF001

    item(None)
    for fn in _drain(tray):
        fn()
    assert notes == [str(saved)]
    title, text = shown[-1]
    assert title == "DoMe diagnostics" and "YouTube: the browser extension" in text and str(saved) in text

    outcome["result"] = OSError(28, "No space left on device")
    item(None)
    for fn in _drain(tray):
        fn()
    assert "Could not save the diagnostics file (OSError)" in notes[-1]
    assert "could not be saved" in shown[-1][1]
