"""Window placement, foreground and full screen (``platform/windows/window_control.py``) on Linux.

:class:`WindowControl` runs against a scripted window manager (``FakeWM``) that models the parts of
Windows the sequences depend on: the foreground lock (refused until the agent sent input, or until its
thread is attached), style bits, ``ShowWindowAsync`` placement changes (a window minimised in F11 full
screen comes back full screen, as on Windows), an app that toggles full screen on an F11 key-down, the
window's process name (the F11 list), and a monotonic clock that advances only when the code sleeps. The
real ctypes seam (:class:`Win32Windows`) is checked against stand-in DLLs: declared signatures, the
per-monitor DPI context around geometry reads, DWM cloaking and the key-state bit."""

from __future__ import annotations

import ctypes
import sys
import threading
import time
import types
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pytest
from dome_protocol import ProtocolError

from dome_agent.platform.protocol import F11NotEnabledError, WindowState, WindowStateError
from dome_agent.platform.windows import input as winput
from dome_agent.platform.windows import window_control as wc
from dome_agent.platform.windows.window_control import Rect, WindowControl, covers_monitor

WS_VISIBLE = 0x10000000
WS_THICKFRAME = 0x00040000
NORMAL_STYLE = WS_VISIBLE | wc.WS_CAPTION | WS_THICKFRAME | wc.WS_MAXIMIZEBOX
FIXED_STYLE = WS_VISIBLE | wc.WS_CAPTION  # a dialog-like window without a maximise box
MONITOR: Rect = (0, 0, 1920, 1080)
NORMAL_RECT: Rect = (100, 100, 900, 700)
MAXIMIZED_RECT: Rect = (-8, -8, 1928, 1048)  # invisible borders past the work area; the taskbar stays visible
MINIMIZED_RECT: Rect = (-32000, -32000, -31840, -31972)
APP, OTHER = 0x1_0000_0A01, 0x2B04  # APP above 32 bits: handles are pointer-sized
ME, OTHER_THREAD = 11, 77


# ----- covers_monitor (pure) ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("rect", "monitor", "covers"),
    [
        ((0, 0, 1920, 1080), MONITOR, True),  # F11 full screen at 100 %
        ((0, 0, 1919, 1079), MONITOR, True),  # 125 %: rounding leaves the window a pixel short
        ((1, 1, 1918, 1078), MONITOR, True),  # 2 px tolerance on every edge
        ((0, 0, 1917, 1080), MONITOR, False),  # 3 px short is a window, not full screen
        ((0, 3, 1920, 1080), MONITOR, False),
        (MAXIMIZED_RECT, MONITOR, False),  # maximised with the taskbar visible
        ((-8, -8, 1928, 1088), MONITOR, True),  # maximised under an auto-hidden taskbar: geometry alone covers
        ((0, 0, 3840, 2160), (0, 0, 3840, 2160), True),  # 150 % on 4K, physical (per-monitor aware) pixels
        ((0, 0, 2560, 1440), (0, 0, 3840, 2160), False),  # the DPI-virtualised rectangle an unaware read gets
        ((-2560, 0, 0, 1440), (-2560, 0, 0, 1440), True),  # secondary monitor left of the primary
        ((0, 0, 1920, 1080), (-2560, 0, 0, 1440), False),  # full screen, but on the other monitor
        (NORMAL_RECT, MONITOR, False),
        (MINIMIZED_RECT, MONITOR, False),
        ((0, 0, 1920, 1080), (0, 0, 0, 0), False),  # monitor could not be read: never full screen
    ],
)
def test_covers_monitor(rect: Rect, monitor: Rect, covers: bool) -> None:
    assert covers_monitor(rect, monitor) is covers


# ----- the scripted window manager ---------------------------------------------------------------------


@dataclass
class Win:
    style: int = NORMAL_STYLE
    rect: Rect = NORMAL_RECT
    thread: int = 50
    hung: bool = False
    elevated: bool | None = False
    supports_f11: bool = True
    ignores_show: bool = False  # the app does not react to ShowWindowAsync
    restore_to_maximized: bool = False  # minimised while maximised (WPF_RESTORETOMAXIMIZED)
    saved: tuple[int, Rect] | None = None  # placement before F11 full screen
    process: str = "msedge.exe"  # an app on the F11 list unless a test says otherwise


def describe(inp: winput.INPUT) -> tuple[Any, ...]:
    if inp.type == winput.INPUT_MOUSE:
        return ("mouse", inp.mi.dwFlags, inp.mi.dx, inp.mi.dy, inp.mi.mouseData)
    return ("key", inp.ki.wVk, inp.ki.wScan, bool(inp.ki.dwFlags & winput.KEYEVENTF_KEYUP))


F11_PRESS = [("key", wc.VK_F11, 0x57, False), ("key", wc.VK_F11, 0x57, True)]
EMPTY_MOUSE = [("mouse", 0, 0, 0, 0)]


