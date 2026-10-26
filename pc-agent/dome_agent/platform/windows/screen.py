"""Monitor enumeration for Screen view (design §8.2, §15 ``platform/windows/screen.py``).

``EnumDisplayMonitors`` + ``GetMonitorInfoW`` read with the calling thread per-monitor DPI aware (v2), so the
rectangles are PHYSICAL pixels in virtual-desktop coordinates: the units Desktop Duplication captures in and
``SetCursorPos`` moves in (``input.py``). The primary monitor is index 0; the others follow by position
(left, then top), so the ``surface_index`` the phone names stays stable while the topology does. The display
orientation comes from ``EnumDisplaySettingsW`` (``dmDisplayOrientation``), 0 when it cannot be read.

The Windows.Graphics.Capture fallback (``windows-capture``) numbers monitors differently: one-based, in the raw
``EnumDisplayMonitors`` order. :meth:`WindowsScreen.capture_position` finds the streamed monitor in that order
(:func:`capture_order_position`, pure and unit-tested on Linux).

Every ctypes call declares ``argtypes``/``restype`` (handles are 64-bit). Exercised on a Windows device only:
**not yet verified on a device** (rotated, HDR and mixed-DPI monitors in particular).
"""

from __future__ import annotations

import contextlib
import ctypes
from collections.abc import Iterator
from ctypes import wintypes
from typing import Any

from dome_protocol import ProtocolError

from ..protocol import Monitor

MONITORINFOF_PRIMARY = 0x00000001
ENUM_CURRENT_SETTINGS = 0xFFFFFFFF
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
CCHDEVICENAME = 32
CCHFORMNAME = 32
LONG = ctypes.c_int32
DWORD = ctypes.c_uint32
WORD = ctypes.c_uint16
SHORT = ctypes.c_int16


class RECT(ctypes.Structure):  # noqa: N801 - Win32 name
    _fields_ = [("left", LONG), ("top", LONG), ("right", LONG), ("bottom", LONG)]


class MONITORINFOEXW(ctypes.Structure):  # noqa: N801 - Win32 name
    _fields_ = [
        ("cbSize", DWORD),
        ("rcMonitor", RECT),
        ("rcWork", RECT),
        ("dwFlags", DWORD),
        ("szDevice", ctypes.c_wchar * CCHDEVICENAME),
    ]


class POINTL(ctypes.Structure):  # noqa: N801 - Win32 name
    _fields_ = [("x", LONG), ("y", LONG)]


class _DisplayUnion(ctypes.Structure):
    _fields_ = [("dmPosition", POINTL), ("dmDisplayOrientation", DWORD), ("dmDisplayFixedOutput", DWORD)]


class _PrinterUnion(ctypes.Structure):
    _fields_ = [
        ("dmOrientation", SHORT),
        ("dmPaperSize", SHORT),
        ("dmPaperLength", SHORT),
        ("dmPaperWidth", SHORT),
        ("dmScale", SHORT),
        ("dmCopies", SHORT),
        ("dmDefaultSource", SHORT),
        ("dmPrintQuality", SHORT),
    ]


class _DevModeUnion(ctypes.Union):
    _fields_ = [("display", _DisplayUnion), ("printer", _PrinterUnion)]


class DEVMODEW(ctypes.Structure):  # noqa: N801 - Win32 name (the display members only are read)
    _anonymous_ = ("u",)
    _fields_ = [
        ("dmDeviceName", ctypes.c_wchar * CCHDEVICENAME),
        ("dmSpecVersion", WORD),
        ("dmDriverVersion", WORD),
        ("dmSize", WORD),
        ("dmDriverExtra", WORD),
        ("dmFields", DWORD),
        ("u", _DevModeUnion),
        ("dmColor", SHORT),
        ("dmDuplex", SHORT),
        ("dmYResolution", SHORT),
        ("dmTTOption", SHORT),
        ("dmCollate", SHORT),
        ("dmFormName", ctypes.c_wchar * CCHFORMNAME),
        ("dmLogPixels", WORD),
        ("dmBitsPerPel", DWORD),
        ("dmPelsWidth", DWORD),
        ("dmPelsHeight", DWORD),
        ("dmDisplayFlags", DWORD),
        ("dmDisplayFrequency", DWORD),
        ("dmICMMethod", DWORD),
        ("dmICMIntent", DWORD),
        ("dmMediaType", DWORD),
        ("dmDitherType", DWORD),
        ("dmReserved1", DWORD),
        ("dmReserved2", DWORD),
        ("dmPanningWidth", DWORD),
        ("dmPanningHeight", DWORD),
    ]


def _monitor_enum_proc() -> Any:
    """The ``MONITORENUMPROC`` callback type (stdcall), built on first use: ``ctypes.WINFUNCTYPE`` exists only on
    Windows, and the pure part of this module (:func:`order_monitors`) is imported by the Linux tests."""
    return ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(RECT), wintypes.LPARAM)


