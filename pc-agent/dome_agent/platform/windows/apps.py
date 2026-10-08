"""Approved-application control with ``pywin32`` + ``psutil``.

* launch: ``subprocess.Popen([exe_path])`` — a one-element argv, no shell, no arguments, cwd = the
  executable's directory. The path comes from the local ``approved_apps`` table only.
* windows: ``EnumWindows`` filtered to visible, titled, un-owned top-level windows of the app's pids
* focus: ``ShowWindow(SW_RESTORE)`` + ``SetForegroundWindow``; success only if ``GetForegroundWindow``
  is the target afterwards (Windows may refuse → ``FOCUS_DENIED`` by the handler)
* close: ``PostMessage(WM_CLOSE)`` only; the process is never terminated
"""

from __future__ import annotations

import os
import subprocess
from typing import Any

import psutil
from dome_protocol import ProtocolError

from ..protocol import AppWindow


def _norm(path: str) -> str:
    return os.path.normcase(os.path.normpath(path))


def _hwnd(window_id: str) -> int:
    if not window_id.isdigit():
        raise ProtocolError("TARGET_GONE", "That window is no longer there")
    return int(window_id)


class WindowsApps:
    def running_pids(self, exe_path: str) -> list[int]:
        wanted = _norm(exe_path)
        pids: list[int] = []
        for proc in psutil.process_iter(["pid", "exe"]):
            try:
                exe = proc.info.get("exe")
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
            if exe and _norm(exe) == wanted:
                pids.append(int(proc.info["pid"]))
        return pids

    def launch(self, exe_path: str) -> int:
        if not os.path.isabs(exe_path):
            raise ProtocolError("APP_LAUNCH_FAILED", "Executable path must be absolute")
        try:
            proc = subprocess.Popen(  # noqa: S603 - argv is exactly the approved executable, no shell
                [exe_path],
                cwd=os.path.dirname(exe_path),
                shell=False,
                close_fds=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            raise ProtocolError(
                "APP_LAUNCH_FAILED", f"Windows could not start the application ({exc.__class__.__name__})"
            ) from exc
        return proc.pid

    def list_windows(self, pids: list[int]) -> list[AppWindow]:
        import win32con
        import win32gui
        import win32process

        wanted = set(pids)
        foreground = win32gui.GetForegroundWindow()
        found: list[AppWindow] = []

        def _cb(hwnd: int, _extra: Any) -> bool:
            if not win32gui.IsWindowVisible(hwnd):
                return True
            if win32gui.GetWindow(hwnd, win32con.GW_OWNER) != 0:
                return True
            ex_style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
            if ex_style & win32con.WS_EX_TOOLWINDOW:
                return True
            try:
                _tid, pid = win32process.GetWindowThreadProcessId(hwnd)
            except Exception:
                return True
            if pid not in wanted:
                return True
            title = win32gui.GetWindowText(hwnd) or ""
            if not title:
                return True
            found.append(
                AppWindow(
                    window_id=str(hwnd),
                    title=title[:200],
                    pid=int(pid),
                    minimized=bool(win32gui.IsIconic(hwnd)),
                    foreground=(hwnd == foreground),
                )
            )
            return True

        win32gui.EnumWindows(_cb, None)
        return found

    def window_exists(self, window_id: str) -> bool:
        import win32gui

        try:
            return bool(win32gui.IsWindow(_hwnd(window_id)))
        except ProtocolError:
            return False

    def focus(self, window_id: str) -> bool:
        import win32con
        import win32gui

        hwnd = _hwnd(window_id)
        if not win32gui.IsWindow(hwnd):
            raise ProtocolError("TARGET_GONE", "That window is no longer there")
        try:
            if win32gui.IsIconic(hwnd):
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            win32gui.BringWindowToTop(hwnd)
            win32gui.SetForegroundWindow(hwnd)
        except Exception:  # noqa: S110 - pywintypes.error: Windows refused the focus change; reported via GetForegroundWindow
            pass
        return bool(win32gui.GetForegroundWindow() == hwnd)

    def minimize(self, window_id: str) -> bool:
        import win32con
        import win32gui

        hwnd = _hwnd(window_id)
        if not win32gui.IsWindow(hwnd):
            raise ProtocolError("TARGET_GONE", "That window is no longer there")
        try:
            win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)
        except Exception as exc:
            raise ProtocolError("OS_ERROR", "Windows refused to minimise the window") from exc
        return bool(win32gui.IsIconic(hwnd))

    def request_close(self, window_id: str) -> None:
        import win32con
        import win32gui

        hwnd = _hwnd(window_id)
        if not win32gui.IsWindow(hwnd):
            raise ProtocolError("TARGET_GONE", "That window is no longer there")
        try:
            win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
        except Exception as exc:
            raise ProtocolError("OS_ERROR", "Windows refused the close request") from exc
