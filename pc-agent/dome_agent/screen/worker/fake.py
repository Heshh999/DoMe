"""Frame sources behind the :class:`FrameSource` seam (design §5.2) and the fakes that run on Linux.

``read()`` blocks for at most one frame interval and returns the next **changed** frame, or None when the screen is
still (the ``ddagrab`` source drops the frames the filter re-sends unchanged): the track keeps the last raw frame and
re-encodes it at ≥ 1 fps so the phone's stall detector stays meaningful. Sources:

* :class:`LavfiTestSource`: FFmpeg's ``testsrc`` through the ``lavfi`` device (the same device the Windows source
  uses for ``ddagrab``), converted to yuv420p in the graph. ``still`` makes it deliver one frame and then nothing.
* :class:`SyntheticSource`: yuv420p frames with a moving bar, built from bytes (no numpy); ``still`` as above.

Both deliver yuv420p unless the source spec says ``pix_fmt: "nv12"`` (2a §3.8): the H.264 candidates take NV12 now,
so the track's conversion to the encoder's format runs on Linux, and an NV12 fake tests the direct path. Neither draws
a pointer; ``cursor_drawn`` says what the start asked for (a pointer "in the picture" or left out), as the Windows
sources report theirs.

:class:`FakeCursorReader` is the pointer behind the cursor poller on Linux (2a §3.7): the source spec's
``cursor_script`` when present, else a walk across the monitor.

Nothing here reads a real screen or pointer; the Windows sources are :mod:`capture_dda` and :mod:`capture_wgc`, the
Windows pointer :mod:`cursor_win`.
"""

from __future__ import annotations

import contextlib
import fractions
import hashlib
import time
from typing import Any, Protocol

from dome_protocol import ProtocolError

from .cursor import NAMED_KINDS
from .encode import VIDEO_TIME_BASE

FAKE_PIX_FMTS = ("yuv420p", "nv12")


class FrameSource(Protocol):
    """A synchronous capture source; the track drives it from a worker thread."""

    width: int
    height: int

    def open(self) -> None: ...

    def read(self) -> Any | None:
        """The next changed frame as an ``av.VideoFrame`` (yuv420p or nv12), or None when nothing changed."""
        ...

    def close(self) -> None: ...


def _blank_frame(width: int, height: int, luma: int = 16, pix_fmt: str = "yuv420p") -> Any:
    """A flat grey frame: Y = ``luma``, U = V = 128, in yuv420p (three planes) or nv12 (Y, then interleaved UV)."""
    import av

    frame = av.VideoFrame(width, height, pix_fmt)
    frame.planes[0].update(bytes([luma]) * frame.planes[0].buffer_size)
    for plane in frame.planes[1:]:
        plane.update(bytes([128]) * plane.buffer_size)
    frame.time_base = VIDEO_TIME_BASE
    return frame


class SyntheticSource:
    """A grey field with a white vertical bar that moves one step per frame (bytes only, no numpy)."""

    kind = "fake"

    def __init__(
        self,
        width: int,
        height: int,
        fps: int,
        *,
        still: bool = False,
        pix_fmt: str = "yuv420p",
        cursor_drawn: bool = True,
    ) -> None:
        self.width, self.height = int(width) // 2 * 2, int(height) // 2 * 2
        self.fps = max(1, int(fps))
        self.still = still
        self.pix_fmt = pix_fmt if pix_fmt in FAKE_PIX_FMTS else "yuv420p"
        self.cursor_drawn = cursor_drawn
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
        frame = _blank_frame(self.width, self.height, luma=90, pix_fmt=self.pix_fmt)
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

    kind = "fake"

    def __init__(
        self,
        width: int,
        height: int,
        fps: int,
        *,
        still: bool = False,
        pix_fmt: str = "yuv420p",
        cursor_drawn: bool = True,
    ) -> None:
        self.width, self.height = int(width) // 2 * 2, int(height) // 2 * 2
        self.fps = max(1, int(fps))
        self.still = still
        self.pix_fmt = pix_fmt if pix_fmt in FAKE_PIX_FMTS else "yuv420p"
        self.cursor_drawn = cursor_drawn
        self._container: Any = None
        self._frames: Any = None
        self._n = 0
        self._next_at = 0.0

    def open(self) -> None:
        import av

        graph = f"testsrc=size={self.width}x{self.height}:rate={self.fps},format={self.pix_fmt}"
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


