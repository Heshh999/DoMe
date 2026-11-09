"""The Windows pointer for the cursor poller (design 2a §3.7): ``user32``/``gdi32`` through ctypes.

* :meth:`Win32CursorReader.read`: ``GetCursorInfo`` (flags, the handle, the position). The poller's thread is made
  per-monitor DPI aware (v2) once, before its first read (:meth:`Win32CursorReader.prepare_thread`; its own thread,
  so there is nothing to restore), so ``ptScreenPos`` is in physical pixels like ``Monitor``.
* :meth:`Win32CursorReader.named_kind`: the handle compared with ``LoadCursorW(NULL, IDC_*)`` of the fourteen system
  cursors (``cursor.NAMED_KINDS``), built once. Shared system cursors keep their handle, so this is an identity test.
* :meth:`Win32CursorReader.render`: ``GetIconInfo`` (hotspot, mask and colour bitmaps) → the size from
  ``GetObjectW`` on the colour bitmap (or the mask's, whose height is two cursors tall: AND above XOR) → a zeroed
  32-bpp top-down DIB section selected into a memory DC → ``DrawIconEx(DI_NORMAL)`` → its pixels. When every alpha
  byte is 0 (a cursor without its own alpha) a second DIB gets ``DrawIconEx(DI_MASK)`` and
  ``cursor.alpha_from_mask`` reads that DIB's pixels (white where the AND bit is 1); otherwise ``DrawIconEx`` has
  alpha-blended the cursor onto the zeroed DIB, so its premultiplied colours are made straight
  (``cursor.straight_alpha``) before the PNG is made. Every GDI object, the bitmaps
  ``GetIconInfo`` created included, is released in ``finally``. A render costs about 0.5 to 3 ms and happens only
  when the handle changed (and the shape is not cached).

Structures use fixed-width fields (``wintypes.LONG`` is 8 bytes on LP64 Linux) so :func:`expected_sizes` and a Linux
unit test check the Win32 layout; the reader takes stand-ins for ``user32``/``gdi32`` in tests. Nothing here keeps or
logs a bitmap. **Not yet verified on a device.**
"""

from __future__ import annotations

import contextlib
import ctypes
import sys
from typing import Any

from .cursor import NAMED_KINDS, alpha_from_mask, straight_alpha, with_alpha

LONG = ctypes.c_int32
DWORD = ctypes.c_uint32
WORD = ctypes.c_uint16
BOOL = ctypes.c_int32
HANDLE = ctypes.c_void_p
DI_MASK = 0x0001
DI_NORMAL = 0x0003
BI_RGB = 0
DIB_RGB_COLORS = 0
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
RENDER_MAX_PX = 256  # a cursor larger than this is not one: refused (it would be scaled to 64 anyway)


class POINT(ctypes.Structure):
    _fields_ = [("x", LONG), ("y", LONG)]


class CURSORINFO(ctypes.Structure):  # noqa: N801 - Win32 name
    _fields_ = [("cbSize", DWORD), ("flags", DWORD), ("hCursor", HANDLE), ("ptScreenPos", POINT)]


class ICONINFO(ctypes.Structure):  # noqa: N801 - Win32 name
    _fields_ = [
        ("fIcon", BOOL),
        ("xHotspot", DWORD),
        ("yHotspot", DWORD),
        ("hbmMask", HANDLE),
        ("hbmColor", HANDLE),
    ]


class BITMAP(ctypes.Structure):  # noqa: N801 - Win32 name
    _fields_ = [
        ("bmType", LONG),
        ("bmWidth", LONG),
        ("bmHeight", LONG),
        ("bmWidthBytes", LONG),
        ("bmPlanes", WORD),
        ("bmBitsPixel", WORD),
        ("bmBits", ctypes.c_void_p),
    ]


class BITMAPINFOHEADER(ctypes.Structure):  # noqa: N801 - Win32 name
    _fields_ = [
        ("biSize", DWORD),
        ("biWidth", LONG),
        ("biHeight", LONG),
        ("biPlanes", WORD),
        ("biBitCount", WORD),
        ("biCompression", DWORD),
        ("biSizeImage", DWORD),
        ("biXPelsPerMeter", LONG),
        ("biYPelsPerMeter", LONG),
        ("biClrUsed", DWORD),
        ("biClrImportant", DWORD),
    ]