def order_monitors(found: list[tuple[tuple[int, int, int, int], bool, int]]) -> list[Monitor]:
    """Pure: ``(left, top, right, bottom), primary, rotation`` tuples → ``Monitor`` rows, primary first then by
    position, indexed 0..n-1. Unit-tested on Linux; the enumeration itself is not."""
    ordered = sorted(found, key=lambda m: (not m[1], m[0][0], m[0][1]))
    out: list[Monitor] = []
    for (left, top, right, bottom), primary, rotation in ordered:
        width, height = right - left, bottom - top
        if width <= 0 or height <= 0:
            continue
        out.append(
            Monitor(index=len(out), left=left, top=top, width=width, height=height, primary=primary, rotation=rotation)
        )
    return out


def capture_order_position(
    found: list[tuple[tuple[int, int, int, int], bool]], *, left: int, top: int, width: int, height: int, primary: bool
) -> int | None:
    """Pure: the ONE-based position, in raw ``EnumDisplayMonitors`` order (``found``: ``(left, top, right, bottom),
    primary`` per monitor, in that order), of the monitor DoMe streams: windows-capture's ``monitor_index``. The
    primary is recognised by its flag (as :func:`dome_agent.screen.session.match_monitor` does), another monitor by
    its exact rectangle in physical pixels. None: it is not there (unplugged, or the topology changed)."""
    rect = (left, top, left + width, top + height)
    for position, (found_rect, found_primary) in enumerate(found, start=1):
        if primary and found_primary:
            return position
        if not primary and not found_primary and tuple(found_rect) == rect:
            return position
    return None


class WindowsScreen:
    """:class:`ScreenAdapter` on ``user32``. Constructed on ``win32`` only; the DLL loads on first use."""

    def __init__(self) -> None:
        self._user32: Any = None
        self._enum_proc: Any = None

    def _libs(self) -> Any:
        if self._user32 is None:
            self._enum_proc = _monitor_enum_proc()
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            user32.EnumDisplayMonitors.argtypes = [wintypes.HDC, ctypes.c_void_p, self._enum_proc, wintypes.LPARAM]
            user32.EnumDisplayMonitors.restype = wintypes.BOOL
            user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(MONITORINFOEXW)]
            user32.GetMonitorInfoW.restype = wintypes.BOOL
            user32.EnumDisplaySettingsW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(DEVMODEW)]
            user32.EnumDisplaySettingsW.restype = wintypes.BOOL
            with contextlib.suppress(AttributeError):  # Windows 10 1607+
                user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
                user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
            self._user32 = user32
        return self._user32

    @staticmethod
    @contextlib.contextmanager
    def _per_monitor_dpi(user32: Any) -> Iterator[None]:
        setter = getattr(user32, "SetThreadDpiAwarenessContext", None)
        previous = setter(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2) if setter is not None else None
        try:
            yield
        finally:
            if setter is not None and previous:
                setter(previous)

    def _enumerate(self) -> list[tuple[tuple[int, int, int, int], bool, int]]:
        """Every monitor in raw ``EnumDisplayMonitors`` order: its rectangle (physical pixels: the calling thread is
        per-monitor DPI aware v2 meanwhile), whether it is the primary, and its rotation."""
        user32 = self._libs()
        found: list[tuple[tuple[int, int, int, int], bool, int]] = []

        def visit(monitor: Any, _hdc: Any, _rect: Any, _lparam: Any) -> int:
            info = MONITORINFOEXW()
            info.cbSize = ctypes.sizeof(MONITORINFOEXW)
            if user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
                r = info.rcMonitor
                found.append(
                    (
                        (int(r.left), int(r.top), int(r.right), int(r.bottom)),
                        bool(info.dwFlags & MONITORINFOF_PRIMARY),
                        self._rotation(user32, str(info.szDevice)),
                    )
                )
            return 1

        with self._per_monitor_dpi(user32):
            if not user32.EnumDisplayMonitors(None, None, self._enum_proc(visit), 0):
                raise ProtocolError(
                    "SCREEN_CAPTURE_UNAVAILABLE",
                    "Windows could not list the monitors.",
                    detail={"detail": "unsupported_display"},
                )
        return found

    def capture_position(self, *, left: int, top: int, width: int, height: int, primary: bool) -> int | None:
        """windows-capture's one-based ``monitor_index`` of the monitor DoMe streams (:func:`capture_order_position`
        on a fresh raw enumeration), or None when it is not there."""
        found = [(rect, is_primary) for rect, is_primary, _rotation in self._enumerate()]
        return capture_order_position(found, left=left, top=top, width=width, height=height, primary=primary)

    def monitors(self) -> list[Monitor]:
        monitors = order_monitors(self._enumerate())
        if not monitors:
            raise ProtocolError(
                "SCREEN_CAPTURE_UNAVAILABLE", "Windows lists no monitor.", detail={"detail": "unsupported_display"}
            )
        return monitors

    @staticmethod
    def _rotation(user32: Any, device: str) -> int:
        mode = DEVMODEW()
        mode.dmSize = ctypes.sizeof(DEVMODEW)
        try:
            if not user32.EnumDisplaySettingsW(device, ENUM_CURRENT_SETTINGS, ctypes.byref(mode)):
                return 0
        except OSError:
            return 0
        orientation = int(mode.display.dmDisplayOrientation)
        return orientation * 90 if orientation in (0, 1, 2, 3) else 0


__all__ = ["DEVMODEW", "MONITORINFOEXW", "WindowsScreen", "capture_order_position", "order_monitors"]