def build_fake_source(
    spec: dict[str, Any], width: int, height: int, fps: int, *, cursor_drawn: bool = True
) -> FrameSource:
    """``source.pattern``: ``testsrc`` (default), ``synthetic``, ``still`` (synthetic, one frame then nothing),
    ``still_testsrc``; ``source.pix_fmt``: ``yuv420p`` (default) or ``nv12``. ``cursor_drawn``: whether the start
    left the pointer in the picture (anything but ``cursor: "overlay"``)."""
    pattern = str(spec.get("pattern", "testsrc"))
    pix_fmt = "nv12" if spec.get("pix_fmt") == "nv12" else "yuv420p"
    if pattern == "synthetic":
        return SyntheticSource(width, height, fps, pix_fmt=pix_fmt, cursor_drawn=cursor_drawn)
    if pattern == "still":
        return SyntheticSource(width, height, fps, still=True, pix_fmt=pix_fmt, cursor_drawn=cursor_drawn)
    if pattern == "still_testsrc":
        return LavfiTestSource(width, height, fps, still=True, pix_fmt=pix_fmt, cursor_drawn=cursor_drawn)
    return LavfiTestSource(width, height, fps, pix_fmt=pix_fmt, cursor_drawn=cursor_drawn)


# ----- the pointer ---------------------------------------------------------------------------------------------------
WALK_CYCLE = 160  # ticks: the default script repeats
WALK_CUSTOM_TICK = 90
WALK_OFF_TICKS = range(150, 160)
WALK_SHAPES: dict[str, Any] = {
    "arrow": "arrow",
    "ibeam": "ibeam",
    "walk-custom": {"custom": {"w": 24, "h": 24, "hx": 3, "hy": 2}},
}
_FIRST_HANDLE = 0x10000


def synthetic_cursor_bitmap(width: int, height: int) -> bytes:
    """A ``width`` x ``height`` BGRA cursor bitmap (rows ``width`` x 4 bytes): opaque deterministic noise (so its PNG
    is about the size of a real colourful cursor's, more than the 1 KiB a data-channel message may otherwise have)
    with a transparent bottom-right quarter. Made up here; never anyone's screen."""
    out = bytearray()
    stream = b""
    counter = 0
    for y in range(height):
        for x in range(width):
            if x >= width // 2 and y >= height // 2 and width > 1 and height > 1:
                out += b"\x00\x00\x00\x00"
                continue
            if len(stream) < 3:
                stream += hashlib.sha256(counter.to_bytes(4, "big")).digest()
                counter += 1
            out += stream[:3] + b"\xff"
            stream = stream[3:]
    return bytes(out)


