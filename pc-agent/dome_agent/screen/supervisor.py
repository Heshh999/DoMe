"""Spawning and supervising the send-only worker process (design §5.1, §7, §15 ``supervisor.py``).

The worker is ``DoMe.exe screen-worker`` (frozen) or ``python -m dome_agent.screen.worker`` (from source), started
with the same user token (never elevated), an anonymous pipe pair (``stdin`` / ``stdout``, length-prefixed JSON,
:mod:`worker.pipe`) and, on Windows, a Job object with ``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE``: if DoMe.exe exits or
crashes, Windows kills the worker, so it can never capture without its parent. Its stderr is not a file: this
module reads it line by line, passes every line through the same redacting pipeline as the agent's own log and
writes it at WARNING, so a library line that slips through at WARNING carries no address (design §4.4).

Liveness runs both ways: the supervisor sends a ``tick`` every 3 s, but only while the agent's UI thread still
answers (``alive``: the thread that owns the "Screen view on" bars ran its check within ~3 s), so a frozen UI thread
whose bars no longer repaint stops the ticks and the worker stops capture and exits after 10 s without one; the
worker's exit is reported to the session manager, which ends the session ``capture_failed``. Stopping follows the end
order: ``stop`` → wait at most 2 s → terminate.

Events and exits are delivered on the supervisor's own reader and exit-watcher tasks. A handler must never stop the
worker from inside them (the stop waits for those very tasks): the session manager schedules its end on a task of its
own, and :meth:`_Subprocess.close` / ``watch_exit`` are written so that even such a call cannot make the two wait on
each other.

:class:`WorkerLauncher` is the seam the tests replace with an in-process fake (``testing/fake_screen_worker.py``):
the session manager is unit-tested without aiortc, and the real launcher is covered by the loopback worker tests.
The Job object and ``CREATE_NO_WINDOW`` are **not yet verified on a device**.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import subprocess
import sys
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from ..logsetup import get_logger, redact_text_line
from .worker.pipe import encode_message, read_message

log = get_logger(__name__)

EventHandler = Callable[[dict[str, Any]], Awaitable[None]]
ExitHandler = Callable[[int | None], Awaitable[None]]
StderrHandler = Callable[[str], None]
TICK_INTERVAL_SECONDS = 3.0
STOP_GRACE_SECONDS = 2.0
STDERR_LINE_MAX = 400


class WorkerProcess(Protocol):
    """One spawned worker, real or fake."""

    @property
    def pid(self) -> int: ...

    @property
    def alive(self) -> bool: ...

    async def send(self, message: dict[str, Any]) -> bool:
        """Write one pipe message; False when the pipe is gone."""
        ...

    async def wait(self, limit_seconds: float) -> int | None:
        """Exit code, or None when still running after ``limit_seconds``."""
        ...

    def terminate(self) -> None: ...

    async def close(self) -> None:
        """Release readers and handles after the process has exited."""
        ...


class WorkerLauncher(Protocol):
    async def spawn(
        self, *, on_event: EventHandler, on_exit: ExitHandler, on_stderr: StderrHandler
    ) -> WorkerProcess: ...


def worker_command() -> list[str]:
    """How to start the worker: the frozen ``DoMe.exe screen-worker`` or the module from source."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "screen-worker"]
    return [sys.executable, "-m", "dome_agent.screen.worker"]