class FakeWM:
    """A :class:`~dome_agent.platform.windows.window_control.WindowApi` with a foreground lock.

    ``fg_rule``: ``allow`` (SetForegroundWindow works), ``after_input`` (only once the agent sent input),
    ``attached`` (only while the agent's thread is attached to the foreground thread), ``never``."""

    def __init__(self, fg_rule: str = "allow") -> None:
        self.windows: dict[int, Win] = {OTHER: Win(thread=OTHER_THREAD)}
        self.fg = OTHER
        self.fg_rule = fg_rule
        self.input_seen = False
        self.attached: set[tuple[int, int]] = set()
        self.attach_ok = True
        self.raise_in_bring_to_top = False
        self.keys: set[int] = set()
        self.on_key_check: Callable[[], None] | None = None
        self.on_set_foreground: Callable[[], None] | None = None
        self.insert_limit: int | None = None
        self.now = 0.0
        self.calls: list[tuple[Any, ...]] = []

    def add(self, hwnd: int = APP, **kw: Any) -> Win:
        self.windows[hwnd] = Win(**kw)
        return self.windows[hwnd]

    def names(self) -> list[str]:
        return [c[0] for c in self.calls]

    def sent(self) -> list[list[tuple[Any, ...]]]:
        return [c[1] for c in self.calls if c[0] == "send_inputs"]

    def keys_sent(self) -> list[tuple[Any, ...]]:
        return [e for batch in self.sent() for e in batch if e[0] == "key"]

    # -- WindowApi --
    def is_hung(self, hwnd: int) -> bool:
        return self.windows[hwnd].hung

    def style(self, hwnd: int) -> int:
        return self.windows[hwnd].style

    def geometry(self, hwnd: int) -> tuple[Rect, Rect]:
        return self.windows[hwnd].rect, MONITOR

    def foreground(self) -> int:
        return self.fg

    def show_async(self, hwnd: int, command: int) -> bool:
        self.calls.append(("show_async", hwnd, command))
        w = self.windows[hwnd]
        if w.ignores_show:
            return True
        if command == wc.SW_RESTORE:
            if w.style & wc.WS_MINIMIZE:
                w.style &= ~wc.WS_MINIMIZE
                if w.saved is not None:  # minimised in F11 full screen: back to that placement
                    w.rect = MONITOR
                elif w.restore_to_maximized:
                    w.style |= wc.WS_MAXIMIZE
                    w.rect = MAXIMIZED_RECT
                else:
                    w.rect = NORMAL_RECT
            elif w.style & wc.WS_MAXIMIZE:
                w.style &= ~wc.WS_MAXIMIZE
                w.rect = NORMAL_RECT
            w.restore_to_maximized = False
        elif command == wc.SW_MAXIMIZE:
            w.style = (w.style & ~wc.WS_MINIMIZE) | wc.WS_MAXIMIZE
            w.rect = MAXIMIZED_RECT
        return True

    def set_foreground(self, hwnd: int) -> bool:
        self.calls.append(("set_foreground", hwnd))
        if self.on_set_foreground is not None:
            self.on_set_foreground()
        allowed = (
            self.fg_rule == "allow"
            or (self.fg_rule == "after_input" and self.input_seen)
            or (self.fg_rule == "attached" and bool(self.attached))
        )
        if allowed:
            self.fg = hwnd
        return allowed

    def bring_to_top(self, hwnd: int) -> bool:
        self.calls.append(("bring_to_top", hwnd))
        if self.raise_in_bring_to_top:
            raise RuntimeError("scripted failure inside the attached section")
        return True

    def send_inputs(self, inputs: list[winput.INPUT]) -> int:
        self.calls.append(("send_inputs", [describe(i) for i in inputs]))
        inserted = len(inputs) if self.insert_limit is None else min(self.insert_limit, len(inputs))
        for inp in inputs[:inserted]:
            if inp.type == winput.INPUT_MOUSE:
                self.input_seen = True
            elif inp.ki.wVk == wc.VK_F11 and not inp.ki.dwFlags & winput.KEYEVENTF_KEYUP:
                self._f11()
        return inserted

    def _f11(self) -> None:
        w = self.windows.get(self.fg)
        if w is None or not w.supports_f11:
            return
        if w.saved is None:  # Chromium: drop the caption and frame, cover the monitor
            w.saved = (w.style, w.rect)
            w.style &= ~(wc.WS_CAPTION | WS_THICKFRAME)
            w.rect = MONITOR
        else:
            w.style, w.rect = w.saved
            w.saved = None

    def window_thread(self, hwnd: int) -> int:
        return self.windows[hwnd].thread if hwnd in self.windows else 0

    def current_thread(self) -> int:
        return ME

    def attach_thread_input(self, attach: int, attach_to: int, attach_flag: bool) -> bool:
        self.calls.append(("attach", attach, attach_to, attach_flag))
        if not self.attach_ok:
            return False
        if attach_flag:
            self.attached.add((attach, attach_to))
        else:
            self.attached.discard((attach, attach_to))
        return True

    def ensure_message_queue(self) -> None:
        self.calls.append(("ensure_message_queue",))

    def key_down(self, vk: int) -> bool:
        if self.on_key_check is not None:
            self.on_key_check()
        return vk in self.keys

    def scan_code(self, vk: int) -> int:
        return 0x57 if vk == wc.VK_F11 else 0

    def elevated(self, hwnd: int) -> bool | None:
        return self.windows[hwnd].elevated

    def is_cloaked(self, hwnd: int) -> bool:
        return False

    def process_name(self, hwnd: int) -> str:
        return self.windows[hwnd].process

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def control(wm: FakeWM) -> WindowControl:
    return WindowControl(wm)


# ----- read_state ----------------------------------------------------------------------------------------


def test_read_state_needs_coverage_and_no_caption() -> None:
    wm = FakeWM()
    wm.add(style=NORMAL_STYLE | wc.WS_MAXIMIZE, rect=(-8, -8, 1928, 1088))  # auto-hidden taskbar
    state = control(wm).read_state(APP)
    assert state == WindowState(maximized=True, fullscreen=False, foreground=False, can_maximize=True)
    wm.windows[APP].style &= ~wc.WS_CAPTION  # what F11 full screen looks like
    wm.windows[APP].rect = MONITOR
    assert control(wm).read_state(APP).fullscreen is True
    wm.windows[APP].style |= wc.WS_MINIMIZE  # minimised windows are never full screen (nor "maximized")
    state = control(wm).read_state(APP, elevated=True)
    assert state.state_name() == "minimized" and not state.fullscreen and not state.maximized and state.elevated


