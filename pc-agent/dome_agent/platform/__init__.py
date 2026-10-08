"""Platform selection.

* Windows → :mod:`dome_agent.platform.windows` (real APIs; imported only on ``win32``)
* anything else → :mod:`dome_agent.platform.unsupported`
* ``DOME_AGENT_PLATFORM=fake`` (explicit opt-in, tests/dev only) → ``dome_agent.testing.fake_platform``
"""

from __future__ import annotations

import sys

from ..logsetup import get_logger
from ..settings import Settings
from .protocol import PlatformSet

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


__all__ = ["PlatformSet", "build_platform"]
