"""Desktop Duplication's failure reasons and the Windows.Graphics.Capture fallback (design §5.2), on Linux.

* ``capture_dda``: FFmpeg's own error lines (collected while the graph opens and around every read; PyAV drops them by
  default) reach the error's message sanitised (no paths, no long strings such as the graph, ≤ 200 characters), and the
  detail is classified from PyAV's exception class and errno and from those lines (or ``exc.log``), never from PyAV's
  exception string (it quotes the graph, ``output_idx`` included). A real ``lavfi`` graph that fails to configure
  stands in for ``ddagrab``; a graph whose open is held back stands in for a still desktop (``probe_output_format``
  waits for a real frame); ``color`` stands in for ``dup_frames=1`` re-sending the same picture; the DXGI output lookup
  runs against fake adapter rows.
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
import json
import shutil
import subprocess
import sys
import sysconfig
import time
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


GRAPH = capture_dda.ddagrab_graph(0, 1280, 720, 30)  # what PyAV quotes as the "filename" of every lavfi error
EXT, UNKN = capture_dda.AVERROR_EXTERNAL, capture_dda.AVERROR_UNKNOWN
DDA = "Parsed_ddagrab_0"
EXTERNAL = "Generic error in an external library"


def line(message: str, name: str = DDA) -> tuple[int, str, str]:
    return (16, name, message + "\n")  # as av.logging.Capture records an AV_LOG_ERROR line


@pytest.mark.parametrize(
    ("exc", "logged", "remote", "detail"),
    [  # realistic PyAV 17 exceptions (class, errno, PyAV's strerror, the graph as filename) and vsrc_ddagrab.c's lines
        # DXGI_ERROR_NOT_CURRENTLY_AVAILABLE -> AVERROR(EBUSY): the MinGW build has no strerror_r, so the text says
        # "Error number -16 occurred", never "busy"; errno 16 decides, with or without the captured line
        (av.error.OSError(16, "Error number -16 occurred", GRAPH), [], False, "busy"),
        (
            av.error.OSError(16, "Error number -16 occurred", GRAPH),
            [line("Too many open duplication sessions")],
            False,
            "busy",
        ),
        # DXGI_ERROR_UNSUPPORTED and an output the default adapter lacks: both AVERROR_EXTERNAL, only the line tells
        (
            av.error.ExternalError(EXT, EXTERNAL, GRAPH),
            [line("Selected output not supported"), line(f"Failed to configure output pad on {DDA}")],
            False,
            "unsupported_display",
        ),
        (
            av.error.ExternalError(EXT, EXTERNAL, GRAPH),
            [line("Failed to enumerate DXGI output 0")],
            False,
            "unsupported_display",
        ),
        # the same exception with NO lines: nothing says "unsupported" (the graph's "output_idx" never counts)
        (av.error.ExternalError(EXT, EXTERNAL, GRAPH), [], False, "driver"),
        # exc.log (PyAV's own last-error tuple) stands in when the capture holds nothing
        (
            av.error.ExternalError(EXT, EXTERNAL, GRAPH, (16, DDA, "Selected output not supported\n")),
            [],
            False,
            "unsupported_display",
        ),
        (av.error.ExternalError(EXT, EXTERNAL, GRAPH), [line("Failed duplicating output")], False, "driver"),
        # E_ACCESSDENIED -> AVERROR(EPERM) -> PermissionError: remote session only when Windows says so
        (
            av.error.PermissionError(1, "Operation not permitted", GRAPH),
            [line("Desktop duplication access denied")],
            True,
            "remote_session",
        ),
        (
            av.error.PermissionError(1, "Operation not permitted", GRAPH),
            [line("Desktop duplication access denied")],
            False,
            "driver",
        ),
        (av.error.PermissionError(13, "Permission denied", GRAPH), [], True, "remote_session"),
        # no D3D11 device (hwcontext_d3d11va.c -> AVERROR_UNKNOWN), scale_d3d11's video processor: the driver
        (
            av.error.UnknownError(UNKN, "Unknown error occurred", GRAPH),
            [line("Failed to create D3D11VA device.")],
            False,
            "driver",
        ),
        (
            av.error.ExternalError(EXT, EXTERNAL, GRAPH),
            [line("Failed to create video processor: HRESULT 0x80070057", "Parsed_scale_d3d11_1")],
            False,
            "driver",
        ),
        (av.error.ArgumentError(22, "Invalid argument", GRAPH), [], False, "driver"),
    ],
)
def test_classify_capture_failure_reads_the_class_errno_and_ffmpegs_lines_never_the_text(
    exc: Exception, logged: list[tuple[int, str, str]], remote: bool, detail: str
) -> None:
    assert "output_idx" in str(exc), "PyAV quotes the graph: the text is what the old classifier matched"
    assert capture_dda.classify_capture_failure(exc, logged, remote_session=lambda: remote) == detail
    error = capture_dda.capture_error("Desktop Duplication could not start", exc, logged, remote_session=lambda: remote)
    assert error.code == "SCREEN_CAPTURE_UNAVAILABLE" and error.detail == {"detail": detail}
    assert error.message.startswith("Desktop Duplication could not start: ")
    assert "output_idx" not in error.message and "ddagrab=" not in error.message and len(error.message) <= 240


def test_ffmpeg_error_text_puts_the_logged_cause_first_and_never_the_graph() -> None:
    exc = av.error.ExternalError(EXT, EXTERNAL, GRAPH)
    assert "output_idx" in str(exc)  # what the old classifier read
    logged = [
        line("Selected output not supported"),
        line(f"Failed to configure output pad on {DDA}"),
        line("Selected output not supported"),  # a repeat is kept once
    ]
    text = capture_dda.ffmpeg_error_text(exc, logged)
    assert text == (
        "Selected output not supported; Failed to configure output pad on Parsed_ddagrab_0; "
        "Generic error in an external library (errno 542398533)"
    )
    # exc.log is used only when nothing was captured; the captured lines win otherwise
    with_log = av.error.ExternalError(EXT, EXTERNAL, GRAPH, (16, DDA, "Failed duplicating output\n"))
    assert capture_dda.ffmpeg_error_text(with_log) == f"Failed duplicating output; {EXTERNAL} (errno {EXT})"
    assert capture_dda.ffmpeg_error_text(with_log, logged).startswith("Selected output not supported; Failed to conf")
    assert capture_dda.ffmpeg_error_text(av.error.ExternalError(EXT, EXTERNAL, GRAPH)) == f"{EXTERNAL} (errno {EXT})"
    assert capture_dda.ffmpeg_error_text(RuntimeError(r"boom at C:\Users\Jane\x")) == "RuntimeError: boom at [path]"
    assert capture_dda.ffmpeg_error_text(RuntimeError()) == "RuntimeError"


@pytest.mark.parametrize(
    ("message", "detail"),
    [
        ("The Graphics Capture API is not supported on this platform.", "unsupported_display"),
        ("Failed to get monitor from index: The monitor was not found.", "driver"),
    ],
)
def test_classify_wgc_error_reads_windows_captures_words(message: str, detail: str) -> None:
    assert capture_wgc.classify_wgc_error(message) == detail


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


def test_read_errors_keep_ffmpegs_lines_too(monkeypatch: pytest.MonkeyPatch) -> None:
    """An access-lost error at read time ("AcquireNextFrame failed: 887a0026") is logged by ddagrab, not said by PyAV:
    the lines are captured around every read as well, and the fourth error in a row carries them."""
    source = dda(monkeypatch, "testsrc=size=64x36:rate=30,format=nv12")
    source.open()
    frames = source._frames  # noqa: SLF001

    class Failing:
        def __next__(self) -> Any:
            av.logging.log(av.logging.ERROR, DDA, "AcquireNextFrame failed: 887a0026")
            raise av.error.ExternalError(EXT, EXTERNAL, GRAPH)

        def __iter__(self) -> Any:
            return self

        def close(self) -> None:
            frames.close()

    source._frames = Failing()  # noqa: SLF001
    monkeypatch.setattr(source, "open", lambda: None)  # a reopen that "succeeds" without a graph: read fails again
    monkeypatch.setattr(capture_dda, "REOPEN_BACKOFF_SECONDS", (0.0,))
    with pytest.raises(ProtocolError) as caught:
        for _ in range(20):
            if source.read() is None and source._frames is None:  # noqa: SLF001
                source._frames = Failing()  # noqa: SLF001 - the "rebuilt" graph fails the same way
    assert caught.value.detail == {"detail": "driver"}
    assert caught.value.message.startswith(
        "Desktop Duplication failed 3 times in a row: AcquireNextFrame failed: 887a0026; "
    )
    assert "output_idx" not in caught.value.message and av.logging.get_level() is None
    source.close()


def test_a_still_desktop_leaves_the_open_pending_and_the_opener_closes_an_abandoned_graph(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ddagrab's probe_output_format makes av.open block until the desktop changes. The open runs on its own thread:
    after `open_wait` the source reports it pending (reads answer None, bounded), adopts the graph when it arrives,
    and a source closed while still pending lets the opener close the container it created, never another thread."""
    import threading

    release = threading.Event()
    real_open = av.open

    def slow_open(graph: str, **kw: Any) -> Any:
        release.wait(10)
        return real_open(graph, **kw)

    monkeypatch.setattr(av, "open", slow_open)
    source = dda(monkeypatch, "testsrc=size=64x36:rate=30,format=nv12")
    source.open_wait = 0.2
    with structlog.testing.capture_logs() as logs:
        t0 = time.monotonic()
        source.open()
    assert 0.15 < time.monotonic() - t0 < 2.0
    assert [e["event"] for e in logs] == ["Desktop Duplication is waiting for the first desktop change"]
    t0 = time.monotonic()
    assert source.read() is None and source.read() is None  # pending: no frame, each wait bounded
    assert time.monotonic() - t0 < 1.0
    release.set()
    frame = next(f for f in (source.read() for _ in range(100)) if f is not None)
    assert (frame.width, frame.height, frame.format.name) == (64, 36, "nv12")
    source.close()
    # abandoned while pending: the helper thread finishes the open and closes what it opened
    release.clear()
    other = dda(monkeypatch, "testsrc=size=64x36:rate=30,format=nv12")
    other.open_wait = 0.1
    other.open()
    pending = other._pending  # noqa: SLF001
    assert pending is not None and not pending.done.is_set()
    other.close()
    release.set()
    pending.thread.join(10)
    assert pending.done.is_set() and pending.error is None and pending.collect() is None  # closed, never handed over
    assert other._frames is None and other._pending is None  # noqa: SLF001


