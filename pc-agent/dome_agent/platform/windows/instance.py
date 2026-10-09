"""Windows single-instance primitives (spec §10): a named mutex in the ``Local\\`` (logon session)
namespace and ``ProcessIdToSessionId``. Imported only on ``win32``; see ``dome_agent.single_instance``."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from typing import Any

ERROR_ALREADY_EXISTS = 183


def _kernel32() -> Any:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.ProcessIdToSessionId.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    kernel32.ProcessIdToSessionId.restype = wintypes.BOOL
    kernel32.GetCurrentProcessId.restype = wintypes.DWORD
    return kernel32


def current_session_id() -> str:
    kernel32 = _kernel32()
    session = wintypes.DWORD(0)
    if kernel32.ProcessIdToSessionId(kernel32.GetCurrentProcessId(), ctypes.byref(session)):
        return str(int(session.value))
    return "unknown"


def session_id_of_pid(pid: int) -> str | None:
    kernel32 = _kernel32()
    session = wintypes.DWORD(0)
    if kernel32.ProcessIdToSessionId(int(pid), ctypes.byref(session)):
        return str(int(session.value))
    return None


def acquire_mutex(name: str) -> tuple[Any | None, int]:
    """``(handle, last_error)``: handle is None when the mutex could not be created; ``last_error ==
    ERROR_ALREADY_EXISTS`` (with a handle, already closed here) means another instance holds it."""
    kernel32 = _kernel32()
    ctypes.set_last_error(0)
    handle = kernel32.CreateMutexW(None, False, name)
    err = ctypes.get_last_error()
    if not handle:
        return None, err
    if err == ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(handle)
        return None, err
    return handle, 0


def close_handle(handle: Any) -> None:
    _kernel32().CloseHandle(handle)
