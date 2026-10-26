"""Desktop Duplication's failure reasons and the Windows.Graphics.Capture fallback (design §5.2), on Linux.

* ``capture_dda``: FFmpeg's own error lines (collected while the graph opens; PyAV drops them by default) reach the
  error's message sanitised (no paths, no long strings such as the graph, ≤ 200 characters), and the detail is
  classified from them, never from PyAV's exception string (it quotes the graph, ``output_idx`` included). A real
  ``lavfi`` graph that fails to configure stands in for ``ddagrab``.
* ``capture_wgc`` against a fake of windows-capture 2.0.1's NATIVE module injected through ``sys.modules`` (its
  constructor signature copied from ``windows-capture-python/src/lib.rs``): the monitor is passed as the package's
  one-based raw position, mapped BGRA frames with a wider row pitch become yuv420p at the track's size, the newest
  frame wins, the first frame's size is checked, stop/close release everything. The by-file loader is exercised with
  a compiled stand-in extension (skipped without a C compiler).
* ``platform.windows.screen.capture_order_position`` (pure).

Nothing here touches Windows.Graphics.Capture or Desktop Duplication: **not yet verified on a device**.
"""

from __future__ import annotations

import ctypes
import gc
import importlib.machinery
import shutil
import subprocess
import sys
import sysconfig
import types
import weakref
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import av
import av.logging
import pytest
import structlog
from dome_protocol import ProtocolError

from dome_agent.platform.windows.screen import capture_order_position
from dome_agent.screen.worker import capture_dda, capture_wgc
from dome_agent.screen.worker import main as worker_main

# ----- capture_dda: FFmpeg's words, sanitised ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (r"Failed to open C:\Users\Jane Doe\AppData\Local\x.dll here", "Failed to open [path] here"),
        (r"load \\server\share\x.dll now", "load [path] now"),
        ("a C:/Program Files/DoMe/_internal/av.libs/avcodec-62.dll b", "a [path] b"),
        ("cannot read /home/jane/.cache/dome/state.json: denied", "cannot read [path]: denied"),
        ("framerate 30/1 at 1920/1080", "framerate 30/1 at 1920/1080"),  # a ratio is not a path
        ("graph 'ddagrab=output_idx=0:framerate=30:draw_mouse=1:dup_frames=0,scale_d3d11=w=1280' x", "graph [...] x"),
        ("two\nlines\x1b[0m\tand\x00nul", "two lines [0m and nul"),
    ],
)
def test_sanitise_reason_drops_paths_long_strings_and_control_characters(raw: str, expected: str) -> None:
    assert capture_dda.sanitise_reason(raw) == expected


def test_sanitise_reason_is_capped() -> None:
    text = capture_dda.sanitise_reason("word " * 100)
    assert len(text) == capture_dda.REASON_MAX_CHARS == 200 and capture_dda.sanitise_reason("x y", 1) == "x"


@pytest.mark.parametrize(
    ("reason", "detail"),
    [  # the phrases are libavfilter/vsrc_ddagrab.c's (FFmpeg n8.1.3), as ffmpeg_error_text composes them
        ("Too many open duplication sessions; Device or resource busy (errno 16)", "busy"),
        (
            "Selected output not supported; Failed to configure output pad on Parsed_ddagrab_0; "
            "Generic error in an external library (errno 542398533)",
            "unsupported_display",
        ),
        (
            "Failed to enumerate DXGI output 1; Generic error in an external library (errno 542398533)",
            "unsupported_display",
        ),
        ("Desktop duplication access denied; Operation not permitted (errno 1)", "remote_session"),
        ("Failed to create D3D11VA device.; Generic error in an external library (errno 542398533)", "driver"),
        ("Failed duplicating output; Generic error in an external library (errno 542398533)", "driver"),
        # the old classifier read PyAV's exception string, which quotes the graph: "output_idx" matched every failure
        ("Invalid argument: 'ddagrab=output_idx=0:framerate=30' returned 22", "driver"),
    ],
)
def test_classify_capture_error_reads_ffmpegs_own_phrases(reason: str, detail: str) -> None:
    assert capture_dda.classify_capture_error(reason) == detail


