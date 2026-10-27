"""Platform adapter interfaces.

Adapters are synchronous (they wrap blocking OS calls) and are invoked by the action handlers via
``asyncio.to_thread``. Failures are reported as :class:`dome_protocol.ProtocolError` with a stable
code from ``errors.json`` so handlers can surface them verbatim.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol

from dome_protocol import ProtocolError

PlatformName = Literal["windows", "unsupported", "fake"]
WindowPlacement = Literal["normal", "minimized", "maximized"]
MediaStatus = Literal["playing", "paused", "stopped", "changing", "closed", "opened", "unknown"]
MediaControl = Literal["play", "pause", "next", "previous"]
PointerButton = Literal["left", "right", "middle"]
ButtonAction = Literal["down", "up", "click", "double_click"]
BrowserKind = Literal["chrome", "edge", "other"]

POINTER_BUTTONS: tuple[str, ...] = ("left", "right", "middle")
BUTTON_ACTIONS: tuple[str, ...] = ("down", "up", "click", "double_click")
NAMED_KEYS: tuple[str, ...] = (
    "enter",
    "tab",
    "escape",
    "backspace",
    "delete",
    "space",
    "arrow_up",
    "arrow_down",
    "arrow_left",
    "arrow_right",
    "home",
    "end",
    "page_up",
    "page_down",
    "f11",  # since 1.4 (Screen view): full screen in apps that are not approved
)
SHORTCUTS: tuple[str, ...] = ("ctrl_a", "ctrl_c", "ctrl_v", "ctrl_z", "ctrl_l")

# Executable name (lower case) → browser kind, for ``foreground_app.browser``.
BROWSER_PROCESSES: dict[str, BrowserKind] = {
    "chrome.exe": "chrome",
    "msedge.exe": "edge",
    "firefox.exe": "other",
    "brave.exe": "other",
    "opera.exe": "other",
    "vivaldi.exe": "other",
}
# What a browser's window is called instead of its title. A browser window's title is its active tab's page
# title: browsing history, and on a YouTube results page the search query (rules.search_queries).
BROWSER_WINDOW_LABELS: dict[str, str] = {
    "chrome.exe": "Google Chrome",
    "msedge.exe": "Microsoft Edge",
    "firefox.exe": "Firefox",
    "brave.exe": "Brave",
    "opera.exe": "Opera",
    "vivaldi.exe": "Vivaldi",
}
assert set(BROWSER_WINDOW_LABELS) == set(BROWSER_PROCESSES)
# Executables (lower case) known to use F11 for their own full screen. F11 is pressed into no other app unless
# the PC owner turned on "Full screen with F11" for it (ApprovedAppRow.fullscreen_f11): elsewhere it can mean
# something else (Excel: a new chart sheet from the selection; Visual Studio: Step Into, which starts debugging;
# Word: next field; Outlook: find a contact), so those apps are FULLSCREEN_UNSUPPORTED with nothing pressed
# (version.json rules.window_actions).
F11_FULLSCREEN_PROCESSES: frozenset[str] = frozenset(
    {
        *BROWSER_PROCESSES,
        "explorer.exe",  # File Explorer
        "code.exe",  # Visual Studio Code
        "windowsterminal.exe",
        "notepad++.exe",
        "sublime_text.exe",
    }
)


def browser_for_process(process_name: str) -> BrowserKind | None:
    return BROWSER_PROCESSES.get(process_name.lower())


def browser_window_label(process_name: str) -> str | None:
    """The fixed name shown for a browser's window in place of its title; None for any other app."""
    return BROWSER_WINDOW_LABELS.get(process_name.lower())


def f11_toggles_fullscreen(process_name: str) -> bool:
    """Whether F11 is this app's own full-screen toggle (:data:`F11_FULLSCREEN_PROCESSES`)."""
    return process_name.lower() in F11_FULLSCREEN_PROCESSES


def f11_not_enabled_message(app_name: str, *, entering: bool) -> str:
    """What the phone is told when F11 was not pressed because *Full screen with F11* is off for the app: where
    to turn it on, in the tray's own words. ``app_name`` is the approved display name (never a window title)."""
    path = f"DoMe tray > Approved apps > {app_name} > Full screen with F11"
    if entering:
        return f"Full screen is not turned on for {app_name}. On the PC: {path}. Or use Maximise."
    return (
        f"{app_name} is in its own full screen, and Full screen with F11 is not turned on for it. Leave full "
        f"screen on the PC, or turn it on: {path}."
    )