class FakeCursorReader:
    """The pointer the cursor poller reads on Linux (``cursor.CursorReader``), one sample per :meth:`read`.

    ``script`` is the source spec's ``cursor_script``: ``{"samples": [[flags, tag, x, y], ...], "shapes": {tag:
    kind | {"custom": {"w", "h", "hx", "hy"}}}}`` with ``x``/``y`` in physical pixels, ``flags`` 1 when the pointer is
    shown, ``tag`` standing for a cursor handle. After the last sample the last one is held (:meth:`add_samples`
    continues the script). A tag without an entry in ``shapes`` is a named kind when it is one (``"arrow"``), else a
    shape the PC cannot render. Python callers may also give ``("custom", bgra, w, h, hx, hy)``. A custom entry from
    JSON gets :func:`synthetic_cursor_bitmap`.

    Without a script: a walk over ``rect`` (2 px per tick diagonally; ``arrow`` and ``ibeam`` change every 30 ticks;
    a 24 x 24 custom shape at tick 90; off the monitor at ticks 150-159), repeating every 160 ticks."""

    def __init__(self, script: Any = None, *, rect: tuple[int, int, int, int] = (0, 0, 1920, 1080)) -> None:
        self.rect = tuple(int(v) for v in rect)
        self.reads = 0
        self._handles: dict[str, int] = {}
        self._tags: dict[int, str] = {}
        self._samples: list[tuple[int, str, int, int]] | None = None
        self._index = 0
        self._shapes: dict[str, Any] = dict(WALK_SHAPES)
        if script is not None:
            if not isinstance(script, dict) or not isinstance(script.get("samples"), list):
                raise ValueError("cursor_script needs a list of samples")
            samples = []
            for sample in script["samples"]:
                flags, tag, px, py = sample
                samples.append((int(flags), str(tag), int(px), int(py)))
            if not samples:
                raise ValueError("cursor_script has no samples")
            self._samples = samples
            shapes = script.get("shapes", {})
            if shapes is None:
                shapes = {}
            if not isinstance(shapes, dict):
                raise ValueError("cursor_script shapes must be a mapping")
            self._shapes = {str(k): v for k, v in shapes.items()}

    def add_samples(self, samples: list[Any]) -> None:
        """Tests step a scripted pointer: the reader goes on with these after the sample it holds."""
        if self._samples is None:
            raise ValueError("the walk takes no samples")
        for flags, tag, px, py in samples:
            self._samples.append((int(flags), str(tag), int(px), int(py)))

    def _handle(self, tag: str) -> int:
        if not tag:
            return 0
        handle = self._handles.get(tag)
        if handle is None:
            handle = _FIRST_HANDLE + len(self._handles)
            self._handles[tag] = handle
            self._tags[handle] = tag
        return handle

    def _walk(self, tick: int) -> tuple[int, str, int, int]:
        left, top, width, height = self.rect
        t = tick % WALK_CYCLE
        x = left + (40 + 2 * t) % max(1, width)
        y = top + (40 + 2 * t) % max(1, height)
        if t in WALK_OFF_TICKS:
            x = left - 10  # on another monitor
        tag = "walk-custom" if t == WALK_CUSTOM_TICK else ("arrow" if (t // 30) % 2 == 0 else "ibeam")
        return 1, tag, x, y

    def read(self) -> tuple[int, int, int, int] | None:
        tick = self.reads
        self.reads += 1
        if self._samples is None:
            flags, tag, px, py = self._walk(tick)
        else:
            flags, tag, px, py = self._samples[self._index]
            if self._index < len(self._samples) - 1:
                self._index += 1  # after the last sample it is held (a test may add more)
        return flags, self._handle(tag), px, py

    def named_kind(self, hcursor: int) -> str | None:
        tag = self._tags.get(int(hcursor))
        if tag is None:
            return None
        shape = self._shapes.get(tag, tag)
        return shape if isinstance(shape, str) and shape in NAMED_KINDS.values() else None

    def render(self, hcursor: int) -> tuple[bytes, int, int, int, int, int] | None:
        tag = self._tags.get(int(hcursor))
        shape = self._shapes.get(tag) if tag is not None else None
        if isinstance(shape, tuple) and len(shape) == 6 and shape[0] == "custom":
            _custom, bgra, w, h, hx, hy = shape
            return bytes(bgra), int(w), int(h), int(w) * 4, int(hx), int(hy)
        if isinstance(shape, dict) and isinstance(shape.get("custom"), dict):
            custom = shape["custom"]
            w, h = int(custom.get("w", 24)), int(custom.get("h", 24))
            if w <= 0 or h <= 0 or w > 256 or h > 256:
                return None
            return synthetic_cursor_bitmap(w, h), w, h, w * 4, int(custom.get("hx", 0)), int(custom.get("hy", 0))
        return None


def frame_time_base() -> fractions.Fraction:
    return VIDEO_TIME_BASE


__all__ = [
    "FakeCursorReader",
    "FrameSource",
    "LavfiTestSource",
    "SyntheticSource",
    "build_fake_source",
    "synthetic_cursor_bitmap",
]