def test_ffmpeg_error_text_puts_the_logged_cause_first_and_never_the_graph() -> None:
    graph = capture_dda.ddagrab_graph(0, 1280, 720, 30)
    exc = av.error.ExternalError(542398533, "Generic error in an external library", graph)
    assert "output_idx" in str(exc)  # what the old classifier read
    logged = [
        (16, "Parsed_ddagrab_0", "Selected output not supported\n"),
        (16, "Parsed_ddagrab_0", "Failed to configure output pad on Parsed_ddagrab_0\n"),
        (16, "Parsed_ddagrab_0", "Selected output not supported\n"),  # a repeat is kept once
    ]
    text = capture_dda.ffmpeg_error_text(exc, logged)
    assert text == (
        "Selected output not supported; Failed to configure output pad on Parsed_ddagrab_0; "
        "Generic error in an external library (errno 542398533)"
    )
    assert capture_dda.classify_capture_error(text) == "unsupported_display"
    assert capture_dda.ffmpeg_error_text(RuntimeError(r"boom at C:\Users\Jane\x")) == "RuntimeError: boom at [path]"
    assert capture_dda.ffmpeg_error_text(RuntimeError()) == "RuntimeError"


def dda(monkeypatch: pytest.MonkeyPatch, graph: str) -> Any:
    """A DdagrabSource whose graph is a real ``lavfi`` graph that runs on Linux."""
    with monkeypatch.context() as m:
        m.setattr(sys, "platform", "win32")
        source = capture_dda.DdagrabSource(0, 64, 36, 30)
    monkeypatch.setattr(capture_dda, "ddagrab_graph", lambda *_args: graph)
    return source


def test_a_graph_that_cannot_open_keeps_ffmpegs_logged_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    """The real path: PyAV's error says only "Invalid argument" (and quotes the graph); the reason FFmpeg logged is
    collected on this thread while the graph opens and leads the message. PyAV's log level is restored after. (The
    graph uses only filters DoMe's LGPL FFmpeg build has.)"""
    source = dda(monkeypatch, "testsrc=size=64x64,format=pix_fmts=nonexistent")
    assert av.logging.get_level() is None
    with pytest.raises(ProtocolError) as caught:
        source.open()
    error = caught.value
    assert error.code == "SCREEN_CAPTURE_UNAVAILABLE" and error.detail == {"detail": "driver"}
    assert error.message.startswith("Desktop Duplication could not start: Invalid pixel format 'nonexistent'")
    assert "Invalid argument (errno 22)" in error.message
    assert "testsrc" not in error.message and "pix_fmts" not in error.message and len(error.message) <= 240
    assert av.logging.get_level() is None  # PyAV's default again: FFmpeg's log dropped outside the open


def test_a_level_someone_set_is_left_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    source = dda(monkeypatch, "testsrc=size=64x64,format=pix_fmts=nonexistent")
    av.logging.set_level(av.logging.WARNING)
    try:
        with pytest.raises(ProtocolError) as caught:
            source.open()
        assert "Invalid pixel format" in caught.value.message and av.logging.get_level() == av.logging.WARNING
    finally:
        av.logging.set_level(None)


def test_the_running_source_is_logged_once_with_its_size(monkeypatch: pytest.MonkeyPatch) -> None:
    source = dda(monkeypatch, "testsrc=size=64x36:rate=30,format=nv12")
    with structlog.testing.capture_logs() as logs:
        source.open()
        frame = next(f for f in (source.read() for _ in range(100)) if f is not None)
        source.close()
        source.open()  # a rebuild (every UAC prompt) does not repeat the line
        source.close()
    assert (frame.width, frame.height, frame.format.name) == (64, 36, "nv12")
    running = [e for e in logs if e["event"] == "screen capture source running"]
    assert running == [
        {
            "event": "screen capture source running",
            "log_level": "info",
            "source": "dda",
            "output_idx": 0,
            "size": "64x36",
        }
    ]


def test_the_fallback_warning_carries_desktop_duplications_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    """The device log said only ``detail=unsupported_display``: the warning now carries FFmpeg's sanitised words."""

    class Dda:
        def __init__(self, *_args: Any) -> None:
            self.width, self.height = 1280, 720

        def open(self) -> None:
            raise ProtocolError(
                "SCREEN_CAPTURE_UNAVAILABLE",
                "Desktop Duplication could not start: Selected output not supported; Generic error in an external "
                "library (errno 542398533)",
                detail={"detail": "unsupported_display"},
            )

    class Wgc:
        def __init__(self, *_args: Any, **_kw: Any) -> None:
            self.width, self.height = 1280, 720

        def open(self) -> None:
            return None

    monkeypatch.setattr(capture_dda, "DdagrabSource", Dda)
    monkeypatch.setattr(capture_wgc, "WgcSource", Wgc)
    with monkeypatch.context() as m:
        m.setattr(sys, "platform", "win32")
        source = worker_main._build_source(
            "ddagrab", {"surface_index": 0, "width": 1920, "height": 1080}, 1280, 720, 30
        )  # noqa: SLF001
    with structlog.testing.capture_logs() as logs:
        source.open()
    [warning] = [e for e in logs if e["log_level"] == "warning"]
    assert warning["event"] == "Desktop Duplication unavailable; trying Windows.Graphics.Capture"
    assert warning["detail"] == "unsupported_display"
    assert warning["reason"].endswith(
        "Selected output not supported; Generic error in an external library (errno 542398533)"
    )


