"""Window placement, foreground and full screen for approved apps (``app.focus``, ``app.maximize``,
``app.restore``, ``app.set_fullscreen``; version.json ``rules.window_actions``).

Two layers, so every decision runs on Linux in CI:

* :class:`Win32Windows` — the OS seam: one thin method per Win32 call, each ctypes function declared with
  explicit ``argtypes``/``restype``. ctypes rather than pywin32 because pywin32 312 has no
  ``ShowWindowAsync``, ``IsHungAppWindow``, ``SendInput``, ``SetThreadDpiAwarenessContext`` or
  ``DwmGetWindowAttribute``, and its ``SetForegroundWindow`` raises instead of returning the BOOL.
  The DLLs load on first use, on Windows only.
* :class:`WindowControl` — the sequences, written against :class:`WindowApi` and unit-tested with a
  scripted window manager (tests/test_windows_window_control.py).

Foreground (``bring_to_front``). A background tray process is normally refused by Windows' foreground
lock (the taskbar button only flashes), so the request escalates, stopping as soon as the window is in
front: restore it when minimised (``ShowWindowAsync(SW_RESTORE)``); plain ``SetForegroundWindow``; one
EMPTY mouse ``SendInput`` (no motion, no buttons: nothing visible happens, but the agent becomes the
process that received the last input event, a documented condition for the request) and retry;
``AttachThreadInput`` to the foreground window's thread around ``BringWindowToTop`` +
``SetForegroundWindow``, always detached again in ``finally`` — but never to a hung foreground window's
thread: sharing an input queue makes focus changes synchronous, so the calls would wait on the frozen app.
Then ``GetForegroundWindow`` is polled for up to 600 ms, because the switch is asynchronous. No Alt-key tap
(it can open a menu bar in either window) and no ``AllowSetForegroundWindow`` (the agent has no foreground
right to hand on).

Placement. Read back from the style bits (``WS_MINIMIZE`` / ``WS_MAXIMIZE``; pywin32 has no
``IsZoomed``). ``SW_RESTORE`` from minimised returns to the placement before it — maximised for a window
minimised while maximised — so ``restore`` issues a second ``SW_RESTORE`` until the window is at normal
size.

Full screen. Judged from geometry read under ``DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2`` (the agent
itself is DPI-unaware, so it would otherwise see virtualised rectangles of per-monitor-aware apps such as
Edge): the window covers its monitor within 2 px (:func:`covers_monitor`) AND has no caption. The caption
test keeps a maximised window under an auto-hidden taskbar from counting as full screen. A minimised window
reads as not full screen, yet Windows shows it again in the placement it had — full screen, for one
minimised while in F11 full screen — so a minimised window without a caption is shown again and read before
anything is decided. F11 is pressed exactly once, only into an app on the F11 list
(:func:`~dome_agent.platform.protocol.f11_toggles_fullscreen`) or one the PC owner turned *Full screen with
F11* on for (``fullscreen_f11``; elsewhere F11 can mean something else, such as Excel's new chart sheet, so
any other app is :class:`~dome_agent.platform.protocol.F11NotEnabledError` with nothing pressed), only when
the state read after the window came to the front still differs, only after the window was verified
foreground immediately before the press, no modifier key is held, no key is held from the phone and the
command's deadline has not passed. An app that does not change is ``FULLSCREEN_UNSUPPORTED`` — never quietly
maximised instead.

One sequence at a time. ``asyncio`` cannot cancel a worker thread: the thread of a command that timed out
keeps running, and a retry on another thread would read the same state and press F11 a second time (the
presses cancel out). Each sequence holds :class:`WindowControl`'s lock, so the next one waits and then reads
the state the previous one left.
"""

from __future__ import annotations

import contextlib
import ctypes
import threading
import time
from collections.abc import Callable, Iterator
from ctypes import wintypes
from typing import Any, Protocol

from dome_protocol import ProtocolError

from ..protocol import F11NotEnabledError, WindowState, WindowStateError, f11_toggles_fullscreen
from .input import DWORD, INPUT, LONG, key_input, mouse_input, process_is_elevated

# ----- Win32 constants -------------------------------------------------------------------------------

