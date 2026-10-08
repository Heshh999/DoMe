"""Platform adapter interfaces.

Adapters are synchronous (they wrap blocking OS calls) and are invoked by the action handlers via
``asyncio.to_thread``. Failures are reported as :class:`dome_protocol.ProtocolError` with a stable
code from ``errors.json`` so handlers can surface them verbatim.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol

PlatformName = Literal["windows", "unsupported", "fake"]
MediaStatus = Literal["playing", "paused", "stopped", "changing", "closed", "opened", "unknown"]
MediaControl = Literal["play", "pause", "next", "previous"]


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
    notes: list[str] = field(default_factory=list)

    @property
    def supports_windows_actions(self) -> bool:
        """Availability condition ``windows``: real Windows adapters or the explicit test double."""
        return self.name in ("windows", "fake")

    @property
    def reported_platform(self) -> str:
        """Value for ``pc_state.platform`` / link ``platform``: only real Windows says ``windows``."""
        return "windows" if self.name == "windows" else "development"
