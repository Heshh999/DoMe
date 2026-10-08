"""In-memory stand-ins for the Windows adapters. Selected ONLY by ``DOME_AGENT_PLATFORM=fake``.

Every call is recorded in ``calls`` so tests can assert that exactly one side effect happened. Power
actions record the request and do nothing. The agent logs a loud warning when this set is active and
reports ``platform: "development"`` in state frames.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from dome_protocol import ProtocolError

from ..platform.protocol import AppWindow, MediaControl, MediaSession, MediaStatus, PlatformSet, VolumeState


@dataclass
class FakeState:
    volume: int = 50
    muted: bool = False
    locked: bool = False
    sessions: dict[str, MediaSession] = field(default_factory=dict)
    processes: dict[str, list[int]] = field(default_factory=dict)  # exe_path → pids
    windows: dict[str, AppWindow] = field(default_factory=dict)  # window_id → window
    window_pids: dict[str, int] = field(default_factory=dict)
    foreground: str | None = None
    refuse_focus: bool = False
    refuse_close: bool = False
    power_fail: str | None = None  # error code to raise from power calls
    start_at_login: bool = False
    next_pid: int = 1000
    calls: list[tuple[str, tuple[object, ...]]] = field(default_factory=list)

    def record(self, name: str, *args: object) -> None:
        self.calls.append((name, args))

    def count(self, name: str) -> int:
        return sum(1 for n, _ in self.calls if n == name)

    def add_session(self, session_id: str, status: MediaStatus = "playing", controls: tuple[MediaControl, ...] = ("play", "pause", "next", "previous"), title: str = "Untitled") -> None:
        self.sessions[session_id] = MediaSession(session_id=session_id, status=status, controls=controls, app_label=session_id.split("#")[0][:64], title=title)

    def add_window(self, pid: int, window_id: str, title: str, minimized: bool = False) -> None:
        self.windows[window_id] = AppWindow(window_id=window_id, title=title, pid=pid, minimized=minimized)
        self.window_pids[window_id] = pid


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
        if self.st.refuse_focus:
            return False
        w = self.st.windows[window_id]
        self.st.windows[window_id] = AppWindow(w.window_id, w.title, w.pid, False, True)
        self.st.foreground = window_id
        return True

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

    def sleep(self) -> None:
        self._do("power_sleep")

    def restart(self) -> None:
        self._do("power_restart")

    def shutdown(self) -> None:
        self._do("power_shutdown")


class FakeStartup:
    def __init__(self, st: FakeState) -> None:
        self.st = st

    def get_start_at_login(self) -> bool:
        return self.st.start_at_login

    def set_start_at_login(self, enabled: bool, command: str) -> None:
        self.st.record("start_at_login", enabled)
        self.st.start_at_login = enabled


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
        notes=["FAKE platform adapters active (DOME_AGENT_PLATFORM=fake): no real PC action happens"],
    )
    return ps
