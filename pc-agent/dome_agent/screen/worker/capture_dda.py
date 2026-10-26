"""Desktop Duplication through FFmpeg's ``ddagrab`` (design §5.2; the primary Windows source).

Opened by PyAV as an ``lavfi`` graph: ``ddagrab=output_idx=N:framerate=30:draw_mouse=1:dup_frames=0,
scale_d3d11=w=W:h=H:format=nv12,hwdownload,format=nv12``. GPU colour conversion and scaling; ``dup_frames=0`` sends
nothing while the screen is still; the ``lavfi`` device emits ``wrapped_avframe`` packets that PyAV's ``decode()``
unwraps (the one decoder the shipping build enables).

* **A still desktop is not an error.** With nothing new, libavdevice's ``lavfi`` read answers ``AVERROR(EAGAIN)``,
  which PyAV raises as ``av.error.BlockingIOError``: :meth:`DdagrabSource.read` waits one frame interval and returns
  None (the track's 1 fps floor keeps the picture alive). If the device blocks instead, the floor cannot fire: the
  device round checks it, and the fallback is PyAV's ``Graph`` pull API (ADR-0002 D4).
* **Access lost / a desktop switch** (every UAC prompt): ``ddagrab`` errors and does not recreate itself, so ``read``
  rebuilds the graph; only three such errors in a row WITHOUT a frame in between give up (the count resets on every
  frame, so a long session survives any number of UAC prompts).
* **A reopen that fails** (the secure desktop is still up) is retried with a back-off and reports None meanwhile:
  the agent's secure-desktop pause and the indicator grace decide when the session ends, not this source; it gives up
  only after :data:`REOPEN_ATTEMPTS` failed reopens while capture is running.
* :class:`DdaWithFallback` tries Desktop Duplication and falls back to Windows.Graphics.Capture. Constructing either
  opens nothing: the only ``open()`` is the track's, after DTLS connected (``rules.screen_sessions``).

Failures are reported honestly as ``SCREEN_CAPTURE_UNAVAILABLE`` with a detail (``driver`` for a graph that cannot
open, ``busy`` for the duplication limit, ``unsupported_display`` for a hybrid-GPU ``UNSUPPORTED`` or an output the
default adapter does not have): the worker then tries :mod:`capture_wgc`, and the agent tells the phone.

* **FFmpeg's own words travel with the error.** ``ddagrab`` names the DXGI cause only in its log ("Selected output
  not supported", "Failed to enumerate DXGI output 1", "Too many open duplication sessions", "Desktop duplication
  access denied"); its return code is mostly ``AVERROR_EXTERNAL`` ("Generic error in an external library"), and PyAV
  drops FFmpeg's log at its default level. :meth:`DdagrabSource.open` therefore collects the ERROR lines of its own
  thread while the graph opens (:func:`ffmpeg_error_lines`), and the error's message carries them, sanitised by
  :func:`sanitise_reason` (one line, no paths, no long strings, at most 200 characters), into the worker's log and
  the agent's. The detail is classified from that text, never from PyAV's exception string, which quotes the graph
  (``ddagrab=output_idx=0:...``) and so used to match ``output_idx`` for every failure.
* **One adapter only.** ``ddagrab`` (FFmpeg n8.1.3, ``libavfilter/vsrc_ddagrab.c``) duplicates output ``output_idx``
  of the adapter behind its D3D11VA device: the default adapter, unless a ``hw_device_ctx`` is handed to the filter,
  which libavdevice's ``lavfi`` cannot do. It has no adapter option (only ``output_idx``, ``draw_mouse``,
  ``framerate``, ``video_size``, ``offset_x``/``offset_y``, ``output_fmt``, ``allow_fallback``, ``force_fmt``,
  ``dup_frames``), so a monitor on another adapter (a hybrid-GPU laptop's external screen) is left to
  Windows.Graphics.Capture, which composes every adapter's output.

Windows only; **not yet verified on a device**: whether PyAV drives this graph is Phase 0 item 1 and the fallback is
PyAV's ``Graph`` API.
"""

from __future__ import annotations

import contextlib
import re
import sys
import time
from collections.abc import Callable, Iterable, Iterator
from typing import Any

import structlog
from dome_protocol import ProtocolError

from .encode import VIDEO_TIME_BASE

log = structlog.get_logger(__name__)

