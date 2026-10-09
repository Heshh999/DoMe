"""One agent per Windows user session (spec §10 "One agent per Windows user session", design §3.3).

* Windows: a named mutex ``Local\\DoMe.Agent.<session id>`` (``Local\\`` = this logon session's
  namespace, so two Windows sessions of the same or different users never conflict by design; the
  session id comes from ``ProcessIdToSessionId``). ``ERROR_ALREADY_EXISTS`` means another agent of this
  session holds it. The handle is kept open for the process lifetime and closed on release.
* elsewhere: ``flock(LOCK_EX | LOCK_NB)`` on ``<state dir>/agent.lock``.

Both write a small ``agent.pid`` file (``{"pid", "session", "started_at"}``) next to the lock so a
second launch and ``dome-agent status`` can name the running instance. The pid file is advisory: the
lock decides. Nothing here ever terminates a process.

Second launch (``dome-agent run`` while the lock is held): ask the running instance over the
authenticated control channel to show its tray/setup window (op ``show``), print what happened, exit 0.
If the lock is held but the control channel does not answer, print a distinct message with the pid
and point to ``dome-agent repair`` (exit 1). :func:`inspect` reports the stale control endpoint, a
permission problem on the state directory and an other-session conflict distinctly for ``status``.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .logsetup import get_logger

log = get_logger(__name__)

LOCK_FILENAME = "agent.lock"
PID_FILENAME = "agent.pid"
MUTEX_PREFIX = "Local\\DoMe.Agent."
ERROR_ALREADY_EXISTS = 183


def current_session_id() -> str:
    """Windows logon session id of this process; ``""`` elsewhere."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        session = wintypes.DWORD(0)
        if kernel32.ProcessIdToSessionId(kernel32.GetCurrentProcessId(), ctypes.byref(session)):
            return str(int(session.value))
        return "unknown"
    return ""


def session_id_of_pid(pid: int) -> str | None:
    """Windows logon session of another process (None when it cannot be determined / not Windows)."""
    if sys.platform != "win32":
        return ""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    session = wintypes.DWORD(0)
    if kernel32.ProcessIdToSessionId(int(pid), ctypes.byref(session)):
        return str(int(session.value))
    return None


def mutex_name(session_id: str) -> str:
    return f"{MUTEX_PREFIX}{session_id or 'default'}"


@dataclass(frozen=True, slots=True)
class PidRecord:
    pid: int
    session: str
    started_at: float

    @staticmethod
    def read(path: Path) -> PidRecord | None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        try:
            return PidRecord(int(data["pid"]), str(data.get("session", "")), float(data.get("started_at", 0)))
        except (KeyError, TypeError, ValueError):
            return None


class InstanceLock:
    """Held for the lifetime of the agent process. ``acquire`` returns None when another instance of
    this user session already holds it."""

    def __init__(self, state_dir: Path) -> None:
        self.state_dir = state_dir
        self.lock_path = state_dir / LOCK_FILENAME
        self.pid_path = state_dir / PID_FILENAME
        self._fh: Any = None
        self._mutex: Any = None
        self.held = False

    @classmethod
    def acquire(cls, state_dir: Path) -> InstanceLock | None:
        lock = cls(state_dir)
        return lock if lock._try_acquire() else None

    def _try_acquire(self) -> bool:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            ok = self._acquire_mutex()
        else:
            ok = self._acquire_flock()
        if not ok:
            return False
        self.held = True
        self._write_pid()
        return True

    def _acquire_mutex(self) -> bool:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        ctypes.set_last_error(0)
        handle = kernel32.CreateMutexW(None, False, mutex_name(current_session_id()))
        err = ctypes.get_last_error()
        if not handle:
            log.error("CreateMutexW failed", error=err)
            return False
        if err == ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            return False
        self._mutex = handle
        return True

    def _acquire_flock(self) -> bool:
        import fcntl

        fh = self.lock_path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            return False
        self._fh = fh
        return True

    def _write_pid(self) -> None:
        record = PidRecord(os.getpid(), current_session_id(), time.time())
        try:
            tmp = self.pid_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(asdict(record)), encoding="utf-8")
            os.replace(tmp, self.pid_path)
        except OSError as exc:
            log.warning("could not write the agent pid file", error=exc.__class__.__name__)

    def release(self) -> None:
        if not self.held:
            return
        self.held = False
        with contextlib.suppress(OSError):
            self.pid_path.unlink()
        if self._fh is not None:
            import fcntl

            with contextlib.suppress(OSError):
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
            self._fh.close()
            self._fh = None
        if self._mutex is not None:
            import ctypes

            ctypes.WinDLL("kernel32").CloseHandle(self._mutex)
            self._mutex = None


# ----- inspection (status / second launch / repair) --------------------------------------------------


@dataclass(slots=True)
class InstanceReport:
    lock_held_by_other: bool = False  # another agent of THIS user session holds the lock
    running_pid: int | None = None  # from agent.pid when its process exists
    pid_file_stale: bool = False  # agent.pid names a process that no longer exists
    control_responding: bool = False  # the control channel answered `ping`
    control_endpoint_stale: bool = False  # control endpoint exists on disk but nobody listens
    permission_problem: str | None = None  # the state dir / lock file cannot be written
    other_session_conflict: bool = False  # the recorded instance runs in another Windows session
    other_session: str | None = None
    message: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _pid_alive(pid: int) -> bool:
    try:
        import psutil

        return psutil.pid_exists(pid)
    except Exception:  # noqa: BLE001
        return False