def test_window_state_result_shape() -> None:
    from dome_protocol import load_registry

    result = WindowState(maximized=True, foreground=True).as_result("note", "4242")
    assert result == {
        "app_id": "note",
        "window_id": "4242",
        "state": "maximized",
        "fullscreen": False,
        "foreground": True,
    }
    load_registry().validate_result("app.maximize", result)


# ----- bring_to_front --------------------------------------------------------------------------------------


def test_bring_to_front_already_in_front_does_nothing() -> None:
    wm = FakeWM()
    wm.add()
    wm.fg = APP
    assert control(wm).bring_to_front(APP) is True
    assert wm.calls == []


def test_bring_to_front_plain_request_when_windows_allows_it() -> None:
    wm = FakeWM("allow")
    wm.add()
    assert control(wm).bring_to_front(APP) is True
    assert wm.calls == [("set_foreground", APP)]  # no input, no thread attachment


def test_bring_to_front_sends_one_empty_mouse_event_then_retries() -> None:
    wm = FakeWM("after_input")
    wm.add()
    assert control(wm).bring_to_front(APP) is True
    assert wm.calls == [("set_foreground", APP), ("send_inputs", EMPTY_MOUSE), ("set_foreground", APP)]
    assert wm.now == pytest.approx(wc.FOREGROUND_STEP_WAIT, abs=0.06)  # waited out the asynchronous switch once


def test_bring_to_front_attaches_to_the_foreground_thread_and_always_detaches() -> None:
    wm = FakeWM("attached")
    wm.add()
    assert control(wm).bring_to_front(APP) is True
    assert wm.names() == [
        "set_foreground",
        "send_inputs",
        "set_foreground",
        "ensure_message_queue",
        "attach",
        "bring_to_top",
        "set_foreground",
        "attach",
    ]
    attach, detach = (c for c in wm.calls if c[0] == "attach")
    assert attach == ("attach", ME, OTHER_THREAD, True) and detach == ("attach", ME, OTHER_THREAD, False)
    assert wm.attached == set()


def test_attachment_is_released_even_when_the_attached_section_fails() -> None:
    wm = FakeWM("never")
    wm.add()
    wm.raise_in_bring_to_top = True
    with pytest.raises(RuntimeError):
        control(wm).bring_to_front(APP)
    assert [c for c in wm.calls if c[0] == "attach"] == [
        ("attach", ME, OTHER_THREAD, True),
        ("attach", ME, OTHER_THREAD, False),
    ]
    assert wm.attached == set()


def test_failed_attach_is_not_detached_and_refusal_is_reported() -> None:
    wm = FakeWM("never")
    wm.add()
    wm.attach_ok = False
    assert control(wm).bring_to_front(APP) is False
    assert [c for c in wm.calls if c[0] == "attach"] == [("attach", ME, OTHER_THREAD, True)]
    total_wait = 2 * wc.FOREGROUND_STEP_WAIT + wc.FOREGROUND_WAIT
    assert wm.now == pytest.approx(total_wait, abs=0.2)  # bounded: the handler's 5 s budget is never at risk


def test_no_attachment_when_the_foreground_is_our_own_thread_or_absent() -> None:
    wm = FakeWM("never")
    wm.add()
    wm.windows[OTHER].thread = ME
    assert control(wm).bring_to_front(APP) is False
    assert "attach" not in wm.names() and wm.names()[-2:] == ["bring_to_top", "set_foreground"]
    wm = FakeWM("never")
    wm.add()
    wm.fg = 0
    assert control(wm).bring_to_front(APP) is False
    assert "attach" not in wm.names()


def test_no_attachment_to_a_hung_foreground_window() -> None:
    """Sharing a frozen app's input queue makes the focus calls wait on it: the worker would hang attached."""
    wm = FakeWM("never")
    wm.add()
    wm.windows[OTHER].hung = True
    assert control(wm).bring_to_front(APP) is False
    assert "attach" not in wm.names() and wm.names()[-2:] == ["bring_to_top", "set_foreground"]
    wm.windows[OTHER].hung = False  # the same sequence attaches to a responsive foreground thread
    wm.calls.clear()
    control(wm).bring_to_front(APP)
    assert [c for c in wm.calls if c[0] == "attach"] == [
        ("attach", ME, OTHER_THREAD, True),
        ("attach", ME, OTHER_THREAD, False),
    ]


def test_bring_to_front_restores_a_minimised_window_first() -> None:
    wm = FakeWM()
    wm.add(style=NORMAL_STYLE | wc.WS_MINIMIZE, rect=MINIMIZED_RECT, restore_to_maximized=True)
    assert control(wm).bring_to_front(APP) is True
    assert wm.calls[0] == ("show_async", APP, wc.SW_RESTORE)
    assert control(wm).read_state(APP).state_name() == "maximized"  # the user's layout is kept


def test_hung_window_is_not_brought_to_front() -> None:
    wm = FakeWM()
    wm.add(hung=True)
    assert control(wm).bring_to_front(APP) is False
    assert wm.calls == []


# ----- maximize / restore ----------------------------------------------------------------------------------


def test_maximize_reads_back_the_placement() -> None:
    wm = FakeWM()
    wm.add()
    state = control(wm).maximize(APP)
    assert state.state_name() == "maximized" and state.foreground and not state.fullscreen
    assert wm.calls[0] == ("show_async", APP, wc.SW_MAXIMIZE)
    assert wm.keys_sent() == []  # no key was pressed


def test_maximize_already_maximised_only_comes_to_front() -> None:
    wm = FakeWM()
    wm.add(style=NORMAL_STYLE | wc.WS_MAXIMIZE, rect=MAXIMIZED_RECT)
    assert control(wm).maximize(APP).state_name() == "maximized"
    assert "show_async" not in wm.names()


