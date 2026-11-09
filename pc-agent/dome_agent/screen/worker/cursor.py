"""The pointer drawn by the phone, the PC's half (rules.screen_cursor, design 2a §3.7; protocol 1.5).

In an ``overlay`` session the source captures WITHOUT the pointer (``ddagrab`` ``draw_mouse=0``, Windows.Graphics.Capture
``cursor_capture=False``) and :class:`CursorPoller`, a daemon thread in the worker, reads the real pointer about 60
times a second through a :class:`CursorReader` (``cursor_win.Win32CursorReader`` on Windows, ``fake.FakeCursorReader``
on Linux) and reports it to the phone on the data channel:

* ``cursor_shape`` when the pointer's handle changed: one of Windows' system shapes by its ``LoadCursorW`` identity
  (:data:`NAMED_KINDS`, drawn by the phone with its own vector art), or ``custom`` with a 32-bit RGBA PNG of at most
  64 x 64 pixels and :data:`CURSOR_PNG_MAX_CHARS` base64 characters plus its hotspot (:func:`encode_cursor_png`;
  larger cursors are scaled down, hotspot included). A shape the PC cannot render within those bounds is sent as
  ``arrow`` and counted (``bitmap_too_large``). Custom shapes are coalesced to at most
  :data:`CURSOR_SHAPE_CUSTOM_PER_SECOND` per second, the latest winning; rendered shapes are kept in a small cache
  (:class:`CursorShapeCache`, 8 entries, 10 s).
* ``cursor_pos{gen, x, y, visible}`` when the position or the visibility changed, normalised 0..65535 over the
  streamed monitor (:func:`normalise_cursor`, the inverse of ``session.map_to_pixels``). ``visible`` is false when
  Windows hides the pointer or it is on another monitor; ``x``/``y`` then hold the last position inside the surface.
  One read per tick, so never more than :data:`CURSOR_POS_HZ` positions a second.
* The first ``cursor_shape`` precedes the first ``cursor_pos``; both are sent again after every ``geometry``
  (:meth:`CursorPoller.set_surface`), after a resume and when the data channel opens (:meth:`CursorPoller.resend`);
  :meth:`CursorPoller.pause` sends one ``visible: false`` and then nothing until the resume.
* :data:`CURSOR_POLL_ERRORS_MAX` failed reads in a row stop the reporting (one ``visible: false``, counted, logged
  ``cursor reporting stopped`` without any content).

A cursor bitmap is screen content (rules.screen_content): nothing here logs, stores or journals a position, a
handle or a bitmap. Log lines carry the kind, the size and the length of the base64 text (``bitmap_chars``) only;
every counter and log key says ``bitmap``, never ``png`` (``png`` is a redacted key part, ``logsetup``).

Everything but the Win32 reader is pure or thread-only and unit-tested on Linux; the reader is **not yet verified on
a device**.
"""

from __future__ import annotations

import base64
import collections
import io
import threading
import time
from collections.abc import Callable
from typing import Any, Protocol

import structlog

log = structlog.get_logger(__name__)

CURSOR_POS_HZ = 60
CURSOR_SHAPE_CUSTOM_PER_SECOND = 4
CURSOR_PNG_MAX_CHARS = 8192  # base64 characters of a custom shape's bitmap (the schema's maxLength)
CURSOR_BITMAP_MAX_PX = 64
CURSOR_SHAPE_CACHE_SIZE = 8
CURSOR_SHAPE_CACHE_TTL_SECONDS = 10.0
CURSOR_POLL_ERRORS_MAX = 10
CURSOR_SHOWING = 0x00000001  # CURSORINFO.flags: the pointer is shown
NORMALISED_MAX = 65535
HOTSPOT_MAX = 63
# IDC_* resource ids → the contract's named kinds (relay-frames screen_dc_cursor_shape_named)
NAMED_KINDS: dict[int, str] = {
    32512: "arrow",
    32513: "ibeam",
    32514: "wait",
    32515: "cross",
    32516: "uparrow",
    32642: "sizenwse",
    32643: "sizenesw",
    32644: "sizewe",
    32645: "sizens",
    32646: "sizeall",
    32648: "no",
    32649: "hand",
    32650: "appstarting",
    32651: "help",
}
_KIND_NAMES = frozenset(NAMED_KINDS.values())