# ----- the monitor windows-capture is asked for -----------------------------------------------------------------------

# raw EnumDisplayMonitors order: a secondary on the left first, the primary second, a third monitor above
RAW = [((-1920, 0, 0, 1080), False), ((0, 0, 2560, 1440), True), ((0, -1080, 1920, 0), False)]


def test_capture_order_position_finds_the_primary_by_its_flag_and_others_by_rectangle() -> None:
    assert capture_order_position(RAW, left=0, top=0, width=2560, height=1440, primary=True) == 2  # not index 1
    assert capture_order_position(RAW, left=0, top=0, width=1, height=1, primary=True) == 2  # the flag decides
    assert capture_order_position(RAW, left=-1920, top=0, width=1920, height=1080, primary=False) == 1
    assert capture_order_position(RAW, left=0, top=-1080, width=1920, height=1080, primary=False) == 3
    assert capture_order_position(RAW, left=0, top=-1080, width=1280, height=720, primary=False) is None  # resized
    assert capture_order_position(RAW[:1], left=0, top=0, width=2560, height=1440, primary=True) is None  # unplugged


# ----- capture_wgc against a fake of the native module ----------------------------------------------------------------


class FakeFrame:
    """A ``NativeMappedFrame``: BGRA rows ``stride`` bytes apart, read through ``buffer_view()`` (a memoryview of a
    ctypes array, as the package's is); ``released`` records when the last reference went (the package unmaps then)."""

    color_format = "bgra8"
    bytes_per_pixel = 4

    def __init__(
        self, width: int, height: int, stride: int, pixel: Callable[[int, int], bytes], released: list[str], tag: str
    ) -> None:
        self.width, self.height, self.bytes_per_row = width, height, stride
        self._buffer = (ctypes.c_ubyte * (stride * height))(*([0xEE] * (stride * height)))  # padding: garbage
        for y in range(height):
            row = b"".join(pixel(x, y) for x in range(width))
            ctypes.memmove(ctypes.addressof(self._buffer) + y * stride, row, len(row))
        weakref.finalize(self, released.append, tag)

    def buffer_view(self) -> memoryview:
        return memoryview(self._buffer)


class FakeControl:
    def __init__(self) -> None:
        self.stops = 0
        self.finished = False

    def stop(self) -> None:
        self.stops += 1
        self.finished = True

    def is_finished(self) -> bool:
        return self.finished


class FakeCapture:
    """``NativeWindowsCapture`` with the 2.0.1 signature: a keyword DoMe got wrong would raise TypeError here too."""

    instances: list[FakeCapture] = []
    start_error: Exception | None = None

    def __init__(
        self,
        on_frame_arrived_callback: Callable[..., None],
        on_closed: Callable[[], None],
        cursor_capture: bool | None = None,
        draw_border: bool | None = None,
        secondary_window: bool | None = None,
        minimum_update_interval: int | None = None,
        dirty_region: bool | None = None,
        monitor_index: int | None = None,
        window_name: str | None = None,
        window_hwnd: int | None = None,
    ) -> None:
        self.on_frame_arrived, self.on_closed = on_frame_arrived_callback, on_closed
        self.settings = {
            "cursor_capture": cursor_capture,
            "draw_border": draw_border,
            "secondary_window": secondary_window,
            "minimum_update_interval": minimum_update_interval,
            "dirty_region": dirty_region,
            "monitor_index": monitor_index,
            "window_name": window_name,
            "window_hwnd": window_hwnd,
        }
        self.control: FakeControl | None = None
        FakeCapture.instances.append(self)

    def start_free_threaded(self) -> FakeControl:
        if FakeCapture.start_error is not None:
            raise FakeCapture.start_error
        self.control = FakeControl()
        return self.control

    def arrive(self, frame: FakeFrame) -> list[bool]:
        """What the package's capture thread does for one frame; returns its stop_list."""
        stop = [False]
        self.on_frame_arrived(frame, frame.bytes_per_row * frame.height, frame.width, frame.height, stop, 1234)
        return stop