SW_MAXIMIZE = 3
SW_RESTORE = 9
GWL_STYLE = -16
WS_MAXIMIZEBOX = 0x00010000
WS_CAPTION = 0x00C00000  # WS_BORDER | WS_DLGFRAME
WS_MAXIMIZE = 0x01000000
WS_MINIMIZE = 0x20000000
MONITOR_DEFAULTTONEAREST = 2
DWMWA_CLOAKED = 14
PM_NOREMOVE = 0
MAPVK_VK_TO_VSC = 0
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
KEY_DOWN_BIT = 0x8000

VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12  # Alt
VK_LWIN = 0x5B
VK_RWIN = 0x5C
VK_F11 = 0x7A
MODIFIER_KEYS: tuple[int, ...] = (VK_SHIFT, VK_CONTROL, VK_MENU, VK_LWIN, VK_RWIN)

FULLSCREEN_TOLERANCE_PX = 2  # rounding at fractional scale factors can leave a full-screen window 1-2 px short
POLL_INTERVAL = 0.05
RESTORE_WAIT = 1.0  # a minimised window animating back
FOREGROUND_STEP_WAIT = 0.3  # each escalation step of bring_to_front
FOREGROUND_WAIT = 0.6  # the final foreground poll
PLACEMENT_WAIT = 1.5  # maximise/restore/F11 read-back (windows animate)
LOCK_WAIT = 3.0  # how long a sequence without a deadline (app.focus) waits for the one still running

Rect = tuple[int, int, int, int]  # left, top, right, bottom


def covers_monitor(rect: Rect, monitor: Rect, tol: int = FULLSCREEN_TOLERANCE_PX) -> bool:
    """True when the window rectangle covers the whole monitor rectangle, each edge within ``tol`` pixels.

    Extending past the monitor still covers it (a maximised window's invisible resize borders do); a
    degenerate monitor rectangle (the monitor could not be read) never counts as covered."""
    left, top, right, bottom = rect
    m_left, m_top, m_right, m_bottom = monitor
    if m_right <= m_left or m_bottom <= m_top:
        return False
    return left <= m_left + tol and top <= m_top + tol and right >= m_right - tol and bottom >= m_bottom - tol


class WindowApi(Protocol):
    """The Win32 calls :class:`WindowControl` makes (``hwnd`` values are plain ints)."""

    def is_hung(self, hwnd: int) -> bool: ...

    def style(self, hwnd: int) -> int:
        """``GetWindowLongW(GWL_STYLE)`` as an unsigned 32-bit value."""
        ...

    def geometry(self, hwnd: int) -> tuple[Rect, Rect]:
        """(window rectangle, its monitor's rectangle) in physical pixels (per-monitor DPI aware)."""
        ...

    def foreground(self) -> int:
        """``GetForegroundWindow``; 0 when there is none."""
        ...

    def show_async(self, hwnd: int, command: int) -> bool: ...

    def set_foreground(self, hwnd: int) -> bool: ...

    def bring_to_top(self, hwnd: int) -> bool: ...

    def send_inputs(self, inputs: list[INPUT]) -> int:
        """``SendInput``; the number of events Windows inserted."""
        ...

    def window_thread(self, hwnd: int) -> int: ...

    def current_thread(self) -> int: ...

    def attach_thread_input(self, attach: int, attach_to: int, attach_flag: bool) -> bool: ...

    def ensure_message_queue(self) -> None:
        """``PeekMessageW(PM_NOREMOVE)``: gives the calling worker thread a message queue (AttachThreadInput)."""
        ...

    def key_down(self, vk: int) -> bool:
        """``GetAsyncKeyState(vk) & 0x8000``: the key is down now (physical or injected)."""
        ...

    def scan_code(self, vk: int) -> int: ...

    def elevated(self, hwnd: int) -> bool | None:
        """The window's process runs at a higher integrity level than the agent; None when undeterminable."""
        ...

    def is_cloaked(self, hwnd: int) -> bool:
        """DWM cloaks windows on other virtual desktops and suspended Store frames: never targeted."""
        ...

    def process_name(self, hwnd: int) -> str:
        """The executable name of the window's process (``msedge.exe``); "" when it cannot be read."""
        ...

    def monotonic(self) -> float: ...

    def sleep(self, seconds: float) -> None: ...