Message = dict[str, Any]
Rect = tuple[int, int, int, int]


# ----- pure helpers --------------------------------------------------------------------------------------------------
def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def normalise_cursor(px: int, py: int, rect: Rect) -> tuple[int, int, bool]:
    """A physical pointer position → ``(x, y, on_surface)`` over the streamed monitor ``(left, top, width, height)``
    (physical pixels, like ``Monitor``): ``x = clamp(round((px − left) · 65535 / (width − 1)), 0, 65535)``, the inverse
    of ``session.map_to_pixels``; ``on_surface`` when the pointer lies inside the rectangle."""
    left, top, width, height = (int(v) for v in rect)
    x = _clamp(round((int(px) - left) * NORMALISED_MAX / max(width - 1, 1)), 0, NORMALISED_MAX)
    y = _clamp(round((int(py) - top) * NORMALISED_MAX / max(height - 1, 1)), 0, NORMALISED_MAX)
    on_surface = left <= int(px) < left + width and top <= int(py) < top + height
    return x, y, on_surface


def fit_cursor(width: int, height: int, limit: int = CURSOR_BITMAP_MAX_PX) -> tuple[int, int]:
    """The size a cursor bitmap is sent at: scaled down to fit ``limit`` x ``limit`` keeping the aspect ratio, never
    up, each side at least 1."""
    width, height = max(1, int(width)), max(1, int(height))
    if width <= limit and height <= limit:
        return width, height
    scale = limit / max(width, height)
    return _clamp(round(width * scale), 1, limit), _clamp(round(height * scale), 1, limit)


def alpha_from_mask(
    mask_bgra: bytes, width: int, height: int, stride: int, *, color_bgra: bytes | None = None
) -> bytes:
    """One alpha byte per pixel from the pixels ``DrawIconEx(DI_MASK)`` painted into a 32-bpp DIB (white where the
    AND mask bit is 1, black where it is 0): AND bit 1 → alpha 0 (transparent), else 255. For a cursor without its own
    alpha (a mask-only or old-style colour cursor). ``color_bgra`` (the ``DI_NORMAL`` drawing on black, same stride)
    makes the "invert the screen" pixels (AND 1, XOR 1: drawn white) opaque white instead of transparent: the
    documented approximation (KNOWN_ISSUES), since the phone cannot invert what is under the pointer."""
    out = bytearray(width * height)
    for y in range(height):
        row = y * stride
        for x in range(width):
            at = row + x * 4
            masked = max(mask_bgra[at], mask_bgra[at + 1], mask_bgra[at + 2]) >= 128
            if masked and color_bgra is not None:
                masked = max(color_bgra[at], color_bgra[at + 1], color_bgra[at + 2]) < 128
            out[y * width + x] = 0 if masked else 255
    return bytes(out)


def with_alpha(bgra: bytes, alpha: bytes, width: int, height: int, stride: int) -> bytes:
    """``bgra`` (rows ``stride`` bytes apart) with its alpha bytes replaced by ``alpha`` (one per pixel), packed at
    ``width`` x 4 bytes per row."""
    out = bytearray(width * height * 4)
    for y in range(height):
        out[y * width * 4 : (y + 1) * width * 4] = bgra[y * stride : y * stride + width * 4]
    out[3::4] = alpha[: width * height]
    return bytes(out)


def straight_alpha(bgra: bytes, width: int, height: int, stride: int) -> bytes:
    """A premultiplied BGRA bitmap (rows ``stride`` bytes apart) as straight BGRA packed at ``width`` x 4 bytes per row.
    ``DrawIconEx(DI_NORMAL)`` alpha-blends a cursor that has its own alpha onto the zeroed DIB, leaving each colour
    already multiplied by its alpha; the PNG and the phone's compositing take straight alpha, so read as it is a 50 %
    white edge would arrive 50 % grey. Opaque and fully transparent pixels are unchanged."""
    from PIL import Image

    image = Image.frombuffer("RGBA", (width, height), bytes(bgra), "raw", "BGRa", stride, 1)
    return image.tobytes("raw", "BGRA")


