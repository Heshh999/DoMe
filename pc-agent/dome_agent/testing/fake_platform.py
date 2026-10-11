"""In-memory stand-ins for the Windows adapters. Selected ONLY by ``DOME_AGENT_PLATFORM=fake``.

Every call is recorded in ``calls`` so tests can assert that exactly one side effect happened. Power
actions record the request and do nothing. The agent logs a loud warning when this set is active and
reports ``platform: "development"`` in state frames.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath

from dome_protocol import ProtocolError

from ..platform.protocol import (
    NAMED_KEYS,
    SHORTCUTS,
    AppWindow,
    F11NotEnabledError,
    ForegroundApp,
    InputHoldError,
    MediaControl,
    MediaSession,
    MediaStatus,
    PlatformSet,
    VolumeState,
    WindowState,
    WindowStateError,
    f11_toggles_fullscreen,
)


@dataclass(frozen=True, slots=True)
class InputRecord:
    """One recorded injection call: ``name`` in move|button|scroll|text|key|shortcut|release."""

    name: str
    args: tuple[object, ...]
    at: float  # time.monotonic() when the fake adapter was called


@dataclass
class FakeWindowFlags:
    """Per-window placement the fake window manager keeps beyond :class:`AppWindow` (whose result shape,
    ``app_window``, is unchanged in protocol 1.2)."""

    maximized: bool = False
    fullscreen: bool = False
    can_maximize: bool = True  # WS_MAXIMIZEBOX
    elevated: bool = False  # the window's process runs as administrator


@dataclass
class FakeState:
    volume: int = 50
    muted: bool = False
    locked: bool = False
    sessions: dict[str, MediaSession] = field(default_factory=dict)
    processes: dict[str, list[int]] = field(default_factory=dict)  # exe_path → pids
    windows: dict[str, AppWindow] = field(default_factory=dict)  # window_id → window
    window_pids: dict[str, int] = field(default_factory=dict)
    window_flags: dict[str, FakeWindowFlags] = field(default_factory=dict)  # window_id → placement
    foreground: str | None = None
    refuse_focus: bool = False
    refuse_fullscreen: bool = False  # F11 does nothing in the app (no full-screen mode)
    held_modifier: bool = False  # Shift/Ctrl/Alt/Windows is down on the PC: F11 must not be pressed
    refuse_close: bool = False
    power_fail: str | None = None  # error code to raise from power calls
    power_block_seconds: float = 0.0  # make sleep/restart/shutdown block (like a real SetSuspendState)
    abort_shutdown_ok: bool = True  # what the fake OS answers to AbortSystemShutdown
    start_at_login: bool = False
    next_pid: int = 1000
    calls: list[tuple[str, tuple[object, ...]]] = field(default_factory=list)
    # ----- manual input (FakeInput) -----
    input_events: list[InputRecord] = field(default_factory=list)  # ordered, timestamped
    foreground_app: ForegroundApp | None = None  # settable "what is in front"
    input_restricted: bool = False  # settable restriction flag (elevated window in front: restricts only)
    secure_desktop: bool = False  # settable secure desktop (lock screen / UAC consent desktop): ends sessions
    input_fail_stuck: bool = False  # a failing shortcut also fails its own recovery release (InputHoldError)
    input_fail: str | None = None  # error code raised by the next injection calls (None = succeed)
    input_fail_remaining: int = 0  # how many calls fail (0 with input_fail set = every call)
    input_delay_seconds: float = 0.0  # blocking delay per injection call (backpressure tests)
    held_buttons: set[str] = field(default_factory=set)  # readable held set
    held_keys: set[str] = field(default_factory=set)

    def record(self, name: str, *args: object) -> None:
        self.calls.append((name, args))

    def count(self, name: str) -> int:
        return sum(1 for n, _ in self.calls if n == name)

    def input_names(self) -> list[str]:
        return [r.name for r in self.input_events]

    def input_count(self, name: str) -> int:
        return sum(1 for r in self.input_events if r.name == name)

    def add_session(
        self,
        session_id: str,
        status: MediaStatus = "playing",
        controls: tuple[MediaControl, ...] = ("play", "pause", "next", "previous"),
        title: str = "Untitled",
    ) -> None:
        self.sessions[session_id] = MediaSession(
            session_id=session_id,
            status=status,
            controls=controls,
            app_label=session_id.split("#")[0][:64],
            title=title,
        )

    def add_window(
        self,
        pid: int,
        window_id: str,
        title: str,
        minimized: bool = False,
        *,
        maximized: bool = False,
        fullscreen: bool = False,
        can_maximize: bool = True,
        elevated: bool = False,
    ) -> None:
        self.windows[window_id] = AppWindow(window_id=window_id, title=title, pid=pid, minimized=minimized)
        self.window_pids[window_id] = pid
        self.window_flags[window_id] = FakeWindowFlags(maximized, fullscreen, can_maximize, elevated)


class FakeVolume:
    def __init__(self, st: FakeState) -> None:
        self.st = st

    def get(self) -> VolumeState:
        return VolumeState(self.st.volume, self.st.muted)

    def set_volume(self, value: int) -> VolumeState:
        self.st.record("set_volume", value)
        self.st.volume = int(value)
        return self.get()

    def set_muted(self, muted: bool) -> VolumeState:
        self.st.record("set_muted", muted)
        self.st.muted = bool(muted)
        return self.get()


class FakeMedia:
    def __init__(self, st: FakeState) -> None:
        self.st = st

    def list_sessions(self) -> list[MediaSession]:
        return list(self.st.sessions.values())

    def get_session(self, session_id: str) -> MediaSession | None:
        return self.st.sessions.get(session_id)

    def _require(self, session_id: str) -> MediaSession:
        s = self.st.sessions.get(session_id)
        if s is None:
            raise ProtocolError("MEDIA_SESSION_GONE", "That media player is no longer available")
        return s

    def set_paused(self, session_id: str, paused: bool) -> MediaSession:
        s = self._require(session_id)
        self.st.record("media_set_paused", session_id, paused)
        new = MediaSession(s.session_id, "paused" if paused else "playing", s.controls, s.app_label, s.title, s.artist)
        self.st.sessions[session_id] = new
        return new

    def next(self, session_id: str) -> MediaSession:
        s = self._require(session_id)
        self.st.record("media_next", session_id)
        new = MediaSession(s.session_id, s.status, s.controls, s.app_label, s.title + " (next)", s.artist)
        self.st.sessions[session_id] = new
        return new

    def previous(self, session_id: str) -> MediaSession:
        s = self._require(session_id)
        self.st.record("media_previous", session_id)
        return s


class FakeApps:
    def __init__(self, st: FakeState) -> None:
        self.st = st
        self._lock = threading.RLock()  # one window sequence at a time, like WindowControl

    def running_pids(self, exe_path: str) -> list[int]:
        return list(self.st.processes.get(exe_path, []))

    def launch(self, exe_path: str) -> int:
        self.st.record("launch", exe_path)
        pid = self.st.next_pid
        self.st.next_pid += 1
        self.st.processes.setdefault(exe_path, []).append(pid)
        return pid

    def list_windows(self, pids: list[int]) -> list[AppWindow]:
        out = []
        for wid, w in self.st.windows.items():
            if w.pid in pids:
                out.append(AppWindow(w.window_id, w.title, w.pid, w.minimized, self.st.foreground == wid))
        return out

    def window_exists(self, window_id: str) -> bool:
        return window_id in self.st.windows

    def focus(self, window_id: str) -> bool:
        self.st.record("focus", window_id)
        if window_id not in self.st.windows:
            raise ProtocolError("TARGET_GONE", "That window is no longer there")
        with self._lock:
            return self._bring_to_front(window_id)

    # ----- window placement (mirrors platform.windows.window_control) -----
    def _require(self, window_id: str) -> AppWindow:
        w = self.st.windows.get(window_id)
        if w is None:
            raise ProtocolError("TARGET_GONE", "That window is no longer there")
        return w

    def _flags(self, window_id: str) -> FakeWindowFlags:
        return self.st.window_flags.setdefault(window_id, FakeWindowFlags())

    def _process_name(self, window_id: str) -> str:
        """The executable name of the window's process (from :attr:`FakeState.processes`); "" when unknown."""
        pid = self._require(window_id).pid
        for exe_path, pids in self.st.processes.items():
            if pid in pids:
                return PureWindowsPath(exe_path).name
        return ""

    def _read(self, window_id: str) -> WindowState:
        w = self._require(window_id)
        f = self._flags(window_id)
        return WindowState(
            minimized=w.minimized,
            maximized=f.maximized and not w.minimized,
            fullscreen=f.fullscreen and not w.minimized,
            foreground=self.st.foreground == window_id,
            can_maximize=f.can_maximize,
            elevated=f.elevated,
        )

    def _set_minimized(self, window_id: str, minimized: bool) -> None:
        w = self._require(window_id)
        self.st.windows[window_id] = AppWindow(w.window_id, w.title, w.pid, minimized, w.foreground)

    def _bring_to_front(self, window_id: str) -> bool:
        """Like ``WindowControl.bring_to_front``: a minimised window is restored first, even when Windows
        then refuses the foreground change (``refuse_focus``)."""
        w = self._require(window_id)
        if w.minimized:
            self._set_minimized(window_id, False)
        if self.st.refuse_focus:
            return False
        self.st.windows[window_id] = AppWindow(w.window_id, w.title, w.pid, False, True)
        self.st.foreground = window_id
        return True

    def _refuse_elevated(self, window_id: str) -> WindowState:
        state = self._read(window_id)
        if state.elevated:
            raise WindowStateError(
                "APP_ELEVATED", "That app runs as administrator, so Windows does not let DoMe change its window.", state
            )
        return state

    def _may_hide_fullscreen(self, window_id: str, state: WindowState) -> bool:
        """Like ``WindowControl._may_hide_fullscreen``: a minimised window reads as not full screen, but comes
        back in the placement it had. The fake's full-screen windows are exactly the caption-less ones."""
        return state.minimized and self._flags(window_id).fullscreen

    def _show_again(self, window_id: str) -> WindowState:
        self._set_minimized(window_id, False)  # back in its previous placement: full screen stays full screen
        return self._read(window_id)

    def _press_f11(self, window_id: str, enabled: bool, deadline: float | None, keys_held: bool) -> WindowState:
        if keys_held:
            raise WindowStateError(
                "ACTION_UNAVAILABLE", "Release the keys held from the phone first.", self._read(window_id)
            )
        self._check_deadline(window_id, deadline)
        if not self._bring_to_front(window_id):
            raise WindowStateError(
                "FOCUS_DENIED",
                "Windows did not let the window come to the front, so nothing was pressed.",
                self._read(window_id),
            )
        state = self._read(window_id)
        if state.fullscreen == enabled:
            return state  # coming to the front changed it: nothing pressed
        if self.st.held_modifier:
            raise WindowStateError(
                "ACTION_UNAVAILABLE", "A key is held down on the PC, so F11 was not pressed.", self._read(window_id)
            )
        self._check_deadline(window_id, deadline)
        self.st.record("f11", window_id)
        flags = self._flags(window_id)
        if not self.st.refuse_fullscreen:
            flags.fullscreen = not flags.fullscreen  # F11 is a toggle
        state = self._read(window_id)
        if state.fullscreen != enabled:
            if enabled:
                raise WindowStateError(
                    "FULLSCREEN_UNSUPPORTED", "That app did not switch to full screen with F11.", state
                )
            raise WindowStateError("WINDOW_STATE_UNCHANGED", "That app did not leave full screen with F11.", state)
        return state

    def _check_deadline(self, window_id: str, deadline: float | None) -> None:
        """Like ``WindowControl._check_deadline`` (``time.monotonic()``, the clock the handler's deadline uses)."""
        if deadline is not None and time.monotonic() >= deadline:
            raise WindowStateError(
                "ACTION_UNAVAILABLE", "Windows took too long, so F11 was not pressed.", self._read(window_id)
            )

    def _f11_allowed(self, window_id: str, fullscreen_f11: bool) -> bool:
        """Like ``WindowControl._f11_allowed``: opted in, or the process is on the F11 list."""
        return fullscreen_f11 or f11_toggles_fullscreen(self._process_name(window_id))

    def _prepare(self, window_id: str, deadline: float | None, keys_held: bool, fullscreen_f11: bool) -> WindowState:
        state = self._refuse_elevated(window_id)
        if self._may_hide_fullscreen(window_id, state):
            state = self._show_again(window_id)
        if state.fullscreen:
            if not self._f11_allowed(window_id, fullscreen_f11):
                raise F11NotEnabledError(False, state)
            state = self._press_f11(window_id, False, deadline, keys_held)
        return state

    def window_state(self, window_id: str) -> WindowState:
        return self._read(window_id)

    def maximize(
        self, window_id: str, *, deadline: float | None = None, keys_held: bool = False, fullscreen_f11: bool = False
    ) -> WindowState:
        self.st.record("maximize", window_id)
        with self._lock:
            state = self._prepare(window_id, deadline, keys_held, fullscreen_f11)
            if not state.maximized:
                if not state.can_maximize:
                    raise WindowStateError("WINDOW_STATE_UNCHANGED", "That window cannot be maximised.", state)
                self._set_minimized(window_id, False)
                self._flags(window_id).maximized = True
            self._bring_to_front(window_id)  # attempted, not required
            return self._read(window_id)

    def restore(
        self, window_id: str, *, deadline: float | None = None, keys_held: bool = False, fullscreen_f11: bool = False
    ) -> WindowState:
        self.st.record("restore", window_id)
        with self._lock:
            self._prepare(window_id, deadline, keys_held, fullscreen_f11)
            self._set_minimized(window_id, False)
            self._flags(window_id).maximized = False
            self._bring_to_front(window_id)
            return self._read(window_id)

    def set_fullscreen(
        self,
        window_id: str,
        enabled: bool,
        *,
        deadline: float | None = None,
        keys_held: bool = False,
        fullscreen_f11: bool = False,
    ) -> WindowState:
        self.st.record("set_fullscreen", window_id, enabled)
        with self._lock:
            state = self._read(window_id)
            hidden = self._may_hide_fullscreen(window_id, state)
            if state.fullscreen == enabled and not hidden:
                return state  # nothing pressed
            self._refuse_elevated(window_id)
            if hidden:
                state = self._show_again(window_id)
                if state.fullscreen == enabled:
                    self._bring_to_front(window_id)
                    return self._read(window_id)
            if not self._f11_allowed(window_id, fullscreen_f11):
                raise F11NotEnabledError(enabled, state)  # nothing pressed
            return self._press_f11(window_id, enabled, deadline, keys_held)

    def minimize(self, window_id: str) -> bool:
        self.st.record("minimize", window_id)
        w = self.st.windows.get(window_id)
        if w is None:
            raise ProtocolError("TARGET_GONE", "That window is no longer there")
        self.st.windows[window_id] = AppWindow(w.window_id, w.title, w.pid, True, False)
        if self.st.foreground == window_id:
            self.st.foreground = None
        return True

    def request_close(self, window_id: str) -> None:
        self.st.record("request_close", window_id)
        if window_id not in self.st.windows:
            raise ProtocolError("TARGET_GONE", "That window is no longer there")
        if not self.st.refuse_close:
            w = self.st.windows.pop(window_id)
            self.st.window_flags.pop(window_id, None)
            pid = self.st.window_pids.pop(window_id, None)
            if pid is not None and not any(x.pid == w.pid for x in self.st.windows.values()):
                for pids in self.st.processes.values():
                    if pid in pids:
                        pids.remove(pid)


