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
open, ``busy`` for the duplication limit, ``unsupported_display`` for a hybrid-GPU ``UNSUPPORTED``): the worker then
tries :mod:`capture_wgc`, and the agent tells the phone. Windows only; **not yet verified on a device**: whether PyAV
drives this graph is Phase 0 item 1 and the fallback is PyAV's ``Graph`` API.
"""

from __future__ import annotations

import contextlib
import sys
import time
from collections.abc import Callable
from typing import Any

from dome_protocol import ProtocolError

from .encode import VIDEO_TIME_BASE

REBUILD_LIMIT = 3  # access-lost errors in a row, without a frame in between, before the source gives up
REOPEN_ATTEMPTS = 10  # failed reopens (while capture runs) before the source gives up
REOPEN_BACKOFF_SECONDS = (0.25, 0.5, 1.0, 2.0)


def ddagrab_graph(output_idx: int, width: int, height: int, fps: int) -> str:
    return (
        f"ddagrab=output_idx={int(output_idx)}:framerate={int(fps)}:draw_mouse=1:dup_frames=0,"
        f"scale_d3d11=w={int(width)}:h={int(height)}:format=nv12,hwdownload,format=nv12"
    )


def classify_capture_error(message: str) -> str:
    """Map an FFmpeg error text to the contract's ``SCREEN_CAPTURE_UNAVAILABLE`` detail."""
    text = message.lower()
    if "not_currently_available" in text or "too many" in text or "busy" in text:
        return "busy"
    if "unsupported" in text or "no such" in text or "output_idx" in text:
        return "unsupported_display"
    if "access" in text and "denied" in text:
        return "remote_session"
    return "driver"


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

    def open(self) -> None:
        import av

        try:
            self._container = av.open(ddagrab_graph(self.output_idx, self.width, self.height, self.fps), format="lavfi")
            self._frames = self._container.decode(self._container.streams.video[0])
        except Exception as exc:  # noqa: BLE001 - every FFmpeg failure becomes the honest contract error
            raise ProtocolError(
                "SCREEN_CAPTURE_UNAVAILABLE",
                f"Desktop Duplication could not start ({exc.__class__.__name__})",
                detail={"detail": classify_capture_error(str(exc))},
            ) from None

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
                raise ProtocolError(
                    "SCREEN_CAPTURE_UNAVAILABLE",
                    f"Desktop Duplication failed {REBUILD_LIMIT} times in a row ({exc.__class__.__name__})",
                    detail={"detail": classify_capture_error(str(exc))},
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
    the duplication limit). Built unopened; :meth:`open` runs once, in the track, after DTLS connected."""

    def __init__(self, primary: Any, fallback: Callable[[], Any], *, on_fallback: Callable[[str], None]) -> None:
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
            detail = exc.detail.get("detail") if isinstance(exc.detail, dict) else None
            self._on_fallback(str(detail or "driver"))
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
    "REBUILD_LIMIT",
    "REOPEN_ATTEMPTS",
    "DdaWithFallback",
    "DdagrabSource",
    "classify_capture_error",
    "ddagrab_graph",
]