class _Subprocess:
    def __init__(self, proc: asyncio.subprocess.Process, tasks: list[asyncio.Task[None]], job: Any) -> None:
        self._proc = proc
        self._tasks = tasks
        self._job = job  # the Windows Job object handle (closing it kills the worker); None elsewhere
        self._write_lock = asyncio.Lock()

    @property
    def pid(self) -> int:
        return int(self._proc.pid)

    @property
    def alive(self) -> bool:
        return self._proc.returncode is None

    async def send(self, message: dict[str, Any]) -> bool:
        stdin = self._proc.stdin
        if stdin is None or not self.alive:
            return False
        try:
            async with self._write_lock:
                stdin.write(encode_message(message))
                await stdin.drain()
            return True
        except (BrokenPipeError, ConnectionResetError, OSError):
            return False

    async def wait(self, limit_seconds: float) -> int | None:
        try:
            return await asyncio.wait_for(self._proc.wait(), limit_seconds)
        except TimeoutError:
            return None

    def terminate(self) -> None:
        if self.alive:
            with contextlib.suppress(ProcessLookupError, OSError):
                self._proc.kill()  # TerminateProcess on Windows; SIGKILL elsewhere: the worker has had its grace

    async def close(self) -> None:
        # The exit watcher delivers `on_exit`; once the process has gone it is given a moment to do so rather than
        # being cancelled with the readers, so an exit is reported exactly once, stop or crash alike. Called from one
        # of the readers themselves (a handler that stopped the worker inline), the watcher is neither awaited nor
        # cancelled: it waits for that reader to return, so waiting for it here would make each wait on the other.
        current = asyncio.current_task()
        exit_watcher = self._tasks[-1]
        on_reader = current is not None and current in self._tasks[:-1]
        if not on_reader and not self.alive and not exit_watcher.done() and exit_watcher is not current:
            with contextlib.suppress(TimeoutError, asyncio.CancelledError, Exception):
                await asyncio.wait_for(asyncio.shield(exit_watcher), 3.0)
        for task in self._tasks:
            if task.done() or task is current or (on_reader and task is exit_watcher):
                continue
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        if self._proc.stdin is not None:
            with contextlib.suppress(Exception):
                self._proc.stdin.close()
        if self._job is not None:
            with contextlib.suppress(Exception):
                self._job.Close()
            self._job = None


def _assign_job_object(pid: int) -> Any:
    """Windows: put the worker in a Job object that kills it when the last handle closes (our exit or crash)."""
    if sys.platform != "win32":
        return None
    import win32api
    import win32con
    import win32job

    # pywin32's stubs type these handles loosely (CreateJobObject as returning nothing): the PyHANDLE objects are Any.
    create_job: Any = win32job.CreateJobObject
    job: Any = create_job(None, "")
    info = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
    info["BasicLimitInformation"]["LimitFlags"] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, info)
    handle: Any = win32api.OpenProcess(win32con.PROCESS_SET_QUOTA | win32con.PROCESS_TERMINATE, False, pid)
    try:
        win32job.AssignProcessToJobObject(job, handle)
    finally:
        handle.Close()
    return job


class ProcessWorkerLauncher:
    """The real launcher: a child process, the pipe pair, redacted stderr and (Windows) the Job object."""

    def __init__(self, *, env: dict[str, str] | None = None, command: list[str] | None = None) -> None:
        self._env = env
        self._command = command

    async def spawn(self, *, on_event: EventHandler, on_exit: ExitHandler, on_stderr: StderrHandler) -> WorkerProcess:
        env = dict(os.environ if self._env is None else self._env)
        env.setdefault("PYTHONUNBUFFERED", "1")
        env.setdefault("PYTHONIOENCODING", "utf-8")
        kwargs: dict[str, Any] = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        proc = await asyncio.create_subprocess_exec(
            *(self._command or worker_command()),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            **kwargs,
        )
        job = None
        try:
            job = _assign_job_object(proc.pid)
        except Exception as exc:  # noqa: BLE001 - no Job object: the ticks and the lease still bound the worker
            log.warning("screen worker started without a Job object", error=exc.__class__.__name__)
        assert proc.stdout is not None and proc.stderr is not None
        loop = asyncio.get_running_loop()
        tasks: list[asyncio.Task[None]] = []
        exited = asyncio.Event()

        async def read_events() -> None:
            assert proc.stdout is not None
            try:
                while True:
                    message = await read_message(proc.stdout)
                    if message is None:
                        return
                    await on_event(message)
            except Exception as exc:  # noqa: BLE001 - a malformed pipe message ends the reader, the exit watcher reports
                log.warning("screen worker pipe reader stopped", error=exc.__class__.__name__)

        async def read_stderr() -> None:
            assert proc.stderr is not None
            while True:
                try:
                    raw = await proc.stderr.readline()
                except (ValueError, OSError):
                    raw = b""
                if not raw:
                    return
                line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                if line:
                    on_stderr(line)

        async def watch_exit() -> None:
            code = await proc.wait()
            # Let the readers drain (the last events precede the exit) for at most 2 s. `asyncio.wait` never cancels
            # them on timeout: a reader may be inside a handler that is itself stopping the worker.
            with contextlib.suppress(Exception):
                await asyncio.wait({tasks[0], tasks[1]}, timeout=2.0)
            exited.set()
            await on_exit(code)

        tasks.append(loop.create_task(read_events(), name="dome-screen-worker-events"))
        tasks.append(loop.create_task(read_stderr(), name="dome-screen-worker-stderr"))
        tasks.append(loop.create_task(watch_exit(), name="dome-screen-worker-exit"))
        return _Subprocess(proc, tasks, job)


