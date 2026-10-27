"""Windows.Graphics.Capture fallback (design §5.2) through ``windows-capture`` 2.0.1 (MIT; pinned on Windows in the
``screen`` extra), used when Desktop Duplication cannot open: a hybrid-GPU laptop answers ``UNSUPPORTED``, a monitor on
another adapter is not among the default adapter's outputs (``capture_dda``), or the duplication limit is reached.
Windows.Graphics.Capture composes the desktop whatever adapter drives the monitor.

* **Only the native module.** The package's ``windows_capture/__init__.py`` imports ``cv2`` (OpenCV, whose wheel
  carries its own FFmpeg) and ``numpy`` for helpers DoMe does not use, so ``[tool.uv] exclude-dependencies`` keeps both
  out, :func:`load_native` loads the Rust extension ``windows_capture.windows_capture`` by file without that
  ``__init__``, and the PyInstaller spec bundles the ``.pyd`` alone. The API used, read from the 2.0.1 source
  (``windows-capture-python/src/lib.rs``), not from the README:

  - ``NativeWindowsCapture(on_frame_arrived, on_closed, cursor_capture, draw_border, secondary_window,
    minimum_update_interval, dirty_region, monitor_index, window_name, window_hwnd)``; ``None`` keeps Windows' default
    for a setting. DoMe sets only ``monitor_index``: every other setting stays at Windows' default (the cursor IS drawn
    by default), because the package refuses any explicit cursor, border, secondary-window or update-interval setting
    on a Windows 10 whose ``GraphicsCaptureSession`` lacks the property (``graphics_capture_api.rs``:
    ``CursorConfigUnsupported`` etc.; ``IsCursorCaptureEnabled`` exists from 1903, the API itself from 1803).
  - ``start_free_threaded()`` sets the session up and starts capturing on the package's own thread, raising when it
    cannot (no Windows.Graphics.Capture, no such monitor). It returns a control whose ``stop()`` posts ``WM_QUIT`` to
    that thread and joins it (the GIL released meanwhile) and whose ``is_finished()`` tells whether it ended.
  - ``on_frame_arrived(frame, buffer_len, width, height, stop_list, timespan)`` runs on that thread, holding the GIL;
    an exception in it ends the capture. ``frame`` is a ``NativeMappedFrame``: a D3D11 staging copy of the frame,
    mapped until the object is released, always ``bgra8`` (the package asks for ``ColorFormat::Bgra8``), its rows
    ``bytes_per_row`` apart (D3D11's row pitch, often more than width × 4), read through ``buffer_view()``.
    ``stop_list[0] = True`` ends the capture after the call.
  - ``on_closed()`` when the captured monitor goes away.
* **Which monitor.** ``monitor_index`` is ONE-based in raw ``EnumDisplayMonitors`` order, and ``None`` means index 1:
  the first monitor enumerated, not necessarily the primary. DoMe's surface index puts the primary first
  (``order_monitors``), so the source looks the streamed monitor up in the raw order (the primary by its flag, another
  monitor by its rectangle: :func:`capture_position`). The first frame must also have the size of the monitor the
  agent maps taps onto, else ``SCREEN_CAPTURE_UNAVAILABLE{unsupported_display}`` (the session ends
  ``capture_failed``) rather than a picture whose taps land elsewhere.
* **Frames.** The callback only hands the mapped frame over, through a one-slot queue where the newest frame wins (a
  slow encoder never builds a backlog; a superseded frame is released unread). :meth:`WgcSource.read` copies it into
  a BGRA ``av.VideoFrame`` honouring both row pitches (:func:`bgra_frame`) and converts it to yuv420p at the track's
  size with libswscale (``reformat``), never per pixel in Python: the size ``ddagrab`` delivers, in a format both
  encoders take.
* Windows draws its yellow capture border around the monitor meanwhile: Windows' own indicator, not DoMe's.

**Not yet verified on a device.**
"""

from __future__ import annotations

import contextlib
import importlib.machinery
import importlib.util
import os
import queue
import sys
import threading
from typing import Any

import structlog
from dome_protocol import ProtocolError

from .capture_dda import sanitise_reason
from .encode import VIDEO_TIME_BASE

log = structlog.get_logger(__name__)

NATIVE_MODULE = "windows_capture.windows_capture"
PIXEL_FORMAT = "bgra8"  # ColorFormat::Bgra8, the only format windows-capture 2.0.1 asks Windows.Graphics.Capture for


def classify_wgc_error(message: str) -> str:
    """The contract's detail for a start failure windows-capture reports: the package raises a bare ``Exception``
    whose text is Windows' or its own reason (``graphics_capture_api.rs``: "The Graphics Capture API is not supported
    on this platform" when ``GraphicsCaptureSession::IsSupported`` is false; a monitor that is not found; a D3D error),
    so the words are all there is. Windows.Graphics.Capture has no duplication limit and no remote-session refusal."""
    text = message.lower()
    if "not supported" in text or "unsupported" in text:
        return "unsupported_display"
    return "driver"