class WindowControl:
    """Window sequences over a :class:`WindowApi`. Every method is synchronous and blocking (run it via
    ``asyncio.to_thread``); failures are :class:`WindowStateError` carrying the best-known state. One
    sequence runs at a time (the lock is re-entrant: a sequence calls :meth:`bring_to_front` itself).

    ``deadline`` (on the :meth:`WindowApi.monotonic` clock) is when the command's time is up: F11 is never
    pressed after it, and a sequence waits for the one still running at most until then."""

    def __init__(self, api: WindowApi) -> None:
        self.api = api
        self._lock = threading.RLock()

    @contextlib.contextmanager
    def _exclusive(self, deadline: float | None) -> Iterator[None]:
        wait = LOCK_WAIT if deadline is None else max(0.0, deadline - self.api.monotonic())
        if not self._lock.acquire(timeout=wait):
            raise WindowStateError(
                "ACTION_UNAVAILABLE", "Another window action is still running on the PC. Try again in a moment."
            )
        try:
            yield
        finally:
            self._lock.release()

    # ----- read back ----------------------------------------------------------------------------------
    def read_state(self, hwnd: int, *, elevated: bool | None = None) -> WindowState:
        style = self.api.style(hwnd)
        minimized = bool(style & WS_MINIMIZE)
        fullscreen = False
        if not minimized and (style & WS_CAPTION) != WS_CAPTION:
            rect, monitor = self.api.geometry(hwnd)
            fullscreen = covers_monitor(rect, monitor)
        return WindowState(
            minimized=minimized,
            maximized=bool(style & WS_MAXIMIZE) and not minimized,
            fullscreen=fullscreen,
            foreground=self.api.foreground() == hwnd,
            can_maximize=bool(style & WS_MAXIMIZEBOX),
            elevated=elevated,
        )

    def _wait_for(
        self, hwnd: int, done: Callable[[WindowState], bool], timeout: float, elevated: bool | None = None
    ) -> tuple[bool, WindowState]:
        deadline = self.api.monotonic() + timeout
        while True:
            state = self.read_state(hwnd, elevated=elevated)
            if done(state):
                return True, state
            if self.api.monotonic() >= deadline:
                return False, state
            self.api.sleep(POLL_INTERVAL)

    def _wait_foreground(self, hwnd: int, timeout: float) -> bool:
        deadline = self.api.monotonic() + timeout
        while True:
            if self.api.foreground() == hwnd:
                return True
            if self.api.monotonic() >= deadline:
                return False
            self.api.sleep(POLL_INTERVAL)

    def _may_hide_fullscreen(self, hwnd: int, state: WindowState) -> bool:
        """A minimised window reads as not full screen, but Windows shows it again in the placement it had:
        full screen, for one minimised while in F11 full screen. Full screen needs a window without a
        caption (:meth:`read_state`), so a minimised window that kept its caption is known not to be."""
        return state.minimized and (self.api.style(hwnd) & WS_CAPTION) != WS_CAPTION

    def _show_again(self, hwnd: int, elevated: bool | None) -> WindowState:
        """``SW_RESTORE`` a minimised window and read it once it is shown (see :meth:`_may_hide_fullscreen`)."""
        self.api.show_async(hwnd, SW_RESTORE)
        reached, state = self._wait_for(hwnd, lambda s: not s.minimized, RESTORE_WAIT, elevated)
        if not reached:
            raise WindowStateError("WINDOW_STATE_UNCHANGED", "Windows did not restore that window.", state)
        return state

    def _f11_allowed(self, hwnd: int, fullscreen_f11: bool) -> bool:
        """The owner opted the app in, or its process is on the F11 list (an unreadable name is not)."""
        return fullscreen_f11 or f11_toggles_fullscreen(self.api.process_name(hwnd))

    def _refuse(self, hwnd: int, state: WindowState, elevated: bool | None) -> None:
        """Elevated and hung windows are never changed."""
        if elevated:
            raise WindowStateError(
                "APP_ELEVATED", "That app runs as administrator, so Windows does not let DoMe change its window.", state
            )
        if self.api.is_hung(hwnd):
            raise WindowStateError("WINDOW_STATE_UNCHANGED", "That window is not responding.", state)

    # ----- foreground ---------------------------------------------------------------------------------
    def bring_to_front(self, hwnd: int, wait: float = FOREGROUND_WAIT) -> bool:
        """True iff the window is observed in front afterwards (``app.focus``; before every F11).
        ``ACTION_UNAVAILABLE`` when another window sequence is still running after :data:`LOCK_WAIT`."""
        with self._exclusive(None):
            api = self.api
            if api.is_hung(hwnd):
                return False  # a window that does not respond cannot take the foreground (Windows shows a ghost)
            if api.style(hwnd) & WS_MINIMIZE:
                api.show_async(hwnd, SW_RESTORE)
                self._wait_for(hwnd, lambda s: not s.minimized, RESTORE_WAIT)
            if api.foreground() == hwnd:
                return True
            api.set_foreground(hwnd)
            if self._wait_foreground(hwnd, FOREGROUND_STEP_WAIT):
                return True
            # Windows lets the process that received the last input event take the foreground: one empty mouse
            # event (no motion, no buttons, so nothing visible happens) makes that the agent.
            api.send_inputs([mouse_input(0)])
            api.set_foreground(hwnd)
            if self._wait_foreground(hwnd, FOREGROUND_STEP_WAIT):
                return True
            self._attached_set_foreground(hwnd)
            return self._wait_foreground(hwnd, wait)

    def _attached_set_foreground(self, hwnd: int) -> None:
        """BringWindowToTop + SetForegroundWindow while sharing the foreground thread's input state.

        Not attached to a hung foreground window's thread: a shared input queue makes the focus change
        synchronous, so the calls (and the detach in ``finally``) would wait on the frozen app for good."""
        api = self.api
        api.ensure_message_queue()
        foreground = api.foreground()
        foreground_thread = api.window_thread(foreground) if foreground and not api.is_hung(foreground) else 0
        me = api.current_thread()
        attached = bool(foreground_thread and foreground_thread != me) and api.attach_thread_input(
            me, foreground_thread, True
        )
        try:
            api.bring_to_top(hwnd)
            api.set_foreground(hwnd)
        finally:
            if attached:  # never leave the agent's input state joined to another app's thread
                api.attach_thread_input(me, foreground_thread, False)

    # ----- placement ----------------------------------------------------------------------------------
    def _prepare(
        self, hwnd: int, deadline: float | None, keys_held: bool, fullscreen_f11: bool
    ) -> tuple[WindowState, bool | None]:
        """Refuse elevated and hung windows; leave full screen first (maximise/restore act on placement) —
        with F11 only in an app on the F11 list or opted in."""
        elevated = self.api.elevated(hwnd)
        state = self.read_state(hwnd, elevated=elevated)
        self._refuse(hwnd, state, elevated)
        if self._may_hide_fullscreen(hwnd, state):
            state = self._show_again(hwnd, elevated)
        if state.fullscreen:
            if not self._f11_allowed(hwnd, fullscreen_f11):
                raise F11NotEnabledError(False, state)  # its own full screen: leave it on the PC
            state = self._press_f11(hwnd, False, elevated, deadline, keys_held)
        return state, elevated

    def maximize(
        self, hwnd: int, *, deadline: float | None = None, keys_held: bool = False, fullscreen_f11: bool = False
    ) -> WindowState:
        with self._exclusive(deadline):
            state, elevated = self._prepare(hwnd, deadline, keys_held, fullscreen_f11)
            if not state.maximized:
                if not state.can_maximize:
                    raise WindowStateError("WINDOW_STATE_UNCHANGED", "That window cannot be maximised.", state)
                self.api.show_async(hwnd, SW_MAXIMIZE)
                reached, state = self._wait_for(hwnd, lambda s: s.maximized, PLACEMENT_WAIT, elevated)
                if not reached:
                    raise WindowStateError("WINDOW_STATE_UNCHANGED", "Windows did not maximise that window.", state)
            self.bring_to_front(hwnd)  # attempted and reported (``foreground``), not required
            return self.read_state(hwnd, elevated=elevated)

    def restore(
        self, hwnd: int, *, deadline: float | None = None, keys_held: bool = False, fullscreen_f11: bool = False
    ) -> WindowState:
        with self._exclusive(deadline):
            state, elevated = self._prepare(hwnd, deadline, keys_held, fullscreen_f11)
            if state.minimized:
                self.api.show_async(hwnd, SW_RESTORE)
                reached, state = self._wait_for(hwnd, lambda s: not s.minimized, PLACEMENT_WAIT, elevated)
                if not reached:
                    raise WindowStateError("WINDOW_STATE_UNCHANGED", "Windows did not restore that window.", state)
            if state.maximized:  # maximised, or just restored from minimised back to maximised
                self.api.show_async(hwnd, SW_RESTORE)
                reached, state = self._wait_for(hwnd, lambda s: s.state_name() == "normal", PLACEMENT_WAIT, elevated)
                if not reached:
                    raise WindowStateError("WINDOW_STATE_UNCHANGED", "Windows did not resize that window.", state)
            self.bring_to_front(hwnd)
            return self.read_state(hwnd, elevated=elevated)

    # ----- full screen --------------------------------------------------------------------------------
    def set_fullscreen(
        self,
        hwnd: int,
        enabled: bool,
        *,
        deadline: float | None = None,
        keys_held: bool = False,
        fullscreen_f11: bool = False,
    ) -> WindowState:
        with self._exclusive(deadline):
            elevated = self.api.elevated(hwnd)
            state = self.read_state(hwnd, elevated=elevated)
            hidden = self._may_hide_fullscreen(hwnd, state)
            if state.fullscreen == enabled and not hidden:
                return state  # nothing to do: F11 is a toggle and is never pressed blind
            self._refuse(hwnd, state, elevated)
            if hidden:
                state = self._show_again(hwnd, elevated)
                if state.fullscreen == enabled:  # it was in full screen all along
                    self.bring_to_front(hwnd)  # attempted and reported, not required
                    return self.read_state(hwnd, elevated=elevated)
            if not self._f11_allowed(hwnd, fullscreen_f11):
                raise F11NotEnabledError(enabled, state)  # nothing pressed, the window untouched
            return self._press_f11(hwnd, enabled, elevated, deadline, keys_held)

    def _check_deadline(self, hwnd: int, elevated: bool | None, deadline: float | None) -> None:
        if deadline is not None and self.api.monotonic() >= deadline:
            raise WindowStateError(
                "ACTION_UNAVAILABLE",
                "Windows took too long to bring the window to the front, so F11 was not pressed. Try again.",
                self.read_state(hwnd, elevated=elevated),
            )

    def _press_f11(
        self, hwnd: int, enabled: bool, elevated: bool | None, deadline: float | None, keys_held: bool
    ) -> WindowState:
        if keys_held:  # F11 on top of a key held from the phone would be another shortcut (Ctrl+F11, ...)
            raise WindowStateError(
                "ACTION_UNAVAILABLE",
                "Release the keys held from the phone first.",
                self.read_state(hwnd, elevated=elevated),
            )
        self._check_deadline(hwnd, elevated, deadline)
        if not self.bring_to_front(hwnd):
            raise WindowStateError(
                "FOCUS_DENIED",
                "Windows did not let the window come to the front, so nothing was pressed.",
                self.read_state(hwnd, elevated=elevated),
            )
        state = self.read_state(hwnd, elevated=elevated)
        if state.fullscreen == enabled:
            return state  # coming to the front changed it (a minimised window shown again in full screen)
        if any(self.api.key_down(vk) for vk in MODIFIER_KEYS):
            raise WindowStateError(
                "ACTION_UNAVAILABLE",
                "A key (Shift, Ctrl, Alt or Windows) is held down on the PC, so F11 was not pressed. "
                "Release it and try again.",
                self.read_state(hwnd, elevated=elevated),
            )
        if self.api.foreground() != hwnd:  # re-checked immediately before the press
            raise WindowStateError(
                "FOCUS_DENIED",
                "Another window came to the front, so nothing was pressed.",
                self.read_state(hwnd, elevated=elevated),
            )
        self._check_deadline(hwnd, elevated, deadline)  # a worker outliving its command never presses
        scan = self.api.scan_code(VK_F11)
        inserted = self.api.send_inputs([key_input(VK_F11, up=False, scan=scan), key_input(VK_F11, up=True, scan=scan)])
        if inserted != 2:
            if inserted == 1:
                self.api.send_inputs([key_input(VK_F11, up=True, scan=scan)])  # never leave F11 logically down
            raise WindowStateError(
                "OS_ERROR",
                f"Windows accepted {inserted} of 2 F11 key events.",
                self.read_state(hwnd, elevated=elevated),
            )
        reached, state = self._wait_for(hwnd, lambda s: s.fullscreen == enabled, PLACEMENT_WAIT, elevated)
        if reached:
            return state
        if enabled:
            raise WindowStateError("FULLSCREEN_UNSUPPORTED", "That app did not switch to full screen with F11.", state)
        raise WindowStateError("WINDOW_STATE_UNCHANGED", "That app did not leave full screen with F11.", state)