REBUILD_LIMIT = 3  # access-lost errors in a row, without a frame in between, before the source gives up
REOPEN_ATTEMPTS = 10  # failed reopens (while capture runs) before the source gives up
REOPEN_BACKOFF_SECONDS = (0.25, 0.5, 1.0, 2.0)
REASON_MAX_CHARS = 200  # a library's error text in a log line and an error message (diagnostics bundle)
# a drive or UNC path runs over its folders, spaces included ("C:\Users\Jane Doe\..."), to its last separator
_WINDOWS_PATH = re.compile(r"(?<!\w)(?:[A-Za-z]:[\\/]|\\\\)(?:[^\\/\r\n'\";]*[\\/])*[^\s'\";,:]*")
_BACKSLASH_RUN = re.compile(r"\S*\\\S*")  # whatever is left with a backslash in it is part of a path
_POSIX_PATH = re.compile(r"(?<![\w.~/])(?:~|\.{1,2})?/(?:[^\s/'\";,]+/)+[^\s'\";,:]*")
_LONG_RUN = re.compile(r"\S{49,}")  # a graph, a hash, a token: never useful in a reason, possibly sensitive


def ddagrab_graph(output_idx: int, width: int, height: int, fps: int) -> str:
    return (
        f"ddagrab=output_idx={int(output_idx)}:framerate={int(fps)}:draw_mouse=1:dup_frames=0,"
        f"scale_d3d11=w={int(width)}:h={int(height)}:format=nv12,hwdownload,format=nv12"
    )


