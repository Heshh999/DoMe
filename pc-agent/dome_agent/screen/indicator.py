"""The fail-closed indicator (design §7, ``rules.screen_sessions`` (2)).

Before the worker starts, the agent asks the UI for a "Screen view on" bar on EVERY monitor and the tray badge and
waits at most ``confirm_timeout`` (3 s) for the UI to confirm that each window exists. No display, no tkinter, no
answer in time or a window that could not be created: the start is ``SCREEN_CAPTURE_UNAVAILABLE{detail: indicator}``
and nothing is captured. The bars' rectangles (physical pixels) are registered as reserved so that taps which would
land on them are dropped (``rules.local_permission_ui``) and are sent to the phone, normalised to the streamed
monitor, in the ``geometry`` message. After a display change the session manager re-checks ``present()``; the bars'
tkinter implementation and the capture exclusion are **not yet verified on a device**.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable
from typing import Any

from dome_protocol import ProtocolError, load_registry

from ..logsetup import get_logger
from ..platform.protocol import Monitor
from ..ui import AgentUI, Rect, ScreenBarRequest, ScreenBars
from .consent import PermissionUiState

log = get_logger(__name__)

CONFIRM_TIMEOUT_SECONDS = 3.0
RESERVED_OWNER = "screen_bars"
NORMALISED_MAX = 65535


def normalise_rects(rects: Iterable[Rect], monitor: Monitor) -> list[dict[str, int]]:
    """Bars intersected with ``monitor``, as ``screen_rect`` (0..65535 over the streamed surface). Empty
    intersections are left out; at most 8 rectangles (the schema's limit)."""
    out: list[dict[str, int]] = []
    for left, top, right, bottom in rects:
        x0, y0 = max(left, monitor.left), max(top, monitor.top)
        x1, y1 = min(right, monitor.right), min(bottom, monitor.bottom)
        if x1 <= x0 or y1 <= y0:
            continue
        sx = NORMALISED_MAX / max(1, monitor.width - 1)
        sy = NORMALISED_MAX / max(1, monitor.height - 1)
        nx = min(NORMALISED_MAX, round((x0 - monitor.left) * sx))
        ny = min(NORMALISED_MAX, round((y0 - monitor.top) * sy))
        nw = min(NORMALISED_MAX - nx, round((x1 - x0) * sx))
        nh = min(NORMALISED_MAX - ny, round((y1 - y0) * sy))
        out.append({"x": nx, "y": ny, "w": nw, "h": nh})
        if len(out) == 8:
            break
    return out


class IndicatorController:
    def __init__(
        self, ui: AgentUI, permission_ui: PermissionUiState, *, confirm_timeout: float = CONFIRM_TIMEOUT_SECONDS
    ) -> None:
        self._ui = ui
        self._permission_ui = permission_ui
        self.confirm_timeout = confirm_timeout
        self._armed = False
        self.reserved: tuple[Rect, ...] = ()
        self.monitors: tuple[Monitor, ...] = ()  # the monitor set the bars were created for (re-checked while live)

    @property
    def armed(self) -> bool:
        return self._armed

    async def arm(
        self,
        *,
        viewer_name: str,
        audio: bool,
        monitors: Iterable[Monitor],
        on_stop: Callable[[str], None],
        on_turn_off: Callable[[], None],
    ) -> ScreenBars:
        """Create and confirm the bars; raise ``SCREEN_CAPTURE_UNAVAILABLE{detail: indicator}`` otherwise."""
        monitor_list = tuple(monitors)
        if not monitor_list:
            raise self._unavailable("no monitor to show the bar on")
        request = ScreenBarRequest(
            viewer_name=viewer_name, audio=audio, monitors=monitor_list, on_stop=on_stop, on_turn_off=on_turn_off
        )
        try:
            future = self._ui.show_screen_bars(request)
        except Exception as exc:  # noqa: BLE001 - a UI that raises is a UI without bars
            raise self._unavailable(f"bars not created ({exc.__class__.__name__})") from None
        try:
            bars = await asyncio.wait_for(asyncio.wrap_future(future), self.confirm_timeout)
        except TimeoutError:
            self._hide_quietly()
            raise self._unavailable("the UI did not confirm the bars within 3 s") from None
        except Exception as exc:  # noqa: BLE001
            self._hide_quietly()
            raise self._unavailable(f"bars failed ({exc.__class__.__name__})") from None
        if not bars.created:
            self._hide_quietly()
            raise self._unavailable(bars.detail or "a bar could not be created")
        self._armed = True
        self.monitors = monitor_list
        self.reserved = tuple(bars.reserved)
        self._permission_ui.set_reserved(RESERVED_OWNER, self.reserved)
        return bars

    def present(self) -> bool:
        """Does a bar still exist on every monitor the bars were created for, and is the thread that owns them still
        answering? (The UI answers False when its last check is stale: a frozen thread's bars no longer repaint. The
        session manager also compares the monitor set with a fresh enumeration: a monitor plugged in since has no
        bar.)"""
        if not self._armed:
            return False
        try:
            return bool(self._ui.screen_bars_present())
        except Exception:  # noqa: BLE001 - an unreadable UI is a missing bar: fail closed
            return False

    def ui_alive(self) -> bool:
        """The liveness probe the worker's ticks need (design §7): the UI thread ran its bar check recently."""
        try:
            return bool(self._ui.screen_ui_alive())
        except Exception:  # noqa: BLE001 - an unreadable UI is a frozen one: fail closed
            return False

    def disarm(self) -> None:
        """Close the bars (only once the worker has exited, design §7) and clear the reserved rectangles."""
        self._armed = False
        self.reserved = ()
        self.monitors = ()
        self._permission_ui.clear_reserved(RESERVED_OWNER)
        self._hide_quietly()

    def reserved_for(self, monitor: Monitor) -> list[dict[str, int]]:
        return normalise_rects(self.reserved, monitor)

    def _hide_quietly(self) -> None:
        try:
            self._ui.hide_screen_bars()
        except Exception as exc:  # noqa: BLE001
            log.debug("hiding the screen bars failed", error=exc.__class__.__name__)

    @staticmethod
    def _unavailable(why: str) -> ProtocolError:
        log.warning("Screen view refused: the indicator could not be shown", why=why)
        err: ProtocolError = load_registry().make_error("SCREEN_CAPTURE_UNAVAILABLE", detail="indicator")
        return err


def monitors_summary(monitors: Iterable[Monitor]) -> list[dict[str, Any]]:
    return [m.as_surface() for m in monitors]


__all__ = ["CONFIRM_TIMEOUT_SECONDS", "IndicatorController", "monitors_summary", "normalise_rects"]