def load_native() -> Any:
    """``windows-capture``'s Rust extension, loaded by file WITHOUT the package's ``__init__`` (which imports cv2 and
    numpy, never installed here). It is found next to where the package would be: in the environment's site-packages,
    or in a frozen build under ``_internal/windows_capture`` (the spec bundles the ``.pyd`` alone). ImportError when
    it is not there or cannot load."""
    loaded = sys.modules.get(NATIVE_MODULE)
    if loaded is not None:
        return loaded
    folders: list[str] = []
    spec = importlib.util.find_spec("windows_capture")  # locates the package; never runs its __init__
    if spec is not None:
        folders += list(spec.submodule_search_locations or [])
    bundle = getattr(sys, "_MEIPASS", None)
    if isinstance(bundle, str):
        folders.append(os.path.join(bundle, "windows_capture"))
    for folder in folders:
        for suffix in importlib.machinery.EXTENSION_SUFFIXES:
            path = os.path.join(folder, "windows_capture" + suffix)
            if not os.path.isfile(path):
                continue
            loader = importlib.machinery.ExtensionFileLoader(NATIVE_MODULE, path)
            native_spec = importlib.util.spec_from_file_location(NATIVE_MODULE, path, loader=loader)
            if native_spec is None:
                continue
            module = importlib.util.module_from_spec(native_spec)
            loader.exec_module(module)
            sys.modules[NATIVE_MODULE] = module
            return module
    raise ImportError("windows-capture's native module is not installed")


def capture_position(*, left: int, top: int, width: int, height: int, primary: bool) -> int | None:
    """windows-capture's one-based ``monitor_index`` of the streamed monitor, or None when Windows does not list it."""
    from ...platform.windows.screen import WindowsScreen

    return WindowsScreen().capture_position(left=left, top=top, width=width, height=height, primary=primary)


def bgra_frame(data: Any, width: int, height: int, stride: int) -> Any:
    """A BGRA ``av.VideoFrame`` from ``data``: ``height`` rows of ``width`` × 4 bytes, ``stride`` bytes apart (D3D11's
    row pitch). One copy: the whole buffer at once when PyAV's row pitch is the same, else row by row through
    memoryview slices (a memcpy each, nothing per pixel)."""
    import av

    row = width * 4
    src = memoryview(data).cast("B")
    if width <= 0 or height <= 0 or stride < row or len(src) < stride * (height - 1) + row:
        raise ValueError(f"a {width}x{height} BGRA frame does not fit {len(src)} bytes {stride} bytes per row")
    frame = av.VideoFrame(width, height, "bgra")
    plane: Any = frame.planes[0]
    line = int(plane.line_size)
    if stride == line and len(src) >= line * height:
        plane.update(src[: line * height])
    else:
        dst = memoryview(plane)
        for y in range(height):
            dst[y * line : y * line + row] = src[y * stride : y * stride + row]
    return frame