@dataclass(frozen=True, slots=True)
class VolumeState:
    value: int  # 0..100
    muted: bool

    def as_result(self) -> dict[str, object]:
        return {"value": int(self.value), "muted": bool(self.muted)}


@dataclass(frozen=True, slots=True)
class MediaSession:
    session_id: str
    status: MediaStatus
    controls: tuple[MediaControl, ...]
    app_label: str = ""
    title: str = ""  # untrusted display data; never logged
    artist: str = ""

    def as_result(self) -> dict[str, object]:
        out: dict[str, object] = {
            "session_id": self.session_id[:256],
            "status": self.status,
            "controls": list(self.controls),
        }
        if self.app_label:
            out["app_label"] = self.app_label[:64]
        if self.title:
            out["title"] = self.title[:200]
        if self.artist:
            out["artist"] = self.artist[:200]
        return out


@dataclass(frozen=True, slots=True)
class AppWindow:
    window_id: str  # ^[A-Za-z0-9_-]+$ (decimal HWND on Windows)
    title: str  # untrusted display data
    pid: int
    minimized: bool = False
    foreground: bool = False

    def as_result(self) -> dict[str, object]:
        return {
            "window_id": self.window_id,
            "title": self.title[:200],
            "minimized": self.minimized,
            "foreground": self.foreground,
        }


@dataclass(frozen=True, slots=True)
class WindowState:
    """A window's placement as read back from the OS (``results#/$defs/app_window_state_result``).

    ``fullscreen`` is judged from DPI-aware geometry (the window covers its monitor and has no caption),
    never assumed from a key press. ``can_maximize`` (``WS_MAXIMIZEBOX``) and ``elevated`` (the window's
    process runs at a higher integrity level than the agent; None when undeterminable) decide errors and
    are never sent. Deliberately NOT part of :class:`AppWindow`: ``app.list`` results must still validate
    on 1.1 phones (``app_window`` is unchanged in 1.2)."""

    minimized: bool = False
    maximized: bool = False
    fullscreen: bool = False
    foreground: bool = False
    can_maximize: bool = True
    elevated: bool | None = None

    def state_name(self) -> WindowPlacement:
        if self.minimized:
            return "minimized"
        if self.maximized:
            return "maximized"
        return "normal"

    def as_result(self, app_id: str, window_id: str) -> dict[str, object]:
        return {
            "app_id": app_id,
            "window_id": window_id,
            "state": self.state_name(),
            "fullscreen": bool(self.fullscreen),
            "foreground": bool(self.foreground),
        }


class WindowStateError(ProtocolError):
    """A window action failed; ``state`` is the best-known placement read back afterwards (None when the
    window could not be read), so the handler can report it with the failure (``rules.window_actions``)."""

    def __init__(self, code: str, message: str, state: WindowState | None = None) -> None:
        super().__init__(code, message)
        self.state = state


class F11NotEnabledError(WindowStateError):
    """F11 was needed but not pressed: the app is not on the F11 list and its *Full screen with F11* is off.
    ``FULLSCREEN_UNSUPPORTED`` when entering full screen, ``WINDOW_STATE_UNCHANGED`` when the app is in its own
    full screen; nothing was pressed and the window is untouched. The handler words the message with the app's
    approved name (:func:`f11_not_enabled_message`); the adapter's own message is the nameless fallback."""

    def __init__(self, entering: bool, state: WindowState | None = None) -> None:
        super().__init__(
            "FULLSCREEN_UNSUPPORTED" if entering else "WINDOW_STATE_UNCHANGED",
            f11_not_enabled_message("the app", entering=entering),
            state,
        )
        self.entering = entering


