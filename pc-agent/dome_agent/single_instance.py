"""One agent per Windows user session (spec §10 "One agent per Windows user session", design §3.3).

* Windows: a named mutex ``Local\\DoMe.Agent.<session id>`` (``Local\\`` = this logon session's
  namespace; the session id comes from ``ProcessIdToSessionId``). ``ERROR_ALREADY_EXISTS`` means another
  agent of this session holds it. The handle is kept open for the process lifetime and closed on release.
* The state directory (``%LOCALAPPDATA%\\DoMe``: identity, PC credential, grants, ``state.sqlite3``) is
  per Windows ACCOUNT, not per logon session. A second agent of the same account in another session
  (RDS, a reconnected or second session) would reuse the same PC identity and supersede the first at the
  relay. :func:`other_session_agent` detects that from ``agent.pid`` and ``dome-agent run`` refuses with
  a distinct message instead of starting a competing agent: one agent per account's state directory.
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
        from .platform.windows.instance import current_session_id as _win_session

        return _win_session()
    return ""


def session_id_of_pid(pid: int) -> str | None:
    """Windows logon session of another process (None when it cannot be determined; ``""`` off Windows)."""
    if sys.platform != "win32":
        return ""
    from .platform.windows.instance import session_id_of_pid as _win_session_of

    return _win_session_of(pid)


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
        self._wrote_pid = False
        self.held = False

    @classmethod
    def acquire(cls, state_dir: Path, *, probe: bool = False) -> InstanceLock | None:
        """``probe=True`` only tests whether the lock is free (no pid file is written or removed)."""
        lock = cls(state_dir)
        return lock if lock._try_acquire(probe=probe) else None

    def _try_acquire(self, *, probe: bool) -> bool:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            ok = self._acquire_mutex()
        else:
            ok = self._acquire_flock()
        if not ok:
            return False
        self.held = True
        self._wrote_pid = False
        if not probe:
            self._write_pid()
            self._wrote_pid = True
        return True

    def _acquire_mutex(self) -> bool:
        from .platform.windows.instance import acquire_mutex

        handle, err = acquire_mutex(mutex_name(current_session_id()))
        if handle is None:
            if err != ERROR_ALREADY_EXISTS:
                log.error("CreateMutexW failed", error=err)
            return False
        self._mutex = handle
        return True

    def _acquire_flock(self) -> bool:
        if sys.platform == "win32":
            raise RuntimeError("flock is not used on Windows (a named mutex is)")
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
        if self._wrote_pid:
            with contextlib.suppress(OSError):
                self.pid_path.unlink()
        if sys.platform != "win32" and self._fh is not None:  # only the flock path sets _fh
            import fcntl

            with contextlib.suppress(OSError):
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
            self._fh.close()
            self._fh = None
        if self._mutex is not None:
            from .platform.windows.instance import close_handle

            close_handle(self._mutex)
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


def other_session_agent(state_dir: Path) -> PidRecord | None:
    """The live agent recorded in ``agent.pid`` when it runs in ANOTHER Windows logon session of this
    account (same state directory, different session id); None otherwise. Both session ids must be known
    and non-empty (they are always empty off Windows). When the recorded pid now belongs to a process in
    a different session than recorded (pid reuse), it is not reported."""
    record = PidRecord.read(state_dir / PID_FILENAME)
    if record is None or not record.session or record.pid == os.getpid():
        return None
    mine = current_session_id()
    if not mine or record.session == mine or not _pid_alive(record.pid):
        return None
    actual = session_id_of_pid(record.pid)
    if actual is not None and actual != record.session:
        return None  # the pid was reused by an unrelated process: no DoMe agent there
    return record


def describe_other_session(record: PidRecord) -> str:
    return (
        f"DoMe already runs for this Windows account in session {record.session} (pid {record.pid}). Both "
        "sessions share this account's DoMe identity, so only one agent can run: use DoMe from that session, "
        "or quit it there (tray → Quit) and start it here."
    )


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
    """Non-destructive probe: acquire-and-release when free (no pid file touched); False when WE could take it."""
    lock = InstanceLock.acquire(state_dir, probe=True)
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
            if other_session_agent(state_dir) is not None:
                report.other_session_conflict = True
                report.other_session = record.session
        else:
            report.pid_file_stale = True
    if report.permission_problem is None:
        report.lock_held_by_other = _lock_held_by_other(state_dir)
    from .bridge import ipc
    from .control import ControlClient, ControlError

    try:
        pong = ControlClient(state_dir, timeout=control_timeout).call("ping")
        report.control_responding = True
        if report.running_pid is None and isinstance(pong, dict) and isinstance(pong.get("pid"), int):
            report.running_pid = int(pong["pid"])
    except ControlError:
        report.control_responding = False
    if not report.control_responding and sys.platform != "win32":
        report.control_endpoint_stale = Path(ipc.endpoint_address(state_dir, "control")).exists()
    report.message = describe(report)
    return report


def describe(report: InstanceReport) -> str:
    pid = f" (pid {report.running_pid})" if report.running_pid else ""
    if report.permission_problem:
        return (
            f"Permission problem: {report.permission_problem}. Run `dome-agent repair` or fix the folder permissions."
        )
    if report.other_session_conflict:
        return (
            f"DoMe already runs for this Windows account in session {report.other_session}{pid}. Both sessions "
            "share this account's DoMe identity, so only one agent can run: use DoMe from that session, or quit "
            "it there (tray → Quit) and start it here."
        )
    if report.control_responding:
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
    if report.control_responding:
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
