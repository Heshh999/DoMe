"""Windows.Graphics.Capture fallback (design §5.2) through the ``windows-capture`` package (MIT), used when Desktop
Duplication reports ``UNSUPPORTED`` (hybrid-GPU laptops) or ``NOT_CURRENTLY_AVAILABLE`` (the duplication limit).

Frames arrive BGRA on a capture thread; this source hands them to the track through a one-slot queue (the newest
frame wins: a slow encoder never builds a backlog) and converts them to yuv420p with libswscale
(``VideoFrame.reformat``), never per pixel in Python. On Windows 10 the yellow capture border cannot be turned off:
an extra indicator, not DoMe's. ``windows-capture`` is NOT a dependency of this build: when it is missing the source
reports ``SCREEN_CAPTURE_UNAVAILABLE{detail: unsupported_display}``. Written against the package's documented API;
**not yet verified on a device**.

Which monitor: windows-capture numbers monitors in raw ``EnumDisplayMonitors`` order, which does not put the primary
first, while DoMe's surface index does (``order_monitors``). Passing DoMe's index would stream one monitor while taps
map onto another. So the primary is asked for as the package's ``monitor_index=None`` (its primary), any other
monitor is refused (Phase 2 maps by device name or rectangle), and the first frame's size must match the monitor
the agent maps taps onto, else ``SCREEN_CAPTURE_UNAVAILABLE{unsupported_display}`` (the session ends
``capture_failed``).
"""

from __future__ import annotations

import contextlib
import queue
import sys
import threading
from typing import Any

from dome_protocol import ProtocolError

from .encode import VIDEO_TIME_BASE


class WgcSource:
    # declared here because the constructor's platform guard hides the assignments from the Linux type-check
    monitor_index: int
    width: int
    height: int
    fps: int
    _frames: queue.Queue[Any]
    _capture: Any
    _thread: threading.Thread | None
    _stopped: threading.Event
    primary: bool
    expected_size: tuple[int, int] | None
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
    ) -> None:
        if sys.platform != "win32":
            raise ProtocolError(
                "SCREEN_CAPTURE_UNAVAILABLE",
                "Windows.Graphics.Capture needs Windows",
                detail={"detail": "unsupported_display"},
            )
        self.monitor_index = monitor_index
        self.width, self.height = int(width) // 2 * 2, int(height) // 2 * 2
        self.fps = max(1, int(fps))
        self._frames = queue.Queue(maxsize=1)
        self._capture = None
        self._thread = None
        self._stopped = threading.Event()
        self.primary = primary
        self.expected_size = expected_size  # the streamed monitor's physical size, as the agent maps taps
        self._size_checked = False

    def open(self) -> None:
        if not self.primary:
            raise ProtocolError(
                "SCREEN_CAPTURE_UNAVAILABLE",
                "Windows.Graphics.Capture streams only the primary monitor in this version",
                detail={"detail": "unsupported_display"},
            )
        try:
            import windows_capture
        except ImportError:
            raise ProtocolError(
                "SCREEN_CAPTURE_UNAVAILABLE",
                "Windows.Graphics.Capture support is not installed",
                detail={"detail": "unsupported_display"},
            ) from None
        try:
            # monitor_index None is the primary monitor
            capture = windows_capture.WindowsCapture(
                cursor_capture=True, draw_border=None, monitor_index=None, window_name=None
            )
        except Exception as exc:  # noqa: BLE001
            raise ProtocolError(
                "SCREEN_CAPTURE_UNAVAILABLE",
                f"Windows.Graphics.Capture could not start ({exc.__class__.__name__})",
                detail={"detail": "driver"},
            ) from None

        def on_frame_arrived(frame: Any, control: Any) -> None:  # the package's callback signature
            if self._stopped.is_set():
                control.stop()
                return
            item = (int(frame.width), int(frame.height), bytes(frame.frame_buffer))
            try:
                self._frames.put_nowait(item)
            except queue.Full:  # the newest frame wins: drop the one the track has not taken yet
                with contextlib.suppress(queue.Empty):
                    self._frames.get_nowait()
                with contextlib.suppress(queue.Full):
                    self._frames.put_nowait(item)

        def on_closed() -> None:
            self._stopped.set()

        # registered as plain calls (the package's `event` decorator is untyped)
        capture.event(on_frame_arrived)
        capture.event(on_closed)
        self._capture = capture
        self._thread = threading.Thread(target=capture.start, name="dome-screen-wgc", daemon=True)
        self._thread.start()

    def read(self) -> Any | None:
        import av

        try:
            width, height, data = self._frames.get(timeout=1.0 / self.fps)
        except queue.Empty:
            if self._stopped.is_set():
                raise ProtocolError(
                    "SCREEN_CAPTURE_UNAVAILABLE", "the capture session closed", detail={"detail": "driver"}
                ) from None
            return None
        if not self._size_checked:
            self._size_checked = True
            if self.expected_size is not None and (width, height) != tuple(self.expected_size):
                raise ProtocolError(
                    "SCREEN_CAPTURE_UNAVAILABLE",
                    "Windows.Graphics.Capture delivered another monitor than the one taps map onto",
                    detail={"detail": "unsupported_display"},
                )
        frame = av.VideoFrame(width, height, "bgra")
        frame.planes[0].update(data)
        out = frame.reformat(width=self.width, height=self.height, format="yuv420p")
        out.time_base = VIDEO_TIME_BASE
        return out

    def close(self) -> None:
        self._stopped.set()
        self._capture = None


__all__ = ["WgcSource"]
