"""Per-monitor advanced colour (HDR) state for the diagnostics bundle, through the DisplayConfig API.

``GetDisplayConfigBufferSizes`` / ``QueryDisplayConfig(QDC_ONLY_ACTIVE_PATHS)`` list the active source → target paths;
``DisplayConfigGetDeviceInfo`` then answers, per path, the GDI device name of the source (``\\\\.\\DISPLAY1``, the name
``EnumDisplayMonitors`` and DXGI use too, which joins the row to the DXGI output) and its
``DISPLAYCONFIG_GET_ADVANCED_COLOR_INFO`` (supported / enabled / wide colour enforced / force-disabled, bits per colour
channel, colour encoding). The monitor's friendly name (its model) is not asked for: the platform's monitors carry no
names and the device name is join enough. Desktop
Duplication with ``ddagrab``'s default 8-bit format is documented to work on an HDR output ("8 Bit formats always
work"), so this is evidence for the bundle, not a decision.

Structure layouts follow ``wingdi.h`` (sizes asserted in the Linux tests: 72, 64, 20, 32 and 84 bytes). Windows
only; failure-tolerant (an empty list when the API is unavailable); **not yet verified on a device**.
"""

from __future__ import annotations

import ctypes
import sys
from typing import Any

from .dxgi import wide

# Fixed-width field types (see dxgi.py): the layouts are then the same on Linux, where the size tests run
UINT32 = ctypes.c_uint32
INT32 = ctypes.c_int32
BOOL = ctypes.c_int32
WCHAR = ctypes.c_uint16
QDC_ONLY_ACTIVE_PATHS = 0x00000002
ERROR_SUCCESS = 0
DISPLAYCONFIG_DEVICE_INFO_GET_SOURCE_NAME = 1
DISPLAYCONFIG_DEVICE_INFO_GET_ADVANCED_COLOR_INFO = 9
MAX_PATHS = 64


class LUID(ctypes.Structure):  # noqa: N801 - Win32 name
    _fields_ = [("LowPart", UINT32), ("HighPart", INT32)]


class DISPLAYCONFIG_RATIONAL(ctypes.Structure):  # noqa: N801 - Win32 name
    _fields_ = [("Numerator", UINT32), ("Denominator", UINT32)]


class DISPLAYCONFIG_PATH_SOURCE_INFO(ctypes.Structure):  # noqa: N801 - Win32 name
    _fields_ = [("adapterId", LUID), ("id", UINT32), ("modeInfoIdx", UINT32), ("statusFlags", UINT32)]


class DISPLAYCONFIG_PATH_TARGET_INFO(ctypes.Structure):  # noqa: N801 - Win32 name
    _fields_ = [
        ("adapterId", LUID),
        ("id", UINT32),
        ("modeInfoIdx", UINT32),
        ("outputTechnology", UINT32),
        ("rotation", UINT32),
        ("scaling", UINT32),
        ("refreshRate", DISPLAYCONFIG_RATIONAL),
        ("scanLineOrdering", UINT32),
        ("targetAvailable", BOOL),
        ("statusFlags", UINT32),
    ]


class DISPLAYCONFIG_PATH_INFO(ctypes.Structure):  # noqa: N801 - Win32 name
    _fields_ = [
        ("sourceInfo", DISPLAYCONFIG_PATH_SOURCE_INFO),
        ("targetInfo", DISPLAYCONFIG_PATH_TARGET_INFO),
        ("flags", UINT32),
    ]


class DISPLAYCONFIG_MODE_INFO(ctypes.Structure):  # noqa: N801 - Win32 name (the mode union is not read)
    _fields_ = [("infoType", UINT32), ("id", UINT32), ("adapterId", LUID), ("mode", ctypes.c_uint64 * 6)]


class DISPLAYCONFIG_DEVICE_INFO_HEADER(ctypes.Structure):  # noqa: N801 - Win32 name
    _fields_ = [("type", UINT32), ("size", UINT32), ("adapterId", LUID), ("id", UINT32)]


class DISPLAYCONFIG_GET_ADVANCED_COLOR_INFO(ctypes.Structure):  # noqa: N801 - Win32 name
    _fields_ = [
        ("header", DISPLAYCONFIG_DEVICE_INFO_HEADER),
        ("value", UINT32),  # bit 0 supported, 1 enabled, 2 wide colour enforced, 3 force disabled
        ("colorEncoding", UINT32),
        ("bitsPerColorChannel", UINT32),
    ]


