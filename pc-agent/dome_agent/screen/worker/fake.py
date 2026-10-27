"""Frame sources behind the :class:`FrameSource` seam (design §5.2) and the fakes that run on Linux.

``read()`` blocks for at most one frame interval and returns the next **changed** frame, or None when the screen is
still (the ``ddagrab`` source drops the frames the filter re-sends unchanged): the track keeps the last raw frame and
re-encodes it at ≥ 1 fps so the phone's stall detector stays meaningful. Sources:

* :class:`LavfiTestSource`: FFmpeg's ``testsrc`` through the ``lavfi`` device (the same device the Windows source
  uses for ``ddagrab``), converted to yuv420p in the graph. ``still`` makes it deliver one frame and then nothing.
* :class:`SyntheticSource`: yuv420p frames with a moving bar, built from bytes (no numpy); ``still`` as above.

Nothing here reads a real screen; the Windows sources are :mod:`capture_dda` and :mod:`capture_wgc`.
"""

from __future__ import annotations

import contextlib
import fractions
import time
from typing import Any, Protocol

from dome_protocol import ProtocolError

from .encode import VIDEO_TIME_BASE


class FrameSource(Protocol):
    """A synchronous capture source; the track drives it from a worker thread."""

    width: int
    height: int

    def open(self) -> None: ...

    def read(self) -> Any | None:
        """The next changed frame as an ``av.VideoFrame`` (yuv420p or nv12), or None when nothing changed."""
        ...

    def close(self) -> None: ...


def _blank_frame(width: int, height: int, luma: int = 16) -> Any:
    import av

    frame = av.VideoFrame(width, height, "yuv420p")
    frame.planes[0].update(bytes([luma]) * frame.planes[0].buffer_size)
    frame.planes[1].update(bytes([128]) * frame.planes[1].buffer_size)
    frame.planes[2].update(bytes([128]) * frame.planes[2].buffer_size)
    frame.time_base = VIDEO_TIME_BASE
    return frame


class SyntheticSource:
    """A grey field with a white vertical bar that moves one step per frame (bytes only, no numpy)."""

    def __init__(self, width: int, height: int, fps: int, *, still: bool = False) -> None:
        self.width, self.height = int(width) // 2 * 2, int(height) // 2 * 2
        self.fps = max(1, int(fps))
        self.still = still
        self._n = 0
        self._next_at = 0.0

    def open(self) -> None:
        self._next_at = time.monotonic()

    def read(self) -> Any | None:
        now = time.monotonic()
        if now < self._next_at:
            time.sleep(min(1.0 / self.fps, self._next_at - now))
        self._next_at = max(self._next_at + 1.0 / self.fps, time.monotonic())
        if self.still and self._n >= 1:
            return None
        frame = _blank_frame(self.width, self.height, luma=90)
        stride = frame.planes[0].line_size
        bar_x = (self._n * 8) % max(1, self.width - 32)
        rows = bytearray(frame.planes[0])
        for y in range(self.height):
            start = y * stride + bar_x
            rows[start : start + 32] = b"\xeb" * 32
        frame.planes[0].update(bytes(rows))
        self._n += 1
        return frame

    def close(self) -> None:
        return None


class LavfiTestSource:
    """``testsrc`` through FFmpeg's ``lavfi`` input device (PyAV ``av.open(..., format="lavfi")``), the path the
    Windows ``ddagrab`` source takes too (design §5.2: the device emits ``wrapped_avframe`` packets, which PyAV's
    ``decode()`` unwraps)."""

    def __init__(self, width: int, height: int, fps: int, *, still: bool = False) -> None:
        self.width, self.height = int(width) // 2 * 2, int(height) // 2 * 2
        self.fps = max(1, int(fps))
        self.still = still
        self._container: Any = None
        self._frames: Any = None
        self._n = 0
        self._next_at = 0.0

    def open(self) -> None:
        import av

        graph = f"testsrc=size={self.width}x{self.height}:rate={self.fps},format=yuv420p"
        try:
            self._container = av.open(graph, format="lavfi")
        except Exception as exc:  # noqa: BLE001 - no lavfi in this build: honest, never silent
            raise ProtocolError(
                "SCREEN_CAPTURE_UNAVAILABLE", f"lavfi testsrc unavailable ({exc.__class__.__name__})"
            ) from None
        self._frames = self._container.decode(self._container.streams.video[0])
        self._next_at = time.monotonic()

    def read(self) -> Any | None:
        now = time.monotonic()
        if now < self._next_at:
            time.sleep(min(1.0 / self.fps, self._next_at - now))
        self._next_at = max(self._next_at + 1.0 / self.fps, time.monotonic())
        if self.still and self._n >= 1:
            return None
        frame = next(self._frames)
        self._n += 1
        frame.time_base = VIDEO_TIME_BASE
        return frame

    def close(self) -> None:
        # The decode generator holds a reference into the container: finalising it AFTER the container is closed
        # aborts the process inside FFmpeg, so it is closed first, explicitly.
        frames, self._frames = self._frames, None
        if frames is not None:
            with contextlib.suppress(Exception):
                frames.close()
        if self._container is not None:
            try:
                self._container.close()
            except Exception:  # noqa: BLE001, S110
                pass
            self._container = None


def build_fake_source(spec: dict[str, Any], width: int, height: int, fps: int) -> FrameSource:
    """``source.pattern``: ``testsrc`` (default), ``synthetic``, ``still`` (synthetic, one frame then nothing),
    ``still_testsrc``."""
    pattern = str(spec.get("pattern", "testsrc"))
    if pattern == "synthetic":
        return SyntheticSource(width, height, fps)
    if pattern == "still":
        return SyntheticSource(width, height, fps, still=True)
    if pattern == "still_testsrc":
        return LavfiTestSource(width, height, fps, still=True)
    return LavfiTestSource(width, height, fps)


def frame_time_base() -> fractions.Fraction:
    return VIDEO_TIME_BASE


__all__ = ["FrameSource", "LavfiTestSource", "SyntheticSource", "build_fake_source"]
