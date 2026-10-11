"""Interactive session: lock (``LockWorkStation``) and lock-state detection.

Lock state is read from ``WTSQuerySessionInformationW(WTS_CURRENT_SESSION, WTSSessionInfoEx)`` →
``WTSINFOEX_LEVEL1_W.SessionFlags`` (``WTS_SESSIONSTATE_LOCK = 0`` / ``UNLOCK = 1`` on Windows 8 and
later). If that query fails, or reports any other value (``WTS_SESSIONSTATE_UNKNOWN``), the
``OpenInputDesktop`` heuristic is used (the secure desktop cannot be opened by a user process while the
session is locked).

Every ctypes call declares ``argtypes``/``restype``: handles are 64-bit, and an undeclared argument or
result is passed as a C ``int``.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

from dome_protocol import ProtocolError

WTS_CURRENT_SERVER_HANDLE = None
WTS_CURRENT_SESSION = 0xFFFFFFFF
WTS_SESSION_INFO_EX = 25
WTS_SESSIONSTATE_LOCK = 0
WTS_SESSIONSTATE_UNLOCK = 1
DESKTOP_READOBJECTS = 0x0001


class _WtsInfoExLevel1(ctypes.Structure):
    _fields_ = [
        ("SessionId", wintypes.ULONG),
        ("SessionState", ctypes.c_int),
        ("SessionFlags", wintypes.LONG),
        ("WinStationName", wintypes.WCHAR * 33),
        ("UserName", wintypes.WCHAR * 21),
        ("DomainName", wintypes.WCHAR * 18),
        ("LogonTime", wintypes.LARGE_INTEGER),
        ("ConnectTime", wintypes.LARGE_INTEGER),
        ("DisconnectTime", wintypes.LARGE_INTEGER),
        ("LastInputTime", wintypes.LARGE_INTEGER),
        ("CurrentTime", wintypes.LARGE_INTEGER),
        ("IncomingBytes", wintypes.DWORD),
        ("OutgoingBytes", wintypes.DWORD),
        ("IncomingFrames", wintypes.DWORD),
        ("OutgoingFrames", wintypes.DWORD),
        ("IncomingCompressedBytes", wintypes.DWORD),
        ("OutgoingCompressedBytes", wintypes.DWORD),
    ]


class _WtsInfoExLevel(ctypes.Union):
    _fields_ = [("WTSInfoExLevel1", _WtsInfoExLevel1)]


class _WtsInfoEx(ctypes.Structure):
    _fields_ = [("Level", wintypes.DWORD), ("Data", _WtsInfoExLevel)]


class WindowsSession:
    def is_locked(self) -> bool:
        flags = self._session_flags()
        if flags in (WTS_SESSIONSTATE_LOCK, WTS_SESSIONSTATE_UNLOCK):
            return flags == WTS_SESSIONSTATE_LOCK
        return self._input_desktop_unavailable()  # query failed, or WTS_SESSIONSTATE_UNKNOWN (-1)

    def _session_flags(self) -> int | None:
        try:
            wtsapi32 = ctypes.WinDLL("wtsapi32", use_last_error=True)
            query = wtsapi32.WTSQuerySessionInformationW
            query.argtypes = [
                wintypes.HANDLE,
                wintypes.DWORD,
                ctypes.c_int,
                ctypes.POINTER(ctypes.c_void_p),
                ctypes.POINTER(wintypes.DWORD),
            ]
            query.restype = wintypes.BOOL
            free = wtsapi32.WTSFreeMemory
            free.argtypes = [ctypes.c_void_p]
            free.restype = None
            buffer = ctypes.c_void_p()
            size = wintypes.DWORD(0)
            ok = query(
                WTS_CURRENT_SERVER_HANDLE,
                WTS_CURRENT_SESSION,
                WTS_SESSION_INFO_EX,
                ctypes.byref(buffer),
                ctypes.byref(size),
            )
            if not ok or not buffer.value or size.value < ctypes.sizeof(_WtsInfoEx):
                return None
            try:
                info = ctypes.cast(buffer, ctypes.POINTER(_WtsInfoEx)).contents
                if info.Level != 1:
                    return None
                return int(info.Data.WTSInfoExLevel1.SessionFlags)
            finally:
                free(buffer)
        except (OSError, AttributeError):
            return None

    def _input_desktop_unavailable(self) -> bool:
        try:
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            user32.OpenInputDesktop.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            user32.OpenInputDesktop.restype = wintypes.HANDLE  # HDESK
            user32.CloseDesktop.argtypes = [wintypes.HANDLE]
            user32.CloseDesktop.restype = wintypes.BOOL
            handle = user32.OpenInputDesktop(0, False, DESKTOP_READOBJECTS)
            if not handle:
                return True
            user32.CloseDesktop(handle)
            return False
        except OSError:
            return False

    def lock(self) -> None:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.LockWorkStation.argtypes = []
        user32.LockWorkStation.restype = wintypes.BOOL
        if not user32.LockWorkStation():
            err = ctypes.get_last_error()
            raise ProtocolError("OS_ERROR", f"LockWorkStation failed (error {err})")