def test_maximize_fixed_size_window_is_unchanged_and_nothing_is_sent() -> None:
    wm = FakeWM()
    wm.add(style=FIXED_STYLE)
    with pytest.raises(WindowStateError) as ei:
        control(wm).maximize(APP)
    assert ei.value.code == "WINDOW_STATE_UNCHANGED" and ei.value.state is not None
    assert ei.value.state.state_name() == "normal" and wm.calls == []


def test_maximize_not_observed_is_unchanged_with_best_known_state() -> None:
    wm = FakeWM()
    wm.add(ignores_show=True)
    with pytest.raises(WindowStateError) as ei:
        control(wm).maximize(APP)
    assert ei.value.code == "WINDOW_STATE_UNCHANGED" and ei.value.state == WindowState(elevated=False)
    assert wm.now == pytest.approx(wc.PLACEMENT_WAIT, abs=0.06)


@pytest.mark.parametrize("action", ["maximize", "restore", "set_fullscreen"])
def test_elevated_window_is_refused_before_anything_happens(action: str) -> None:
    wm = FakeWM()
    wm.add(elevated=True)
    with pytest.raises(WindowStateError) as ei:
        if action == "set_fullscreen":
            control(wm).set_fullscreen(APP, True)
        else:
            getattr(control(wm), action)(APP)
    assert ei.value.code == "APP_ELEVATED" and ei.value.state is not None
    assert wm.calls == []


@pytest.mark.parametrize("action", ["maximize", "restore"])
def test_hung_window_is_unchanged(action: str) -> None:
    wm = FakeWM()
    wm.add(hung=True)
    with pytest.raises(WindowStateError) as ei:
        getattr(control(wm), action)(APP)
    assert ei.value.code == "WINDOW_STATE_UNCHANGED" and "not responding" in ei.value.message
    assert wm.calls == []


def test_restore_of_a_window_minimised_while_maximised_needs_two_restores() -> None:
    wm = FakeWM()
    wm.add(style=NORMAL_STYLE | wc.WS_MINIMIZE, rect=MINIMIZED_RECT, restore_to_maximized=True)
    state = control(wm).restore(APP)
    assert state.state_name() == "normal" and not state.fullscreen and state.foreground
    assert [c for c in wm.calls if c[0] == "show_async"] == [
        ("show_async", APP, wc.SW_RESTORE),  # back to maximised (the placement before minimising)
        ("show_async", APP, wc.SW_RESTORE),  # then to normal size
    ]


def test_restore_maximised_and_normal_windows() -> None:
    wm = FakeWM()
    wm.add(style=NORMAL_STYLE | wc.WS_MAXIMIZE, rect=MAXIMIZED_RECT)
    assert control(wm).restore(APP).state_name() == "normal"
    assert [c for c in wm.calls if c[0] == "show_async"] == [("show_async", APP, wc.SW_RESTORE)]
    wm.calls.clear()
    assert control(wm).restore(APP).state_name() == "normal"
    assert "show_async" not in wm.names()  # already normal: placement untouched


@pytest.mark.parametrize("action", ["maximize", "restore"])
def test_placement_actions_leave_full_screen_first(action: str) -> None:
    wm = FakeWM()
    wm.add()
    wm.fg = APP
    wm._f11()  # the app is in F11 full screen (entered from a normal window)
    state = getattr(control(wm), action)(APP)
    assert not state.fullscreen and state.state_name() == ("maximized" if action == "maximize" else "normal")
    assert wm.keys_sent() == F11_PRESS  # exactly one F11 press, to leave full screen


# ----- set_fullscreen -----------------------------------------------------------------------------------


def test_set_fullscreen_presses_f11_once_after_the_window_is_in_front() -> None:
    wm = FakeWM("after_input")
    wm.add()
    state = control(wm).set_fullscreen(APP, True)
    assert state.fullscreen and state.foreground
    assert wm.sent() == [EMPTY_MOUSE, F11_PRESS]
    assert wm.names().index("send_inputs") < wm.names().index("set_foreground", 1)  # front first, then F11
    wm.calls.clear()
    state = control(wm).set_fullscreen(APP, False)
    assert not state.fullscreen and state.state_name() == "normal" and wm.keys_sent() == F11_PRESS


def test_set_fullscreen_matching_state_presses_nothing() -> None:
    wm = FakeWM()
    wm.add()
    assert control(wm).set_fullscreen(APP, False).fullscreen is False
    wm.fg = APP
    wm._f11()
    assert control(wm).set_fullscreen(APP, True).fullscreen is True
    assert wm.calls == []  # F11 is a toggle: never pressed blind


def test_set_fullscreen_focus_denied_presses_nothing() -> None:
    wm = FakeWM("never")
    wm.add()
    with pytest.raises(WindowStateError) as ei:
        control(wm).set_fullscreen(APP, True)
    assert ei.value.code == "FOCUS_DENIED" and ei.value.state is not None and not ei.value.state.foreground
    assert wm.keys_sent() == []


@pytest.mark.parametrize("vk", wc.MODIFIER_KEYS)
def test_held_modifier_blocks_f11(vk: int) -> None:
    wm = FakeWM()
    wm.add()
    wm.keys.add(vk)
    with pytest.raises(WindowStateError) as ei:
        control(wm).set_fullscreen(APP, True)
    assert ei.value.code == "ACTION_UNAVAILABLE" and wm.keys_sent() == []


def test_foreground_rechecked_immediately_before_the_press() -> None:
    wm = FakeWM()
    wm.add()

    def steal() -> None:  # another window takes the foreground after bring_to_front succeeded
        wm.fg = OTHER

    wm.on_key_check = steal
    with pytest.raises(WindowStateError) as ei:
        control(wm).set_fullscreen(APP, True)
    assert ei.value.code == "FOCUS_DENIED" and wm.keys_sent() == []


