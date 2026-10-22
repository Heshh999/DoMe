"""User-interface seam. The agent core talks to an :class:`AgentUI`; implementations are the tray +
tkinter windows (:mod:`dome_agent.tray`, Windows), a console presenter used by the CLI, and
:class:`NullUI` for headless runs where the CLI drives pairing over the control channel.

Screen view (protocol 1.4, design §2.1, §2.3, §7) adds the grant window, the consent window and the "Screen view
on" bars. Windows answer through the ``respond`` / ``on_stop`` callables carried by their request (the agent wraps
them so they may be called from any thread). The bars are the fail-closed indicator: :meth:`AgentUI.show_screen_bars`
returns a future the agent waits on for at most 3 s, and a UI that cannot create them (no display, no tkinter,
:class:`NullUI`) answers ``created: False`` so nothing is ever captured without a bar.

Nothing here logs: pairing codes and verification codes are shown to the local user only."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass, field
from typing import Literal, Protocol

from .platform.protocol import Monitor

Rect = tuple[int, int, int, int]  # left, top, right, bottom in physical virtual-desktop pixels
ConsentAction = Literal["allow", "deny", "block", "turn_off"]
ConsentCause = Literal["ask_mode", "cooldown"]


@dataclass(frozen=True, slots=True)
class PairingDisplay:
    """What the PC shows while waiting for the phone: formatted code, QR payload, expiry."""

    pairing_id: str
    code_formatted: str
    qr_url: str
    expires_at: str


@dataclass(frozen=True, slots=True)
class PairingApproval:
    """A phone claimed the code: show its (untrusted) name, the 6 digits and the requested scopes."""

    pairing_id: str
    controller_display_name: str
    verification_code: str
    requested_capabilities: tuple[str, ...]
    kid: str
    expires_at: str
    # screen / screen_audio the phone asked for: shown unticked and greyed, "add later from Paired phones" (1.4)
    deferred_capabilities: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ScreenGrantDecision:
    """What the owner chose in the grant window when pressing Allow (design §2.1 options)."""

    screen_audio: bool = False
    ask_each_time: bool = False
    cooldown_seconds: int = 600  # 600 | 60 | 0 (off)


@dataclass(frozen=True, slots=True)
class ScreenGrantRequest:
    """Open the Screen view grant window for one paired phone (tray "Allow Screen view…", `grant --screen`).
    ``respond(None)`` is Cancel or the window closing; ``respond(decision)`` is Allow after the 5 s hold."""

    request_id: str
    controller_id: str
    display_name: str
    screen: bool  # already granted (the window then only changes the options)
    screen_audio: bool
    ask_each_time: bool
    cooldown_seconds: int
    respond: Callable[[ScreenGrantDecision | None], None]


@dataclass(frozen=True, slots=True)
class ScreenConsentRequest:
    """ "<phone> wants to see this screen" (design §2.3): Deny, Allow (after 1 s), Block this phone for 1 hour,
    Turn off Screen view for this phone. No answer within ``timeout_seconds`` is a timeout (the agent closes it)."""

    request_id: str
    controller_id: str
    display_name: str
    cause: ConsentCause
    timeout_seconds: int
    respond: Callable[[ConsentAction], None]


@dataclass(frozen=True, slots=True)
class ScreenBarRequest:
    """The "Screen view on" bars, one per monitor, created and confirmed BEFORE the worker starts (design §7).
    ``on_stop(source)`` is the bar's Stop (``"bar"``) or the hotkey (``"hotkey"``); ``on_turn_off`` is "Stop and turn
    off Screen view for this phone"."""

    viewer_name: str
    audio: bool
    monitors: tuple[Monitor, ...]
    on_stop: Callable[[str], None]
    on_turn_off: Callable[[], None]


@dataclass(frozen=True, slots=True)
class ScreenBars:
    """The UI's answer: whether a bar exists on every monitor, the bars' rectangles in physical pixels (reserved
    for taps, rules.local_permission_ui) and, when not created, why (``detail`` goes into
    ``SCREEN_CAPTURE_UNAVAILABLE{detail: indicator}``'s log line, never to the phone)."""

    created: bool
    reserved: tuple[Rect, ...] = ()
    detail: str = ""


@dataclass(frozen=True, slots=True)
class ScreenSessionView:
    """``StatusView.screen_session``: who is watching since when (tray tooltip, badge, status window)."""

    phone_name: str
    since: str
    audio: bool