def classify_capture_error(message: str) -> str:
    """Map FFmpeg's error text (:func:`ffmpeg_error_text`, never PyAV's exception string: it quotes the graph) to the
    contract's ``SCREEN_CAPTURE_UNAVAILABLE`` detail. The phrases are ``vsrc_ddagrab.c``'s (FFmpeg n8.1.3)."""
    text = message.lower()
    if "not_currently_available" in text or "too many" in text or "busy" in text:
        return "busy"  # DXGI_ERROR_NOT_CURRENTLY_AVAILABLE: "Too many open duplication sessions" (EBUSY)
    if "not supported" in text or "unsupported" in text or "enumerate dxgi output" in text:
        # DXGI_ERROR_UNSUPPORTED ("Selected output not supported": a hybrid-GPU laptop), or an output the default
        # adapter does not have ("Failed to enumerate DXGI output N": a monitor on another adapter)
        return "unsupported_display"
    if "access" in text and "denied" in text:
        return "remote_session"  # E_ACCESSDENIED: "Desktop duplication access denied"
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

    PyAV's default log level (None) drops FFmpeg's log, and with it the only place ``ddagrab`` names the DXGI cause.
    The level is ERROR only inside the block (and only if it was None: a level someone set is left alone);
    ``av.logging.Capture`` keeps this thread's lines out of Python logging. Lines another thread logs meanwhile go to
    the ``libav.*`` loggers (the worker's stderr), which the agent relogs redacted."""
    import av.logging

    previous = av.logging.get_level()
    if previous is None:
        av.logging.set_level(av.logging.ERROR)
    try:
        with av.logging.Capture() as lines:
            yield lines
    finally:
        if previous is None:
            av.logging.set_level(None)


def ffmpeg_error_text(exc: BaseException, logged: Iterable[tuple[int, str, str]] = ()) -> str:
    """FFmpeg's own words for a failure, sanitised: the error lines it logged first (``ddagrab``'s DXGI cause), then
    PyAV's error string and number, without the "filename" PyAV appends (for ``lavfi`` that is the whole graph)."""
    parts: list[str] = []
    for _level, _name, message in logged:
        line = str(message).strip()
        if line and line not in parts:
            parts.append(line)
    strerror, errno = getattr(exc, "strerror", None), getattr(exc, "errno", None)
    if isinstance(strerror, str) and strerror:
        parts.append(f"{strerror} (errno {errno})" if isinstance(errno, int) else strerror)
    else:
        parts.append(f"{exc.__class__.__name__}: {exc}" if str(exc) else exc.__class__.__name__)
    return sanitise_reason("; ".join(parts))


class DdagrabSource:
    # declared here because the constructor's platform guard hides the assignments from the Linux type-check
    output_idx: int
    width: int
    height: int
    fps: int
    _container: Any
    _frames: Any
    rebuilds: int
    reopen_failures: int
    _reopen_at: float
    _reported_running: bool

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
        self._container = None
        self._frames = None
        self.rebuilds = 0
        self.reopen_failures = 0
        self._reopen_at = 0.0
        self._reported_running = False

    def open(self) -> None:
        import av

        container: Any = None
        with ffmpeg_error_lines() as logged:
            try:
                container = av.open(ddagrab_graph(self.output_idx, self.width, self.height, self.fps), format="lavfi")
                frames = container.decode(container.streams.video[0])
            except Exception as exc:  # noqa: BLE001 - every FFmpeg failure becomes the honest contract error
                if container is not None:
                    with contextlib.suppress(Exception):
                        container.close()
                reason = ffmpeg_error_text(exc, logged)
                raise ProtocolError(
                    "SCREEN_CAPTURE_UNAVAILABLE",
                    f"Desktop Duplication could not start: {reason}",
                    detail={"detail": classify_capture_error(reason)},
                ) from None
        self._container, self._frames = container, frames
        if not self._reported_running:  # once: a rebuild after every UAC prompt would repeat it
            self._reported_running = True
            log.info("screen capture source running", source="dda", output_idx=self.output_idx, size=self.size_text)

    @property
    def size_text(self) -> str:
        return f"{self.width}x{self.height}"

    def read(self) -> Any | None:
        if self._frames is None:
            return self._reopen()
        try:
            frame = next(self._frames)
        except StopIteration:
            return None
        except BlockingIOError:  # EAGAIN (av.error.BlockingIOError): a still desktop, nothing new to deliver
            time.sleep(1.0 / self.fps)
            return None
        except Exception as exc:  # noqa: BLE001 - access lost / desktop switch: rebuild the graph
            self.rebuilds += 1
            self.close()
            if self.rebuilds > REBUILD_LIMIT:
                reason = ffmpeg_error_text(exc)
                raise ProtocolError(
                    "SCREEN_CAPTURE_UNAVAILABLE",
                    f"Desktop Duplication failed {REBUILD_LIMIT} times in a row: {reason}",
                    detail={"detail": classify_capture_error(reason)},
                ) from None
            self.reopen_failures = 0
            self._reopen_at = 0.0
            return self._reopen()
        self.rebuilds = 0  # a frame: the next access-lost starts counting afresh
        frame.time_base = VIDEO_TIME_BASE
        return frame

    def _reopen(self) -> Any | None:
        """Rebuild the graph, or wait out the back-off; None either way (no frame this round)."""
        now = time.monotonic()
        if now < self._reopen_at:
            time.sleep(min(1.0 / self.fps, self._reopen_at - now))
            return None
        try:
            self.open()
        except ProtocolError:
            self.reopen_failures += 1
            if self.reopen_failures >= REOPEN_ATTEMPTS:
                raise
            step = REOPEN_BACKOFF_SECONDS[min(self.reopen_failures, len(REOPEN_BACKOFF_SECONDS)) - 1]
            self._reopen_at = time.monotonic() + step
            return None
        self.reopen_failures = 0
        return None

    def close(self) -> None:
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


class DdaWithFallback:
    """Desktop Duplication first, Windows.Graphics.Capture when it cannot open (design §5.2: hybrid-GPU laptops,
    a monitor on another adapter, the duplication limit). Built unopened; :meth:`open` runs once, in the track, after
    DTLS connected. ``on_fallback`` gets Desktop Duplication's error (its message carries FFmpeg's reason)."""

    def __init__(
        self, primary: Any, fallback: Callable[[], Any], *, on_fallback: Callable[[ProtocolError], None]
    ) -> None:
        self._primary = primary
        self._fallback = fallback
        self._on_fallback = on_fallback
        self._active: Any | None = None
        self.width, self.height = int(primary.width), int(primary.height)

    def open(self) -> None:
        try:
            self._primary.open()
            self._active = self._primary
            return
        except ProtocolError as exc:
            self._on_fallback(exc)
        source = self._fallback()
        source.open()
        self._active = source
        self.width, self.height = int(source.width), int(source.height)

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
    "REASON_MAX_CHARS",
    "REBUILD_LIMIT",
    "REOPEN_ATTEMPTS",
    "DdaWithFallback",
    "DdagrabSource",
    "classify_capture_error",
    "ddagrab_graph",
    "ffmpeg_error_lines",
    "ffmpeg_error_text",
    "sanitise_reason",
]