def test_app_without_f11_is_unsupported_and_never_maximised_instead() -> None:
    wm = FakeWM()
    wm.add(supports_f11=False)
    with pytest.raises(WindowStateError) as ei:
        control(wm).set_fullscreen(APP, True)
    assert ei.value.code == "FULLSCREEN_UNSUPPORTED"
    assert ei.value.state == WindowState(foreground=True, elevated=False)  # best-known: normal, in front
    assert wm.keys_sent() == F11_PRESS and "show_async" not in wm.names()


def test_leaving_full_screen_not_observed_is_unchanged() -> None:
    wm = FakeWM()
    wm.add()
    wm.fg = APP
    wm._f11()
    wm.windows[APP].supports_f11 = False  # stuck in full screen
    with pytest.raises(WindowStateError) as ei:
        control(wm).set_fullscreen(APP, False)
    assert ei.value.code == "WINDOW_STATE_UNCHANGED" and ei.value.state is not None and ei.value.state.fullscreen


def test_partially_inserted_f11_is_released_and_reported() -> None:
    wm = FakeWM()
    wm.add()
    wm.fg = APP
    wm.insert_limit = 1
    with pytest.raises(WindowStateError) as ei:
        control(wm).set_fullscreen(APP, True)
    assert ei.value.code == "OS_ERROR" and "1 of 2" in ei.value.message
    assert wm.sent() == [F11_PRESS, [F11_PRESS[1]]]  # the key-up is sent on its own: F11 never stays down
    wm.insert_limit = 0
    with pytest.raises(WindowStateError) as ei:
        control(wm).set_fullscreen(APP, False)
    assert ei.value.code == "OS_ERROR" and "0 of 2" in ei.value.message


def test_set_fullscreen_on_a_minimised_window_restores_it_first() -> None:
    wm = FakeWM()
    wm.add(style=NORMAL_STYLE | wc.WS_MINIMIZE, rect=MINIMIZED_RECT)
    assert control(wm).set_fullscreen(APP, True).fullscreen is True
    assert wm.calls[0] == ("show_async", APP, wc.SW_RESTORE) and wm.keys_sent() == F11_PRESS


# ----- a window minimised while in full screen ---------------------------------------------------------------


def minimised_in_full_screen(wm: FakeWM) -> Win:
    """F11 full screen, then minimised (app.minimize, Win+M, the taskbar): no caption, and SW_RESTORE brings
    it back full screen. While minimised it reads as not full screen."""
    w = wm.add()
    wm.fg = APP
    wm._f11()
    w.style |= wc.WS_MINIMIZE
    w.rect = MINIMIZED_RECT
    wm.fg = OTHER
    assert control(wm).read_state(APP).fullscreen is False
    return w


def test_full_screen_again_on_a_window_minimised_in_full_screen_presses_nothing() -> None:
    wm = FakeWM()
    minimised_in_full_screen(wm)
    state = control(wm).set_fullscreen(APP, True)
    assert state.fullscreen and state.foreground and state.state_name() == "normal"
    assert wm.keys_sent() == []  # an F11 here would have taken it OUT of full screen
    assert wm.calls[0] == ("show_async", APP, wc.SW_RESTORE)


def test_leaving_full_screen_on_a_window_minimised_in_full_screen_presses_once() -> None:
    wm = FakeWM()
    w = minimised_in_full_screen(wm)
    state = control(wm).set_fullscreen(APP, False)
    assert not state.fullscreen and state.state_name() == "normal" and wm.keys_sent() == F11_PRESS
    assert w.saved is None and w.style == NORMAL_STYLE  # really out of full screen, not just shown minimised


@pytest.mark.parametrize(("action", "placement"), [("restore", "normal"), ("maximize", "maximized")])
def test_placement_of_a_window_minimised_in_full_screen_leaves_full_screen(action: str, placement: str) -> None:
    wm = FakeWM()
    minimised_in_full_screen(wm)
    state = getattr(control(wm), action)(APP)
    assert state.state_name() == placement and not state.fullscreen and wm.keys_sent() == F11_PRESS


def test_a_minimised_window_with_its_caption_is_known_not_to_be_full_screen() -> None:
    wm = FakeWM()
    wm.add(style=NORMAL_STYLE | wc.WS_MINIMIZE, rect=MINIMIZED_RECT)
    state = control(wm).set_fullscreen(APP, False)
    assert state.state_name() == "minimized" and wm.calls == []  # left alone: nothing to leave


def test_state_is_read_again_after_coming_to_the_front() -> None:
    """Whatever changed the window while it was brought to the front (here: F11 on the PC keyboard), the
    press is decided on the state read afterwards: never a blind toggle."""
    wm = FakeWM("after_input")
    wm.add()

    def pressed_on_the_pc() -> None:
        if wm.input_seen and wm.windows[APP].saved is None:
            wm.fg = APP
            wm._f11()

    wm.on_set_foreground = pressed_on_the_pc
    state = control(wm).set_fullscreen(APP, True)
    assert state.fullscreen and wm.keys_sent() == []


# ----- the F11 list ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("process", ["EXCEL.EXE", "devenv.exe", "WINWORD.EXE", "OUTLOOK.EXE", "notepad.exe", ""])
def test_f11_is_never_pressed_into_an_app_off_the_list(process: str) -> None:
    """In Excel F11 adds a chart sheet, in Visual Studio it starts debugging: nothing is pressed, nothing moves."""
    wm = FakeWM()
    wm.add(process=process)
    with pytest.raises(WindowStateError) as ei:
        control(wm).set_fullscreen(APP, True)
    assert ei.value.code == "FULLSCREEN_UNSUPPORTED" and ei.value.state == WindowState(elevated=False)
    assert isinstance(ei.value, F11NotEnabledError) and ei.value.entering  # the handler names the app
    assert wm.calls == []  # not even brought to the front


