"""Desktop Duplication through FFmpeg's ``ddagrab`` (design §5.2; the primary Windows source).

Opened by PyAV as an ``lavfi`` graph: ``ddagrab=output_idx=N:framerate=30:draw_mouse=1:dup_frames=1,
scale_d3d11=w=W:h=H:format=nv12,hwdownload,format=nv12``. GPU colour conversion and scaling; the ``lavfi`` device emits
``wrapped_avframe`` packets that PyAV's ``decode()`` unwraps (the one decoder the shipping build enables).

What ``vsrc_ddagrab.c`` (FFmpeg n8.1.3) really does, and what follows from it here:

* **``dup_frames=1``, and this module drops the repeats.** With ``dup_frames=0`` the filter's ``request_frame`` loops on
  ``AcquireNextFrame`` until the desktop changes, so a read through ``lavfi`` BLOCKS on a still screen: the track's 1 fps
  floor could never fire, and ``close()`` could release the container under a thread still inside it (the premise that
  a still desktop raised ``av.error.BlockingIOError`` was wrong; DECISIONS #83, revised by #94). With ``dup_frames=1``
  every read returns within about one frame interval: the filter paces to ``framerate``, waits half an interval for a
  new frame and otherwise re-sends its last one. A re-sent frame is the same picture downloaded again (a new buffer and
  a new ``pts``), so :meth:`DdagrabSource.read` compares a content signature (:func:`frame_signature`: CRC-32 of the
  visible bytes, about 0.4 ms for 1280x720 NV12) and answers None for a repeat. The FrameSource contract ("the next
  CHANGED frame, or None") holds, and the track's time-driven floor re-encodes the held frame at >= 1 fps as before.
  A cursor move is a change (``draw_mouse=1`` paints the pointer into the frame).
* **The open can block too, so a thread of its own owns it.** ``probe_output_format`` runs inside ``av.open`` and loops
  until the desktop produces a real frame (pointer-only updates do not count); no option skips it.
  :meth:`DdagrabSource.open` therefore opens on a helper thread and waits :data:`OPEN_WAIT_SECONDS` (shorter than the
  track's 2 s ``aclose`` wait, so a track closed during the open still waits the open out): a failure in that time is
  the honest error (the duplication itself fails within milliseconds); a still desktop leaves the open PENDING, reads
  answer None meanwhile (bounded waits) and the source adopts the graph once the helper has it. The container is
  closed only by the thread that created it or by the thread that reads it: a pending open abandoned by :meth:`close`
  is closed by the helper when ``av.open`` returns, never from another thread while it is still inside.
* **PyAV's decode iterator is a generator**: an exception raised through it finishes it, and every later ``next`` is
  StopIteration. After EAGAIN (no frame yet) :meth:`DdagrabSource.read` takes a fresh iterator over the same container;
  an iterator that ends (EOF: the graph is over) counts as a failure and rebuilds the graph like access lost.
* **Access lost / a desktop switch** (every UAC prompt): ``ddagrab`` errors and does not recreate itself, so ``read``
  rebuilds the graph; only :data:`REBUILD_LIMIT` such errors in a row WITHOUT a frame in between give up (the count
  resets on every frame, so a long session survives any number of UAC prompts).
* **A reopen that fails** (the secure desktop is still up) is retried with a back-off and reports None meanwhile: the
  agent's secure-desktop pause and the indicator grace decide when the session ends, not this source; it gives up only
  after :data:`REOPEN_ATTEMPTS` failed reopens while capture is running.
* **Failures are classified from PyAV's exception type and ``errno`` and from FFmpeg's own log lines, never from the
  exception's text**: PyAV quotes the graph (``ddagrab=output_idx=...``) in every error string, which is how the first
  device run reported ``unsupported_display`` for every failure. PyAV installs a no-op log callback, so FFmpeg's lines
  exist only inside :func:`ffmpeg_error_lines` (level ERROR plus an ``av.logging.Capture`` for this thread), which wraps
  the open and every read; ``exc.log`` is the fallback when the capture holds nothing. ``AVERROR(EBUSY)`` is
  ``errno == 16`` (the MinGW build prints "Error number -16 occurred", never "busy"); ``PermissionError``
  (``E_ACCESSDENIED``) is ``remote_session`` only when Windows says this is a remote session, else ``driver``;
  "Selected output not supported" and "Failed to enumerate DXGI output" (``AVERROR_EXTERNAL``) are
  ``unsupported_display``; a D3D11 device or ``scale_d3d11`` video-processor line is ``driver``.
* **``output_idx`` is an index among the DEFAULT adapter's outputs**, not DoMe's primary-first monitor index. The filter
  has no adapter option (``lavfi`` cannot hand it a device), so :class:`DdaWithFallback` asks
  :func:`locate_ddagrab_output` (DXGI, ``platform.windows.dxgi``) at open time for the output whose desktop rectangle
  is the streamed monitor's: on the default adapter its index is used; on another adapter Desktop Duplication is skipped
  with a logged reason and Windows.Graphics.Capture, which composes every adapter's output, runs at once; when the lookup
  itself cannot answer the surface index is used as before.
* **The agent's pre-flight** (``screen/preflight.py``) may have found Desktop Duplication failing before the bars: its
  verdict travels in the source spec (``preflight``: the detail and the HRESULT's name) and :func:`preflight_skip`
  turns it into the error that makes :class:`DdaWithFallback` skip Desktop Duplication and open Windows.Graphics.Capture
  at once, the HRESULT in the fallback warning.
* Constructing a source opens nothing: the only ``open()`` is the track's, after DTLS connected (``rules.screen_sessions``).

Windows only; **not yet verified on a device**.
"""