@dataclass(slots=True)
class StatusView:
    connection: str = "offline"  # offline | connecting | connected | reconnecting | superseded | stopped
    remote_enabled: bool = False
    linked: bool = False
    relink_required: bool = False
    pc_name: str = ""
    extension_connected: bool = False
    notes: list[str] = field(default_factory=list)
    screen_session: ScreenSessionView | None = None  # a Screen view is live (red badge, "Stop Screen view")


class AgentUI(Protocol):
    def show_pairing_code(self, display: PairingDisplay) -> None: ...

    def show_pairing_request(self, approval: PairingApproval) -> None: ...

    def pairing_finished(self, pairing_id: str, decision: str) -> None: ...

    def notify(self, title: str, message: str) -> None: ...

    def update_status(self, status: StatusView) -> None: ...

    def show_main(self) -> None:
        """A second launch asked the running instance to come to the front (spec §10 single instance)."""
        ...

    # ----- Screen view (1.4) -----
    def show_screen_grant(self, request: ScreenGrantRequest) -> None:
        """Open the grant window; the UI answers through ``request.respond``. A UI without windows cancels."""
        ...

    def show_screen_consent(self, request: ScreenConsentRequest) -> None:
        """Open the consent window; the UI answers through ``request.respond`` (or never: the agent times out)."""
        ...

    def close_screen_consent(self, request_id: str) -> None:
        """The agent timed the request out or the session was refused meanwhile: take the window down."""
        ...

    def show_screen_bars(self, request: ScreenBarRequest) -> Future[ScreenBars]:
        """Create a bar on every monitor and the tray badge; the future resolves once every window exists (or
        says why not). The agent spawns the worker only after ``created`` is True."""
        ...

    def hide_screen_bars(self) -> None:
        """Close the bars and clear the badge (called only after the worker has exited)."""
        ...

    def screen_bars_present(self) -> bool:
        """Re-check (after a display change): does a bar still exist on every monitor? False as well when the UI
        thread's own check has not run for ~3 s (a frozen thread's bars no longer repaint)."""
        ...

    def screen_ui_alive(self) -> bool:
        """Is the thread that owns the bars still processing events? The worker's liveness ticks are sent only while
        it is (design §7), so a frozen UI thread stops capture within the worker's 10 s tick timeout."""
        ...


class NullUI:
    """Headless: everything is reachable through the control channel / CLI instead. Screen view cannot run
    headless: with no window to show there is no indicator, so the bars report ``created: False`` and a start is
    refused ``SCREEN_CAPTURE_UNAVAILABLE{detail: indicator}`` (design §7, fail-closed)."""

    def show_pairing_code(self, display: PairingDisplay) -> None:
        return None

    def show_pairing_request(self, approval: PairingApproval) -> None:
        return None

    def pairing_finished(self, pairing_id: str, decision: str) -> None:
        return None

    def notify(self, title: str, message: str) -> None:
        return None

    def update_status(self, status: StatusView) -> None:
        return None

    def show_main(self) -> None:
        return None

    def show_screen_grant(self, request: ScreenGrantRequest) -> None:
        request.respond(
            None
        )  # no window: widening screen / screen_audio is impossible here (rules.local_permission_ui)

    def show_screen_consent(self, request: ScreenConsentRequest) -> None:
        return None  # nobody can answer: the agent reports SCREEN_CONSENT_TIMEOUT

    def close_screen_consent(self, request_id: str) -> None:
        return None

    def show_screen_bars(self, request: ScreenBarRequest) -> Future[ScreenBars]:
        done: Future[ScreenBars] = Future()
        done.set_result(ScreenBars(created=False, detail="headless: no display for the Screen view bar"))
        return done

    def hide_screen_bars(self) -> None:
        return None

    def screen_bars_present(self) -> bool:
        return False

    def screen_ui_alive(self) -> bool:
        return False  # no UI thread, no bars: nothing may be captured


def qr_ascii(url: str) -> str:
    """Render the QR payload as ASCII blocks for a console (never logged)."""
    import io

    import qrcode

    qr = qrcode.QRCode(border=1, error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(url)
    qr.make(fit=True)
    out = io.StringIO()
    qr.print_ascii(out=out, invert=True)
    return out.getvalue()