def test_repeated_frames_are_dropped_by_their_content_signature(monkeypatch: pytest.MonkeyPatch) -> None:
    """dup_frames=1: ddagrab re-sends its last frame at the frame rate while the desktop is still. The same picture
    downloaded again has a new buffer and pts, so the signature of its visible bytes decides: None for a repeat (the
    track's floor takes over), a frame for a change."""
    source = dda(monkeypatch, "color=c=black:size=64x36:rate=60,format=nv12")  # the same frame, forever
    source.open()
    first = next(f for f in (source.read() for _ in range(10)) if f is not None)
    assert first.format.name == "nv12"
    assert all(source.read() is None for _ in range(10)) and source.frames_repeated == 10
    source.close()
    moving = dda(monkeypatch, "testsrc=size=64x36:rate=60,format=nv12")
    moving.open()
    frames = [moving.read() for _ in range(6)]
    assert sum(f is not None for f in frames) >= 5 and moving.frames_repeated == 0  # testsrc changes every frame
    moving.close()


def test_frame_signature_ignores_row_padding_and_sees_any_visible_change() -> None:
    a = av.VideoFrame(66, 4, "nv12")  # width 66: PyAV pads the rows
    for plane in a.planes:
        plane.update(bytes([0x40]) * plane.buffer_size)
    b = av.VideoFrame(66, 4, "nv12")
    for plane in b.planes:
        plane.update(bytes([0x40]) * plane.buffer_size)
    assert a.planes[0].line_size > 66
    assert capture_dda.frame_signature(a) == capture_dda.frame_signature(b)
    padding = bytearray(a.planes[0])
    padding[66] = 0xFF  # the first padding byte of row 0: stale pool memory on a real frame
    a.planes[0].update(bytes(padding))
    assert capture_dda.frame_signature(a) == capture_dda.frame_signature(b)
    visible = bytearray(b.planes[1])
    visible[0] ^= 0x01  # one chroma byte: a change the phone must see
    b.planes[1].update(bytes(visible))
    assert capture_dda.frame_signature(a) != capture_dda.frame_signature(b)
    rgb = av.VideoFrame(3, 2, "rgb24")  # a format the table does not know is hashed whole
    assert capture_dda.frame_signature(rgb)[:3] == (3, 2, "rgb24")