@pytest.mark.parametrize("action", ["set_fullscreen", "maximize", "restore"])
def test_full_screen_of_an_app_off_the_list_is_not_left_with_f11(action: str) -> None:
    wm = FakeWM()
    wm.add(process="EXCEL.EXE")
    wm.fg = APP
    wm._f11()  # (for this test) the app's own full screen
    with pytest.raises(WindowStateError) as ei:
        if action == "set_fullscreen":
            control(wm).set_fullscreen(APP, False)
        else:
            getattr(control(wm), action)(APP)
    assert ei.value.code == "WINDOW_STATE_UNCHANGED" and ei.value.state is not None and ei.value.state.fullscreen
    assert wm.calls == []


@pytest.mark.parametrize("process", ["msedge.exe", "Chrome.exe", "firefox.exe", "Code.exe", "WindowsTerminal.exe"])
def test_apps_on_the_list_get_their_f11(process: str) -> None:
    wm = FakeWM()
    wm.add(process=process)
    assert control(wm).set_fullscreen(APP, True).fullscreen and wm.keys_sent() == F11_PRESS


# ----- the owner's per-app opt-in ("Full screen with F11") ------------------------------------------------------


@pytest.mark.parametrize("process", ["zstream-app.exe", "vlc.exe", ""])
def test_an_app_the_owner_opted_in_gets_its_f11(process: str) -> None:
    """ZStream (the owner's own app) is not on the F11 list: with Full screen with F11 turned on, F11 is pressed
    once to enter and once to leave, with every other safeguard unchanged."""
    wm = FakeWM()
    wm.add(process=process)
    ctl = control(wm)
    assert ctl.set_fullscreen(APP, True, fullscreen_f11=True).fullscreen and wm.keys_sent() == F11_PRESS
    assert ctl.set_fullscreen(APP, True, fullscreen_f11=True).fullscreen and wm.keys_sent() == F11_PRESS
    assert not ctl.set_fullscreen(APP, False, fullscreen_f11=True).fullscreen
    assert wm.keys_sent() == F11_PRESS * 2
    wm.keys.add(wc.VK_CONTROL)  # opted in or not, never on top of a held modifier
    with pytest.raises(WindowStateError) as ei:
        ctl.set_fullscreen(APP, True, fullscreen_f11=True)
    assert ei.value.code == "ACTION_UNAVAILABLE" and wm.keys_sent() == F11_PRESS * 2


@pytest.mark.parametrize("action", ["maximize", "restore"])
def test_placement_of_an_opted_in_app_leaves_its_full_screen_with_f11(action: str) -> None:
    wm = FakeWM()
    wm.add(process="zstream-app.exe")
    wm.fg = APP
    wm._f11()  # in full screen
    with pytest.raises(F11NotEnabledError) as ei:
        getattr(control(wm), action)(APP)
    assert ei.value.code == "WINDOW_STATE_UNCHANGED" and not ei.value.entering and wm.keys_sent() == []
    state = getattr(control(wm), action)(APP, fullscreen_f11=True)
    assert not state.fullscreen and wm.keys_sent() == F11_PRESS


def test_without_the_opt_in_nothing_is_pressed_and_the_message_says_where_to_turn_it_on() -> None:
    wm = FakeWM()
    wm.add(process="zstream-app.exe")
    with pytest.raises(F11NotEnabledError) as ei:
        control(wm).set_fullscreen(APP, True, fullscreen_f11=False)
    assert ei.value.code == "FULLSCREEN_UNSUPPORTED" and wm.calls == []
    assert "Approved apps" in ei.value.message and "Full screen with F11" in ei.value.message


# ----- deadline, held keys, one sequence at a time ----------------------------------------------------------


def test_f11_is_not_pressed_once_the_deadline_has_passed() -> None:
    """The command's time ran out while Windows was slow to hand over the foreground: the worker thread
    (which asyncio cannot cancel) must not press F11 after the phone was answered."""
    wm = FakeWM("after_input")  # the foreground switch takes one 0.3 s escalation step
    wm.add()
    with pytest.raises(WindowStateError) as ei:
        control(wm).set_fullscreen(APP, True, deadline=0.2)
    assert ei.value.code == "ACTION_UNAVAILABLE" and wm.keys_sent() == []
    assert ei.value.state is not None and ei.value.state.foreground and not ei.value.state.fullscreen
    wm.calls.clear()
    with pytest.raises(WindowStateError) as ei:  # already past it: not even brought to the front
        control(wm).set_fullscreen(APP, True, deadline=wm.now)
    assert ei.value.code == "ACTION_UNAVAILABLE" and wm.calls == []
    assert control(wm).set_fullscreen(APP, True, deadline=wm.now + 5).fullscreen  # in time: pressed
    assert wm.keys_sent() == F11_PRESS


@pytest.mark.parametrize("action", ["maximize", "restore"])
def test_placement_actions_respect_the_deadline_and_held_keys_before_leaving_full_screen(action: str) -> None:
    wm = FakeWM()
    wm.add()
    wm.fg = APP
    wm._f11()
    for kwargs in ({"deadline": wm.now}, {"keys_held": True}):
        with pytest.raises(WindowStateError) as ei:
            getattr(control(wm), action)(APP, **kwargs)
        assert ei.value.code == "ACTION_UNAVAILABLE" and wm.keys_sent() == []
        assert ei.value.state is not None and ei.value.state.fullscreen
    assert "Release the keys" in ei.value.message
    wm._f11()  # not in full screen: no F11 needed, so held keys do not matter
    assert getattr(control(wm), action)(APP, keys_held=True).fullscreen is False and wm.keys_sent() == []


