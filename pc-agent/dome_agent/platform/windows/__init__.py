"""Real Windows adapters. Imported only when ``sys.platform == "win32"``.

Each module talks to the actual OS API named in docs/design/pc-agent.md (pycaw/IAudioEndpointVolume,
winsdk GlobalSystemMediaTransportControls, pywin32 window control, LockWorkStation,
SetSuspendState / InitiateSystemShutdownExW without forcing apps, HKCU Run key, HKCU native
messaging manifests). They cannot be executed in the Linux build environment; see README.md →
"Windows verification checklist" for the manual steps.
"""

from __future__ import annotations

import sys

from ..protocol import PlatformSet


def build_windows_platform() -> PlatformSet:
    if sys.platform != "win32":  # pragma: no cover - defensive
        raise RuntimeError("Windows adapters requested on a non-Windows host")
    from .apps import WindowsApps
    from .media import WindowsMedia
    from .nativehost import WindowsNativeHostRegistrar
    from .power import WindowsPower
    from .session import WindowsSession
    from .startup import WindowsStartup
    from .volume import WindowsVolume

    return PlatformSet(
        name="windows",
        volume=WindowsVolume(),
        media=WindowsMedia(),
        apps=WindowsApps(),
        session=WindowsSession(),
        power=WindowsPower(),
        startup=WindowsStartup(),
        native_host=WindowsNativeHostRegistrar(),
    )


__all__ = ["build_windows_platform"]