def _probe_permissions(state_dir: Path) -> str | None:
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
        probe = state_dir / ".write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return f"cannot write to the state directory {state_dir} ({exc.__class__.__name__})"
    return None


def _lock_held_by_other(state_dir: Path) -> bool:
    """Non-destructive probe: acquire-and-release when free; False when WE could take it."""
    lock = InstanceLock.acquire(state_dir)
    if lock is None:
        return True
    lock.release()
    return False


def inspect(state_dir: Path, *, control_timeout: float = 2.0) -> InstanceReport:
    """Describe the instance situation distinctly (never changes anything, never kills anything).

    When called from the running agent itself the lock is of course held — callers inside the agent
    use :func:`inspect_from_inside` instead."""
    report = InstanceReport()
    report.permission_problem = _probe_permissions(state_dir)
    record = PidRecord.read(state_dir / PID_FILENAME)
    if record is not None:
        if _pid_alive(record.pid):
            report.running_pid = record.pid
            mine = current_session_id()
            if sys.platform == "win32" and record.session and record.session != mine:
                report.other_session_conflict = True
                report.other_session = record.session
        else:
            report.pid_file_stale = True
    if report.permission_problem is None:
        report.lock_held_by_other = _lock_held_by_other(state_dir)
    from .bridge import ipc
    from .control import ControlClient, ControlError

    try:
        ControlClient(state_dir, timeout=control_timeout).call("ping")
        report.control_responding = True
    except ControlError:
        report.control_responding = False
    if not report.control_responding and sys.platform != "win32":
        report.control_endpoint_stale = Path(ipc.endpoint_address(state_dir, "control")).exists()
    report.message = describe(report)
    return report


def describe(report: InstanceReport) -> str:
    pid = f" (pid {report.running_pid})" if report.running_pid else ""
    if report.permission_problem:
        return f"Permission problem: {report.permission_problem}. Run `dome-agent repair` or fix the folder permissions."
    if report.other_session_conflict:
        return (
            f"DoMe is running in another Windows session{pid} (session {report.other_session}); this session has "
            "no agent. Sign in to that session to use it, or start DoMe here to run one per session."
        )
    if report.lock_held_by_other and report.control_responding:
        return f"DoMe is already running in this session{pid} and responding."
    if report.lock_held_by_other and not report.control_responding:
        return (
            f"Another DoMe instance is running but not responding{pid}. It may still be starting; if this persists, "
            "quit it from the tray (or sign out and back in) and run `dome-agent repair`."
        )
    if report.control_endpoint_stale:
        return "Stale control endpoint: a previous DoMe did not shut down cleanly. `dome-agent repair` removes it."
    if report.pid_file_stale:
        return "Stale pid file from a previous DoMe (no such process). Harmless; `dome-agent repair` cleans it up."
    return "No DoMe agent is running in this session."


def repair(state_dir: Path, *, host_path: Path | None, dev_extension_id: str) -> list[str]:
    """Re-register the native host and clean stale local endpoints. Preserves identity, credential,
    grants and approved apps (they are never touched); never kills a process. Returns a report."""
    from .bridge import ipc
    from .bridge.manifest import HOST_NAME, allowed_origins, write_manifest
    from .platform import build_platform
    from .settings import load_settings

    out: list[str] = []
    problem = _probe_permissions(state_dir)
    if problem:
        out.append(f"NOT FIXED: {problem}")
        return out
    out.append("state directory writable: ok")
    report = inspect(state_dir)
    if report.lock_held_by_other and report.control_responding:
        out.append(f"running agent found (pid {report.running_pid}); left running")
    elif report.lock_held_by_other:
        out.append(
            f"an agent holds the instance lock but does not answer (pid {report.running_pid}); NOT killed — quit it "
            "from the tray or sign out/in, then run repair again"
        )
    else:
        if sys.platform != "win32":
            for kind in ("control", "bridge"):
                path = Path(ipc.endpoint_address(state_dir, kind))
                if path.exists():
                    with contextlib.suppress(OSError):
                        path.unlink()
                        out.append(f"removed stale {kind} endpoint {path.name}")
        pid_file = state_dir / PID_FILENAME
        if report.pid_file_stale and pid_file.exists():
            with contextlib.suppress(OSError):
                pid_file.unlink()
                out.append("removed stale pid file")
    for name in ("identity.json", "state.sqlite3"):
        out.append(f"{name}: {'present, untouched' if (state_dir / name).exists() else 'absent'}")
    out.append(f"secrets: {'present, untouched' if any((state_dir / 'secrets').glob('*.bin')) else 'absent'}")
    settings = load_settings(state_dir=state_dir)
    try:
        origins = allowed_origins(dev_extension_id or settings.dev_extension_id)
    except ValueError as exc:
        out.append(f"native host manifest NOT written: {exc}")
        return out
    if host_path is None:
        from .tray import host_executable_path

        host_path = host_executable_path()
    if not host_path.exists():
        out.append(f"native host executable not found at {host_path}; manifest not re-registered (pass --host-path)")
        return out
    manifest_path = write_manifest(state_dir / f"{HOST_NAME}.json", host_path, origins)
    out.append(f"native host manifest rewritten: {manifest_path}")
    try:
        for location in build_platform(settings).native_host.install(manifest_path):
            out.append(f"registered {location}")
    except Exception as exc:  # noqa: BLE001 - PLATFORM_UNSUPPORTED on non-Windows, registry errors on Windows
        out.append(f"registry registration skipped: {getattr(exc, 'message', exc.__class__.__name__)}")
    return out