@dataclass(frozen=True, slots=True)
class ForegroundApp:
    """What the PC has in front, as far as the agent can observe (``relay-frames#/$defs/foreground_app``).

    ``window_id``/``pid`` identify the window for the input session's target-change check and are never
    sent; ``window_title`` is untrusted display data and is never logged, and a browser's is never sent
    (its active tab's page title: browsing history, and a YouTube results page's title is the search query;
    the phone names the browser from ``browser``). ``elevated`` is None when the agent could not determine
    the foreground process's integrity level (reported as unknown, never guessed). Field-level focus inside
    the window is not observable and is never claimed."""

    process_name: str
    window_title: str = ""
    browser: BrowserKind | None = None
    elevated: bool | None = None
    window_id: str = ""
    pid: int = 0

    @property
    def identity(self) -> tuple[str, int]:
        return (self.window_id, self.pid)

    def as_result(self) -> dict[str, object]:
        out: dict[str, object] = {"process_name": self.process_name[:64]}
        if self.window_title and self.browser is None:
            out["window_title"] = self.window_title[:200]
        if self.browser is not None:
            out["browser"] = self.browser
        if self.elevated is not None:
            out["elevated"] = bool(self.elevated)
        return out


@dataclass(frozen=True, slots=True)
class Monitor:
    """One monitor of the virtual desktop in PHYSICAL pixels (Screen view, design §5.2/§8.2). ``index`` is the
    agent's own stable numbering (the primary monitor first, then by position), the ``surface_index`` of
    ``screen_session_result.surfaces``. ``rotation`` is the display orientation in degrees. No names."""

    index: int
    left: int
    top: int
    width: int
    height: int
    primary: bool
    rotation: int = 0

    @property
    def right(self) -> int:
        return self.left + self.width

    @property
    def bottom(self) -> int:
        return self.top + self.height

    def as_surface(self) -> dict[str, object]:
        """``results#/$defs/screen_surface``."""
        return {"index": self.index, "width": self.width, "height": self.height, "primary": self.primary}


@dataclass(frozen=True, slots=True)
class DuplicationProbe:
    """What the Screen view pre-flight found out about Desktop Duplication for one monitor, BEFORE a start answers
    (``platform/windows/dxgi.py``; the detail is decided by ``screen.preflight``). ``hresult`` is the raw result of
    ``IDXGIOutput5::DuplicateOutput1`` (or ``IDXGIOutput1::DuplicateOutput``) on the output the monitor maps to, as an
    unsigned 32-bit number (0 = S_OK), or of ``D3D11CreateDevice`` when ``stage`` is ``device``; None when the probe
    could not run (no DXGI, the monitor is not on the default adapter, an older Windows): the start then proceeds as
    without a pre-flight. ``remote_session`` is ``GetSystemMetrics(SM_REMOTESESSION) != 0``. ``adapter`` is the default
    adapter's description (hardware, no identifiers), ``output_index`` the output's index on it. ``reason`` is a short,
    content-free explanation for the log."""

    hresult: int | None
    remote_session: bool = False
    stage: str = ""  # device | output | duplicate
    adapter: str = ""
    output_index: int | None = None
    reason: str = ""


class ScreenAdapter(Protocol):
    """Monitor enumeration for Screen view (design §15: ``platform/windows/screen.py``). Synchronous."""

    def monitors(self) -> list[Monitor]:
        """Every monitor in physical pixels, primary first; ``PLATFORM_UNSUPPORTED`` where none can be read."""
        ...

    def duplication_probe(self, monitor: Monitor) -> DuplicationProbe:
        """Whether Desktop Duplication of ``monitor`` would start right now (no frame is captured; the duplication is
        released at once). Never raises: a probe that cannot run answers ``hresult=None``."""
        ...


class NoScreen:
    """The default when a platform set names no screen adapter: nothing can be enumerated, so Screen view
    fails closed (``SCREEN_CAPTURE_UNAVAILABLE``) rather than guessing a monitor."""

    def monitors(self) -> list[Monitor]:
        raise ProtocolError("PLATFORM_UNSUPPORTED", "screen enumeration is only available on Windows")

    def duplication_probe(self, monitor: Monitor) -> DuplicationProbe:
        return DuplicationProbe(None, reason="no screen adapter")


