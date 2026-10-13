"""Platform selection.

* Windows → :mod:`dome_agent.platform.windows` (real APIs; imported only on ``win32``)
* anything else → :mod:`dome_agent.platform.unsupported`
* ``DOME_AGENT_PLATFORM=fake`` (explicit opt-in, tests/dev only) → ``dome_agent.testing.fake_platform``
"""

from __future__ import annotations

import sys

from ..logsetup import get_logger
from ..settings import Settings
from .protocol import NativeHostRegistrar, PlatformSet, StartupAdapter

log = get_logger(__name__)


def build_platform(settings: Settings) -> PlatformSet:
    if settings.use_fake_platform:
        from ..testing.fake_platform import build_fake_platform

        log.warning(
            "DOME_AGENT_PLATFORM=fake: using the in-memory FAKE platform adapters. "
            "No real PC action will happen and state frames report platform=development."
        )
        return build_fake_platform()
    if sys.platform == "win32":
        from .windows import build_windows_platform

        return build_windows_platform()
    from .unsupported import build_unsupported_platform

    return build_unsupported_platform()


def build_startup(settings: Settings) -> StartupAdapter:
    """Only the start-at-login adapter. The commands Setup and its uninstaller run (``start-at-login``,
    ``install-native-host``, ``uninstall-native-host``) use these two builders instead of
    :func:`build_platform`, so they never import the audio, media or input adapters: less code runs
    on the paths that must not fail."""
    if settings.use_fake_platform:
        return build_platform(settings).startup
    if sys.platform == "win32":
        from .windows.startup import WindowsStartup

        return WindowsStartup()
    from .unsupported import UnsupportedStartup

    return UnsupportedStartup()


def build_native_host_registrar(settings: Settings) -> NativeHostRegistrar:
    """Only the native-messaging registrar (see :func:`build_startup`)."""
    if settings.use_fake_platform:
        return build_platform(settings).native_host
    if sys.platform == "win32":
        from .windows.nativehost import WindowsNativeHostRegistrar

        return WindowsNativeHostRegistrar()
    from .unsupported import UnsupportedNativeHost

    return UnsupportedNativeHost()


__all__ = ["PlatformSet", "build_native_host_registrar", "build_platform", "build_startup"]
