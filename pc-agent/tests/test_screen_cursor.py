"""The pointer drawn by the phone, the PC's half (protocol 1.5, design 2a §3.7; rules.screen_cursor): the pure helpers
(normalisation as the inverse of ``map_to_pixels``, bitmap fitting, the DI_MASK alpha, the PNG encoding and its
8192-character cap, the messages against the shared vocabulary), the poller against ``FakeCursorReader`` and scripted
readers (shape before position, only on change, at most 60 positions a second, custom shapes at most 4 a second with
the latest winning, pause/resume, the generation handoff, the shape cache, ten errors stop it), and the Win32 reader's
structure layouts and its GDI sequence against stand-ins for ``user32``/``gdi32``.

Evidence: unit-tested on Linux; ``GetCursorInfo``/``DrawIconEx`` on a real Windows desktop are **not yet verified on a
device**."""

from __future__ import annotations

import base64
import contextlib
import ctypes
import hashlib
import io
import logging
import threading
import time
from collections.abc import Iterator, MutableMapping
from typing import Any

import pytest
import structlog
from dome_protocol import dumps_compact, parse_screen_dc_message
from PIL import Image

from dome_agent.platform.protocol import Monitor
from dome_agent.screen.session import map_to_pixels
from dome_agent.screen.worker import cursor, cursor_win, fake
from dome_agent.screen.worker.cursor import (
    CURSOR_PNG_MAX_CHARS,
    CURSOR_POLL_ERRORS_MAX,
    CURSOR_SHAPE_CACHE_TTL_SECONDS,
    CursorPoller,
    CursorShapeCache,
    alpha_from_mask,
    encode_cursor_png,
    fit_cursor,
    normalise_cursor,
    pos_message,
    shape_message,
    with_alpha,
)

RECT = (0, 0, 1920, 1080)


@contextlib.contextmanager
def debug_lines() -> Iterator[list[MutableMapping[str, Any]]]:
    """structlog's events at DEBUG too (the shape lines are debug lines), the configuration restored afterwards:
    nothing here touches the stdlib handlers other tests read."""
    previous = structlog.get_config()
    structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.DEBUG))
    try:
        with structlog.testing.capture_logs() as logs:
            yield logs
    finally:
        structlog.configure(**previous)


def valid(message: dict[str, Any]) -> dict[str, Any]:
    """Every message the poller makes is in the PC → phone vocabulary (the worker sends it through the same check)."""
    return parse_screen_dc_message(dumps_compact(message), "pc_to_phone")


def bitmap(width: int, height: int, seed: int = 0) -> bytes:
    """A BGRA test bitmap with distinct, opaque-ish pixels (made up; never a screen)."""
    return bytes(
        ((x * 7 + y * 13 + c * 31 + seed) % 256) if c < 3 else 200
        for y in range(height)
        for x in range(width)
        for c in range(4)
    )


def noise(width: int, height: int) -> bytes:
    """Incompressible BGRA: a PNG of it is larger than the 8192-character cap at 64 x 64."""
    out = bytearray()
    counter = 0
    while len(out) < width * height * 4:
        out += hashlib.sha256(counter.to_bytes(4, "big")).digest()
        counter += 1
    return bytes(out[: width * height * 4])


class Recorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []
        self.times: list[float] = []

    def pos(self, message: dict[str, Any]) -> None:
        self.events.append(("pos", valid(message)))
        self.times.append(time.perf_counter())

    def shape(self, message: dict[str, Any]) -> None:
        self.events.append(("shape", valid(message)))

    def positions(self) -> list[dict[str, Any]]:
        return [m for kind, m in self.events if kind == "pos"]

    def shapes(self) -> list[dict[str, Any]]:
        return [m for kind, m in self.events if kind == "shape"]


def poller_for(reader: Any, *, rect: tuple[int, int, int, int] = RECT, gen: int = 1) -> tuple[CursorPoller, Recorder]:
    rec = Recorder()
    poller = CursorPoller(reader, on_pos=rec.pos, on_shape=rec.shape)
    poller.set_surface(rect, gen)
    return poller, rec


def scripted(samples: list[list[Any]], shapes: dict[str, Any] | None = None) -> fake.FakeCursorReader:
    return fake.FakeCursorReader({"samples": samples, "shapes": shapes or {}})


def run_ticks(poller: CursorPoller, count: int, *, start: float = 100.0, hz: float = 60.0) -> float:
    for i in range(count):
        poller.tick(start + i / hz)  # computed per tick: no float drift at the 1 s boundary
    return start + count / hz


# ----- pure helpers -------------------------------------------------------------------------------------------------------
MONITORS = [
    Monitor(index=0, left=0, top=0, width=2560, height=1440, primary=True),
    Monitor(index=1, left=-1920, top=-300, width=1920, height=1080, primary=False),
    Monitor(index=2, left=2560, top=0, width=1280, height=1024, primary=False),
    Monitor(index=3, left=-7, top=-5, width=7, height=5, primary=False),
]