from __future__ import annotations

import contextlib
import errno as errno_codes
import re
import sys
import threading
import time
import zlib
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from typing import Any

import structlog
from dome_protocol import ProtocolError

from .encode import VIDEO_TIME_BASE

log = structlog.get_logger(__name__)

REBUILD_LIMIT = 3  # access-lost errors in a row, without a frame in between, before the source gives up
REOPEN_ATTEMPTS = 10  # failed reopens (while capture runs) before the source gives up
REOPEN_BACKOFF_SECONDS = (0.25, 0.5, 1.0, 2.0)
OPEN_WAIT_SECONDS = 1.5  # a failing duplication fails in milliseconds; longer is a still desktop (< aclose's 2 s wait)
REASON_MAX_CHARS = 200  # a library's error text in a log line and an error message (diagnostics bundle)
# FFmpeg's error tags (libavutil/error.h FFERRTAG) as PyAV reports them in ``errno``
AVERROR_EXTERNAL = int.from_bytes(b"EXT ", "little")  # 542398533: "Generic error in an external library"
AVERROR_UNKNOWN = int.from_bytes(b"UNKN", "little")  # 1313558101: "Unknown error occurred"
# vsrc_ddagrab.c, vf_scale_d3d11.c and hwcontext_d3d11va.c phrases (FFmpeg n8.1.3), lower-cased
_BUSY_LINES = ("too many open duplication sessions",)
_DENIED_LINES = ("desktop duplication access denied",)
_UNSUPPORTED_LINES = ("selected output not supported", "failed to enumerate dxgi output")
_PREFLIGHT_DETAILS = frozenset({"busy", "unsupported_display", "remote_session", "driver"})
# a drive or UNC path runs over its folders, spaces included ("C:\Users\Jane Doe\..."), to its last separator
_WINDOWS_PATH = re.compile(r"(?<!\w)(?:[A-Za-z]:[\\/]|\\\\)(?:[^\\/\r\n'\";]*[\\/])*[^\s'\";,:]*")
_BACKSLASH_RUN = re.compile(r"\S*\\\S*")  # whatever is left with a backslash in it is part of a path
_POSIX_PATH = re.compile(r"(?<![\w.~/])(?:~|\.{1,2})?/(?:[^\s/'\";,]+/)+[^\s'\";,:]*")
_LONG_RUN = re.compile(r"\S{49,}")  # a graph, a hash, a token: never useful in a reason, possibly sensitive
# bytes per row of each plane, from the frame's width (padding excluded), for the formats the sources deliver
_ROW_BYTES: dict[str, tuple[Callable[[int], int], ...]] = {
    "nv12": (lambda w: w, lambda w: (w + 1) // 2 * 2),
    "yuv420p": (lambda w: w, lambda w: (w + 1) // 2, lambda w: (w + 1) // 2),
    "bgra": (lambda w: w * 4,),
}
_level_lock = threading.Lock()
_level_users = 0
_level_was_set = False


def ddagrab_graph(output_idx: int, width: int, height: int, fps: int) -> str:
    return (
        f"ddagrab=output_idx={int(output_idx)}:framerate={int(fps)}:draw_mouse=1:dup_frames=1,"
        f"scale_d3d11=w={int(width)}:h={int(height)}:format=nv12,hwdownload,format=nv12"
    )


def _remote_session_now() -> bool:
    """Windows' answer to ``GetSystemMetrics(SM_REMOTESESSION)``; False off Windows or when it cannot be asked."""
    if sys.platform != "win32":
        return False
    try:
        from ...platform.windows.dxgi import remote_session_active

        return remote_session_active()
    except Exception:  # noqa: BLE001 - an unanswered question is "not remote", never a crash
        return False


def _logged_text(exc: BaseException, logged: Iterable[tuple[int, str, str]]) -> list[str]:
    """FFmpeg's ERROR lines for this failure: the ones captured on this thread, else the one PyAV kept in ``exc.log``
    (its ``(level, name, message)`` tuple, None at PyAV's default log level). Deduplicated, stripped."""
    lines: list[str] = []
    for _level, _name, message in logged:
        line = str(message).strip()
        if line and line not in lines:
            lines.append(line)
    last = getattr(exc, "log", None)
    if not lines and isinstance(last, tuple) and len(last) == 3:
        line = str(last[2]).strip()
        if line:
            lines.append(line)
    return lines


def classify_capture_failure(
    exc: BaseException,
    logged: Iterable[tuple[int, str, str]] = (),
    *,
    remote_session: Callable[[], bool] = _remote_session_now,
) -> str:
    """The contract's ``SCREEN_CAPTURE_UNAVAILABLE`` detail for a PyAV failure, from the exception's class and
    ``errno`` and from FFmpeg's own ERROR lines (:func:`ffmpeg_error_lines`, or ``exc.log``) — never from ``str(exc)``,
    which quotes the graph. ``vsrc_ddagrab.c`` (n8.1.3) after ``DuplicateOutput1``: ``DXGI_ERROR_NOT_CURRENTLY_AVAILABLE``
    → "Too many open duplication sessions" + ``AVERROR(EBUSY)`` (``busy``); ``E_ACCESSDENIED`` → "Desktop duplication
    access denied" + ``AVERROR(EPERM)`` (PyAV ``PermissionError``: ``remote_session`` when Windows says this is a remote
    session, else ``driver`` — the secure desktop is refused earlier by the agent); ``DXGI_ERROR_UNSUPPORTED`` →
    "Selected output not supported" and an output the adapter lacks → "Failed to enumerate DXGI output N", both
    ``AVERROR_EXTERNAL`` (``unsupported_display``); "Failed to create D3D11VA device" (``AVERROR_UNKNOWN``),
    ``scale_d3d11``'s video-processor lines and everything else: ``driver``."""
    code = getattr(exc, "errno", None)
    text = " ".join(_logged_text(exc, logged)).lower()
    if code == errno_codes.EBUSY or any(line in text for line in _BUSY_LINES):
        return "busy"
    if (
        isinstance(exc, PermissionError)
        or code in (errno_codes.EACCES, errno_codes.EPERM)
        or any(line in text for line in _DENIED_LINES)
    ):
        return "remote_session" if remote_session() else "driver"
    if any(line in text for line in _UNSUPPORTED_LINES):
        return "unsupported_display"
    return "driver"


def sanitise_reason(text: str, limit: int = REASON_MAX_CHARS) -> str:
    """A library's error text made fit for a log line and an error message (the diagnostics bundle keeps both): one
    line of printable characters, file paths and long unbroken strings (a graph, a hash, a token) replaced, at most
    ``limit`` characters. The log pipeline still masks addresses and secrets on top of this."""
    text = _WINDOWS_PATH.sub("[path]", text)
    text = _BACKSLASH_RUN.sub("[path]", text)
    text = _POSIX_PATH.sub("[path]", text)
    text = _LONG_RUN.sub("[...]", text)
    text = "".join(ch if ch.isprintable() else " " for ch in text)
    return " ".join(text.split())[:limit]


@contextlib.contextmanager
def ffmpeg_error_lines() -> Iterator[list[tuple[int, str, str]]]:
    """FFmpeg's ERROR log lines raised on this thread inside the block, as PyAV's ``(level, name, message)``.

    PyAV's default log level (None) installs a no-op callback that drops FFmpeg's log, and with it the only place
    ``ddagrab`` names the DXGI cause. The level is ERROR while any thread is inside such a block (counted under a lock:
    the helper thread opening a graph and the reader thread never undo each other) and only if nobody set a level
    (one someone set is left alone); ``av.logging.Capture`` keeps this thread's lines out of Python logging. Lines
    another thread logs meanwhile go to the ``libav.*`` loggers (the worker's stderr), which the agent relogs redacted."""
    import av.logging

    global _level_users, _level_was_set
    with _level_lock:
        if _level_users == 0:
            _level_was_set = av.logging.get_level() is not None
            if not _level_was_set:
                av.logging.set_level(av.logging.ERROR)
        _level_users += 1
    try:
        with av.logging.Capture() as lines:
            yield lines
    finally:
        with _level_lock:
            _level_users -= 1
            if _level_users == 0 and not _level_was_set:
                av.logging.set_level(None)


def ffmpeg_error_text(exc: BaseException, logged: Iterable[tuple[int, str, str]] = ()) -> str:
    """FFmpeg's own words for a failure, sanitised: the error lines it logged first (``ddagrab``'s DXGI cause; from
    the capture, else ``exc.log``), then PyAV's error string and number, without the "filename" PyAV appends (for
    ``lavfi`` that is the whole graph)."""
    parts = _logged_text(exc, logged)
    strerror, errno = getattr(exc, "strerror", None), getattr(exc, "errno", None)
    if isinstance(strerror, str) and strerror:
        parts.append(f"{strerror} (errno {errno})" if isinstance(errno, int) else strerror)
    else:
        parts.append(f"{exc.__class__.__name__}: {exc}" if str(exc) else exc.__class__.__name__)
    return sanitise_reason("; ".join(parts))


def capture_error(
    prefix: str,
    exc: BaseException,
    logged: Iterable[tuple[int, str, str]] = (),
    *,
    remote_session: Callable[[], bool] = _remote_session_now,
) -> ProtocolError:
    """The honest contract error for a PyAV failure: ``prefix: <FFmpeg's words>`` and the structural detail."""
    reason = ffmpeg_error_text(exc, logged)
    return ProtocolError(
        "SCREEN_CAPTURE_UNAVAILABLE",
        f"{prefix}: {reason}",
        detail={"detail": classify_capture_failure(exc, logged, remote_session=remote_session)},
    )


def frame_signature(frame: Any) -> tuple[int, int, str, int]:
    """``(width, height, format, CRC-32 of the visible bytes of every plane)``: two frames with the same signature show
    the same picture. Row padding is skipped (``hwdownload`` never writes it), so it is compared row by row where the
    line size exceeds the row; a format this module does not know is hashed whole."""
    width, height, name = int(frame.width), int(frame.height), str(frame.format.name)
    rows_of = _ROW_BYTES.get(name)
    crc = 0
    for index, plane in enumerate(frame.planes):
        data = memoryview(plane).cast("B")
        line = int(plane.line_size)
        row = rows_of[index](width) if rows_of is not None and index < len(rows_of) else line
        if row >= line or line <= 0:
            crc = zlib.crc32(data, crc)
            continue
        for y in range(int(plane.height)):
            crc = zlib.crc32(data[y * line : y * line + row], crc)
    return (width, height, name, crc)


@dataclass(slots=True)
class OutputChoice:
    """What :func:`locate_ddagrab_output` decided: the ``output_idx`` to give ``ddagrab`` (the surface index when DXGI
    could not answer), or ``skip`` with the ``reason`` when the monitor is driven by another adapter."""

    output_idx: int | None
    skip: bool = False
    reason: str = ""


def locate_ddagrab_output(
    *, left: int, top: int, width: int, height: int, fallback_idx: int, enumerate: Callable[[], Any] | None = None
) -> OutputChoice:
    """``ddagrab``'s ``output_idx`` for the monitor at this rectangle: its index among the DEFAULT adapter's outputs
    (``dxgi.locate_output`` on ``dxgi.enumerate_adapters()``); ``skip`` when the monitor is an output of another
    adapter (``lavfi`` cannot pick the adapter: Windows.Graphics.Capture is the path); ``fallback_idx`` (today's
    behaviour) when DXGI cannot be asked or does not list the monitor. Never raises."""
    if enumerate is None:
        if sys.platform != "win32":
            return OutputChoice(fallback_idx, reason="not Windows")
        from ...platform.windows import dxgi

        enumerate = dxgi.enumerate_adapters
    from ...platform.windows.dxgi import locate_output

    try:
        found = locate_output(enumerate(), left=left, top=top, width=width, height=height)
    except Exception as exc:  # noqa: BLE001 - DXGI unavailable: the surface index as before
        log.warning("DXGI could not list the outputs; using the surface index", error=exc.__class__.__name__)
        return OutputChoice(fallback_idx, reason=f"DXGI lookup failed ({exc.__class__.__name__})")
    if found is None:
        log.warning("the monitor is not among the DXGI outputs; using the surface index", surface_index=fallback_idx)
        return OutputChoice(fallback_idx, reason="not among the DXGI outputs")
    if not found.on_default_adapter:
        return OutputChoice(
            None,
            skip=True,
            reason=(
                f"the monitor is output {found.output.index} of adapter {found.adapter.index} "
                f"({sanitise_reason(found.adapter.description, 48)}), not of the default adapter"
            ),
        )
    return OutputChoice(found.output.index)


def preflight_skip(verdict: Any) -> ProtocolError | None:
    """The error that makes :class:`DdaWithFallback` skip Desktop Duplication when the agent's pre-flight found it
    failing (the source spec's ``preflight`` entry: ``detail``, ``hresult``, ``hresult_name``; ``screen/preflight.py``
    ``preflight_verdict``); None when the spec carries no verdict. The detail is the contract's, the HRESULT's name is
    the reason the fallback warning carries."""
    if not isinstance(verdict, dict) or not isinstance(verdict.get("detail"), str):
        return None
    detail = verdict["detail"] if verdict["detail"] in _PREFLIGHT_DETAILS else "driver"
    name = verdict.get("hresult_name") or verdict.get("hresult") or "a failure"
    return ProtocolError(
        "SCREEN_CAPTURE_UNAVAILABLE",
        f"Desktop Duplication skipped: the pre-flight answered {sanitise_reason(str(name), 48)}",
        detail={"detail": detail},
    )


class _Opened:
    def __init__(self, container: Any, frames: Any) -> None:
        self.container = container
        self.frames = frames

    def close(self) -> None:
        # The decode generator holds a reference into the container: finalising it AFTER the container is closed
        # aborts the process inside FFmpeg, so it is closed first, explicitly (see fake.py).
        with contextlib.suppress(Exception):
            self.frames.close()
        with contextlib.suppress(Exception):
            self.container.close()


class _PendingOpen:
    """``av.open`` on a thread of its own. The graph is taken by the reader (:meth:`collect`) once the helper has it;
    an open abandoned meanwhile (:meth:`abandon`) is closed by whichever side holds the container when both have
    passed the lock: the helper when ``av.open`` returns, the caller when the helper had already finished."""

    def __init__(self, graph: str) -> None:
        self.graph = graph
        self.done = threading.Event()
        self.error: ProtocolError | None = None
        self._result: _Opened | None = None
        self._abandoned = False
        self._lock = threading.Lock()
        self.thread = threading.Thread(target=self._run, name="dome-ddagrab-open", daemon=True)

    def start(self) -> None:
        self.thread.start()

    def _run(self) -> None:
        import av

        try:
            container: Any = None
            with ffmpeg_error_lines() as logged:
                try:
                    container = av.open(self.graph, format="lavfi")
                    frames = container.decode(container.streams.video[0])
                except Exception as exc:  # noqa: BLE001 - every FFmpeg failure becomes the honest contract error
                    if container is not None:
                        with contextlib.suppress(Exception):
                            container.close()
                    self.error = capture_error("Desktop Duplication could not start", exc, logged)
                    return
            opened = _Opened(container, frames)
            with self._lock:
                if self._abandoned:
                    opened.close()  # nobody will read it: the thread that opened it closes it
                    return
                self._result = opened
        finally:
            self.done.set()

    def collect(self) -> _Opened | None:
        """The reader thread: the opened graph once the helper has it, None while it is still pending; raises the
        open's error. The graph is handed over once."""
        if not self.done.is_set():
            return None
        if self.error is not None:
            raise self.error
        with self._lock:
            opened, self._result = self._result, None
        return opened

    def abandon(self) -> None:
        with self._lock:
            self._abandoned = True
            opened, self._result = self._result, None
        if opened is not None:
            opened.close()  # the helper is done with it and nobody read it


class DdagrabSource:
    # declared here because the constructor's platform guard hides the assignments from the Linux type-check
    output_idx: int
    width: int
    height: int
    fps: int
    open_wait: float
    _container: Any
    _frames: Any
    _pending: _PendingOpen | None
    _signature: tuple[int, int, str, int] | None
    rebuilds: int
    reopen_failures: int
    _reopen_at: float
    _reported_running: bool
    _failure_lines: list[tuple[int, str, str]]
    frames_repeated: int

    def __init__(self, output_idx: int, width: int, height: int, fps: int) -> None:
        if sys.platform != "win32":
            raise ProtocolError(
                "SCREEN_CAPTURE_UNAVAILABLE",
                "Desktop Duplication needs Windows",
                detail={"detail": "unsupported_display"},
            )
        self.output_idx = output_idx
        self.width, self.height = int(width) // 2 * 2, int(height) // 2 * 2
        self.fps = max(1, int(fps))
        self.open_wait = OPEN_WAIT_SECONDS
        self._container = None
        self._frames = None
        self._pending = None
        self._signature = None
        self.rebuilds = 0
        self.reopen_failures = 0
        self._reopen_at = 0.0
        self._reported_running = False
        self._failure_lines = []  # FFmpeg's lines over a run of read failures (PyAV skips repeated lines)
        self.frames_repeated = 0  # re-sent frames dropped (dup_frames=1): the still-desktop evidence in the log

    def open(self) -> None:
        """Start the graph on a helper thread and wait up to ``open_wait``: a failure in that time is raised as the
        classified error; success is adopted; a still desktop (``probe_output_format`` waits for a real frame) leaves
        the open pending, and :meth:`read` answers None until the helper has the graph."""
        pending = _PendingOpen(ddagrab_graph(self.output_idx, self.width, self.height, self.fps))
        self._pending = pending
        pending.start()
        pending.done.wait(self.open_wait)
        if not self._adopt():
            log.info(
                "Desktop Duplication is waiting for the first desktop change",
                output_idx=self.output_idx,
                size=self.size_text,
            )

    @property
    def size_text(self) -> str:
        return f"{self.width}x{self.height}"

    def _adopt(self) -> bool:
        """Take the graph from a finished pending open: True once frames can be read, False while still pending;
        raises the open's error (the pending open is forgotten)."""
        pending = self._pending
        if pending is None:
            return self._frames is not None
        try:
            opened = pending.collect()
        except ProtocolError:
            self._pending = None
            raise
        if opened is None:
            return False
        self._pending = None
        self._container, self._frames = opened.container, opened.frames
        self._signature = None  # the first frame of a new graph is a change
        self.reopen_failures = 0
        if not self._reported_running:  # once: a rebuild after every UAC prompt would repeat it
            self._reported_running = True
            log.info("screen capture source running", source="dda", output_idx=self.output_idx, size=self.size_text)
        return True

    def read(self) -> Any | None:
        if self._frames is None:
            return self._reopen()
        with ffmpeg_error_lines() as logged:
            try:
                frame = next(self._frames)
            except BlockingIOError:  # EAGAIN: no frame yet (the first read of a graph on a still desktop)
                self._restart_frames()  # the raise finished PyAV's generator
                time.sleep(1.0 / self.fps)
                return None
            except Exception as exc:  # noqa: BLE001 - access lost / desktop switch / EOF (StopIteration): rebuild
                failure: Exception | None = exc
                self._failure_lines.extend(logged)  # across the run: PyAV logs an identical repeated line once
            else:
                failure = None
        if failure is not None:
            self.rebuilds += 1
            self.close()
            if self.rebuilds > REBUILD_LIMIT:
                lines, self._failure_lines = self._failure_lines, []
                raise capture_error(
                    f"Desktop Duplication failed {REBUILD_LIMIT} times in a row", failure, lines
                ) from None
            self.reopen_failures = 0
            self._reopen_at = 0.0
            return self._reopen()
        self.rebuilds = 0  # a frame: the next access-lost starts counting afresh
        self._failure_lines.clear()
        signature = frame_signature(frame)
        if signature == self._signature:
            self.frames_repeated += 1  # ddagrab re-sent its last frame (dup_frames=1): the desktop is still
            return None
        self._signature = signature
        frame.time_base = VIDEO_TIME_BASE
        return frame

    def _restart_frames(self) -> None:
        """A fresh decode iterator over the same container: the one that raised is finished (every later ``next`` would
        be StopIteration). Should the container refuse one, the source is closed and the next read rebuilds the graph."""
        frames, self._frames = self._frames, None
        if frames is not None:
            with contextlib.suppress(Exception):
                frames.close()
        try:
            self._frames = self._container.decode(self._container.streams.video[0])
        except Exception as exc:  # noqa: BLE001 - the container is unusable: rebuilt on the next read
            log.warning("Desktop Duplication decode iterator could not be restarted", error=exc.__class__.__name__)
            self.close()

    def _reopen(self) -> Any | None:
        """Rebuild the graph, wait for a pending open, or wait out the back-off; None either way (no frame this round)."""
        if self._pending is not None:
            self._pending.done.wait(1.0 / self.fps)  # bounded: one frame interval, then the track may close us
            try:
                self._adopt()
            except ProtocolError:
                if self._note_reopen_failure():
                    raise
            return None
        now = time.monotonic()
        if now < self._reopen_at:
            time.sleep(min(1.0 / self.fps, self._reopen_at - now))
            return None
        try:
            self.open()
        except ProtocolError:
            if self._note_reopen_failure():
                raise
        return None

    def _note_reopen_failure(self) -> bool:
        """One more failed reopen: True when the source gives up (the caller re-raises), else the back-off is set."""
        self.reopen_failures += 1
        if self.reopen_failures >= REOPEN_ATTEMPTS:
            return True
        step = REOPEN_BACKOFF_SECONDS[min(self.reopen_failures, len(REOPEN_BACKOFF_SECONDS)) - 1]
        self._reopen_at = time.monotonic() + step
        return False

    def close(self) -> None:
        pending, self._pending = self._pending, None
        if pending is not None:
            pending.abandon()  # a graph still opening is closed by the thread that opens it
        frames, self._frames = self._frames, None
        if frames is not None:
            with contextlib.suppress(Exception):
                frames.close()  # before the container: see fake.py
        if self._container is not None:
            try:
                self._container.close()
            except Exception:  # noqa: BLE001, S110
                pass
            self._container = None
        self._signature = None


class DdaWithFallback:
    """Desktop Duplication first, Windows.Graphics.Capture when it cannot open (design §5.2: hybrid-GPU laptops,
    a monitor on another adapter, the duplication limit). Built unopened; :meth:`open` runs once, in the track, after
    DTLS connected. ``skip`` (:func:`preflight_skip`: the agent's pre-flight found Desktop Duplication failing) skips
    it outright; else ``locate`` (:func:`locate_ddagrab_output`) runs first and may change the primary's ``output_idx``
    or skip Desktop Duplication altogether; ``on_fallback`` gets Desktop Duplication's error (its message carries
    FFmpeg's reason, or why it was skipped)."""

    def __init__(
        self,
        primary: Any,
        fallback: Callable[[], Any],
        *,
        on_fallback: Callable[[ProtocolError], None],
        locate: Callable[[], OutputChoice] | None = None,
        skip: ProtocolError | None = None,
    ) -> None:
        self._primary = primary
        self._fallback = fallback
        self._on_fallback = on_fallback
        self._locate = locate
        self._skip = skip
        self._active: Any | None = None
        self.width, self.height = int(primary.width), int(primary.height)

    def open(self) -> None:
        skipped = self._aim_primary()
        if skipped is None:
            try:
                self._primary.open()
                self._active = self._primary
                return
            except ProtocolError as exc:
                self._on_fallback(exc)
        else:
            self._on_fallback(skipped)
        source = self._fallback()
        source.open()
        self._active = source
        self.width, self.height = int(source.width), int(source.height)

    def _aim_primary(self) -> ProtocolError | None:
        """Give Desktop Duplication the right ``output_idx``, or the error that says why it is skipped."""
        if self._skip is not None:
            return self._skip
        if self._locate is None:
            return None
        try:
            choice = self._locate()
        except Exception as exc:  # noqa: BLE001 - the lookup never raises by contract; today's behaviour otherwise
            log.warning("DXGI output lookup raised; using the surface index", error=exc.__class__.__name__)
            return None
        if choice.skip:
            return ProtocolError(
                "SCREEN_CAPTURE_UNAVAILABLE",
                f"Desktop Duplication skipped: {choice.reason}",
                detail={"detail": "unsupported_display"},
            )
        if choice.output_idx is not None and choice.output_idx != getattr(self._primary, "output_idx", None):
            log.info(
                "Desktop Duplication output index differs from the surface index",
                surface_index=getattr(self._primary, "output_idx", None),
                output_idx=choice.output_idx,
            )
            self._primary.output_idx = choice.output_idx
        return None

    def read(self) -> Any | None:
        if self._active is None:
            raise ProtocolError(
                "SCREEN_CAPTURE_UNAVAILABLE", "the capture source is not open", detail={"detail": "driver"}
            )
        return self._active.read()

    def close(self) -> None:
        active, self._active = self._active, None
        if active is not None:
            active.close()


__all__ = [
    "AVERROR_EXTERNAL",
    "AVERROR_UNKNOWN",
    "OPEN_WAIT_SECONDS",
    "REASON_MAX_CHARS",
    "REBUILD_LIMIT",
    "REOPEN_ATTEMPTS",
    "DdaWithFallback",
    "DdagrabSource",
    "OutputChoice",
    "capture_error",
    "classify_capture_failure",
    "ddagrab_graph",
    "ffmpeg_error_lines",
    "ffmpeg_error_text",
    "frame_signature",
    "locate_ddagrab_output",
    "preflight_skip",
    "sanitise_reason",
]
