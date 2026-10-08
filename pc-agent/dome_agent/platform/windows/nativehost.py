"""Native-messaging host registration for Chrome and Edge (per-user, no elevation):

* ``HKCU\\Software\\Google\\Chrome\\NativeMessagingHosts\\com.dome.agent``
* ``HKCU\\Software\\Microsoft\\Edge\\NativeMessagingHosts\\com.dome.agent``

The default value of each key is the absolute path of the manifest JSON written by
:func:`dome_agent.bridge.manifest.write_manifest`.
"""

from __future__ import annotations

import winreg
from pathlib import Path

from ...bridge.manifest import HOST_NAME

REGISTRY_KEYS = (
    rf"Software\Google\Chrome\NativeMessagingHosts\{HOST_NAME}",
    rf"Software\Microsoft\Edge\NativeMessagingHosts\{HOST_NAME}",
)


class WindowsNativeHostRegistrar:
    def install(self, manifest_path: Path) -> list[str]:
        written: list[str] = []
        for sub_key in REGISTRY_KEYS:
            with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, sub_key, 0, winreg.KEY_SET_VALUE) as key:
                winreg.SetValueEx(key, None, 0, winreg.REG_SZ, str(manifest_path))
            written.append(rf"HKCU\{sub_key}")
        return written

    def uninstall(self) -> list[str]:
        removed: list[str] = []
        for sub_key in REGISTRY_KEYS:
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, sub_key)
                removed.append(rf"HKCU\{sub_key}")
            except FileNotFoundError:
                pass
        return removed