class FakeSession:
    def __init__(self, st: FakeState) -> None:
        self.st = st

    def is_locked(self) -> bool:
        return self.st.locked

    def lock(self) -> None:
        self.st.record("lock")
        self.st.locked = True


class FakePower:
    def __init__(self, st: FakeState) -> None:
        self.st = st

    def _do(self, what: str) -> None:
        self.st.record(what)
        if self.st.power_fail:
            raise ProtocolError(self.st.power_fail, f"fake platform refused {what}")
        if self.st.power_block_seconds:
            time.sleep(self.st.power_block_seconds)

    def sleep(self) -> None:
        self._do("power_sleep")

    def restart(self) -> None:
        self._do("power_restart")

    def shutdown(self) -> None:
        self._do("power_shutdown")

    def abort_shutdown(self) -> bool:
        self.st.record("power_abort_shutdown")
        return self.st.abort_shutdown_ok


class FakeStartup:
    def __init__(self, st: FakeState) -> None:
        self.st = st

    def get_start_at_login(self) -> bool:
        return self.st.start_at_login

    def set_start_at_login(self, enabled: bool, command: str) -> None:
        self.st.record("start_at_login", enabled)
        self.st.start_at_login = enabled


class FakeInput:
    """Records every injection in order with timestamps, keeps the held set, honours the settable
    foreground app / restriction flag / injected failures. Never touches a real desktop."""

    def __init__(self, st: FakeState) -> None:
        self.st = st

    def _inject(self, name: str, *args: object) -> None:
        if self.st.input_delay_seconds:
            time.sleep(self.st.input_delay_seconds)
        if self.st.input_fail:
            code = self.st.input_fail
            if self.st.input_fail_remaining > 0:
                self.st.input_fail_remaining -= 1
                if self.st.input_fail_remaining == 0:
                    self.st.input_fail = None
            raise ProtocolError(code, f"fake platform refused {name}")
        self.st.input_events.append(InputRecord(name, args, time.monotonic()))

    def move(self, dx: int, dy: int) -> None:
        self._inject("move", int(dx), int(dy))

    def button(self, button: str, action: str) -> None:
        self._inject("button", button, action)
        if action == "down":
            self.st.held_buttons.add(button)
        elif action in ("up", "click", "double_click"):
            self.st.held_buttons.discard(button)

    def scroll(self, dx: int, dy: int) -> None:
        self._inject("scroll", int(dx), int(dy))

    def text(self, text: str) -> None:
        self._inject("text", text)

    def key(self, key: str) -> None:
        if key not in NAMED_KEYS:
            raise ProtocolError("MALFORMED_MESSAGE", f"unknown key {key!r}")
        self._inject("key", key)

    def shortcut(self, name: str) -> None:
        if name not in SHORTCUTS:
            raise ProtocolError("MALFORMED_MESSAGE", f"unknown shortcut {name!r}")
        try:
            self._inject("shortcut", name)
        except ProtocolError as exc:
            if self.st.input_fail_stuck:
                # models SendInput stopping after CTRL/letter down and the recovery key-up failing too
                self.st.held_keys.update({"ctrl", name})
                raise InputHoldError(exc.code, exc.message, {"ctrl", name}) from exc
            raise

    def release(self, buttons: set[str], keys: set[str]) -> int:
        self.st.input_events.append(
            InputRecord("release", (tuple(sorted(buttons)), tuple(sorted(keys))), time.monotonic())
        )
        released = 0
        for b in buttons:
            if b in self.st.held_buttons:
                self.st.held_buttons.discard(b)
            released += 1
        for k in keys:
            self.st.held_keys.discard(k)
            released += 1
        return released

    def foreground(self) -> ForegroundApp | None:
        return self.st.foreground_app

    def input_restricted(self) -> bool:
        return self.st.input_restricted or self.st.secure_desktop or self.st.locked

    def secure_desktop_active(self) -> bool:
        return self.st.secure_desktop or self.st.locked


class FakeNativeHost:
    def __init__(self, st: FakeState) -> None:
        self.st = st

    def install(self, manifest_path: Path) -> list[str]:
        self.st.record("native_host_install", str(manifest_path))
        return [f"fake:{manifest_path}"]

    def uninstall(self) -> list[str]:
        self.st.record("native_host_uninstall")
        return []


def build_fake_platform(state: FakeState | None = None) -> PlatformSet:
    st = state or FakeState()
    ps = PlatformSet(
        name="fake",
        volume=FakeVolume(st),
        media=FakeMedia(st),
        apps=FakeApps(st),
        session=FakeSession(st),
        power=FakePower(st),
        startup=FakeStartup(st),
        native_host=FakeNativeHost(st),
        input=FakeInput(st),
        notes=["FAKE platform adapters active (DOME_AGENT_PLATFORM=fake): no real PC action happens"],
    )
    return ps
