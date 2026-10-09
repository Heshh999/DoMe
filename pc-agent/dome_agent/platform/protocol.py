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
)
SHORTCUTS: tuple[str, ...] = ("ctrl_a", "ctrl_c", "ctrl_v", "ctrl_z", "ctrl_l")


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
class ForegroundApp:
    """What the PC has in front, as far as the agent can observe (``relay-frames#/$defs/foreground_app``).

    ``window_id``/``pid`` identify the window for the input session's target-change check and are never
    sent; ``window_title`` is untrusted display data and is never logged. ``elevated`` is None when the
    agent could not determine the foreground process's integrity level (reported as unknown, never
    guessed). Field-level focus inside the window is not observable and is never claimed."""

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
        if self.window_title:
            out["window_title"] = self.window_title[:200]
        if self.browser is not None:
            out["browser"] = self.browser
        if self.elevated is not None:
            out["elevated"] = bool(self.elevated)
        return out


class InputAdapter(Protocol):
    """Manual pointer/keyboard injection (spec §10A). Every method is synchronous and blocking.

    Failures are ``ProtocolError`` with ``INPUT_INJECTION_FAILED`` (the OS accepted fewer events than
    requested), ``INPUT_RESTRICTED`` (access denied while the adapter can see a protected desktop /
    locked session / elevated foreground) or ``PLATFORM_UNSUPPORTED``. The adapter never claims UIPI
    from a return value alone."""

    def move(self, dx: int, dy: int) -> None:
        """Relative cursor motion in desktop pixels (never clamped to one display)."""
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

    @property
    def supports_windows_actions(self) -> bool:
        """Availability condition ``windows``: real Windows adapters or the explicit test double."""
        return self.name in ("windows", "fake")

    @property
    def reported_platform(self) -> str:
        """Value for ``pc_state.platform`` / link ``platform``: only real Windows says ``windows``."""
        return "windows" if self.name == "windows" else "development"