@pytest.mark.parametrize("monitor", MONITORS, ids=lambda m: f"{m.left},{m.top},{m.width}x{m.height}")
def test_normalise_cursor_is_the_inverse_of_map_to_pixels(monitor: Monitor) -> None:
    """``cursor_pos`` uses the same 0..65535 frame as ``pointer_move_to``: a reported position mapped back with the
    agent's own ``map_to_pixels`` is the pixel the pointer is on, on negative origins and mixed sizes alike."""
    rect = (monitor.left, monitor.top, monitor.width, monitor.height)
    columns = (
        range(monitor.width)
        if monitor.width <= 64
        else [0, 1, 2, monitor.width // 3, monitor.width - 2, monitor.width - 1]
    )
    rows = range(monitor.height) if monitor.height <= 64 else [0, 1, monitor.height // 2, monitor.height - 1]
    for dx in columns:
        for dy in rows:
            px, py = monitor.left + dx, monitor.top + dy
            x, y, on_surface = normalise_cursor(px, py, rect)
            assert on_surface and 0 <= x <= 65535 and 0 <= y <= 65535
            assert map_to_pixels(monitor, x, y) == (px, py), (dx, dy, x, y)
    for x in (0, 1, 16384, 32767, 65534, 65535):
        px, py = map_to_pixels(monitor, x, 65535 - x)
        assert map_to_pixels(monitor, *normalise_cursor(px, py, rect)[:2]) == (px, py)
    # on another monitor: not on the surface, clamped to the edge
    assert normalise_cursor(monitor.left - 1, monitor.top, rect) == (0, 0, False)
    assert normalise_cursor(monitor.left + monitor.width, monitor.top + monitor.height, rect) == (65535, 65535, False)


def test_fit_cursor_scales_down_keeping_the_aspect_and_never_up() -> None:
    assert fit_cursor(32, 32) == (32, 32) and fit_cursor(64, 64) == (64, 64) and fit_cursor(1, 1) == (1, 1)
    assert fit_cursor(70, 70) == (64, 64) and fit_cursor(128, 32) == (64, 16) and fit_cursor(32, 256) == (8, 64)
    assert fit_cursor(300, 1) == (64, 1) and fit_cursor(0, 10) == (1, 10)


def test_alpha_from_mask_reads_the_di_mask_dib_and_keeps_inverted_pixels_white() -> None:
    """``DrawIconEx(DI_MASK)`` into a 32-bpp DIB paints the AND mask white (bit 1) or black (bit 0); bit 1 is
    transparent. With the DI_NORMAL drawing on black given, a pixel that inverts the screen (AND 1, XOR 1: white) is
    kept opaque white (the documented approximation)."""
    white, black = b"\xff\xff\xff\x00", b"\x00\x00\x00\x00"
    stride = 16  # 3 pixels of 4 bytes plus 4 bytes of padding per row
    mask = white + black + white + b"PADS" + black + white + black + b"PADS"
    assert alpha_from_mask(mask, 3, 2, stride) == bytes([0, 255, 0, 255, 0, 255])
    color = black + white + white + b"PADS" + white + black + black + b"PADS"  # (0,2) inverts the screen
    alpha = alpha_from_mask(mask, 3, 2, stride, color_bgra=color)
    assert alpha == bytes([0, 255, 255, 255, 0, 255])
    merged = with_alpha(color, alpha, 3, 2, stride)
    assert len(merged) == 3 * 2 * 4 and merged[3::4] == alpha and merged[8:11] == b"\xff\xff\xff"


def test_straight_alpha_undoes_the_premultiplication_drawiconex_leaves() -> None:
    """A half-transparent white pixel drawn by DrawIconEx(DI_NORMAL) into a zeroed DIB reads (128, 128, 128, 128):
    straight it is white at 50 %, and the PNG carries that (read as straight alpha it would be 50 % grey)."""
    premultiplied = b"\x80\x80\x80\x80" + b"\xff\x00\x00\xff" + b"\x00\x00\x00\x00" + b"PADS"
    straight = cursor.straight_alpha(premultiplied, 3, 1, 16)
    assert straight == b"\xff\xff\xff\x80" + b"\xff\x00\x00\xff" + b"\x00\x00\x00\x00"  # packed, padding gone
    encoded = encode_cursor_png(straight, 3, 1, 12, 0, 0)
    assert encoded is not None
    with Image.open(io.BytesIO(encoded[0])) as image:
        assert image.convert("RGBA").getpixel((0, 0)) == (255, 255, 255, 128)
        assert image.convert("RGBA").getpixel((1, 0)) == (0, 0, 255, 255)  # opaque pixels are unchanged


def test_encode_cursor_png_round_trips_and_scales_larger_cursors_down() -> None:
    raw = bitmap(24, 20)
    encoded = encode_cursor_png(raw, 24, 20, 24 * 4, 3, 2)
    assert encoded is not None
    png, width, height, hx, hy = encoded
    assert (width, height, hx, hy) == (24, 20, 3, 2) and png.startswith(b"\x89PNG\r\n\x1a\n")
    with Image.open(io.BytesIO(png)) as image:
        assert image.mode == "RGBA" and image.size == (24, 20)
        decoded = image.tobytes()
    expected = bytearray()
    for at in range(0, len(raw), 4):
        b, g, r, a = raw[at : at + 4]
        expected += bytes((r, g, b, a))
    assert decoded == bytes(expected)  # BGRA in, the same pixels out
    assert len(base64.b64encode(png)) <= CURSOR_PNG_MAX_CHARS
    # a wider stride (row padding) reads the same pixels
    padded = b"".join(raw[y * 96 : (y + 1) * 96] + b"\x00" * 32 for y in range(20))
    assert encode_cursor_png(padded, 24, 20, 128, 3, 2) == encoded
    # 70 x 70 is scaled to 64 x 64, the hotspot with it (and kept inside, at most 63)
    big = encode_cursor_png(bitmap(70, 70), 70, 70, 280, 35, 69)
    assert big is not None and big[1:] == (64, 64, 32, 63)
    with Image.open(io.BytesIO(big[0])) as image:
        assert image.size == (64, 64)
    assert encode_cursor_png(bitmap(4, 4), 4, 4, 16, 9, 9)[3:] == (3, 3)  # type: ignore[index]
    assert encode_cursor_png(b"short", 4, 4, 16, 0, 0) is None and encode_cursor_png(b"", 0, 4, 0, 0, 0) is None


def test_shape_and_pos_messages_are_in_the_vocabulary_and_the_bitmap_is_capped() -> None:
    assert valid(shape_message("ibeam")) == {"type": "cursor_shape", "kind": "ibeam"}  # type: ignore[arg-type]
    for kind in cursor.NAMED_KINDS.values():
        assert valid(shape_message(kind)) == {"type": "cursor_shape", "kind": kind}  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        shape_message("pirate")
    with pytest.raises(ValueError):
        shape_message("custom", width=1, height=1, hotspot_x=0, hotspot_y=0)
    encoded = encode_cursor_png(bitmap(24, 24), 24, 24, 96, 3, 2)
    assert encoded is not None
    custom = shape_message("custom", width=24, height=24, hotspot_x=3, hotspot_y=2, png=encoded[0])
    assert custom is not None and valid(custom) == custom and base64.b64decode(custom["png"]) == encoded[0]
    # exactly 8192 base64 characters still go (6144 bytes); one byte more does not
    assert (
        shape_message("custom", width=1, height=1, hotspot_x=0, hotspot_y=0, png=b"\x89PNG" + bytes(6140)) is not None
    )
    assert shape_message("custom", width=1, height=1, hotspot_x=0, hotspot_y=0, png=bytes(6145)) is None
    big = shape_message("custom", width=64, height=64, hotspot_x=0, hotspot_y=0, png=bytes(6144))
    assert big is not None and 1024 < len(dumps_compact(big).encode()) <= 12288 and valid(big) == big
    assert valid(pos_message(3, 0, 65535, False)) == {
        "type": "cursor_pos",
        "gen": 3,
        "x": 0,
        "y": 65535,
        "visible": False,
    }


# ----- the poller ---------------------------------------------------------------------------------------------------------
SHAPES = {
    "a": "arrow",
    "i": "ibeam",
    "c": {"custom": {"w": 24, "h": 24, "hx": 3, "hy": 2}},
}


def test_the_poller_sends_the_shape_before_the_position_and_only_what_changed() -> None:
    reader = scripted(
        [
            [1, "a", 960, 540],
            [1, "a", 960, 540],
            [1, "a", 961, 540],
            [1, "i", 961, 540],
            [1, "c", 1500, 200],
            [1, "c", 1500, 200],
            [0, "c", 1500, 200],  # Windows hides the pointer: not visible, where it is
            [1, "c", 3000, 200],  # on another monitor: not visible, the last position inside
        ],
        SHAPES,
    )
    poller, rec = poller_for(reader)
    run_ticks(poller, 10)
    kinds = [(kind, m.get("kind") or (m["x"], m["y"], m["visible"])) for kind, m in rec.events]
    x0, y0 = round(960 * 65535 / 1919), round(540 * 65535 / 1079)
    x1 = round(961 * 65535 / 1919)
    x2, y2 = round(1500 * 65535 / 1919), round(200 * 65535 / 1079)
    assert kinds == [
        ("shape", "arrow"),
        ("pos", (x0, y0, True)),
        ("pos", (x1, y0, True)),
        ("shape", "ibeam"),
        ("shape", "custom"),
        ("pos", (x2, y2, True)),
        ("pos", (x2, y2, False)),
    ], kinds
    assert all(m["gen"] == 1 for m in rec.positions())
    custom = rec.shapes()[-1]
    assert (custom["width"], custom["height"], custom["hotspot_x"], custom["hotspot_y"]) == (24, 24, 3, 2)
    assert len(custom["png"]) <= CURSOR_PNG_MAX_CHARS and poller.bitmap_too_large == 0


def test_nothing_is_sent_until_a_shape_could_be_sent() -> None:
    """The pointer starts on another monitor: no position (the phone shows nothing before the first report)."""
    reader = scripted([[1, "a", -50, 10], [1, "a", -40, 10], [1, "a", 100, 100]], SHAPES)
    poller, rec = poller_for(reader)
    run_ticks(poller, 2)
    assert rec.events == []
    run_ticks(poller, 1)
    assert [kind for kind, _ in rec.events] == ["shape", "pos"] and rec.positions()[0]["visible"] is True


def test_the_poller_reads_at_most_60_times_a_second_whatever_the_pointer_does() -> None:
    """A pointer that moves at every read (a 240 Hz mouse): one read per tick, so at most 60 positions in any
    second; the thread stops within one tick."""

    class Moving:
        def __init__(self) -> None:
            self.reads = 0

        def read(self) -> tuple[int, int, int, int]:
            self.reads += 1
            return 1, 0x10, (self.reads * 4) % 1900, 500

        def named_kind(self, _hcursor: int) -> str:
            return "arrow"

        def render(self, _hcursor: int) -> None:
            return None

    reader = Moving()
    poller, rec = poller_for(reader)
    started = time.perf_counter()
    poller.start()
    time.sleep(1.3)
    stopping = time.perf_counter()
    poller.stop()
    assert not poller.is_alive() and time.perf_counter() - stopping < 0.2
    elapsed = stopping - started
    times = rec.times
    assert 10 <= len(times) <= 60 * elapsed + 1, (len(times), elapsed)
    for i, t in enumerate(times):
        assert len([u for u in times[i:] if u < t + 1.0]) <= 60
    assert len(rec.shapes()) == 1  # the handle never changed


def test_custom_shapes_are_limited_to_four_a_second_and_the_latest_wins() -> None:
    shapes: dict[str, Any] = {f"c{i}": ("custom", bitmap(8 + i % 40, 8), 8 + i % 40, 8, 0, 0) for i in range(80)}
    shapes["a"] = "arrow"
    reader = scripted([[1, f"c{i}", 500, 500] for i in range(70)] + [[1, "c69", 500, 500]] * 30, shapes)
    poller, rec = poller_for(reader)
    run_ticks(poller, 130)  # 130 ticks at 60 Hz: a new custom shape at each of the first 70, then c69 held
    sent = [m["width"] for m in rec.shapes()]
    width = [8 + i % 40 for i in range(70)]
    # c0..c3 at once; nothing until a second after each of them (c60..c63); the newest of those held back since
    # (c69) once the window allows, a second after c60: at most 4 in any second, and the phone ends on the latest
    assert sent == [width[i] for i in (0, 1, 2, 3, 60, 61, 62, 63, 69)], sent
    # a named shape replaces a custom one held back by the limit
    reader = scripted([[1, f"c{i}", 500, 500] for i in range(5)] + [[1, "a", 500, 500]] * 3, shapes)
    poller, rec = poller_for(reader)
    run_ticks(poller, 8)
    assert [m["kind"] for m in rec.shapes()] == ["custom"] * 4 + ["arrow"]
    run_ticks(poller, 120, start=200.0)
    assert [m["kind"] for m in rec.shapes()][-1] == "arrow"  # the held-back custom shape never went out


def test_pause_sends_one_invisible_position_and_resume_sends_shape_and_position_again() -> None:
    reader = scripted([[1, "a", 960, 540], [1, "a", 970, 540]], SHAPES)
    poller, rec = poller_for(reader)
    run_ticks(poller, 2)
    assert [kind for kind, _ in rec.events] == ["shape", "pos", "pos"]
    poller.pause()
    poller.pause()  # twice: still one
    assert rec.events[-1] == ("pos", {**rec.positions()[-1], "visible": False}) and len(rec.events) == 4
    hidden = rec.events[-1][1]
    assert (hidden["x"], hidden["y"]) == (round(970 * 65535 / 1919), round(540 * 65535 / 1079))
    run_ticks(poller, 5)
    assert len(rec.events) == 4 and poller.paused  # nothing while paused
    poller.resume()
    run_ticks(poller, 1)
    assert [kind for kind, _ in rec.events[4:]] == ["shape", "pos"] and rec.events[-1][1]["visible"] is True
    poller.resend()  # the data channel opened: the same again
    run_ticks(poller, 1)
    assert [kind for kind, _ in rec.events[6:]] == ["shape", "pos"]
    poller.pause()
    poller.resend()  # paused stays paused
    run_ticks(poller, 3)
    assert rec.events[-1][1]["visible"] is False and len(rec.events) == 9


def test_set_surface_hands_over_to_the_new_generation_without_an_old_one_leaving() -> None:
    """A rebuild (or new reserved rectangles) is announced with the next ``gen``: the worker calls ``set_surface``
    before the geometry goes out; from its return no position of the old generation is handed out (the poller's
    thread included), and shape and position follow at once in the new one."""
    reader = fake.FakeCursorReader(rect=(0, 0, 1920, 1080))  # the walk: a new position at every read
    poller, rec = poller_for(reader)
    poller.start()
    try:
        deadline = time.monotonic() + 10
        while len(rec.positions()) < 5 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert len(rec.positions()) >= 5
        poller.set_surface((0, 0, 1280, 720), 2)
        mark = len(rec.events)
        while not any(kind == "pos" for kind, _ in rec.events[mark:]) and time.monotonic() < deadline:
            time.sleep(0.01)
    finally:
        poller.stop()
    after = rec.events[mark:]
    assert after and after[0][0] == "shape"  # shape before position after a handoff
    assert all(m["gen"] == 2 for kind, m in after if kind == "pos")
    assert all(m["gen"] == 1 for kind, m in rec.events[:mark] if kind == "pos")


def test_the_walk_shows_named_and_custom_shapes_and_leaves_the_monitor() -> None:
    reader = fake.FakeCursorReader(rect=(100, 50, 1920, 1080))
    poller, rec = poller_for(reader, rect=(100, 50, 1920, 1080))
    run_ticks(poller, 165)
    kinds = [m["kind"] for m in rec.shapes()]
    assert kinds[:5] == ["arrow", "ibeam", "arrow", "custom", "ibeam"]  # custom at tick 90, back at 91
    custom = next(m for m in rec.shapes() if m["kind"] == "custom")
    assert (custom["width"], custom["height"], custom["hotspot_x"], custom["hotspot_y"]) == (24, 24, 3, 2)
    hidden = [m for m in rec.positions() if not m["visible"]]
    assert len(hidden) == 1  # ticks 150-159: off the monitor (one message: unchanged after that)
    visible = rec.positions()
    assert visible[visible.index(hidden[0]) + 1]["visible"] is True  # back at tick 160


def test_custom_cursors_that_cannot_be_sent_become_the_arrow_and_are_counted() -> None:
    shapes = {
        "unrenderable": {"nothing": True},
        "noise": ("custom", noise(64, 64), 64, 64, 0, 0),
        "huge": ("custom", noise(300, 300), 300, 300, 150, 150),  # scaled to 64 x 64: still incompressible
    }
    reader = scripted([[1, "unrenderable", 10, 10], [1, "noise", 10, 10], [1, "huge", 10, 10]], shapes)
    poller, rec = poller_for(reader)
    with debug_lines() as logs:
        run_ticks(poller, 3)
    assert [m["kind"] for m in rec.shapes()] == ["arrow", "arrow", "arrow"] and poller.bitmap_too_large == 3
    assert len(logs) == 3
    for entry in logs:  # kind, size and character count only: never the bitmap, the handle or a position
        assert set(entry) <= {"event", "log_level", "kind", "w", "h", "bitmap_chars"}, entry
        assert all("png" not in key for key in entry)


def test_a_shape_that_fails_to_render_is_the_arrow_and_positions_keep_flowing() -> None:
    """A render or an encode that raises anything (a TypeError from a ctypes conversion, MemoryError, a PIL error):
    the shape goes out as the arrow, counted, its class name logged; the handle is not left half-decided, so the
    positions that need a shape first keep flowing."""

    class Broken(fake.FakeCursorReader):
        renders = 0

        def render(self, hcursor: int) -> tuple[bytes, int, int, int, int, int] | None:
            Broken.renders += 1
            raise TypeError("wrong type")

    reader = Broken({"samples": [[1, "c", 10 + i, 10] for i in range(30)], "shapes": SHAPES})
    poller, rec = poller_for(reader)
    with debug_lines() as logs:
        run_ticks(poller, 30)
    assert [m["kind"] for m in rec.shapes()] == ["arrow"] and len(rec.positions()) == 30
    assert (poller.bitmap_too_large, poller.poll_errors, poller.failed, Broken.renders) == (1, 0, False, 1)
    [entry] = logs
    assert entry == {
        "event": "screen cursor shape failed; sent as arrow",
        "log_level": "debug",
        "kind": "custom",
        "error": "TypeError",
    }


def test_a_failing_shape_lookup_is_retried_and_ten_in_a_row_stop_the_reporting() -> None:
    """``named_kind`` raising leaves the handle undecided (the next tick tries again), counts a poll error each time,
    and ten in a row trip the error stop like ten failed reads (before, the handle was marked as seen first, so the
    shape was never retried and no position ever followed, with no error stop)."""

    class Flaky(fake.FakeCursorReader):
        failures = 0

        def named_kind(self, hcursor: int) -> str | None:
            if Flaky.failures:
                Flaky.failures -= 1
                raise OSError("LoadCursorW failed")
            return super().named_kind(hcursor)

    Flaky.failures = 2
    reader = Flaky({"samples": [[1, "a", 960, 540]], "shapes": SHAPES})
    poller, rec = poller_for(reader)
    run_ticks(poller, 3)
    assert [kind for kind, _ in rec.events] == ["shape", "pos"] and poller.poll_errors == 2 and not poller.failed
    Flaky.failures = CURSOR_POLL_ERRORS_MAX
    poller.resend()  # the shape is decided again: it fails ten times in a row
    run_ticks(poller, CURSOR_POLL_ERRORS_MAX, start=200.0)
    assert poller.failed and poller.poll_errors == 2 + CURSOR_POLL_ERRORS_MAX
    assert rec.events[-1][1]["visible"] is False  # the stop hides the pointer once


def test_a_shape_is_rendered_with_the_mutex_released_and_dropped_if_forgotten_meanwhile() -> None:
    """GDI, the mask and the PNG encode take milliseconds; the worker's event loop calls set_surface / pause / resume /
    resend, which must not wait for them. A shape rendered while one of those ran is not offered (the next tick
    decides afresh, the rendered shape coming from the cache)."""
    rendering, release = threading.Event(), threading.Event()
    renders: list[int] = []

    class Slow(fake.FakeCursorReader):
        def render(self, hcursor: int) -> tuple[bytes, int, int, int, int, int] | None:
            renders.append(hcursor)
            rendering.set()
            assert release.wait(5)
            return super().render(hcursor)

    reader = Slow({"samples": [[1, "c", 960, 540]], "shapes": SHAPES})
    poller, rec = poller_for(reader)
    ticking = threading.Thread(target=poller.tick, args=(100.0,))
    ticking.start()
    try:
        assert rendering.wait(5)
        began = time.perf_counter()
        poller.set_surface(RECT, 2)  # the loop's call while the render runs: not held up by it
        poller.pause()
        assert time.perf_counter() - began < 0.5
    finally:
        release.set()
        ticking.join(5)
    assert rec.events == []  # the shape rendered for gen 1 before the handoff is not offered, nor any position
    poller.resume()
    poller.tick(101.0)
    assert [kind for kind, _ in rec.events] == ["shape", "pos"] and rec.positions()[0]["gen"] == 2
    assert rec.shapes()[0]["kind"] == "custom" and len(renders) == 1  # from the cache


def test_the_shape_cache_keeps_eight_shapes_for_ten_seconds() -> None:
    renders: list[int] = []

    class Counting(fake.FakeCursorReader):
        def render(self, hcursor: int) -> tuple[bytes, int, int, int, int, int] | None:
            renders.append(hcursor)
            return super().render(hcursor)

    shapes = {f"c{i}": ("custom", bitmap(10, 10, i), 10, 10, 1, 1) for i in range(10)}
    samples = [[1, "c0", 5, 5], [1, "c1", 5, 5], [1, "c0", 5, 5]]
    reader = Counting({"samples": samples, "shapes": shapes})
    poller, rec = poller_for(reader)
    for now in (0.0, 1.0, 2.0):  # c0, c1, c0 again inside the TTL: from the cache
        poller.tick(now)
    assert len(renders) == 2 and [m["kind"] for m in rec.shapes()] == ["custom"] * 3
    assert rec.shapes()[0] == rec.shapes()[2]
    handle = renders[0]  # stored at 0.0: kept for ten seconds from then
    assert poller.cache.get(handle, CURSOR_SHAPE_CACHE_TTL_SECONDS - 0.1) is not None
    assert poller.cache.get(handle, CURSOR_SHAPE_CACHE_TTL_SECONDS + 0.1) is None  # expired: rendered again
    assert all(len(key) == 5 for key in poller.cache.keys())  # (hcursor, hotspot_x, hotspot_y, width, height)
    cache = CursorShapeCache()
    for i in range(12):
        cache.put((i, 0, 0, 1, 1), {"type": "cursor_shape", "kind": "arrow"}, 0.0)
    assert len(cache) == 8 and cache.get(0, 1.0) is None and cache.get(11, 1.0) is not None
    cache.put((11, 2, 2, 3, 3), {"type": "cursor_shape", "kind": "ibeam"}, 1.0)  # a handle reused: one entry
    assert len(cache) == 8 and cache.get(11, 1.5) == {"type": "cursor_shape", "kind": "ibeam"}


def test_ten_failed_reads_in_a_row_stop_the_reporting_once() -> None:
    class Failing(fake.FakeCursorReader):
        failing = False

        def read(self) -> tuple[int, int, int, int] | None:
            if self.failing:
                raise OSError("GetCursorInfo failed at 0x7ffdeadbeef")
            return super().read()

    reader = Failing({"samples": [[1, "a", 960, 540]], "shapes": SHAPES})
    poller, rec = poller_for(reader)
    run_ticks(poller, 1)
    reader.failing = True
    with structlog.testing.capture_logs() as logs:
        run_ticks(poller, CURSOR_POLL_ERRORS_MAX - 1)
        assert not poller.failed and len(rec.events) == 2
        run_ticks(poller, 1)
        assert poller.failed and poller.poll_errors == CURSOR_POLL_ERRORS_MAX
        run_ticks(poller, 5)
    assert [kind for kind, _ in rec.events] == ["shape", "pos", "pos"] and rec.events[-1][1]["visible"] is False
    [stopped] = [e for e in logs if e["event"] == "cursor reporting stopped"]
    assert stopped == {"event": "cursor reporting stopped", "log_level": "warning", "errors": 10, "error": "OSError"}
    # a read that fails now and then is counted but does not stop it
    reader = Failing({"samples": [[1, "a", 960, 540]], "shapes": SHAPES})
    poller, rec = poller_for(reader)
    for i in range(30):
        reader.failing = i % 3 == 0
        poller.tick(float(i))
    assert not poller.failed and poller.poll_errors == 10


def test_a_poller_thread_survives_a_reader_whose_thread_setup_fails() -> None:
    class Unprepared(fake.FakeCursorReader):
        prepared = threading.Event()

        def prepare_thread(self) -> None:
            Unprepared.prepared.set()
            raise OSError("no DPI awareness")

    reader = Unprepared({"samples": [[1, "a", 10, 10]], "shapes": SHAPES})
    poller, rec = poller_for(reader)
    poller.start()
    try:
        assert Unprepared.prepared.wait(5)
        deadline = time.monotonic() + 5
        while not rec.positions() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert rec.positions()
    finally:
        poller.stop()


def test_the_fake_reader_reads_the_source_specs_script() -> None:
    reader = fake.FakeCursorReader(
        {"samples": [[1, "x", 1, 2], [0, "", 3, 4]], "shapes": {"x": {"custom": {"w": 5, "h": 3}}}}
    )
    flags, handle, px, py = reader.read()  # type: ignore[misc]
    assert (flags, px, py) == (1, 1, 2) and handle and reader.named_kind(handle) is None
    rendered = reader.render(handle)
    assert rendered is not None and rendered[1:] == (5, 3, 20, 0, 0) and len(rendered[0]) == 60
    assert reader.read() == (0, 0, 3, 4) and reader.read() == (0, 0, 3, 4)  # the last sample is held
    reader.add_samples([[1, "arrow", 7, 8]])
    assert reader.read() == (0, 0, 3, 4)
    flags, handle, px, py = reader.read()  # type: ignore[misc]
    assert (px, py) == (7, 8) and reader.named_kind(handle) == "arrow"  # a tag that is a kind needs no shapes entry
    for bad in ({}, {"samples": []}, {"samples": [[1, "a"]]}, {"samples": [[1, "a", 1, 2]], "shapes": []}, "walk"):
        with pytest.raises((ValueError, TypeError)):
            fake.FakeCursorReader(bad)
    with pytest.raises(ValueError):
        fake.FakeCursorReader().add_samples([[1, "a", 1, 1]])


# ----- the Win32 reader ---------------------------------------------------------------------------------------------------
def test_the_win32_structures_have_the_win32_layout() -> None:
    sizes = cursor_win.expected_sizes()
    assert sizes == {
        "CURSORINFO": ctypes.sizeof(cursor_win.CURSORINFO),
        "ICONINFO": ctypes.sizeof(cursor_win.ICONINFO),
        "BITMAP": ctypes.sizeof(cursor_win.BITMAP),
        "BITMAPINFOHEADER": ctypes.sizeof(cursor_win.BITMAPINFOHEADER),
    }
    if ctypes.sizeof(ctypes.c_void_p) == 8:
        assert sizes == {"CURSORINFO": 24, "ICONINFO": 32, "BITMAP": 32, "BITMAPINFOHEADER": 40}
    assert cursor_win.CURSORINFO.ptScreenPos.offset == (16 if ctypes.sizeof(ctypes.c_void_p) == 8 else 12)


class FakeGdi:
    """``gdi32`` as far as the reader uses it: handles, DIB sections backed by ctypes buffers, a selection per DC;
    every object made and deleted is recorded so the test can see nothing leaked."""

    def __init__(self) -> None:
        self.next = 0x5000
        self.live: set[int] = set()
        self.dibs: dict[int, Any] = {}
        self.selected: dict[int, int | None] = {}
        self.bitmaps: dict[int, tuple[int, int]] = {}

    def new(self) -> int:
        self.next += 1
        self.live.add(self.next)
        return self.next

    def GetObjectW(self, handle: int, size: int, pointer: Any) -> int:  # noqa: N802 - Win32 name
        found = self.bitmaps.get(int(handle))
        if found is None:
            return 0
        pointer._obj.bmWidth, pointer._obj.bmHeight = found
        return size

    def CreateCompatibleDC(self, _dc: Any) -> int:  # noqa: N802
        return self.new()

    def CreateDIBSection(self, _dc: int, header: Any, _usage: int, bits: Any, _section: Any, _offset: int) -> int:  # noqa: N802
        head = header._obj.bmiHeader
        assert head.biBitCount == 32 and head.biHeight < 0 and head.biCompression == cursor_win.BI_RGB
        buffer = (ctypes.c_ubyte * (head.biWidth * -head.biHeight * 4))(*([0x55] * (head.biWidth * -head.biHeight * 4)))
        handle = self.new()
        self.dibs[handle] = buffer
        bits._obj.value = ctypes.addressof(buffer)
        return handle

    def SelectObject(self, dc: int, obj: int | None) -> int | None:  # noqa: N802
        previous = self.selected.get(dc)
        self.selected[dc] = obj
        return previous

    def DeleteObject(self, handle: int) -> int:  # noqa: N802
        self.live.discard(int(handle))
        return 1

    def DeleteDC(self, handle: int) -> int:  # noqa: N802
        self.live.discard(int(handle))
        return 1

    def GdiFlush(self) -> int:  # noqa: N802
        return 1


class FakeUser:
    """``user32``: one cursor per handle, drawn as scripted for DI_NORMAL and DI_MASK."""

    def __init__(self, gdi: FakeGdi) -> None:
        self.gdi = gdi
        self.cursors: dict[int, dict[str, Any]] = {}
        self.draws: list[int] = []
        self.dpi: list[int] = []
        self.fail_draw = False

    def GetCursorInfo(self, pointer: Any) -> int:  # noqa: N802
        info = pointer._obj
        assert info.cbSize == ctypes.sizeof(cursor_win.CURSORINFO)
        info.flags, info.hCursor = 1, 0x777
        info.ptScreenPos.x, info.ptScreenPos.y = -1900, 40
        return 1

    def LoadCursorW(self, _module: Any, ident: Any) -> int:  # noqa: N802
        return 0x100000 + int(ident.value)

    def GetIconInfo(self, handle: Any, pointer: Any) -> int:  # noqa: N802
        found = self.cursors.get(int(handle.value))
        if found is None:
            return 0
        info = pointer._obj
        info.xHotspot, info.yHotspot = found["hx"], found["hy"]
        mask = self.gdi.new()
        self.gdi.bitmaps[mask] = (found["w"], found["h"] * (1 if found["color"] is not None else 2))
        info.hbmMask = mask
        if found["color"] is not None:
            color = self.gdi.new()
            self.gdi.bitmaps[color] = (found["w"], found["h"])
            info.hbmColor = color
        return 1

    def DrawIconEx(  # noqa: N802
        self, dc: int, _x: int, _y: int, handle: Any, w: int, h: int, _step: int, _brush: Any, flags: int
    ) -> int:
        self.draws.append(flags)
        if self.fail_draw:
            return 0
        found = self.cursors[int(handle.value)]
        data = found["normal"] if flags == cursor_win.DI_NORMAL else found["mask"]
        ctypes.memmove(self.gdi.dibs[int(self.gdi.selected[dc] or 0)], data, len(data))
        return 1

    def SetThreadDpiAwarenessContext(self, value: int) -> int:  # noqa: N802
        self.dpi.append(value)
        return 0


def test_the_win32_reader_reads_names_and_renders_through_the_stand_ins() -> None:
    gdi = FakeGdi()
    user = FakeUser(gdi)
    reader = cursor_win.Win32CursorReader(user32=user, gdi32=gdi)
    reader.prepare_thread()
    assert user.dpi == [cursor_win.DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2]
    assert reader.read() == (1, 0x777, -1900, 40)
    assert reader.named_kind(0x100000 + 32513) == "ibeam" and reader.named_kind(0x100000 + 32649) == "hand"
    assert reader.named_kind(0x777) is None
    # a colour cursor with its own alpha: drawn once; DrawIconEx blended it onto the zeroed DIB, so its premultiplied
    # colours come back straight (a 50 % white edge stays white, not grey)
    drawn = (b"\x80\x80\x80\x80" + b"\x00\x00\xff\xff" + b"\x00\x00\x00\x00" + b"\x40\x20\x10\x80") * 3
    straight = (b"\xff\xff\xff\x80" + b"\x00\x00\xff\xff" + b"\x00\x00\x00\x00" + b"\x7f\x3f\x1f\x80") * 3
    user.cursors[0x10] = {"w": 4, "h": 3, "hx": 1, "hy": 2, "color": True, "normal": drawn, "mask": b""}
    assert reader.render(0x10) == (straight, 4, 3, 16, 1, 2)
    assert user.draws == [cursor_win.DI_NORMAL] and gdi.live == set()  # every GDI object released
    # a mask-only cursor (no alpha): its mask DIB says what is transparent; an inverting pixel stays white
    white, black = b"\xff\xff\xff\x00", b"\x00\x00\x00\x00"
    user.cursors[0x11] = {
        "w": 2,
        "h": 2,
        "hx": 0,
        "hy": 0,
        "color": None,
        "normal": black + white + white + black,  # DI_NORMAL on black: XOR bits
        "mask": white + white + black + black,  # DI_MASK: AND bits
    }
    user.draws.clear()
    rendered = reader.render(0x11)
    assert rendered is not None and rendered[1:] == (2, 2, 8, 0, 0)
    assert user.draws == [cursor_win.DI_NORMAL, cursor_win.DI_MASK]
    assert rendered[0][3::4] == bytes([0, 255, 255, 255]) and rendered[0][4:7] == b"\xff\xff\xff"
    assert gdi.live == set()
    # failures: no icon info, a draw that fails (still everything released)
    assert reader.render(0x99) is None
    user.fail_draw = True
    assert reader.render(0x10) is None and gdi.live == set()


def test_the_win32_reader_needs_windows_without_stand_ins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cursor_win.sys, "platform", "linux")
    with pytest.raises(OSError):
        cursor_win.Win32CursorReader()