# ----- which output ddagrab is asked for ----------------------------------------------------------------------------------
def adapters() -> list[Any]:
    from dome_agent.platform.windows.dxgi import DxgiAdapter, DxgiOutput

    return [
        DxgiAdapter(
            0,
            "Intel(R) UHD Graphics",
            0x8086,
            0x9A49,
            False,
            (DxgiOutput(0, r"\\.\DISPLAY1", 0, 0, 2560, 1440, True, 0),),
        ),
        DxgiAdapter(
            1,
            "NVIDIA GeForce RTX 3060 Laptop GPU",
            0x10DE,
            0x2520,
            False,
            (
                DxgiOutput(0, r"\\.\DISPLAY2", 2560, 0, 4480, 1080, True, 0),
                DxgiOutput(1, r"\\.\DISPLAY3", -1920, 0, 0, 1080, True, 90),
            ),
        ),
        DxgiAdapter(2, "Microsoft Basic Render Driver", 0x1414, 0x8C, True),
    ]


def test_locate_ddagrab_output_maps_the_monitor_to_the_default_adapters_output_or_skips() -> None:
    located = capture_dda.locate_ddagrab_output
    on_default = located(left=0, top=0, width=2560, height=1440, fallback_idx=3, enumerate=adapters)
    assert (on_default.output_idx, on_default.skip) == (0, False)
    elsewhere = located(left=-1920, top=0, width=1920, height=1080, fallback_idx=2, enumerate=adapters)
    assert elsewhere.skip and elsewhere.output_idx is None
    assert (
        elsewhere.reason
        == "the monitor is output 1 of adapter 1 (NVIDIA GeForce RTX 3060 Laptop GPU), not of the default adapter"
    )
    # DXGI does not list the monitor (unplugged, resized) or cannot be asked: today's behaviour, the surface index
    with structlog.testing.capture_logs() as logs:
        missing = located(left=0, top=0, width=800, height=600, fallback_idx=1, enumerate=adapters)

        def broken() -> list[Any]:
            raise OSError("CreateDXGIFactory1 failed (0x887a0004)")

        failed = located(left=0, top=0, width=2560, height=1440, fallback_idx=1, enumerate=broken)
    assert (missing.output_idx, missing.skip) == (1, False) and (failed.output_idx, failed.skip) == (1, False)
    assert [e["log_level"] for e in logs] == ["warning", "warning"] and logs[1]["error"] == "OSError"
    assert "0x887a0004" not in json.dumps(logs)