class InputAdapter(Protocol):
    """Manual pointer/keyboard injection (spec §10A). Every method is synchronous and blocking.

    Failures are ``ProtocolError`` with ``INPUT_INJECTION_FAILED`` (the OS accepted fewer events than
    requested), ``INPUT_RESTRICTED`` (access denied while the adapter can see a protected desktop /
    locked session / elevated foreground) or ``PLATFORM_UNSUPPORTED``. The adapter never claims UIPI
    from a return value alone."""

    def move(self, dx: int, dy: int) -> None:
        """Relative cursor motion in desktop pixels (never clamped to one display)."""
        ...

    def move_to(self, x: int, y: int) -> None:
        """Absolute cursor position in PHYSICAL virtual-desktop pixels (Screen view ``pointer_move_to`` after the
        agent mapped the normalised position onto the streamed monitor, design §8.2): ``SetCursorPos`` inside
        per-monitor DPI awareness, exact pixels, no pointer acceleration."""
        ...

    def wheel(self, dx: int, dy: int) -> None:
        """Raw wheel units (120 = one notch; ``pointer_wheel``), ``dy > 0`` scrolls content up, ``dx > 0`` right."""
        ...

    def cursor_position(self) -> tuple[int, int] | None:
        """The cursor in physical virtual-desktop pixels, or None when unknown (rules.local_permission_ui: a
        press that would land on DoMe's own bars or windows is dropped)."""
        ...

    def button(self, button: str, action: str) -> None:
        """``left|right|middle`` × ``down|up|click|double_click``."""
        ...

    def scroll(self, dx: int, dy: int) -> None:
        """Wheel notches; ``dy > 0`` scrolls content up (wheel away from the user), ``dx > 0`` right."""
        ...

    def text(self, text: str) -> None:
        """Literal Unicode text, one key down/up per UTF-16 code unit, surrogate pairs kept together."""
        ...

    def key(self, key: str) -> None:
        """One press-and-release of a named key (``NAMED_KEYS``)."""
        ...

    def shortcut(self, name: str) -> None:
        """A tested CTRL shortcut (``SHORTCUTS``); the modifier is always released afterwards."""
        ...

    def release(self, buttons: set[str], keys: set[str]) -> int:
        """Release exactly these buttons/keys (the ones this agent injected); returns how many were released."""
        ...

    def foreground(self) -> ForegroundApp | None:
        """The foreground window's process/title/browser/elevation, or None when unknown."""
        ...

    def input_restricted(self) -> bool:
        """True when injection is known to be refused: locked session, secure desktop, elevated foreground."""
        ...

    def secure_desktop_active(self) -> bool:
        """True only when the input desktop is a protected one (lock screen, sign-in, UAC consent) — the
        session-ending case. An elevated window on the normal desktop is NOT a secure desktop: it only
        restricts (``input_restricted``), the customer can click elsewhere."""
        ...


class InputHoldError(ProtocolError):
    """An injection failed AND the adapter's own recovery release failed too: ``stuck_keys`` (held-key
    names as the session tracks them, e.g. ``ctrl`` and a shortcut name for its letter key) may still be
    down. The session keeps them tracked so the end-of-session release and crash recovery retry them."""

    def __init__(self, code: str, message: str, stuck_keys: frozenset[str] | set[str]) -> None:
        super().__init__(code, message)
        self.stuck_keys = frozenset(stuck_keys)


class VolumeAdapter(Protocol):
    def get(self) -> VolumeState: ...

    def set_volume(self, value: int) -> VolumeState:
        """Set the master volume and return the state read back from the OS."""
        ...

    def set_muted(self, muted: bool) -> VolumeState: ...


class MediaAdapter(Protocol):
    def list_sessions(self) -> list[MediaSession]: ...

    def get_session(self, session_id: str) -> MediaSession | None: ...

    def set_paused(self, session_id: str, paused: bool) -> MediaSession: ...

    def next(self, session_id: str) -> MediaSession: ...

    def previous(self, session_id: str) -> MediaSession: ...


