"""TEST DOUBLE: an :class:`AgentUI` that records everything and lets a test play the person at the PC.

Bars: ``bars_ok`` decides whether "Screen view on" bars can be created (False models a UI without a display: the
start must then fail closed), ``bars_delay`` how long the confirmation takes (3 s and more models a frozen UI
thread), ``bars_present`` what a re-check answers (set False to simulate a monitor change that lost a bar),
``ui_frozen`` a UI thread whose heartbeat stopped (its checks go stale: no bar counts as present, no tick is sent,
and new bars are never confirmed), ``bars_misplaced`` bars whose real rectangle is not on their monitor (the tray's
placement check: a start is refused, a live bar that moves off its monitor counts as missing).
Consent and grant windows are recorded; the test answers them with :meth:`answer_consent` / :meth:`answer_grant`.
"""

from __future__ import annotations

import threading
from concurrent.futures import Future
from dataclasses import dataclass, field

from ..ui import (
    ConsentAction,
    PairingApproval,
    PairingDisplay,
    ScreenBarRequest,
    ScreenBars,
    ScreenConsentRequest,
    ScreenGrantDecision,
    ScreenGrantRequest,
    StatusView,
)


@dataclass
class FakeScreenUI:
    bars_ok: bool = True
    bars_delay: float = 0.0
    bars_present: bool = True
    ui_frozen: bool = False
    bars_misplaced: bool = False
    bar_height: int = 40
    notifications: list[tuple[str, str]] = field(default_factory=list)
    statuses: list[StatusView] = field(default_factory=list)
    bar_requests: list[ScreenBarRequest] = field(default_factory=list)
    hidden: int = 0
    consent_requests: list[ScreenConsentRequest] = field(default_factory=list)
    closed_consents: list[str] = field(default_factory=list)
    grant_requests: list[ScreenGrantRequest] = field(default_factory=list)
    pairing_requests: list[PairingApproval] = field(default_factory=list)
    bars_shown: bool = False

    # ----- AgentUI -----
    def show_pairing_code(self, display: PairingDisplay) -> None:
        return None

    def show_pairing_request(self, approval: PairingApproval) -> None:
        self.pairing_requests.append(approval)

    def pairing_finished(self, pairing_id: str, decision: str) -> None:
        return None

    def notify(self, title: str, message: str) -> None:
        self.notifications.append((title, message))

    def update_status(self, status: StatusView) -> None:
        self.statuses.append(status)

    def show_main(self) -> None:
        return None

    def show_screen_grant(self, request: ScreenGrantRequest) -> None:
        self.grant_requests.append(request)

    def show_screen_consent(self, request: ScreenConsentRequest) -> None:
        self.consent_requests.append(request)

    def close_screen_consent(self, request_id: str) -> None:
        self.closed_consents.append(request_id)

    def show_screen_bars(self, request: ScreenBarRequest) -> Future[ScreenBars]:
        self.bar_requests.append(request)
        done: Future[ScreenBars] = Future()
        reserved = tuple((m.left, m.top, m.right, m.top + self.bar_height) for m in request.monitors)

        def resolve() -> None:
            if done.done() or self.ui_frozen:  # the agent gave up waiting (a frozen UI) and cancelled it
                return
            if self.bars_misplaced:
                done.set_result(ScreenBars(created=False, detail="bar not on its monitor"))
            elif self.bars_ok:
                self.bars_shown = True
                done.set_result(ScreenBars(created=True, reserved=reserved))
            else:
                done.set_result(ScreenBars(created=False, detail="fake UI refuses to create a window"))

        if self.bars_delay > 0:
            threading.Timer(self.bars_delay, resolve).start()
        else:
            resolve()
        return done

    def hide_screen_bars(self) -> None:
        self.hidden += 1
        self.bars_shown = False

    def screen_bars_present(self) -> bool:
        return self.bars_shown and self.bars_present and not self.ui_frozen and not self.bars_misplaced

    def screen_ui_alive(self) -> bool:
        return not self.ui_frozen

    # ----- the person at the PC -----
    def answer_consent(self, action: ConsentAction, index: int = -1) -> None:
        self.consent_requests[index].respond(action)

    def answer_grant(self, decision: ScreenGrantDecision | None, index: int = -1) -> None:
        self.grant_requests[index].respond(decision)

    def press_stop_on_bar(self) -> None:
        self.bar_requests[-1].on_stop("bar")

    def press_hotkey(self) -> None:
        self.bar_requests[-1].on_stop("hotkey")

    def press_turn_off_on_bar(self) -> None:
        self.bar_requests[-1].on_turn_off()


__all__ = ["FakeScreenUI"]