class DISPLAYCONFIG_SOURCE_DEVICE_NAME(ctypes.Structure):  # noqa: N801 - Win32 name
    _fields_ = [("header", DISPLAYCONFIG_DEVICE_INFO_HEADER), ("viewGdiDeviceName", WCHAR * 32)]


def _user32() -> Any:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetDisplayConfigBufferSizes.argtypes = [UINT32, ctypes.POINTER(UINT32), ctypes.POINTER(UINT32)]
    user32.GetDisplayConfigBufferSizes.restype = ctypes.c_long
    user32.QueryDisplayConfig.argtypes = [
        UINT32,
        ctypes.POINTER(UINT32),
        ctypes.POINTER(DISPLAYCONFIG_PATH_INFO),
        ctypes.POINTER(UINT32),
        ctypes.POINTER(DISPLAYCONFIG_MODE_INFO),
        ctypes.c_void_p,
    ]
    user32.QueryDisplayConfig.restype = ctypes.c_long
    user32.DisplayConfigGetDeviceInfo.argtypes = [ctypes.POINTER(DISPLAYCONFIG_DEVICE_INFO_HEADER)]
    user32.DisplayConfigGetDeviceInfo.restype = ctypes.c_long
    return user32


def _device_info(user32: Any, packet: Any, kind: int, adapter: LUID, target_id: int) -> bool:
    packet.header.type = kind
    packet.header.size = ctypes.sizeof(packet)
    packet.header.adapterId = adapter
    packet.header.id = target_id
    header = ctypes.cast(ctypes.pointer(packet), ctypes.POINTER(DISPLAYCONFIG_DEVICE_INFO_HEADER))
    return bool(user32.DisplayConfigGetDeviceInfo(header) == ERROR_SUCCESS)


def advanced_colour() -> list[dict[str, Any]]:
    """One row per active display path: the GDI device name, the monitor's friendly name, the output technology and
    the advanced-colour flags. Empty when the API is unavailable (older Windows, no desktop). Raises nothing."""
    if sys.platform != "win32":
        return []
    try:
        user32 = _user32()
        paths_n, modes_n = UINT32(0), UINT32(0)
        if user32.GetDisplayConfigBufferSizes(QDC_ONLY_ACTIVE_PATHS, ctypes.byref(paths_n), ctypes.byref(modes_n)):
            return []
        count = min(int(paths_n.value), MAX_PATHS)
        if count == 0:
            return []
        paths = (DISPLAYCONFIG_PATH_INFO * count)()
        modes = (DISPLAYCONFIG_MODE_INFO * max(1, int(modes_n.value)))()
        paths_n, modes_n = UINT32(count), UINT32(len(modes))
        status = user32.QueryDisplayConfig(
            QDC_ONLY_ACTIVE_PATHS, ctypes.byref(paths_n), paths, ctypes.byref(modes_n), modes, None
        )
        if status != ERROR_SUCCESS:
            return []
        rows: list[dict[str, Any]] = []
        for path in paths[: int(paths_n.value)]:
            row: dict[str, Any] = {"output_technology": int(path.targetInfo.outputTechnology)}
            source = DISPLAYCONFIG_SOURCE_DEVICE_NAME()
            if _device_info(
                user32, source, DISPLAYCONFIG_DEVICE_INFO_GET_SOURCE_NAME, path.sourceInfo.adapterId, path.sourceInfo.id
            ):
                row["device_name"] = wide(source.viewGdiDeviceName)
            colour = DISPLAYCONFIG_GET_ADVANCED_COLOR_INFO()
            if _device_info(
                user32,
                colour,
                DISPLAYCONFIG_DEVICE_INFO_GET_ADVANCED_COLOR_INFO,
                path.targetInfo.adapterId,
                path.targetInfo.id,
            ):
                value = int(colour.value)
                row.update(
                    {
                        "advanced_colour_supported": bool(value & 1),
                        "advanced_colour_enabled": bool(value & 2),
                        "wide_colour_enforced": bool(value & 4),
                        "advanced_colour_force_disabled": bool(value & 8),
                        "bits_per_colour_channel": int(colour.bitsPerColorChannel),
                        "colour_encoding": int(colour.colorEncoding),
                    }
                )
            rows.append(row)
        return rows
    except (OSError, AttributeError, ValueError):
        return []


__all__ = [
    "DISPLAYCONFIG_DEVICE_INFO_HEADER",
    "DISPLAYCONFIG_GET_ADVANCED_COLOR_INFO",
    "DISPLAYCONFIG_MODE_INFO",
    "DISPLAYCONFIG_PATH_INFO",
    "DISPLAYCONFIG_SOURCE_DEVICE_NAME",
    "advanced_colour",
]
