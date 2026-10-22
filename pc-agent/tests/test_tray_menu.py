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


# ----- tray icon ---------------------------------------------------------------------------------------------------
def test_tray_icon_is_the_brand_tile_with_a_connection_badge(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("PIL")
    from dome_agent import tray

    monkeypatch.setattr(tray, "_base_icon_cache", [])
    assert tray.TRAY_ICON_PNG.is_file(), "brand/scripts/sync-consumers.mjs copies the brand tile here"
    connected = tray._icon_image(tray._COLOURS["connected"])  # noqa: SLF001
    offline = tray._icon_image(tray._COLOURS["offline"])  # noqa: SLF001
    assert connected.size == (256, 256) and connected.mode == "RGBA"
    assert connected.getpixel((0, 0))[3] == 0, "rounded tile corner stays transparent"
    assert connected.getpixel((83, 20)) == (1, 66, 251, 255), "DoMe Blue tile from the brand PNG"
    assert connected.getpixel((206, 75)) == (249, 96, 84, 255), "coral cursor from the brand PNG"
    # the badge: status colour in the bottom-right corner inside a white ring; the rest of the icon is unchanged
    assert connected.getpixel((201, 201))[:3] == tray._COLOURS["connected"]  # noqa: SLF001
    assert offline.getpixel((201, 201))[:3] == tray._COLOURS["offline"]  # noqa: SLF001
    assert connected.getpixel((201, 150)) == (255, 255, 255, 255)
    assert connected.crop((0, 0, 140, 256)).tobytes() == offline.crop((0, 0, 140, 256)).tobytes()


def test_tray_icon_is_drawn_when_the_png_is_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    pytest.importorskip("PIL")
    from dome_agent import tray

    monkeypatch.setattr(tray, "_base_icon_cache", [])
    monkeypatch.setattr(tray, "TRAY_ICON_PNG", tmp_path / "missing.png")
    image = tray._icon_image(tray._COLOURS["disabled"])  # noqa: SLF001
    assert image.size == (256, 256)
    assert image.getpixel((128, 6)) == (1, 66, 251, 255), "drawn DoMe Blue tile"
    assert image.getpixel((201, 201))[:3] == tray._COLOURS["disabled"]  # noqa: SLF001


# ----- Screen view (protocol 1.4) ------------------------------------------------------------------------------------
async def test_screen_view_menu_items_follow_the_session_and_the_grant(
    pystray: Any, settings: Settings, store: Store
) -> None:
    """Tray: "Stop Screen view" only while someone watches (it stops at the PC), the status line names the viewer,
    and each phone offers "Allow Screen view…" (the grant window, never a silent checkbox) until it has Screen view,
    then "Screen view settings…" and "Turn off Screen view"."""
    import asyncio

    from dome_agent.ui import ScreenSessionView, StatusView

    calls: list[tuple[str, Any]] = []

    class _Screen:
        async def stop_on_pc(self, source: str) -> bool:
            calls.append(("stop_on_pc", source))
            return True

        async def turn_off_on_pc(self, controller_id: str, source: str) -> bool:
            calls.append(("turn_off_on_pc", (controller_id, source)))
            return False

    class _Agent:
        def __init__(self) -> None:
            self.store = store
            self.screen = _Screen()

        async def request_screen_grant(self, controller_id: str) -> dict[str, Any]:
            calls.append(("request_screen_grant", controller_id))
            return {"window_opened": True}

        async def update_grant_capabilities(self, controller_id: str, **kw: Any) -> None:
            calls.append(("update_grant_capabilities", (controller_id, kw)))

    tray = TrayUI(settings)
    tray.bind(_Agent(), asyncio.get_running_loop())
    tray._tk._root = object()  # noqa: SLF001  # a display exists (no Tk call is made by these items)
    cid = "0f6d2c1e-1111-4222-8333-444455556666"
    store.add_grant(
        controller_id=cid, kid="kid-1", public_jwk={"kty": "EC"}, capabilities=["status"], display_name="iPhone"
    )
    menu = tray._menu()  # noqa: SLF001
    stop = _item(menu, "Stop Screen view")
    tray.update_status(StatusView(connection="connected", remote_enabled=True, linked=True))
    assert stop.enabled is False and tray._status_line() == "Connected"  # noqa: SLF001
    tray.update_status(
        StatusView(
            connection="connected",
            remote_enabled=True,
            linked=True,
            screen_session=ScreenSessionView(phone_name="iPhone", since="2026-10-22T10:00:00Z", audio=False),
        )
    )
    assert stop.enabled is True and tray._status_line() == "Screen view on (iPhone)"  # noqa: SLF001
    stop(None)
    phone = list(_submenu(menu, "Paired phones"))[0].submenu
    allow = next(i for i in phone if i.text == "Allow Screen view…")
    assert not next(i for i in phone.items if i.text == "Turn off Screen view").visible
    allow(None)
    for _ in range(100):
        if len(calls) == 2:
            break
        await asyncio.sleep(0.01)
    assert calls == [("stop_on_pc", "tray"), ("request_screen_grant", cid)]
    store.update_grant_capabilities(cid, ["status", "screen"])
    phone = list(_submenu(menu, "Paired phones"))[0].submenu
    assert [i.text for i in phone if i.text.startswith(("Screen view", "Turn off"))] == [
        "Screen view settings…",
        "Turn off Screen view",
    ]
    next(i for i in phone if i.text == "Turn off Screen view")(None)
    for _ in range(100):
        if len(calls) == 3:
            break
        await asyncio.sleep(0.01)
    assert calls[-1] == ("turn_off_on_pc", (cid, "tray"))  # the cooldown starts there when this phone is watching


def test_without_a_display_the_bars_fail_closed_and_no_grant_window_widens(settings: Settings) -> None:
    from dome_agent.platform.protocol import Monitor
    from dome_agent.ui import ScreenBarRequest, ScreenGrantRequest

    tray = TrayUI(settings)  # the Tk thread never started: no root, as on a PC without a desktop
    bars = tray.show_screen_bars(
        ScreenBarRequest(
            viewer_name="iPhone",
            audio=False,
            monitors=(Monitor(0, 0, 0, 1920, 1080, True),),
            on_stop=lambda _s: None,
            on_turn_off=lambda: None,
        )
    ).result(timeout=1)
    assert bars.created is False and not tray.screen_bars_present()
    answers: list[Any] = []
    tray.show_screen_grant(
        ScreenGrantRequest(
            request_id="r1",
            controller_id="c1",
            display_name="iPhone",
            screen=False,
            screen_audio=False,
            ask_each_time=False,
            cooldown_seconds=600,
            respond=answers.append,
        )
    )
    assert answers == [None]  # Cancel: widening Screen view needs a window (rules.local_permission_ui)


def test_a_bar_answer_after_the_agent_gave_up_is_dropped() -> None:
    """The agent waits 3 s for the bars and then refuses the start: a late confirmation from the Tk thread must not
    raise on the cancelled future, and tells the caller to take the orphan bars down."""
    from concurrent.futures import Future

    from dome_agent.tray import _cooldown_label, _cooldown_seconds, _resolve_bars
    from dome_agent.ui import ScreenBars

    ok: Future[ScreenBars] = Future()
    assert _resolve_bars(ok, ScreenBars(created=True)) is True and ok.result().created
    assert _resolve_bars(ok, ScreenBars(created=False)) is False  # answered once only
    gave_up: Future[ScreenBars] = Future()
    gave_up.cancel()
    assert _resolve_bars(gave_up, ScreenBars(created=True)) is False
    for seconds, label in ((600, "10 minutes"), (60, "1 minute"), (0, "off")):
        assert _cooldown_label(seconds) == label and _cooldown_seconds(label) == seconds
    assert _cooldown_label(42) == "10 minutes" and _cooldown_seconds("??") == 600  # unknown → the secure default


# ----- review round (2026-10-22): bar placement, a frozen Tk thread, a build without Screen view --------------------------
class _FakeWidget:
    """Enough of a tkinter widget for ScreenBarsController: every other call is accepted and ignored."""

    def __init__(self, *args: Any, **_kw: Any) -> None:
        self.children: list[_FakeWidget] = []
        self.after_calls: list[Any] = []
        if args and isinstance(args[0], _FakeWidget):
            args[0].children.append(self)

    def __getattr__(self, name: str) -> Any:
        return lambda *a, **k: None

    def winfo_exists(self) -> bool:
        return True

    def winfo_id(self) -> int:
        return 1

    def winfo_viewable(self) -> bool:
        return True

    def winfo_children(self) -> list[_FakeWidget]:
        return self.children

    def winfo_screenwidth(self) -> int:
        return 4800

    def winfo_screenheight(self) -> int:
        return 1620

    def after(self, ms: int, fn: Any) -> None:
        self.after_calls.append(fn)


class _FakeLabel(_FakeWidget):
    pass


def test_bar_placement_problem_is_pure_and_fails_closed() -> None:
    from dome_agent.platform.protocol import Monitor
    from dome_agent.tray import BarPlacement, bar_placement_problem

    left = Monitor(1, -2880, 0, 2880, 1620, False)  # 150 %: physical -2880..0, logical -1920..0
    on = BarPlacement((-2880, 0, 0, 54), (-2880, 0, 0, 1620))
    assert bar_placement_problem(on, left) is None
    assert bar_placement_problem(BarPlacement((-2880, 0, 0, 54), None), left) is None  # not Windows: no monitor query
    # a DPI-unaware placement: x=-2880 read as logical lands off the desktop, Windows puts it elsewhere
    assert (
        bar_placement_problem(BarPlacement((-1920, 0, 960, 36), (0, 0, 1920, 1080)), left) == "bar not on its monitor"
    )
    assert bar_placement_problem(BarPlacement((-2880, 0, 0, 54), (0, 0, 0, 0)), left) == "bar not on its monitor"
    assert bar_placement_problem(BarPlacement((-2880, 400, 0, 454), (-2880, 0, 0, 1620)), left) is not None
    assert bar_placement_problem(BarPlacement((-2880, 0, -2880, 54), None), left) == "bar has no size"


def test_bars_are_confirmed_only_on_their_monitor_and_a_frozen_tk_thread_counts_as_no_bar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Design §7 fail-closed indicator, the tray side: a bar whose real rectangle is not on its monitor is no bar
    (the start is refused), its measured rectangle is what taps are refused on, a bar that moves off its monitor or a
    Tk thread whose check has not run for 3 s makes `present()` False (capture pauses) and `ui_alive()` False (the
    worker's ticks are withheld)."""
    import sys

    from dome_agent.platform.protocol import Monitor
    from dome_agent.tray import BarPlacement, ScreenBarsController
    from dome_agent.ui import ScreenBarRequest

    monkeypatch.setitem(
        sys.modules,
        "tkinter",
        SimpleNamespace(Toplevel=_FakeWidget, Label=_FakeLabel, Menu=_FakeWidget, Menubutton=_FakeWidget),
    )
    root = _FakeWidget()
    now = [100.0]
    misplaced: dict[int, BarPlacement] = {}

    def measure(win: Any, monitor: Monitor) -> BarPlacement:
        good = BarPlacement(
            (monitor.left, monitor.top, monitor.right, monitor.top + 54),
            (monitor.left, monitor.top, monitor.right, monitor.bottom),
        )
        return misplaced.get(monitor.index, good)

    ctl = ScreenBarsController(
        SimpleNamespace(root=root, post=lambda fn: fn()),  # type: ignore[arg-type]
        on_display_change=lambda: None,
        measure=measure,
        clock=lambda: now[0],
    )
    monitors = (Monitor(0, 0, 0, 1920, 1080, True), Monitor(1, -2880, 0, 2880, 1620, False))
    request = ScreenBarRequest(
        viewer_name="iPhone", audio=False, monitors=monitors, on_stop=lambda _s: None, on_turn_off=lambda: None
    )
    misplaced[1] = BarPlacement((-1920, 0, 960, 36), (0, 0, 1920, 1080))
    bars = ctl.show(request).result(timeout=1)
    assert bars.created is False and bars.detail == "bar not on its monitor" and not ctl.present()
    del misplaced[1]
    bars = ctl.show(request).result(timeout=1)
    assert bars.created is True and bars.reserved == ((0, 0, 1920, 54), (-2880, 0, 0, 54))  # the measured rectangles
    assert ctl.present() and ctl.ui_alive()
    misplaced[1] = BarPlacement((-1920, 0, 960, 36), (0, 0, 1920, 1080))  # a scaling change moved it
    ctl._check()  # noqa: SLF001
    assert not ctl.present() and ctl.ui_alive()  # a missing bar, not a frozen thread
    del misplaced[1]
    ctl._check()  # noqa: SLF001
    assert ctl.present()
    now[0] += 3.5  # the Tk thread stopped running its checks
    assert not ctl.present() and not ctl.ui_alive()
    ctl._check()  # noqa: SLF001  # it recovers
    assert ctl.present() and ctl.ui_alive()


async def test_a_build_without_screen_view_says_so_in_the_tray(pystray: Any, settings: Settings, store: Store) -> None:
    import asyncio

    from dome_agent.screen import NOT_INCLUDED_MESSAGE

    notes: list[tuple[str, str]] = []

    class _Screen:
        media_available = False

    class _Agent:
        def __init__(self) -> None:
            self.store = store
            self.screen = _Screen()

        async def request_screen_grant(self, controller_id: str) -> dict[str, Any]:
            raise AssertionError("no grant window in a build without Screen view")

    tray = TrayUI(settings)
    tray.bind(_Agent(), asyncio.get_running_loop())
    tray._tk._root = object()  # noqa: SLF001
    tray.notify = lambda title, message: notes.append((title, message))  # type: ignore[method-assign]
    store.add_grant(
        controller_id="0f6d2c1e-1111-4222-8333-444455556666",
        kid="kid-1",
        public_jwk={"kty": "EC"},
        capabilities=["status", "pointer"],
        display_name="iPhone",
    )
    menu = tray._menu()  # noqa: SLF001
    phone = list(_submenu(menu, "Paired phones"))[0].submenu
    item = next(i for i in phone if "Screen view" in i.text)
    assert item.text == "Screen view: not in this version of DoMe" and item.enabled is False
    item(None)
    await asyncio.sleep(0.05)
    assert notes == [("DoMe", NOT_INCLUDED_MESSAGE)]
