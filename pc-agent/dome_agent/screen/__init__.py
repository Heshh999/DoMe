"""Screen view (roadmap #52, protocol 1.4; docs/design/screen-view.md, docs/adr/0002-screen-view-transport.md).

The PC's picture reaches one phone over WebRTC between the two devices; the relay carries signed signaling only.
This package holds the agent side: the strict offer check (:mod:`sdp_check`), the PC-signed answer
(:mod:`signing`), consent and the permission-UI rules (:mod:`consent`), the fail-closed indicator
(:mod:`indicator`), the worker supervisor (:mod:`supervisor`), the session manager (:mod:`session`), the local
history (:mod:`history`) and, in :mod:`worker`, the separate send-only aiortc process. Phase 0's probe stays in
:mod:`probe`. Nothing here is imported by the worker except :mod:`worker.pipe`; the worker imports no relay or
API code (design §5.1, tested).

Evidence: unit-tested on Linux with the fake platform, a fake worker and real aiortc peers on loopback; capture,
encoding, the bars and the hotkey on Windows are **not yet verified on a device**.

A build without the media stack (every Windows build so far is made without the ``screen`` extra) still speaks
protocol 1.4, because pairing needs ``pc_key_mac``, but :func:`media_stack_available` is False there: a start is
refused before any bar with ``ACTION_UNAVAILABLE{reason: screen_view_not_included}`` and the tray says Screen view is
not in this version, instead of a capture failure that blames the display.
"""

from __future__ import annotations

import functools
import importlib.util

SCREEN_VIEW_NOT_INCLUDED = "screen_view_not_included"
NOT_INCLUDED_MESSAGE = "This version of DoMe on your PC doesn't include Screen view yet."


@functools.cache
def media_stack_available() -> bool:
    """aiortc and PyAV can be imported in this build (found, not imported: the agent never loads the media stack;
    only the worker process does)."""
    try:
        return all(importlib.util.find_spec(name) is not None for name in ("aiortc", "av"))
    except (ImportError, ValueError):
        return False