@pytest.fixture
def native(monkeypatch: pytest.MonkeyPatch) -> Iterator[types.ModuleType]:
    module = types.ModuleType(capture_wgc.NATIVE_MODULE)
    module.NativeWindowsCapture = FakeCapture  # type: ignore[attr-defined]
    module.NativeCaptureControl = FakeControl  # type: ignore[attr-defined]
    module.NativeMappedFrame = FakeFrame  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, capture_wgc.NATIVE_MODULE, module)
    FakeCapture.instances = []
    FakeCapture.start_error = None
    yield module
    FakeCapture.instances = []
    FakeCapture.start_error = None


@pytest.fixture
def located(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """capture_position's arguments; it answers 2 (the primary is the second monitor Windows enumerates)."""
    calls: list[dict[str, Any]] = []

    def position(**kw: Any) -> int | None:
        calls.append(kw)
        return 2

    monkeypatch.setattr(capture_wgc, "capture_position", position)
    return calls


def wgc(monkeypatch: pytest.MonkeyPatch, width: int = 64, height: int = 4, **kw: Any) -> capture_wgc.WgcSource:
    with monkeypatch.context() as m:
        m.setattr(sys, "platform", "win32")
        return capture_wgc.WgcSource(0, width, height, 30, **kw)


def red(_x: int, _y: int) -> bytes:
    return bytes((0, 0, 255, 255))  # BGRA


def test_wgc_asks_for_the_streamed_monitor_by_its_raw_position_and_streams_yuv420p(
    monkeypatch: pytest.MonkeyPatch, native: Any, located: list[dict[str, Any]]
) -> None:
    released: list[str] = []
    source = wgc(monkeypatch, expected_size=(66, 4), origin=(0, 0))
    with structlog.testing.capture_logs() as logs:
        source.open()
    [capture] = FakeCapture.instances
    assert located == [{"left": 0, "top": 0, "width": 66, "height": 4, "primary": True}]
    # only the cursor and the monitor are set: Windows 10 refuses a border or update-interval setting it lacks
    assert capture.settings == {
        "cursor_capture": True,
        "draw_border": None,
        "secondary_window": None,
        "minimum_update_interval": None,
        "dirty_region": None,
        "monitor_index": 2,
        "window_name": None,
        "window_hwnd": None,
    }
    assert source.capture_index == 2
    assert {
        "event": "screen capture source running",
        "source": "wgc",
        "capture_index": 2,
        "size": "64x4",
    }.items() <= logs[0].items()
    assert source.read() is None  # nothing arrived yet: not an error
    line = av.VideoFrame(66, 4, "bgra").planes[0].line_size
    stride = 384  # D3D11's row pitch: wider than 66 x 4 = 264 and than PyAV's own
    assert stride != line
    assert capture.arrive(FakeFrame(66, 4, stride, red, released, "a")) == [False]
    with structlog.testing.capture_logs() as logs:
        out = source.read()
    assert out is not None and (out.width, out.height, out.format.name) == (64, 4, "yuv420p")
    assert out.time_base == capture_dda.VIDEO_TIME_BASE
    y, u, v = (bytes(plane)[0] for plane in out.planes)
    assert abs(y - 81) <= 2 and abs(u - 90) <= 2 and abs(v - 240) <= 2  # red, BT.601 limited range
    assert [e["event"] for e in logs] == ["screen capture first frame"] and logs[0]["size"] == "66x4"
    gc.collect()
    assert released == ["a"]  # copied, then released: the staging texture is unmapped
    source.close()
    assert capture.control is not None and capture.control.stops == 1


def test_bgra_frame_copies_rows_whatever_the_row_pitches(monkeypatch: pytest.MonkeyPatch) -> None:
    def shade(x: int, y: int) -> bytes:
        return bytes((y * 40 + 1, y * 40 + 2, (x * 3) % 256, 255))

    for width in (64, 66):
        line = av.VideoFrame(width, 3, "bgra").planes[0].line_size
        for stride in {line, width * 4, width * 4 + 128}:
            frame = FakeFrame(width, 3, stride, shade, [], "row")
            out = capture_wgc.bgra_frame(frame.buffer_view(), width, 3, stride)
            plane = bytes(out.planes[0])
            for y in range(3):
                assert plane[y * line : y * line + width * 4] == b"".join(shade(x, y) for x in range(width))
    with pytest.raises(ValueError):
        capture_wgc.bgra_frame(bytes(100), 64, 4, 256)


def test_wgc_newest_frame_wins_and_superseded_frames_are_released(
    monkeypatch: pytest.MonkeyPatch, native: Any, located: Any
) -> None:
    released: list[str] = []
    source = wgc(monkeypatch, expected_size=(64, 4))
    source.open()
    [capture] = FakeCapture.instances
    for tag in ("1", "2", "3"):
        capture.arrive(FakeFrame(64, 4, 256, red, released, tag))
    gc.collect()
    assert released == ["1", "2"]  # a slow encoder never builds a backlog
    assert source.read() is not None and source.read() is None
    capture.arrive(FakeFrame(64, 4, 256, red, released, "4"))
    source.close()  # stops the capture and releases what the track never took
    gc.collect()
    assert released == ["1", "2", "3", "4"]
    assert capture.arrive(FakeFrame(64, 4, 256, red, released, "5")) == [True]  # a late frame ends the loop


def test_wgc_refuses_a_first_frame_of_another_size(monkeypatch: pytest.MonkeyPatch, native: Any, located: Any) -> None:
    source = wgc(monkeypatch, expected_size=(2560, 1440))
    source.open()
    FakeCapture.instances[0].arrive(FakeFrame(64, 4, 256, red, [], "small"))
    with pytest.raises(ProtocolError) as caught:
        source.read()
    assert caught.value.detail == {"detail": "unsupported_display"}
    assert "another monitor than the one taps map onto" in caught.value.message
    source.close()


def test_wgc_reports_a_closed_session_and_an_unreadable_frame(
    monkeypatch: pytest.MonkeyPatch, native: Any, located: Any
) -> None:
    source = wgc(monkeypatch, expected_size=(64, 4))
    source.open()
    capture = FakeCapture.instances[0]
    short = FakeFrame(64, 4, 256, red, [], "short")
    short.bytes_per_row = 200  # a row pitch below width x 4
    capture.arrive(short)
    del short
    with pytest.raises(ProtocolError) as caught:
        source.read()
    assert caught.value.detail == {"detail": "driver"} and "unreadable frame" in caught.value.message
    capture.on_closed()  # the monitor went away
    with pytest.raises(ProtocolError, match="session closed"):
        source.read()
    source.close()
    # the package's thread ended without on_closed (an exception in a callback ends it): reported too
    other = wgc(monkeypatch, expected_size=(64, 4))
    other.open()
    assert FakeCapture.instances[1].control is not None
    FakeCapture.instances[1].control.finished = True
    with pytest.raises(ProtocolError, match="session closed"):
        other.read()


@pytest.mark.parametrize(
    ("message", "detail"),
    [
        (
            "Capture session threw an exception: The Graphics Capture API is not supported on this platform.",
            "unsupported_display",
        ),
        (r"Failed to get monitor from index: The monitor was not found. C:\Users\Jane\x.log", "driver"),
    ],
)
def test_wgc_start_failures_name_windows_reason(
    monkeypatch: pytest.MonkeyPatch, native: Any, located: Any, message: str, detail: str
) -> None:
    FakeCapture.start_error = Exception(message)
    source = wgc(monkeypatch, expected_size=(64, 4))
    with pytest.raises(ProtocolError) as caught:
        source.open()
    assert caught.value.detail == {"detail": detail}
    assert caught.value.message.startswith("Windows.Graphics.Capture could not start: Exception: ")
    assert "Jane" not in caught.value.message


def test_wgc_without_the_package_or_the_monitor(monkeypatch: pytest.MonkeyPatch, located: Any) -> None:
    monkeypatch.delitem(sys.modules, capture_wgc.NATIVE_MODULE, raising=False)
    if sys.platform == "win32":  # where the package is a dependency, stand for a build that left it out

        def missing() -> Any:
            raise ImportError("windows-capture's native module is not installed")

        monkeypatch.setattr(capture_wgc, "load_native", missing)
    with pytest.raises(ProtocolError, match="support is not installed") as caught:
        wgc(monkeypatch, expected_size=(64, 4)).open()  # windows-capture is a Windows-only dependency
    assert caught.value.detail == {"detail": "unsupported_display"}
    monkeypatch.setattr(capture_wgc, "load_native", lambda: types.ModuleType("native"))
    monkeypatch.setattr(capture_wgc, "capture_position", lambda **_kw: None)
    with pytest.raises(ProtocolError, match="cannot find the monitor") as caught:
        wgc(monkeypatch, expected_size=(64, 4), primary=False, origin=(-1920, 0)).open()
    assert caught.value.detail == {"detail": "unsupported_display"}

    def enumeration_fails(**_kw: Any) -> int | None:
        raise OSError(r"user32 failed at C:\Users\Jane")

    monkeypatch.setattr(capture_wgc, "capture_position", enumeration_fails)
    with pytest.raises(ProtocolError) as caught:
        wgc(monkeypatch, expected_size=(64, 4)).open()
    assert caught.value.detail == {"detail": "driver"}
    assert caught.value.message == "Windows.Graphics.Capture could not list the monitors (OSError)"


def test_the_worker_passes_the_monitors_rectangle_to_wgc(monkeypatch: pytest.MonkeyPatch, native: Any) -> None:
    seen: list[dict[str, Any]] = []
    monkeypatch.setattr(capture_wgc, "capture_position", lambda **kw: seen.append(kw) or 3)
    spec = {"surface_index": 1, "primary": False, "left": -1920, "top": 0, "width": 1920, "height": 1080}
    with monkeypatch.context() as m:
        m.setattr(sys, "platform", "win32")
        source = worker_main._build_source("wgc", spec, 1280, 720, 30)  # noqa: SLF001
    source.open()
    assert seen == [{"left": -1920, "top": 0, "width": 1920, "height": 1080, "primary": False}]
    assert FakeCapture.instances[0].settings["monitor_index"] == 3
    source.close()


def test_the_self_test_loads_the_native_module_on_windows_only(monkeypatch: pytest.MonkeyPatch, native: Any) -> None:
    if sys.platform != "win32":
        assert "wgc_native" not in worker_main.self_test(dev_h264=True)["checks"]
    with monkeypatch.context() as m:
        m.setattr(sys, "platform", "win32")
        report = worker_main.self_test(dev_h264=True)
    assert report["checks"]["wgc_native"] == {
        "ok": True,
        "value": {"classes": ["NativeCaptureControl", "NativeMappedFrame", "NativeWindowsCapture"]},
    }
    assert not FakeCapture.instances  # loaded only: no capture session was created


# ----- load_native: the extension by file, never the package's wrapper ------------------------------------------------

STAND_IN = r"""
#include <Python.h>
static struct PyModuleDef def = {PyModuleDef_HEAD_INIT, "windows_capture", NULL, -1, NULL};
PyMODINIT_FUNC PyInit_windows_capture(void) {
    PyObject *m = PyModule_Create(&def);
    if (m != NULL) PyModule_AddStringConstant(m, "NativeWindowsCapture", "stand-in");
    return m;
}
"""
WRAPPER = "raise RuntimeError('the wrapper ran: it imports cv2 and numpy')\n"


def package_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    folder = tmp_path / "site" / "windows_capture"
    folder.mkdir(parents=True)
    (folder / "__init__.py").write_text(WRAPPER, encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path / "site"))
    monkeypatch.delitem(sys.modules, capture_wgc.NATIVE_MODULE, raising=False)
    monkeypatch.delitem(sys.modules, "windows_capture", raising=False)
    return folder