class BITMAPINFO(ctypes.Structure):  # noqa: N801 - Win32 name
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", DWORD * 3)]


def expected_sizes() -> dict[str, int]:
    """``sizeof`` of each structure per the Win32 ABI of this process's pointer size (x64 / x86)."""
    wide = ctypes.sizeof(ctypes.c_void_p) == 8
    return {
        "CURSORINFO": 24 if wide else 20,
        "ICONINFO": 32 if wide else 20,
        "BITMAP": 32 if wide else 24,
        "BITMAPINFOHEADER": 40,
    }


def _load_libs() -> tuple[Any, Any]:
    if sys.platform != "win32":
        raise OSError("user32 and gdi32 exist on Windows only")
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    user32.GetCursorInfo.argtypes = [ctypes.POINTER(CURSORINFO)]
    user32.GetCursorInfo.restype = BOOL
    user32.LoadCursorW.argtypes = [HANDLE, ctypes.c_void_p]
    user32.LoadCursorW.restype = HANDLE
    user32.GetIconInfo.argtypes = [HANDLE, ctypes.POINTER(ICONINFO)]
    user32.GetIconInfo.restype = BOOL
    user32.DrawIconEx.argtypes = [
        HANDLE,
        ctypes.c_int,
        ctypes.c_int,
        HANDLE,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_uint,
        HANDLE,
        ctypes.c_uint,
    ]
    user32.DrawIconEx.restype = BOOL
    with contextlib.suppress(AttributeError):  # Windows 10 1607+
        user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
    gdi32.GetObjectW.argtypes = [HANDLE, ctypes.c_int, ctypes.c_void_p]
    gdi32.GetObjectW.restype = ctypes.c_int
    gdi32.CreateCompatibleDC.argtypes = [HANDLE]
    gdi32.CreateCompatibleDC.restype = HANDLE
    gdi32.CreateDIBSection.argtypes = [
        HANDLE,
        ctypes.POINTER(BITMAPINFO),
        ctypes.c_uint,
        ctypes.POINTER(ctypes.c_void_p),
        HANDLE,
        DWORD,
    ]
    gdi32.CreateDIBSection.restype = HANDLE
    gdi32.SelectObject.argtypes = [HANDLE, HANDLE]
    gdi32.SelectObject.restype = HANDLE
    gdi32.DeleteObject.argtypes = [HANDLE]
    gdi32.DeleteObject.restype = BOOL
    gdi32.DeleteDC.argtypes = [HANDLE]
    gdi32.DeleteDC.restype = BOOL
    gdi32.GdiFlush.restype = BOOL
    return user32, gdi32