def encode_cursor_png(
    bgra: bytes, width: int, height: int, stride: int, hotspot_x: int, hotspot_y: int
) -> tuple[bytes, int, int, int, int] | None:
    """A cursor bitmap (BGRA rows ``stride`` bytes apart, top-down) as a PNG of at most 64 x 64 pixels:
    ``(png, width, height, hotspot_x, hotspot_y)`` with the hotspot scaled like the bitmap and kept inside it (at most
    63). None when the bitmap cannot be read."""
    try:
        from PIL import Image

        if width <= 0 or height <= 0 or stride < width * 4 or len(bgra) < stride * (height - 1) + width * 4:
            return None
        image = Image.frombuffer("RGBA", (width, height), bytes(bgra), "raw", "BGRA", stride, 1)
        out_w, out_h = fit_cursor(width, height)
        hx, hy = int(hotspot_x), int(hotspot_y)
        if (out_w, out_h) != (width, height):
            image = image.resize((out_w, out_h), Image.Resampling.LANCZOS)
            hx, hy = round(hx * out_w / width), round(hy * out_h / height)
        hx = _clamp(hx, 0, min(HOTSPOT_MAX, out_w - 1))
        hy = _clamp(hy, 0, min(HOTSPOT_MAX, out_h - 1))
        buffer = io.BytesIO()
        image.save(buffer, "PNG", optimize=True)
    except (OSError, ValueError):
        return None
    return buffer.getvalue(), out_w, out_h, hx, hy


def shape_message(
    kind: str,
    *,
    width: int | None = None,
    height: int | None = None,
    hotspot_x: int | None = None,
    hotspot_y: int | None = None,
    png: bytes | None = None,
) -> Message | None:
    """The ``cursor_shape`` message: a named kind, or ``custom`` with the PNG in standard base64. None when the base64
    text would exceed :data:`CURSOR_PNG_MAX_CHARS` (the caller sends ``arrow`` and counts ``bitmap_too_large``)."""
    if kind != "custom":
        if kind not in _KIND_NAMES:
            raise ValueError(f"not a named cursor kind: {kind[:16]!r}")
        return {"type": "cursor_shape", "kind": kind}
    if png is None or width is None or height is None or hotspot_x is None or hotspot_y is None:
        raise ValueError("a custom cursor shape needs its bitmap, size and hotspot")
    text = base64.b64encode(png).decode("ascii")
    if len(text) > CURSOR_PNG_MAX_CHARS:
        return None
    return {
        "type": "cursor_shape",
        "kind": "custom",
        "width": int(width),
        "height": int(height),
        "hotspot_x": int(hotspot_x),
        "hotspot_y": int(hotspot_y),
        "png": text,
    }


def pos_message(gen: int, x: int, y: int, visible: bool) -> Message:
    """The ``cursor_pos`` message."""
    return {"type": "cursor_pos", "gen": int(gen), "x": int(x), "y": int(y), "visible": bool(visible)}


ARROW: Message = {"type": "cursor_shape", "kind": "arrow"}


# ----- the reader seam -----------------------------------------------------------------------------------------------
class CursorReader(Protocol):
    """What the poller asks of the platform. A reader may also offer ``prepare_thread()``, called once on the
    poller's own thread before the first read (the Win32 reader makes that thread per-monitor DPI aware)."""

    def read(self) -> tuple[int, int, int, int] | None:
        """``(flags, hcursor, px, py)`` with the position in physical pixels; None when the call failed (not
        visible)."""
        ...

    def named_kind(self, hcursor: int) -> str | None:
        """The named kind when ``hcursor`` is one of Windows' system cursors, else None."""
        ...

    def render(self, hcursor: int) -> tuple[bytes, int, int, int, int, int] | None:
        """``(bgra, width, height, stride, hotspot_x, hotspot_y)`` of the cursor's bitmap, or None."""
        ...