# ----- the real OS seam (Windows only) ---------------------------------------------------------------


class RECT(ctypes.Structure):  # noqa: N801 - Win32 name; fixed-width fields like input.py's structures
    _fields_ = [("left", LONG), ("top", LONG), ("right", LONG), ("bottom", LONG)]


class MONITORINFO(ctypes.Structure):  # noqa: N801 - Win32 name
    _fields_ = [("cbSize", DWORD), ("rcMonitor", RECT), ("rcWork", RECT), ("dwFlags", DWORD)]


def _rect(r: RECT) -> Rect:
    return (int(r.left), int(r.top), int(r.right), int(r.bottom))


@contextlib.contextmanager
def per_monitor_dpi(user32: Any) -> Iterator[None]:
    """Run the block with the calling thread per-monitor DPI aware (v2), restoring the previous context.

    Without it a DPI-unaware agent reads virtualised rectangles of per-monitor-aware windows on scaled
    displays. Windows before 10 1607 has no ``SetThreadDpiAwarenessContext``: the block runs unchanged."""
    setter = getattr(user32, "SetThreadDpiAwarenessContext", None)
    previous = setter(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2) if setter is not None else None
    try:
        yield
    finally:
        if setter is not None and previous:
            setter(previous)


class Win32Windows:
    """:class:`WindowApi` on ``user32``/``kernel32``/``dwmapi`` through ctypes. Constructed on ``win32``
    only; the DLLs load on first use."""

    def __init__(self) -> None:
        self._dlls: tuple[Any, Any, Any] | None = None

    def _libs(self) -> tuple[Any, Any, Any]:
        if self._dlls is None:
            hwnd_t = wintypes.HWND
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            user32.IsWindow.argtypes = [hwnd_t]
            user32.IsWindow.restype = wintypes.BOOL
            user32.IsHungAppWindow.argtypes = [hwnd_t]
            user32.IsHungAppWindow.restype = wintypes.BOOL
            user32.GetWindowLongW.argtypes = [hwnd_t, ctypes.c_int]
            user32.GetWindowLongW.restype = wintypes.LONG
            user32.GetWindowRect.argtypes = [hwnd_t, ctypes.POINTER(RECT)]
            user32.GetWindowRect.restype = wintypes.BOOL
            user32.MonitorFromWindow.argtypes = [hwnd_t, wintypes.DWORD]
            user32.MonitorFromWindow.restype = wintypes.HMONITOR
            user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(MONITORINFO)]
            user32.GetMonitorInfoW.restype = wintypes.BOOL
            user32.GetForegroundWindow.argtypes = []
            user32.GetForegroundWindow.restype = hwnd_t
            user32.ShowWindowAsync.argtypes = [hwnd_t, ctypes.c_int]
            user32.ShowWindowAsync.restype = wintypes.BOOL
            user32.SetForegroundWindow.argtypes = [hwnd_t]
            user32.SetForegroundWindow.restype = wintypes.BOOL
            user32.BringWindowToTop.argtypes = [hwnd_t]
            user32.BringWindowToTop.restype = wintypes.BOOL
            user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
            user32.SendInput.restype = wintypes.UINT
            user32.GetWindowThreadProcessId.argtypes = [hwnd_t, ctypes.POINTER(wintypes.DWORD)]
            user32.GetWindowThreadProcessId.restype = wintypes.DWORD
            user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
            user32.AttachThreadInput.restype = wintypes.BOOL
            user32.PeekMessageW.argtypes = [
                ctypes.POINTER(wintypes.MSG),
                hwnd_t,
                wintypes.UINT,
                wintypes.UINT,
                wintypes.UINT,
            ]
            user32.PeekMessageW.restype = wintypes.BOOL
            user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
            user32.GetAsyncKeyState.restype = wintypes.SHORT
            user32.MapVirtualKeyW.argtypes = [wintypes.UINT, wintypes.UINT]
            user32.MapVirtualKeyW.restype = wintypes.UINT
            with contextlib.suppress(AttributeError):  # Windows 10 1607+
                user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
                user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.GetCurrentThreadId.argtypes = []
            kernel32.GetCurrentThreadId.restype = wintypes.DWORD
            dwmapi = ctypes.WinDLL("dwmapi")
            dwmapi.DwmGetWindowAttribute.argtypes = [hwnd_t, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
            dwmapi.DwmGetWindowAttribute.restype = ctypes.c_long  # HRESULT, checked here (ctypes.HRESULT raises)
            self._dlls = (user32, kernel32, dwmapi)
        return self._dlls

    def is_window(self, hwnd: int) -> bool:
        return bool(self._libs()[0].IsWindow(hwnd))

    def is_hung(self, hwnd: int) -> bool:
        return bool(self._libs()[0].IsHungAppWindow(hwnd))

    def style(self, hwnd: int) -> int:
        return int(self._libs()[0].GetWindowLongW(hwnd, GWL_STYLE)) & 0xFFFFFFFF

    def geometry(self, hwnd: int) -> tuple[Rect, Rect]:
        user32 = self._libs()[0]
        with per_monitor_dpi(user32):
            rect = RECT()
            if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                raise ProtocolError("TARGET_GONE", "That window is no longer there")
            info = MONITORINFO()
            info.cbSize = ctypes.sizeof(MONITORINFO)
            monitor = user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
            if not monitor or not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
                return _rect(rect), (0, 0, 0, 0)  # degenerate: never "covers" (not full screen)
            return _rect(rect), _rect(info.rcMonitor)

    def foreground(self) -> int:
        return int(self._libs()[0].GetForegroundWindow() or 0)

    def show_async(self, hwnd: int, command: int) -> bool:
        return bool(self._libs()[0].ShowWindowAsync(hwnd, command))

    def set_foreground(self, hwnd: int) -> bool:
        return bool(self._libs()[0].SetForegroundWindow(hwnd))

    def bring_to_top(self, hwnd: int) -> bool:
        return bool(self._libs()[0].BringWindowToTop(hwnd))

    def send_inputs(self, inputs: list[INPUT]) -> int:
        if not inputs:
            return 0
        array = (INPUT * len(inputs))(*inputs)
        return int(self._libs()[0].SendInput(len(inputs), array, ctypes.sizeof(INPUT)))

    def window_thread(self, hwnd: int) -> int:
        return int(self._libs()[0].GetWindowThreadProcessId(hwnd, None))

    def window_pid(self, hwnd: int) -> int:
        pid = wintypes.DWORD(0)
        self._libs()[0].GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return int(pid.value)

    def current_thread(self) -> int:
        return int(self._libs()[1].GetCurrentThreadId())

    def attach_thread_input(self, attach: int, attach_to: int, attach_flag: bool) -> bool:
        return bool(self._libs()[0].AttachThreadInput(attach, attach_to, attach_flag))

    def ensure_message_queue(self) -> None:
        msg = wintypes.MSG()
        self._libs()[0].PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_NOREMOVE)

    def key_down(self, vk: int) -> bool:
        return bool(int(self._libs()[0].GetAsyncKeyState(vk)) & KEY_DOWN_BIT)

    def scan_code(self, vk: int) -> int:
        return int(self._libs()[0].MapVirtualKeyW(vk, MAPVK_VK_TO_VSC)) & 0xFFFF

    def elevated(self, hwnd: int) -> bool | None:
        return process_is_elevated(self.window_pid(hwnd))

    def process_name(self, hwnd: int) -> str:
        pid = self.window_pid(hwnd)
        if pid <= 0:
            return ""
        try:
            import psutil

            return str(psutil.Process(pid).name())
        except Exception:  # noqa: BLE001 - gone / access denied: unknown, so F11 is not pressed
            return ""

    def is_cloaked(self, hwnd: int) -> bool:
        dwmapi = self._libs()[2]
        value = DWORD(0)
        result = dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(value), ctypes.sizeof(value))
        return int(result) == 0 and value.value != 0

    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)
