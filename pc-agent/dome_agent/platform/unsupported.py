"""Adapters for non-Windows hosts: every Windows-only operation fails with ``PLATFORM_UNSUPPORTED``.

The YouTube path (browser bridge) does not go through these adapters and therefore works on every
platform; everything else is honestly reported as unavailable.
"""

from __future__ import annotations

from pathlib import Path

from dome_protocol import ProtocolError

from .protocol import AppWindow, ForegroundApp, MediaSession, PlatformSet, VolumeState


def _unsupported(what: str) -> ProtocolError:
    return ProtocolError("PLATFORM_UNSUPPORTED", f"{what} is only available on Windows")


class UnsupportedVolume:
    def get(self) -> VolumeState:
        raise _unsupported("Windows volume")

    def set_volume(self, value: int) -> VolumeState:
        raise _unsupported("Windows volume")

    def set_muted(self, muted: bool) -> VolumeState:
        raise _unsupported("Windows volume")


class UnsupportedMedia:
    def list_sessions(self) -> list[MediaSession]:
        raise _unsupported("Windows media control")

    def get_session(self, session_id: str) -> MediaSession | None:
        raise _unsupported("Windows media control")

    def set_paused(self, session_id: str, paused: bool) -> MediaSession:
        raise _unsupported("Windows media control")

    def next(self, session_id: str) -> MediaSession:
        raise _unsupported("Windows media control")

    def previous(self, session_id: str) -> MediaSession:
        raise _unsupported("Windows media control")


class UnsupportedApps:
    def running_pids(self, exe_path: str) -> list[int]:
        raise _unsupported("application control")

    def launch(self, exe_path: str) -> int:
        raise _unsupported("application control")

    def list_windows(self, pids: list[int]) -> list[AppWindow]:
        raise _unsupported("application control")

    def window_exists(self, window_id: str) -> bool:
        raise _unsupported("application control")

    def focus(self, window_id: str) -> bool:
        raise _unsupported("application control")

    def minimize(self, window_id: str) -> bool:
        raise _unsupported("application control")

    def request_close(self, window_id: str) -> None:
        raise _unsupported("application control")


class UnsupportedSession:
    def is_locked(self) -> bool:
        return False

    def lock(self) -> None:
        raise _unsupported("locking the session")


class UnsupportedPower:
    def sleep(self) -> None:
        raise _unsupported("power control")

    def restart(self) -> None:
        raise _unsupported("power control")

    def shutdown(self) -> None:
        raise _unsupported("power control")

    def abort_shutdown(self) -> bool:
        raise _unsupported("power control")


class UnsupportedStartup:
    def get_start_at_login(self) -> bool:
        return False

    def set_start_at_login(self, enabled: bool, command: str) -> None:
        raise _unsupported("start at login")


class UnsupportedNativeHost:
    def install(self, manifest_path: Path) -> list[str]:
        raise _unsupported("native messaging host registration")

    def uninstall(self) -> list[str]:
        raise _unsupported("native messaging host registration")


class UnsupportedInput:
    """Manual input needs SendInput: every injection call fails with PLATFORM_UNSUPPORTED (development
    runs on Linux/macOS without ``DOME_AGENT_PLATFORM=fake``). Observation calls answer honestly
    "unknown" / "not restricted" instead of inventing a foreground window."""

    def move(self, dx: int, dy: int) -> None:
        raise _unsupported("manual pointer input")

    def button(self, button: str, action: str) -> None:
        raise _unsupported("manual pointer input")

    def scroll(self, dx: int, dy: int) -> None:
        raise _unsupported("manual pointer input")

    def text(self, text: str) -> None:
        raise _unsupported("manual keyboard input")

    def key(self, key: str) -> None:
        raise _unsupported("manual keyboard input")

    def shortcut(self, name: str) -> None:
        raise _unsupported("manual keyboard input")

    def release(self, buttons: set[str], keys: set[str]) -> int:
        if not buttons and not keys:
            return 0
        raise _unsupported("manual input release")

    def foreground(self) -> ForegroundApp | None:
        return None

    def input_restricted(self) -> bool:
        return False

    def secure_desktop_active(self) -> bool:
        return False


def build_unsupported_platform() -> PlatformSet:
    return PlatformSet(
        name="unsupported",
        volume=UnsupportedVolume(),
        media=UnsupportedMedia(),
        apps=UnsupportedApps(),
        session=UnsupportedSession(),
        power=UnsupportedPower(),
        startup=UnsupportedStartup(),
        native_host=UnsupportedNativeHost(),
        input=UnsupportedInput(),
        notes=[
            "Non-Windows host: Windows-only actions answer PLATFORM_UNSUPPORTED; YouTube control via the browser bridge works."
        ],
    )