class Win32CursorReader:
    """``cursor.CursorReader`` on Windows. ``user32``/``gdi32`` are loaded on first use (stand-ins in tests); off
    Windows it cannot be constructed without them."""

    def __init__(self, *, user32: Any = None, gdi32: Any = None) -> None:
        if (user32 is None or gdi32 is None) and sys.platform != "win32":
            raise OSError("the Windows pointer reader needs Windows")
        self._user32 = user32
        self._gdi32 = gdi32
        self._named: dict[int, str] | None = None

    def _libs(self) -> tuple[Any, Any]:
        if self._user32 is None or self._gdi32 is None:
            self._user32, self._gdi32 = _load_libs()
        return self._user32, self._gdi32

    def prepare_thread(self) -> None:
        """The poller's thread, once: per-monitor DPI aware (v2), so positions are physical pixels."""
        user32, _gdi32 = self._libs()
        setter = getattr(user32, "SetThreadDpiAwarenessContext", None)
        if setter is not None:
            setter(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)

    def read(self) -> tuple[int, int, int, int] | None:
        user32, _gdi32 = self._libs()
        info = CURSORINFO()
        info.cbSize = ctypes.sizeof(CURSORINFO)
        if not user32.GetCursorInfo(ctypes.byref(info)):
            return None
        return int(info.flags), int(info.hCursor or 0), int(info.ptScreenPos.x), int(info.ptScreenPos.y)

    def named_kind(self, hcursor: int) -> str | None:
        if self._named is None:
            user32, _gdi32 = self._libs()
            named: dict[int, str] = {}
            for ident, kind in NAMED_KINDS.items():
                handle = user32.LoadCursorW(None, ctypes.c_void_p(ident))  # MAKEINTRESOURCE(IDC_*)
                if handle:
                    named[int(handle)] = kind
            self._named = named
        return self._named.get(int(hcursor))

    def render(self, hcursor: int) -> tuple[bytes, int, int, int, int, int] | None:
        try:
            return self._render(int(hcursor))
        except (OSError, ValueError, ctypes.ArgumentError):
            return None

    def _render(self, hcursor: int) -> tuple[bytes, int, int, int, int, int] | None:
        user32, gdi32 = self._libs()
        info = ICONINFO()
        if not user32.GetIconInfo(ctypes.c_void_p(hcursor), ctypes.byref(info)):
            return None
        created = [handle for handle in (info.hbmMask, info.hbmColor) if handle]  # GetIconInfo made these: ours
        dibs: list[Any] = []
        dc: Any = None
        try:
            bitmap = BITMAP()
            source = info.hbmColor or info.hbmMask
            if not source or not gdi32.GetObjectW(source, ctypes.sizeof(BITMAP), ctypes.byref(bitmap)):
                return None
            width, height = int(bitmap.bmWidth), abs(int(bitmap.bmHeight))
            if not info.hbmColor:
                height //= 2  # a mask-only cursor: the AND mask above the XOR mask
            if not (0 < width <= RENDER_MAX_PX and 0 < height <= RENDER_MAX_PX):
                return None
            dc = gdi32.CreateCompatibleDC(None)
            if not dc:
                return None
            color = self._draw(user32, gdi32, dc, hcursor, width, height, DI_NORMAL, dibs)
            if color is None:
                return None
            stride = width * 4
            if not any(color[3::4]):  # no alpha of its own: the mask says what is transparent
                mask = self._draw(user32, gdi32, dc, hcursor, width, height, DI_MASK, dibs)
                if mask is None:
                    return None
                alpha = alpha_from_mask(mask, width, height, stride, color_bgra=color)
                color = with_alpha(color, alpha, width, height, stride)
            else:
                # its own alpha: DrawIconEx blended it onto the zeroed DIB, so the colours are premultiplied
                color = straight_alpha(color, width, height, stride)
            return color, width, height, stride, int(info.xHotspot), int(info.yHotspot)
        finally:
            for dib in dibs:
                gdi32.DeleteObject(dib)
            if dc:
                gdi32.DeleteDC(dc)
            for handle in created:
                gdi32.DeleteObject(handle)

    @staticmethod
    def _draw(
        user32: Any, gdi32: Any, dc: Any, hcursor: int, width: int, height: int, flags: int, dibs: list[Any]
    ) -> bytes | None:
        """The cursor drawn into a fresh zeroed 32-bpp top-down DIB with ``flags``; its pixels (``width`` x 4 per row)."""
        header = BITMAPINFO()
        header.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        header.bmiHeader.biWidth = width
        header.bmiHeader.biHeight = -height  # top-down
        header.bmiHeader.biPlanes = 1
        header.bmiHeader.biBitCount = 32
        header.bmiHeader.biCompression = BI_RGB
        bits = ctypes.c_void_p()
        dib = gdi32.CreateDIBSection(dc, ctypes.byref(header), DIB_RGB_COLORS, ctypes.byref(bits), None, 0)
        if not dib:
            return None
        dibs.append(dib)
        if not bits.value:
            return None
        size = width * height * 4
        ctypes.memset(bits.value, 0, size)
        previous = gdi32.SelectObject(dc, dib)
        try:
            if not user32.DrawIconEx(dc, 0, 0, ctypes.c_void_p(hcursor), width, height, 0, None, flags):
                return None
            gdi32.GdiFlush()
            return ctypes.string_at(bits.value, size)
        finally:
            gdi32.SelectObject(dc, previous)


__all__ = [
    "BITMAP",
    "BITMAPINFO",
    "BITMAPINFOHEADER",
    "CURSORINFO",
    "DI_MASK",
    "DI_NORMAL",
    "ICONINFO",
    "Win32CursorReader",
    "expected_sizes",
]