class CursorShapeCache:
    """At most :data:`CURSOR_SHAPE_CACHE_SIZE` decided shape messages, keyed ``(hcursor, hotspot_x, hotspot_y,
    width, height)`` and looked up by handle, each for :data:`CURSOR_SHAPE_CACHE_TTL_SECONDS` (a handle Windows
    reuses for another cursor inside that time shows one wrong shape: KNOWN_ISSUES). Kept in memory only."""

    def __init__(
        self, size: int = CURSOR_SHAPE_CACHE_SIZE, ttl_seconds: float = CURSOR_SHAPE_CACHE_TTL_SECONDS
    ) -> None:
        self.size = int(size)
        self.ttl_seconds = float(ttl_seconds)
        self._entries: collections.OrderedDict[tuple[int, int, int, int, int], tuple[float, Message]] = (
            collections.OrderedDict()
        )

    def __len__(self) -> int:
        return len(self._entries)

    def keys(self) -> list[tuple[int, int, int, int, int]]:
        return list(self._entries)

    def get(self, hcursor: int, now: float) -> Message | None:
        for key, (stored_at, message) in list(self._entries.items()):
            if key[0] != hcursor:
                continue
            if now - stored_at >= self.ttl_seconds:
                del self._entries[key]
                return None
            self._entries.move_to_end(key)
            return message
        return None

    def put(self, key: tuple[int, int, int, int, int], message: Message, now: float) -> None:
        for other in [k for k in self._entries if k[0] == key[0]]:
            del self._entries[other]
        self._entries[key] = (now, message)
        while len(self._entries) > self.size:
            self._entries.popitem(last=False)