def test_dda_with_fallback_aims_or_skips_desktop_duplication_before_opening_it() -> None:
    from dome_agent.screen.worker.capture_dda import DdaWithFallback, OutputChoice, preflight_skip

    class Dda:
        def __init__(self) -> None:
            self.width, self.height, self.output_idx, self.opened = 1280, 720, 0, 0

        def open(self) -> None:
            self.opened += 1

        def read(self) -> None:
            return None

        def close(self) -> None:
            return None

    class Wgc(Dda):
        pass

    fell: list[ProtocolError] = []
    primary, built = Dda(), []

    def wgc() -> Wgc:
        built.append(1)
        return Wgc()

    # the monitor is output 1 of the default adapter: ddagrab gets output_idx=1 and runs
    with structlog.testing.capture_logs() as logs:
        source = DdaWithFallback(primary, wgc, on_fallback=fell.append, locate=lambda: OutputChoice(1))
        source.open()
    assert primary.output_idx == 1 and primary.opened == 1 and built == [] and fell == []
    assert logs[0]["event"] == "Desktop Duplication output index differs from the surface index"
    assert (logs[0]["surface_index"], logs[0]["output_idx"]) == (0, 1)
    # the monitor is on another adapter: ddagrab is never opened, WGC runs, the reason is reported
    primary = Dda()
    skip = OutputChoice(
        None, skip=True, reason="the monitor is output 0 of adapter 1 (GPU B), not of the default adapter"
    )
    source = DdaWithFallback(primary, wgc, on_fallback=fell.append, locate=lambda: skip)
    source.open()
    assert primary.opened == 0 and built == [1]
    assert fell[0].detail == {"detail": "unsupported_display"}
    assert (
        fell[0].message
        == "Desktop Duplication skipped: the monitor is output 0 of adapter 1 (GPU B), not of the default adapter"
    )
    # a lookup that raises (it never should) changes nothing
    primary = Dda()

    def raising() -> OutputChoice:
        raise RuntimeError("ctypes")

    DdaWithFallback(primary, wgc, on_fallback=fell.append, locate=raising).open()
    assert primary.opened == 1 and primary.output_idx == 0 and built == [1]
    # the agent's pre-flight found Desktop Duplication failing (the source spec's `preflight`): skipped before the
    # lookup, WGC at once, the HRESULT's name in the reason the fallback warning carries
    primary, fell, built = Dda(), [], []
    verdict = {"detail": "busy", "hresult": "0x887a0022", "hresult_name": "DXGI_ERROR_NOT_CURRENTLY_AVAILABLE"}
    asked: list[int] = []

    def locating() -> OutputChoice:
        asked.append(1)
        return OutputChoice(1)

    DdaWithFallback(primary, wgc, on_fallback=fell.append, locate=locating, skip=preflight_skip(verdict)).open()
    assert primary.opened == 0 and built == [1] and asked == []
    assert fell[0].detail == {"detail": "busy"}
    assert fell[0].message == "Desktop Duplication skipped: the pre-flight answered DXGI_ERROR_NOT_CURRENTLY_AVAILABLE"