class WgcSource:
    # declared here because the constructor's platform guard hides the assignments from the Linux type-check
    monitor_index: int
    width: int
    height: int
    fps: int
    primary: bool
    origin: tuple[int, int]
    expected_size: tuple[int, int] | None
    capture_index: int | None
    _frames: queue.Queue[tuple[Any, int, int]]
    _control: Any
    _stopped: threading.Event
    _size_checked: bool

    def __init__(
        self,
        monitor_index: int,
        width: int,
        height: int,
        fps: int,
        *,
        primary: bool = True,
        expected_size: tuple[int, int] | None = None,
        origin: tuple[int, int] = (0, 0),
    ) -> None:
        if sys.platform != "win32":
            raise ProtocolError(
                "SCREEN_CAPTURE_UNAVAILABLE",
                "Windows.Graphics.Capture needs Windows",
                detail={"detail": "unsupported_display"},
            )
        self.monitor_index = monitor_index  # DoMe's surface index (primary first), for the log only
        self.width, self.height = int(width) // 2 * 2, int(height) // 2 * 2
        self.fps = max(1, int(fps))
        self.primary = primary
        self.origin = (int(origin[0]), int(origin[1]))  # the monitor's top-left corner, physical pixels
        self.expected_size = expected_size  # the streamed monitor's physical size, as the agent maps taps
        self.capture_index = None  # windows-capture's one-based monitor_index, once open
        self._frames = queue.Queue(maxsize=1)
        self._control = None
        self._stopped = threading.Event()
        self._size_checked = False

    def open(self) -> None:
        try:
            native = load_native()
        except ImportError:
            raise ProtocolError(
                "SCREEN_CAPTURE_UNAVAILABLE",
                "Windows.Graphics.Capture support is not installed",
                detail={"detail": "unsupported_display"},
            ) from None
        if self.expected_size is None and not self.primary:
            raise ProtocolError(
                "SCREEN_CAPTURE_UNAVAILABLE",
                "Windows.Graphics.Capture needs the monitor's size to find it",
                detail={"detail": "unsupported_display"},
            )
        width, height = self.expected_size or (0, 0)
        try:
            position = capture_position(
                left=self.origin[0], top=self.origin[1], width=int(width), height=int(height), primary=self.primary
            )
        except ProtocolError:
            raise
        except Exception as exc:  # noqa: BLE001 - the monitor enumeration (ctypes) failed
            raise ProtocolError(
                "SCREEN_CAPTURE_UNAVAILABLE",
                f"Windows.Graphics.Capture could not list the monitors ({exc.__class__.__name__})",
                detail={"detail": "driver"},
            ) from None
        if position is None:
            raise ProtocolError(
                "SCREEN_CAPTURE_UNAVAILABLE",
                "Windows.Graphics.Capture cannot find the monitor to stream among Windows' monitors",
                detail={"detail": "unsupported_display"},
            )
        try:
            capture = native.NativeWindowsCapture(self._on_frame_arrived, self._on_closed, monitor_index=position)
            self._control = capture.start_free_threaded()
        except Exception as exc:  # noqa: BLE001 - the package raises a bare Exception with Windows' reason
            reason = sanitise_reason(f"{exc.__class__.__name__}: {exc}")
            raise ProtocolError(
                "SCREEN_CAPTURE_UNAVAILABLE",
                f"Windows.Graphics.Capture could not start: {reason}",
                detail={"detail": classify_wgc_error(reason)},
            ) from None
        self.capture_index = position
        log.info(
            "screen capture source running",
            source="wgc",
            surface_index=self.monitor_index,
            capture_index=position,
            size=f"{self.width}x{self.height}",
        )

    def _on_frame_arrived(
        self, frame: Any, _buffer_len: int, width: int, height: int, stop_list: Any, _timespan: int
    ) -> None:
        """The package's capture thread, holding the GIL: hand the mapped frame over and return at once (it stays
        mapped until :meth:`read` has copied it or a newer frame replaces it). Never raises: an exception here would
        end the capture."""
        if self._stopped.is_set():
            stop_list[0] = True  # the package ends its capture loop after this call
            return
        item = (frame, int(width), int(height))
        try:
            self._frames.put_nowait(item)
        except queue.Full:  # the newest frame wins: release the one the track has not taken yet
            with contextlib.suppress(queue.Empty):
                self._frames.get_nowait()
            with contextlib.suppress(queue.Full):
                self._frames.put_nowait(item)

    def _on_closed(self) -> None:
        self._stopped.set()

    def read(self) -> Any | None:
        try:
            frame, width, height = self._frames.get(timeout=1.0 / self.fps)
        except queue.Empty:
            control = self._control
            if self._stopped.is_set() or (control is not None and control.is_finished()):
                raise ProtocolError(
                    "SCREEN_CAPTURE_UNAVAILABLE",
                    "the Windows.Graphics.Capture session closed",
                    detail={"detail": "driver"},
                ) from None
            return None
        try:
            if not self._size_checked:
                self._size_checked = True
                log.info(
                    "screen capture first frame",
                    source="wgc",
                    size=f"{width}x{height}",
                    expected=f"{self.expected_size[0]}x{self.expected_size[1]}" if self.expected_size else None,
                )
                if self.expected_size is not None and (width, height) != tuple(self.expected_size):
                    raise ProtocolError(
                        "SCREEN_CAPTURE_UNAVAILABLE",
                        "Windows.Graphics.Capture delivered another monitor than the one taps map onto",
                        detail={"detail": "unsupported_display"},
                    )
            pixel_format = str(getattr(frame, "color_format", PIXEL_FORMAT))
            if pixel_format != PIXEL_FORMAT:
                raise ProtocolError(
                    "SCREEN_CAPTURE_UNAVAILABLE",
                    f"Windows.Graphics.Capture delivered {sanitise_reason(pixel_format, 16)} frames, not {PIXEL_FORMAT}",
                    detail={"detail": "driver"},
                )
            try:
                image = bgra_frame(frame.buffer_view(), width, height, int(frame.bytes_per_row))
            except ValueError as exc:
                raise ProtocolError(
                    "SCREEN_CAPTURE_UNAVAILABLE",
                    f"Windows.Graphics.Capture delivered an unreadable frame: {sanitise_reason(str(exc))}",
                    detail={"detail": "driver"},
                ) from None
        finally:
            del frame  # the last reference: the staging copy is unmapped now
        out = image.reformat(width=self.width, height=self.height, format="yuv420p")
        out.time_base = VIDEO_TIME_BASE
        return out

    def close(self) -> None:
        self._stopped.set()
        control, self._control = self._control, None
        if control is not None:
            try:
                control.stop()  # posts WM_QUIT to the package's capture thread and joins it
            except Exception as exc:  # noqa: BLE001 - it already ended, or ended on an error it kept
                log.debug("Windows.Graphics.Capture stopped with an error", error=exc.__class__.__name__)
        while True:  # release a mapped frame the track never took
            try:
                self._frames.get_nowait()
            except queue.Empty:
                break


__all__ = ["NATIVE_MODULE", "WgcSource", "bgra_frame", "capture_position", "classify_wgc_error", "load_native"]