# ----- the poller ----------------------------------------------------------------------------------------------------
class CursorPoller(threading.Thread):
    """Reads the pointer at ``hz`` on a daemon thread and hands ``cursor_shape`` / ``cursor_pos`` messages to
    ``on_shape`` / ``on_pos`` (called on the poller's thread; the worker hops to its loop). :meth:`tick` is one read;
    the thread calls it paced by ``perf_counter``, tests call it with their own clock."""

    def __init__(
        self,
        reader: CursorReader,
        *,
        on_pos: Callable[[Message], None],
        on_shape: Callable[[Message], None],
        hz: float = CURSOR_POS_HZ,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        super().__init__(name="dome-screen-cursor", daemon=True)
        self._reader = reader
        self._on_pos = on_pos
        self._on_shape = on_shape
        self._interval = 1.0 / max(1.0, float(hz))
        self._clock = clock
        self._mutex = threading.Lock()
        self._halt = threading.Event()
        self._rect: Rect | None = None
        self._gen = 0
        self._paused = False
        self._last_sent: tuple[int, int, bool, int] | None = None  # (x, y, visible, gen) of the last cursor_pos
        self._shown: tuple[int, int, int] | None = None  # (gen, x, y) of the last visible cursor_pos not yet hidden
        self._last_inside: tuple[int, int] | None = None  # the last normalised position inside the surface
        self._last_hcursor: int | None = None
        # bumped whenever shape and position are forgotten (set_surface, resume, resend): a shape worked out with the
        # mutex released is offered only if nothing was forgotten meanwhile
        self._epoch = 0
        self._shape_ready = False  # a cursor_shape went out since the last resend: positions may follow
        self._pending_custom: Message | None = None  # a custom shape held back by the 4/s limit (the latest wins)
        self._custom_sent_at: collections.deque[float] = collections.deque()
        self._errors_in_row = 0
        self.cache = CursorShapeCache()
        self.poll_errors = 0  # reads (or ticks) that raised, in all
        self.bitmap_too_large = 0  # custom shapes sent as arrow: over 8192 base64 characters, or not renderable
        self.failed = False  # stopped after CURSOR_POLL_ERRORS_MAX errors in a row

    # ----- controls (any thread) -----
    def set_surface(self, rect: Rect, gen: int) -> None:
        """The streamed monitor's rectangle (physical pixels) and the geometry generation positions are reported in.
        Called before the ``geometry`` message is sent; after it returns no position of the old generation is handed
        out, and shape and position are sent again."""
        with self._mutex:
            self._rect = (int(rect[0]), int(rect[1]), int(rect[2]), int(rect[3]))
            self._gen = int(gen)
            self._last_inside = None
            self._forget_locked()

    def pause(self) -> None:
        """One ``visible: false`` (when the phone draws the pointer now), then nothing until :meth:`resume`."""
        with self._mutex:
            if self._paused:
                return
            self._paused = True
            self._hide_locked()

    def resume(self) -> None:
        """Reporting again: shape and position are sent at the next tick."""
        with self._mutex:
            self._paused = False
            self._forget_locked()

    def resend(self) -> None:
        """Shape and position are sent again at the next tick (the data channel opened; pause state unchanged)."""
        with self._mutex:
            self._forget_locked()

    def stop(self, timeout: float = 1.0) -> None:
        """Ends the thread; it exits within one tick."""
        self._halt.set()
        if self.is_alive() and threading.current_thread() is not self:
            self.join(timeout)

    @property
    def paused(self) -> bool:
        return self._paused

    # ----- the thread -----
    def run(self) -> None:
        prepare = getattr(self._reader, "prepare_thread", None)
        if callable(prepare):
            try:
                prepare()
            except Exception as exc:  # noqa: BLE001 - positions in logical pixels are better than none
                log.warning("cursor reader thread setup failed", error=exc.__class__.__name__)
        while not self._halt.is_set():
            started = self._clock()
            self.tick(started)
            if self.failed:
                return
            # the next read one interval after this one began, never sooner: never more than hz reads in any second
            # (a slow read or render delays the next instead of bunching reads to catch up)
            next_at = started + self._interval
            while not self._halt.is_set() and (remaining := next_at - self._clock()) > 0:
                self._halt.wait(remaining)

    def tick(self, now: float | None = None) -> None:
        """One read of the pointer; messages for whatever changed. The shape of a new pointer handle is worked out
        with the mutex released (GDI, the mask and the PNG encode take milliseconds, and the worker's event loop takes
        the mutex for :meth:`set_surface`, :meth:`pause`, :meth:`resume` and :meth:`resend`); it is offered, and the
        position sent, only when nothing was forgotten or paused meanwhile (else the next tick decides afresh)."""
        now = self._clock() if now is None else now
        with self._mutex:
            if self._paused or self.failed or self._rect is None:
                return
            try:
                visible, hcursor, gen = self._read_locked()
                if not (visible and hcursor and hcursor != self._last_hcursor):
                    self._finish_locked(now, visible, gen, None)
                    self._errors_in_row = 0
                    return
            except Exception as exc:  # noqa: BLE001 - a failing read never escapes the thread
                self._failed_tick_locked(exc)
                return
            epoch = self._epoch
        try:
            message = self._shape_for(hcursor, now)
        except Exception as exc:  # noqa: BLE001 - named_kind failed: counted, and the next tick tries again
            with self._mutex:
                self._failed_tick_locked(exc)
            return
        with self._mutex:
            if self._epoch != epoch or self._paused or self.failed:
                return
            try:
                self._finish_locked(now, visible, gen, (hcursor, message))
            except Exception as exc:  # noqa: BLE001
                self._failed_tick_locked(exc)
                return
            self._errors_in_row = 0

    # ----- under the mutex -----
    def _failed_tick_locked(self, exc: Exception) -> None:
        self._errors_in_row += 1
        self.poll_errors += 1
        if self._errors_in_row >= CURSOR_POLL_ERRORS_MAX:
            self.failed = True
            self._halt.set()
            self._hide_locked()
            log.warning("cursor reporting stopped", errors=self.poll_errors, error=exc.__class__.__name__)

    def _forget_locked(self) -> None:
        self._epoch += 1
        self._last_sent = None
        self._last_hcursor = None
        self._shape_ready = False
        self._pending_custom = None

    def _hide_locked(self) -> None:
        shown = self._shown
        if shown is None:
            return
        gen, x, y = shown
        self._shown = None
        self._last_sent = (x, y, False, gen)
        self._on_pos(pos_message(gen, x, y, False))

    def _read_locked(self) -> tuple[bool, int, int]:
        """One read: ``(visible, hcursor, gen)``; the last position inside the surface is kept."""
        assert self._rect is not None
        rect, gen = self._rect, self._gen
        sample = self._reader.read()
        visible = False
        hcursor = 0
        if sample is not None:
            flags, hcursor, px, py = sample
            x, y, on_surface = normalise_cursor(px, py, rect)
            if on_surface:
                self._last_inside = (x, y)
            visible = bool(int(flags) & CURSOR_SHOWING) and on_surface
        return visible, int(hcursor), gen

    def _finish_locked(self, now: float, visible: bool, gen: int, shape: tuple[int, Message] | None) -> None:
        """The read's messages: the new handle's shape (``shape``) or a custom one held back by the 4/s limit, then
        the position when it changed."""
        if shape is not None:
            self._last_hcursor = shape[0]  # only once its shape was worked out: a failure tries again next tick
            self._offer_shape(shape[1], now)
        elif self._pending_custom is not None and self._custom_allowed(now):
            pending, self._pending_custom = self._pending_custom, None
            self._send_shape(pending, now)
        if not self._shape_ready or self._last_inside is None:
            return  # the first cursor_shape precedes the first cursor_pos
        x, y = self._last_inside
        current = (x, y, visible, gen)
        if current == self._last_sent:
            return
        self._last_sent = current
        self._shown = (gen, x, y) if visible else None
        self._on_pos(pos_message(gen, x, y, visible))

    def _shape_for(self, hcursor: int, now: float) -> Message:
        kind = self._reader.named_kind(hcursor)
        if kind is not None and kind in _KIND_NAMES:
            return {"type": "cursor_shape", "kind": kind}
        cached = self.cache.get(hcursor, now)
        if cached is not None:
            return cached
        try:
            rendered = self._reader.render(hcursor)
            if rendered is None:
                self.bitmap_too_large += 1
                log.debug("screen cursor shape not renderable; sent as arrow", kind="custom")
                return dict(ARROW)
            bgra, width, height, stride, hotspot_x, hotspot_y = rendered
            encoded = encode_cursor_png(bgra, width, height, stride, hotspot_x, hotspot_y)
        except Exception as exc:  # noqa: BLE001 - a shape that fails (MemoryError, a ctypes or PIL error) is the arrow
            self.bitmap_too_large += 1
            log.debug("screen cursor shape failed; sent as arrow", kind="custom", error=exc.__class__.__name__)
            return dict(ARROW)
        message: Message | None = None
        if encoded is not None:
            png, w2, h2, hx2, hy2 = encoded
            message = shape_message("custom", width=w2, height=h2, hotspot_x=hx2, hotspot_y=hy2, png=png)
            chars = len(base64.b64encode(png))
            if message is None:
                log.debug("screen cursor shape too large; sent as arrow", kind="custom", w=w2, h=h2, bitmap_chars=chars)
            else:
                log.debug("screen cursor shape", kind="custom", w=w2, h=h2, bitmap_chars=chars)
        if message is None:
            self.bitmap_too_large += 1
            message = dict(ARROW)
        self.cache.put((hcursor, int(hotspot_x), int(hotspot_y), int(width), int(height)), message, now)
        return message

    def _custom_allowed(self, now: float) -> bool:
        while self._custom_sent_at and now - self._custom_sent_at[0] >= 1.0:
            self._custom_sent_at.popleft()
        return len(self._custom_sent_at) < CURSOR_SHAPE_CUSTOM_PER_SECOND

    def _offer_shape(self, message: Message, now: float) -> None:
        if message.get("kind") == "custom" and not self._custom_allowed(now):
            self._pending_custom = message  # sent when the window allows; a newer shape replaces it
            return
        self._pending_custom = None
        self._send_shape(message, now)

    def _send_shape(self, message: Message, now: float) -> None:
        if message.get("kind") == "custom":
            self._custom_sent_at.append(now)
        self._shape_ready = True
        self._on_shape(message)


__all__ = [
    "ARROW",
    "CURSOR_BITMAP_MAX_PX",
    "CURSOR_PNG_MAX_CHARS",
    "CURSOR_POLL_ERRORS_MAX",
    "CURSOR_POS_HZ",
    "CURSOR_SHAPE_CACHE_SIZE",
    "CURSOR_SHAPE_CACHE_TTL_SECONDS",
    "CURSOR_SHAPE_CUSTOM_PER_SECOND",
    "CURSOR_SHOWING",
    "NAMED_KINDS",
    "CursorPoller",
    "CursorReader",
    "CursorShapeCache",
    "alpha_from_mask",
    "encode_cursor_png",
    "fit_cursor",
    "normalise_cursor",
    "pos_message",
    "shape_message",
    "straight_alpha",
    "with_alpha",
]