def test_concurrent_sequences_press_f11_once() -> None:
    """A retry after a timeout runs on another worker thread while the first is still going. Both would read
    "not full screen" and press F11, and the two presses would cancel out. The second waits, then reads."""
    wm = FakeWM()
    wm.add()
    ctl = control(wm)
    reached, release = threading.Event(), threading.Event()

    def hold_first_press() -> None:
        if threading.current_thread() is holder:
            reached.set()
            release.wait(5)

    wm.on_key_check = hold_first_press  # the first worker stops right before its press
    errors: list[BaseException] = []

    def run() -> None:
        try:
            ctl.set_fullscreen(APP, True)
        except BaseException as exc:  # noqa: BLE001 - collected for the assertion
            errors.append(exc)

    holder = threading.Thread(target=run)
    holder.start()
    assert reached.wait(5)
    second = threading.Thread(target=run)
    second.start()
    time.sleep(0.2)  # the second worker is now waiting for the first
    release.set()
    holder.join(5)
    second.join(5)
    assert errors == []
    assert wm.keys_sent() == F11_PRESS and wm.windows[APP].saved is not None  # one press: full screen


def test_a_sequence_does_not_wait_past_its_deadline_for_another() -> None:
    wm = FakeWM()
    wm.add()
    ctl = control(wm)
    taken, release = threading.Event(), threading.Event()

    def busy() -> None:
        with ctl._exclusive(None):  # noqa: SLF001
            taken.set()
            release.wait(5)

    t = threading.Thread(target=busy)
    t.start()
    assert taken.wait(5)
    try:
        with pytest.raises(WindowStateError) as ei:
            ctl.set_fullscreen(APP, True, deadline=wm.now + 0.1)
        assert ei.value.code == "ACTION_UNAVAILABLE" and "still running" in ei.value.message
        assert wm.calls == []
    finally:
        release.set()
        t.join(5)


# ----- the real ctypes seam against stand-in DLLs --------------------------------------------------------


class FakeFunction:
    def __init__(self, impl: Callable[..., Any]) -> None:
        self.impl = impl
        self.argtypes: Any = "undeclared"
        self.restype: Any = "undeclared"
        self.calls: list[tuple[Any, ...]] = []

    def __call__(self, *args: Any) -> Any:
        self.calls.append(args)
        return self.impl(*args)


class FakeDLL:
    def __init__(self, **functions: Callable[..., Any]) -> None:
        self.functions = {name: FakeFunction(impl) for name, impl in functions.items()}

    def __getattr__(self, name: str) -> FakeFunction:
        try:
            return self.functions[name]
        except KeyError:
            raise AttributeError(name) from None


def _write_rect(ref: Any, rect: Rect) -> None:
    ref._obj.left, ref._obj.top, ref._obj.right, ref._obj.bottom = rect