class AppsAdapter(Protocol):
    def running_pids(self, exe_path: str) -> list[int]:
        """PIDs whose executable path equals ``exe_path`` (case-insensitive, normalised)."""
        ...

    def launch(self, exe_path: str) -> int:
        """Start ``exe_path`` with NO arguments and NO shell; return the new pid."""
        ...

    def list_windows(self, pids: list[int]) -> list[AppWindow]: ...

    def window_exists(self, window_id: str) -> bool: ...

    def focus(self, window_id: str) -> bool:
        """Ask Windows to bring the window to the foreground; True iff it is the foreground window afterwards."""
        ...

    def minimize(self, window_id: str) -> bool: ...

    def request_close(self, window_id: str) -> None:
        """Post WM_CLOSE (graceful). Never terminates the process."""
        ...

    # The window actions below never start an app and raise :class:`WindowStateError` (carrying the
    # best-known state when the window could be read) or plain ``TARGET_GONE`` / ``PLATFORM_UNSUPPORTED``.
    # One window sequence runs at a time (a worker thread outlives a timed-out command), and the next one
    # reads the state the previous one left. F11 is pressed only into an app on the F11 list
    # (:func:`f11_toggles_fullscreen`) or one the PC owner opted in (``fullscreen_f11``: the approval's
    # *Full screen with F11*, passed by the handler; otherwise :class:`F11NotEnabledError`, nothing pressed),
    # never once ``deadline`` (``time.monotonic()``) has passed — the command's time is up, so a worker
    # still running cannot press it after the phone was answered — and never while ``keys_held`` (keys held
    # down from the phone's input session would turn F11 into another shortcut): ``ACTION_UNAVAILABLE``,
    # nothing pressed.

    def window_state(self, window_id: str) -> WindowState:
        """The window's placement read back now. ``TARGET_GONE`` when the window no longer exists."""
        ...

    def maximize(
        self, window_id: str, *, deadline: float | None = None, keys_held: bool = False, fullscreen_f11: bool = False
    ) -> WindowState:
        """Maximise (restoring a minimised window, leaving full screen first); the state read back.

        ``APP_ELEVATED``, ``WINDOW_STATE_UNCHANGED`` (hung, no maximise box, not observed, or a full-screen
        window of an app F11 may not be pressed in), plus the :meth:`set_fullscreen` errors when full screen
        must be left first."""
        ...

    def restore(
        self, window_id: str, *, deadline: float | None = None, keys_held: bool = False, fullscreen_f11: bool = False
    ) -> WindowState:
        """Normal placement: neither minimised nor maximised (full screen is left first); the state read
        back. ``APP_ELEVATED``, ``WINDOW_STATE_UNCHANGED``, plus the :meth:`set_fullscreen` errors."""
        ...

    def set_fullscreen(
        self,
        window_id: str,
        enabled: bool,
        *,
        deadline: float | None = None,
        keys_held: bool = False,
        fullscreen_f11: bool = False,
    ) -> WindowState:
        """Press F11 exactly once into the window when its observed full-screen state differs from
        ``enabled`` (nothing is pressed when it already matches; a minimised window is shown again first
        when it may be hiding a full-screen placement), only after the window was verified foreground right
        before the press and no modifier key is held.

        ``APP_ELEVATED``, ``FOCUS_DENIED`` / ``ACTION_UNAVAILABLE`` (nothing pressed), ``OS_ERROR`` (Windows
        did not accept the key press), ``FULLSCREEN_UNSUPPORTED`` (an app off the F11 list and not opted in:
        :class:`F11NotEnabledError`, nothing pressed; or entering not observed: never replaced by a maximise)
        and ``WINDOW_STATE_UNCHANGED`` (leaving not observed or impossible, or the window is hung)."""
        ...


class SessionAdapter(Protocol):
    def is_locked(self) -> bool: ...

    def lock(self) -> None: ...


class PowerAdapter(Protocol):
    def sleep(self) -> None: ...

    def restart(self) -> None: ...

    def shutdown(self) -> None: ...

    def abort_shutdown(self) -> bool:
        """Ask the OS to abort a restart/shutdown it already accepted; True iff it was aborted."""
        ...


class StartupAdapter(Protocol):
    def get_start_at_login(self) -> bool: ...

    def set_start_at_login(self, enabled: bool, command: str) -> None: ...


class NativeHostRegistrar(Protocol):
    def install(self, manifest_path: Path) -> list[str]:
        """Register the native-messaging manifest for Chrome and Edge; returns the registry locations written."""
        ...

    def uninstall(self) -> list[str]: ...


@dataclass(slots=True)
class PlatformSet:
    name: PlatformName
    volume: VolumeAdapter
    media: MediaAdapter
    apps: AppsAdapter
    session: SessionAdapter
    power: PowerAdapter
    startup: StartupAdapter
    native_host: NativeHostRegistrar
    input: InputAdapter
    notes: list[str] = field(default_factory=list)
    screen: ScreenAdapter = field(default_factory=NoScreen)  # Screen view monitor enumeration (1.4)

    @property
    def supports_windows_actions(self) -> bool:
        """Availability condition ``windows``: real Windows adapters or the explicit test double."""
        return self.name in ("windows", "fake")

    @property
    def reported_platform(self) -> str:
        """Value for ``pc_state.platform`` / link ``platform``: only real Windows says ``windows``."""
        return "windows" if self.name == "windows" else "development"
