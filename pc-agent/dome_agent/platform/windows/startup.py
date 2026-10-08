"""Start-at-login opt-in via the per-user Run key (no elevation):
``HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run\\DoMe``."""

from __future__ import annotations

import winreg

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "DoMe"


class WindowsStartup:
    def get_start_at_login(self) -> bool:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as key:
                value, _kind = winreg.QueryValueEx(key, VALUE_NAME)
                return bool(value)
        except FileNotFoundError:
            return False

    def set_start_at_login(self, enabled: bool, command: str) -> None:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, command)
            else:
                try:
                    winreg.DeleteValue(key, VALUE_NAME)
                except FileNotFoundError:
                    pass