@pytest.mark.parametrize(
    ("verdict", "detail", "reason"),
    [
        (
            {"detail": "unsupported_display", "hresult": "0x887a0004", "hresult_name": "DXGI_ERROR_UNSUPPORTED"},
            "unsupported_display",
            "DXGI_ERROR_UNSUPPORTED",
        ),
        ({"detail": "remote_session", "hresult": "0x80070005", "hresult_name": None}, "remote_session", "0x80070005"),
        ({"detail": "driver", "hresult": None, "hresult_name": None}, "driver", "a failure"),
        ({"detail": "indicator"}, "driver", "a failure"),  # not a capture detail: never forwarded as one
    ],
)
def test_preflight_skip_reads_the_agents_verdict(verdict: dict[str, Any], detail: str, reason: str) -> None:
    from dome_agent.screen.worker.capture_dda import preflight_skip

    skip = preflight_skip(verdict)
    assert skip is not None and skip.code == "SCREEN_CAPTURE_UNAVAILABLE" and skip.detail == {"detail": detail}
    assert skip.message == f"Desktop Duplication skipped: the pre-flight answered {reason}"


def test_preflight_skip_is_none_without_a_verdict() -> None:
    from dome_agent.screen.worker.capture_dda import preflight_skip

    assert preflight_skip(None) is None and preflight_skip({}) is None and preflight_skip({"detail": 3}) is None
    assert preflight_skip("busy") is None


def test_the_worker_asks_dxgi_for_the_output_at_open_time_only(monkeypatch: pytest.MonkeyPatch) -> None:
    asked: list[dict[str, Any]] = []

    def locate(**kw: Any) -> Any:
        asked.append(kw)
        return capture_dda.OutputChoice(kw["fallback_idx"])

    monkeypatch.setattr(capture_dda, "locate_ddagrab_output", locate)

    class Dda:
        def __init__(self, *_args: Any) -> None:
            self.width, self.height, self.output_idx = 1280, 720, 1

        def open(self) -> None:
            return None

    monkeypatch.setattr(capture_dda, "DdagrabSource", Dda)
    spec = {"surface_index": 1, "primary": False, "left": 2560, "top": 0, "width": 1920, "height": 1080}
    with monkeypatch.context() as m:
        m.setattr(sys, "platform", "win32")
        source = worker_main._build_source("ddagrab", spec, 1280, 720, 30)  # noqa: SLF001
    assert asked == []  # constructing asks nothing (and captures nothing)
    source.open()
    assert asked == [{"left": 2560, "top": 0, "width": 1920, "height": 1080, "fallback_idx": 1}]


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
    # only the monitor is set: the package refuses an explicit cursor, border or update-interval setting on a Windows 10
    # that lacks the property (IsCursorCaptureEnabled is 1903+), and Windows draws the cursor by default
    assert capture.settings == {
        "cursor_capture": None,
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
    from dome_agent.platform.windows import dxgi

    if sys.platform != "win32":
        checks = worker_main.self_test(dev_h264=True)["checks"]
        assert "wgc_native" not in checks and "dxgi_outputs" not in checks
    monkeypatch.setattr(dxgi, "enumerate_adapters", adapters)  # the ctypes enumeration needs Windows
    with monkeypatch.context() as m:
        m.setattr(sys, "platform", "win32")
        report = worker_main.self_test(dev_h264=True)
    assert report["checks"]["wgc_native"] == {
        "ok": True,
        "value": {"classes": ["NativeCaptureControl", "NativeMappedFrame", "NativeWindowsCapture"]},
    }
    assert report["checks"]["dxgi_outputs"] == {  # numbers only: no description, no device name reaches the self-test
        "ok": True,
        "value": {"adapters": 3, "outputs_per_adapter": [1, 2, 0], "software_adapters": 1},
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
