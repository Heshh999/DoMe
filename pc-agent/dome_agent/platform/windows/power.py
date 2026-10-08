"""Power actions without forcing applications closed.

* sleep:    ``powrprof!SetSuspendState(Hibernate=FALSE, ForceCritical=FALSE, DisableWakeEvent=FALSE)``
* restart / shutdown: enable ``SeShutdownPrivilege`` on our own token, then
  ``advapi32!InitiateSystemShutdownExW(NULL, message, 0, bForceAppsClosed=FALSE, bRebootAfterShutdown,
  SHTDN_REASON_MAJOR_OTHER | SHTDN_REASON_FLAG_PLANNED)``. With ``bForceAppsClosed=FALSE`` Windows asks
  the user about unsaved work instead of discarding it (spec §10).

The countdown itself lives in :mod:`dome_agent.actions.power`; this adapter issues the OS call once.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

from dome_protocol import ProtocolError

SHTDN_REASON_MAJOR_OTHER = 0x00000000
SHTDN_REASON_FLAG_PLANNED = 0x80000000
ERROR_ACCESS_DENIED = 5
ERROR_SHUTDOWN_IN_PROGRESS = 1115


def _enable_shutdown_privilege() -> None:
    import win32api
    import win32con
    import win32security

    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_ADJUST_PRIVILEGES | win32con.TOKEN_QUERY)
    luid = win32security.LookupPrivilegeValue(None, win32security.SE_SHUTDOWN_NAME)
    win32security.AdjustTokenPrivileges(token, 0, [(luid, win32con.SE_PRIVILEGE_ENABLED)])


def _map_error(err: int, what: str) -> ProtocolError:
    if err == ERROR_ACCESS_DENIED:
        return ProtocolError("POWER_DENIED", f"Windows denied the {what} request (policy or privilege)")
    if err == ERROR_SHUTDOWN_IN_PROGRESS:
        return ProtocolError("POWER_DENIED", "A shutdown is already in progress")
    return ProtocolError("OS_ERROR", f"{what} failed (Windows error {err})")


class WindowsPower:
    def sleep(self) -> None:
        powrprof = ctypes.WinDLL("powrprof", use_last_error=True)
        powrprof.SetSuspendState.argtypes = [wintypes.BOOLEAN, wintypes.BOOLEAN, wintypes.BOOLEAN]
        powrprof.SetSuspendState.restype = wintypes.BOOLEAN
        if not powrprof.SetSuspendState(False, False, False):
            raise _map_error(ctypes.get_last_error(), "sleep")

    def _initiate(self, reboot: bool) -> None:
        try:
            _enable_shutdown_privilege()
        except Exception as exc:
            raise ProtocolError("POWER_DENIED", "Could not obtain the shutdown privilege") from exc
        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        fn = advapi32.InitiateSystemShutdownExW
        fn.argtypes = [wintypes.LPWSTR, wintypes.LPWSTR, wintypes.DWORD, wintypes.BOOL, wintypes.BOOL, wintypes.DWORD]
        fn.restype = wintypes.BOOL
        message = "DoMe: " + ("restart" if reboot else "shutdown") + " requested from your phone."
        ok = fn(None, message, 0, False, reboot, SHTDN_REASON_MAJOR_OTHER | SHTDN_REASON_FLAG_PLANNED)
        if not ok:
            raise _map_error(ctypes.get_last_error(), "restart" if reboot else "shutdown")

    def restart(self) -> None:
        self._initiate(reboot=True)

    def shutdown(self) -> None:
        self._initiate(reboot=False)
