"""Power actions without forcing applications closed.

* sleep:    ``powrprof!SetSuspendState(Hibernate=FALSE, ForceCritical=FALSE, DisableWakeEvent=FALSE)``
* restart / shutdown: enable ``SeShutdownPrivilege`` on our own token, then
  ``advapi32!InitiateSystemShutdownExW(NULL, message, 0, bForceAppsClosed=FALSE, bRebootAfterShutdown,
  SHTDN_REASON_MAJOR_OTHER | SHTDN_REASON_FLAG_PLANNED)``. With ``bForceAppsClosed=FALSE`` Windows asks
  the user about unsaved work instead of discarding it (spec §10).

* abort: ``advapi32!AbortSystemShutdownW(NULL)`` for ``power.cancel`` after a restart/shutdown was
  initiated (only possible inside the OS grace period; see :meth:`WindowsPower.abort_shutdown`).

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
ERROR_NO_SHUTDOWN_IN_PROGRESS = 1116


def _enable_shutdown_privilege() -> None:
    import win32api
    import win32con
    import win32security

    token = win32security.OpenProcessToken(
        win32api.GetCurrentProcess(), win32con.TOKEN_ADJUST_PRIVILEGES | win32con.TOKEN_QUERY
    )
    # pywin32 documents None as "the local system" and a list of (LUID, attributes) tuples as the new
    # state; the types-pywin32 stubs are stricter than the runtime here.
    luid = win32security.LookupPrivilegeValue(None, win32security.SE_SHUTDOWN_NAME)  # type: ignore[arg-type, unused-ignore]
    win32security.AdjustTokenPrivileges(token, 0, [(luid, win32con.SE_PRIVILEGE_ENABLED)])  # type: ignore[arg-type, unused-ignore]


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

    def abort_shutdown(self) -> bool:
        """``AbortSystemShutdownW(NULL)``: True iff Windows aborted a pending shutdown/restart.

        Windows can only abort during the ``dwTimeout`` grace period of ``InitiateSystemShutdownExW``;
        this adapter issues the call with ``dwTimeout = 0`` (the cancellable countdown is the agent's
        own), so in practice Windows answers ``ERROR_NO_SHUTDOWN_IN_PROGRESS`` → False, and the agent
        reports ``canceled: false`` rather than pretending."""
        try:
            _enable_shutdown_privilege()
        except Exception as exc:
            raise ProtocolError("POWER_DENIED", "Could not obtain the shutdown privilege") from exc
        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        fn = advapi32.AbortSystemShutdownW
        fn.argtypes = [wintypes.LPWSTR]
        fn.restype = wintypes.BOOL
        if fn(None):
            return True
        err = ctypes.get_last_error()
        if err in (ERROR_NO_SHUTDOWN_IN_PROGRESS, 0):
            return False
        raise _map_error(err, "abort")