def test_load_native_never_runs_the_wrapper(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    package_dir(tmp_path, monkeypatch)  # the wrapper, without the native module
    with pytest.raises(ImportError, match="native module is not installed"):
        capture_wgc.load_native()
    assert "windows_capture" not in sys.modules


@pytest.mark.skipif(shutil.which("cc") is None, reason="needs a C compiler for the stand-in extension")
def test_load_native_loads_the_extension_by_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    folder = package_dir(tmp_path, monkeypatch)
    source = tmp_path / "stand_in.c"
    source.write_text(STAND_IN, encoding="utf-8")
    target = folder / ("windows_capture" + importlib.machinery.EXTENSION_SUFFIXES[0])
    built = subprocess.run(
        ["cc", "-shared", "-fPIC", f"-I{sysconfig.get_paths()['include']}", str(source), "-o", str(target)],
        capture_output=True,
        text=True,
        check=False,
    )
    if built.returncode != 0:
        pytest.skip(f"the stand-in extension did not build: {built.stderr[-200:]}")
    module = capture_wgc.load_native()
    assert module.NativeWindowsCapture == "stand-in" and module.__name__ == capture_wgc.NATIVE_MODULE
    assert sys.modules[capture_wgc.NATIVE_MODULE] is module and capture_wgc.load_native() is module
    assert "windows_capture" not in sys.modules  # the wrapper never ran