class WorkerSupervisor:
    """Ticks, redacted stderr and the stop order for one worker at a time."""

    def __init__(
        self,
        launcher: WorkerLauncher,
        *,
        tick_interval: float = TICK_INTERVAL_SECONDS,
        stop_grace: float = STOP_GRACE_SECONDS,
        alive: Callable[[], bool] | None = None,
    ) -> None:
        self._launcher = launcher
        self.tick_interval = tick_interval
        self.stop_grace = stop_grace
        self._alive = alive  # the liveness probe a tick needs (the UI thread that owns the bars still answers)
        self._proc: WorkerProcess | None = None
        self._ticker: asyncio.Task[None] | None = None
        self._stop_lock = asyncio.Lock()
        self.stderr_lines = 0
        self.ticks_withheld = 0

    @property
    def process(self) -> WorkerProcess | None:
        return self._proc

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.alive

    async def start(
        self, start_message: dict[str, Any], *, on_event: EventHandler, on_exit: ExitHandler
    ) -> WorkerProcess:
        if self._proc is not None:
            raise RuntimeError("a screen worker is already running")
        proc = await self._launcher.spawn(on_event=on_event, on_exit=on_exit, on_stderr=self._on_stderr)
        self._proc = proc
        if not await proc.send(start_message):
            await self.stop()
            raise RuntimeError("the screen worker closed its pipe before the start message")
        self._ticker = asyncio.get_running_loop().create_task(self._tick(), name="dome-screen-ticks")
        return proc

    async def send(self, message: dict[str, Any]) -> bool:
        proc = self._proc
        return proc is not None and await proc.send(message)

    async def stop(self) -> int | None:
        """The end order's worker part: ``stop`` → wait at most ``stop_grace`` → terminate → wait. Two callers (an
        end and an abandoned start) are serialised: the second finds the worker gone."""
        async with self._stop_lock:
            return await self._stop()

    async def _stop(self) -> int | None:
        proc = self._proc
        if proc is None:
            return None
        if self._ticker is not None and not self._ticker.done():
            self._ticker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._ticker
        self._ticker = None
        code: int | None = None
        if proc.alive:
            await proc.send({"op": "stop"})
            code = await proc.wait(self.stop_grace)
            if code is None:
                log.warning("screen worker did not exit after stop; terminating")
                proc.terminate()
                code = await proc.wait(self.stop_grace)
        else:
            code = await proc.wait(0.01)
        await proc.close()
        self._proc = None
        return code

    def _on_stderr(self, line: str) -> None:
        self.stderr_lines += 1
        log.warning("screen worker", line=redact_text_line(line)[:STDERR_LINE_MAX])

    async def _tick(self) -> None:
        while True:
            await asyncio.sleep(self.tick_interval)
            proc = self._proc
            if proc is None or not proc.alive:
                return
            if not self._probe_alive():
                # design §7: a frozen UI thread cannot leave an unattended stream behind bars that no longer repaint.
                # No tick: the worker stops capture and exits by its own 10 s timeout unless the thread recovers.
                self.ticks_withheld += 1
                if self.ticks_withheld == 1:
                    log.warning("the UI thread that owns the Screen view bars is not answering; ticks withheld")
                continue
            self.ticks_withheld = 0
            if not await proc.send({"op": "tick"}):
                return

    def _probe_alive(self) -> bool:
        if self._alive is None:
            return True
        try:
            return bool(self._alive())
        except Exception:  # noqa: BLE001 - an unreadable probe is a frozen UI: fail closed
            return False


__all__ = [
    "STOP_GRACE_SECONDS",
    "TICK_INTERVAL_SECONDS",
    "ProcessWorkerLauncher",
    "WorkerLauncher",
    "WorkerProcess",
    "WorkerSupervisor",
    "worker_command",
]