@pytest.fixture
def dlls(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    unaware = 0x1234  # the thread's own (DPI-unaware) context handle
    state: dict[str, Any] = {"context": unaware, "dpi": ["unaware"], "rect_ok": True, "cloak": 2, "hr": 0}
    state["key"] = -32768

    def set_dpi(context: Any) -> Any:
        previous, state["context"] = state["context"], context
        state["dpi"].append(
            {wc.DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2: "per_monitor_v2", unaware: "unaware"}[context]
        )
        return previous

    def get_rect(hwnd: Any, ref: Any) -> bool:
        state["rect_read_under"] = state["dpi"][-1]
        state["monitor_read_under"] = None
        if not state["rect_ok"]:
            return False
        _write_rect(ref, (0, 0, 2880, 1620))
        return True

    def monitor_info(monitor: Any, ref: Any) -> bool:
        assert ref._obj.cbSize == ctypes.sizeof(wc.MONITORINFO)
        state["monitor_read_under"] = state["dpi"][-1]
        _write_rect(types.SimpleNamespace(_obj=ref._obj.rcMonitor), (0, 0, 2880, 1620))
        return True

    def dwm(hwnd: Any, attribute: int, ref: Any, size: int) -> int:
        assert attribute == wc.DWMWA_CLOAKED and size == 4
        ref._obj.value = state["cloak"]
        return state["hr"]

    user32 = {
        "IsWindow": lambda hwnd: True,
        "IsHungAppWindow": lambda hwnd: False,
        "GetWindowLongW": lambda hwnd, index: -0x6A000000,  # WS_POPUP etc.: a negative LONG
        "GetWindowRect": get_rect,
        "MonitorFromWindow": lambda hwnd, flags: 0x10001,
        "GetMonitorInfoW": monitor_info,
        "GetForegroundWindow": lambda: None,
        "ShowWindowAsync": lambda hwnd, cmd: True,
        "SetForegroundWindow": lambda hwnd: False,
        "BringWindowToTop": lambda hwnd: True,
        "SendInput": lambda n, array, size: n,
        "GetWindowThreadProcessId": lambda hwnd, ref: 77,
        "AttachThreadInput": lambda a, b, flag: True,
        "PeekMessageW": lambda msg, hwnd, a, b, flags: False,
        "GetAsyncKeyState": lambda vk: state["key"],
        "MapVirtualKeyW": lambda vk, kind: 0x57,
        "SetThreadDpiAwarenessContext": set_dpi,
    }
    state["user32"] = FakeDLL(**user32)
    state["kernel32"] = FakeDLL(GetCurrentThreadId=lambda: 11)
    state["dwmapi"] = FakeDLL(DwmGetWindowAttribute=dwm)
    monkeypatch.setattr(ctypes, "WinDLL", lambda name, use_last_error=False: state[name], raising=False)
    return state


def test_every_ctypes_function_is_declared(dlls: dict[str, Any]) -> None:
    api = wc.Win32Windows()
    api._libs()  # noqa: SLF001
    for dll in ("user32", "kernel32", "dwmapi"):
        for name, fn in dlls[dll].functions.items():
            assert fn.argtypes != "undeclared" and fn.restype != "undeclared", f"{dll}.{name} is undeclared"
    assert dlls["user32"].ShowWindowAsync.argtypes == [ctypes.wintypes.HWND, ctypes.c_int]
    assert dlls["user32"].SendInput.argtypes[1]._type_ is winput.INPUT  # the shared, layout-tested INPUT
    assert dlls["dwmapi"].DwmGetWindowAttribute.restype is ctypes.c_long  # HRESULT checked, never raised


def test_geometry_is_read_per_monitor_dpi_aware_and_the_context_is_restored(dlls: dict[str, Any]) -> None:
    api = wc.Win32Windows()
    assert api.geometry(APP) == ((0, 0, 2880, 1620), (0, 0, 2880, 1620))
    assert dlls["rect_read_under"] == dlls["monitor_read_under"] == "per_monitor_v2"
    assert dlls["dpi"] == ["unaware", "per_monitor_v2", "unaware"]  # the previous context is put back
    assert dlls["user32"].GetWindowRect.calls[0][0] == APP  # 64-bit handle passed through unchanged
    dlls["rect_ok"] = False  # the window vanished: TARGET_GONE, and the thread context is still restored
    with pytest.raises(ProtocolError) as ei:
        api.geometry(APP)
    assert ei.value.code == "TARGET_GONE" and dlls["dpi"][-2:] == ["per_monitor_v2", "unaware"]


def test_geometry_without_the_dpi_api_still_reads(dlls: dict[str, Any]) -> None:
    del dlls["user32"].functions["SetThreadDpiAwarenessContext"]  # Windows 10 before 1607
    assert wc.Win32Windows().geometry(APP)[1] == (0, 0, 2880, 1620)


def test_cloaking_key_state_and_style_reads(dlls: dict[str, Any]) -> None:
    api = wc.Win32Windows()
    assert api.is_cloaked(APP) is True  # DWM_CLOAKED_SHELL (other virtual desktop)
    dlls["cloak"] = 0
    assert api.is_cloaked(APP) is False
    dlls["cloak"], dlls["hr"] = 2, -2147024809  # E_INVALIDARG: not known to be cloaked
    assert api.is_cloaked(APP) is False
    assert api.key_down(wc.VK_CONTROL) is True  # SHORT 0x8000 arrives as a negative value
    dlls["key"] = 1  # only the "pressed since the last call" bit: not down now
    assert api.key_down(wc.VK_CONTROL) is False
    assert api.style(APP) == (-0x6A000000) & 0xFFFFFFFF
    assert api.foreground() == 0 and api.current_thread() == 11 and api.scan_code(wc.VK_F11) == 0x57
    assert api.send_inputs([winput.mouse_input(0)]) == 1 and api.send_inputs([]) == 0


def test_process_name_is_read_through_psutil_and_unknown_on_failure(
    dlls: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    class Process:
        def __init__(self, pid: int) -> None:
            if pid != 4321:
                raise PermissionError(pid)

        def name(self) -> str:
            return "msedge.exe"

    monkeypatch.setitem(sys.modules, "psutil", types.SimpleNamespace(Process=Process))

    def pid_of(hwnd: Any, ref: Any) -> int:
        ref._obj.value = 4321 if hwnd == APP else 9
        return 77

    dlls["user32"].functions["GetWindowThreadProcessId"].impl = pid_of
    api = wc.Win32Windows()
    assert api.process_name(APP) == "msedge.exe"
    assert api.process_name(OTHER) == ""  # access denied: unknown, so F11 is not pressed


# ----- WindowsApps on the seam ---------------------------------------------------------------------------


def test_windows_apps_lists_only_uncloaked_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    from dome_agent.platform.windows.apps import WindowsApps

    visible_cloaked, visible = 501, 502
    win32gui = types.SimpleNamespace(
        GetForegroundWindow=lambda: visible,
        EnumWindows=lambda cb, extra: [cb(h, extra) for h in (visible_cloaked, visible)] and None,
        IsWindowVisible=lambda hwnd: True,
        GetWindow=lambda hwnd, cmd: 0,
        GetWindowLong=lambda hwnd, index: 0,
        GetWindowText=lambda hwnd: f"Window {hwnd}",
        IsIconic=lambda hwnd: False,
    )
    win32con = types.SimpleNamespace(GW_OWNER=4, GWL_EXSTYLE=-20, WS_EX_TOOLWINDOW=0x80)
    win32process = types.SimpleNamespace(GetWindowThreadProcessId=lambda hwnd: (1, 700))
    for name, module in (("win32gui", win32gui), ("win32con", win32con), ("win32process", win32process)):
        monkeypatch.setitem(sys.modules, name, module)
    apps = WindowsApps()
    wm = FakeWM()
    wm.is_cloaked = lambda hwnd: hwnd == visible_cloaked  # type: ignore[method-assign]
    apps._win = wm  # type: ignore[assignment]  # noqa: SLF001
    windows = apps.list_windows([700])
    assert [w.window_id for w in windows] == [str(visible)] and windows[0].foreground


def test_windows_apps_focus_uses_the_escalating_sequence() -> None:
    from dome_agent.platform.windows.apps import WindowsApps

    apps = WindowsApps()
    wm = FakeWM("after_input")
    wm.add()
    wm.is_window = lambda hwnd: hwnd in wm.windows  # type: ignore[attr-defined]
    apps._win = wm  # type: ignore[assignment]  # noqa: SLF001
    apps._control = WindowControl(wm)  # noqa: SLF001
    assert apps.focus(str(APP)) is True and ("send_inputs", EMPTY_MOUSE) in wm.calls
    for gone in ("12345", "not-a-handle"):
        with pytest.raises(ProtocolError) as ei:
            apps.focus(gone)
        assert ei.value.code == "TARGET_GONE"
    assert apps.window_state(str(APP)) == WindowState(foreground=True, elevated=False)
    assert apps.maximize(str(APP)).state_name() == "maximized"
    assert apps.restore(str(APP)).state_name() == "normal"
    assert apps.set_fullscreen(str(APP), True).fullscreen is True
