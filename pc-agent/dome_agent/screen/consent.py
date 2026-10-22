"""Consent, the post-Stop cooldown and DoMe's own permission UI (design §6.3, §6.4, ``rules.screen_sessions`` (5),
``rules.local_permission_ui``).

* :class:`PermissionUiState`: thread-safe. Which DoMe permission windows are open (pairing approval, grant window,
  consent window, a Stop confirmation) and which screen rectangles DoMe's own bars and windows occupy. The input
  dispatcher reads it: while a window is open every pointer button and keyboard event from every phone is dropped
  (``INPUT_PAUSED_ON_PC``), and a press that would land on a reserved rectangle is dropped (``INPUT_TARGET_RESERVED``).
  A phone therefore cannot click its own Allow.
* :class:`ConsentGate`: the policy. Ask mode is a per-grant local setting; the cooldown is PER PC (someone at the PC
  pressed Stop; it applies to every controller for the longest length among the PC's screen grants' settings). At
  most one consent window opens per minute per PC; after a Deny or a timeout every controller's starts are refused
  without a window for 1, 2, 5, then 10 minutes (reset by an Allow or by the cooldown ending); "Block this phone for
  1 hour" is a per-controller block. Time is injectable for tests.
* :class:`ConsentBroker`: opens the window through :class:`AgentUI` and waits at most
  ``screen_consent_timeout_seconds`` for the answer. Deny → ``SCREEN_CONSENT_DECLINED``; no answer →
  ``SCREEN_CONSENT_TIMEOUT``. *Disable remote control* withdraws a waiting window (``withdrawn``): it closes, the
  start is refused, and nothing counts as a refusal.
* :class:`GrantWindowModel` / :class:`ConsentWindowModel`: the pure button logic the tkinter windows follow (Allow
  enabled only after the 5 s / 1 s hold), unit-tested without a display.
"""

from __future__ import annotations

import asyncio
import contextlib
import secrets
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Literal

from dome_protocol import format_rfc3339, load_registry, now_utc, parse_rfc3339

from ..logsetup import get_logger
from ..ui import AgentUI, ConsentAction, ConsentCause, Rect, ScreenConsentRequest, ScreenGrantDecision

if TYPE_CHECKING:
    from ..store import Store

log = get_logger(__name__)

PermissionWindowKind = Literal["pairing_approval", "grant_window", "consent_window", "stop_confirmation"]
GateKind = Literal["not_required", "ask", "refused"]
RefusalCause = Literal["backoff", "blocked"]
WINDOW_INTERVAL_SECONDS = 60.0
BACKOFF_SECONDS: tuple[float, ...] = (60.0, 120.0, 300.0, 600.0)
BLOCK_SECONDS = 3600.0
GRANT_HOLD_SECONDS = 5.0
CONSENT_HOLD_SECONDS = 1.0
COOLDOWN_CHOICES: tuple[tuple[int, str], ...] = ((600, "10 minutes"), (60, "1 minute"), (0, "off"))


class PermissionUiState:
    """Open permission windows and reserved rectangles, readable from any thread.

    Windows the agent opens and closes itself (grant, consent) hold a token; a window whose life another component
    owns (the pairing approval: open while a request waits for the person at the PC, gone on approve, decline or
    expiry) is a registered probe that is asked each time. A probe that fails counts as an open window (fail closed:
    a phone press is dropped rather than injected into a DoMe window)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._open: dict[str, PermissionWindowKind] = {}
        self._reserved: dict[str, tuple[Rect, ...]] = {}
        self._probes: dict[PermissionWindowKind, Callable[[], bool]] = {}

    def open(self, kind: PermissionWindowKind) -> str:
        token = secrets.token_urlsafe(8)
        with self._lock:
            self._open[token] = kind
        return token

    def close(self, token: str) -> None:
        with self._lock:
            self._open.pop(token, None)

    def register_probe(self, kind: PermissionWindowKind, probe: Callable[[], bool]) -> None:
        """``probe()`` answers whether a window of ``kind`` is open right now (read-only, any thread)."""
        with self._lock:
            self._probes[kind] = probe

    def _probed_kinds(self) -> tuple[PermissionWindowKind, ...]:
        with self._lock:
            probes = list(self._probes.items())
        out: list[PermissionWindowKind] = []
        for kind, probe in probes:
            try:
                is_open = bool(probe())
            except Exception:  # noqa: BLE001 - fail closed: an unreadable window state counts as open
                is_open = True
            if is_open:
                out.append(kind)
        return tuple(out)

    @property
    def is_open(self) -> bool:
        with self._lock:
            if self._open:
                return True
        return bool(self._probed_kinds())

    def open_kinds(self) -> tuple[PermissionWindowKind, ...]:
        with self._lock:
            opened = tuple(self._open.values())
        return opened + self._probed_kinds()

    def set_reserved(self, owner: str, rects: Iterable[Rect]) -> None:
        with self._lock:
            self._reserved[owner] = tuple((int(a), int(b), int(c), int(d)) for a, b, c, d in rects)

    def clear_reserved(self, owner: str) -> None:
        with self._lock:
            self._reserved.pop(owner, None)

    def reserved_rects(self) -> tuple[Rect, ...]:
        with self._lock:
            return tuple(r for rects in self._reserved.values() for r in rects)

    def hit(self, x: int, y: int) -> bool:
        """Does a press at these physical pixels land on DoMe's own UI?"""
        return any(left <= x < right and top <= y < bottom for left, top, right, bottom in self.reserved_rects())


@dataclass(frozen=True, slots=True)
class GateDecision:
    kind: GateKind
    cause: ConsentCause | None = None  # why a window is needed (ask)
    refusal: RefusalCause | None = None  # why no window opens (refused)


class ConsentGate:
    def __init__(
        self,
        store: Store,
        *,
        now: Callable[[], float] = time.monotonic,
        window_interval: float = WINDOW_INTERVAL_SECONDS,
        backoff: tuple[float, ...] = BACKOFF_SECONDS,
        block_seconds: float = BLOCK_SECONDS,
    ) -> None:
        self._store = store
        self._now = now
        self.window_interval = window_interval
        self.backoff = backoff
        self.block_seconds = block_seconds
        self.default_cooldown = int(load_registry().limits["screen_pc_stop_cooldown_default_seconds"])
        self._last_window_at: float | None = None
        self._refusals = 0
        self._backoff_until = 0.0
        self._blocks: dict[str, float] = {}

    # ----- cooldown (persisted: a Stop at the desk must survive a restart) ----------------------------------------
    def cooldown_active(self) -> bool:
        until, _by = self._store.get_screen_cooldown()
        if until is None:
            return False
        if parse_rfc3339(until) <= now_utc():
            self._store.set_screen_cooldown(None, None)
            self._refusals = 0  # the cooldown ending resets the back-off (design §6.3)
            self._backoff_until = 0.0
            return False
        return True

    def cooldown_length(self, screen_controller_ids: Iterable[str]) -> int:
        """The longest cooldown setting among the PC's screen grants (default for grants without a row)."""
        lengths = [
            self._store.get_screen_grant_settings(cid, default_cooldown=self.default_cooldown).cooldown_seconds
            for cid in screen_controller_ids
        ]
        return max(lengths, default=self.default_cooldown)

    def note_stop_on_pc(self, stopped_controller_id: str, screen_controller_ids: Iterable[str]) -> int:
        """Someone at the PC pressed Stop: start the PC-wide cooldown. Returns its length in seconds (0 = off)."""
        length = self.cooldown_length(screen_controller_ids)
        if length <= 0:
            self._store.set_screen_cooldown(None, None)
            return 0
        self._store.set_screen_cooldown(format_rfc3339(now_utc() + timedelta(seconds=length)), stopped_controller_id)
        return length

    # ----- the decision ------------------------------------------------------------------------------------------
    def decide(self, controller_id: str) -> GateDecision:
        now = self._now()
        block = self._blocks.get(controller_id)
        if block is not None:
            if now < block:
                return GateDecision("refused", refusal="blocked")
            del self._blocks[controller_id]
        cooldown = self.cooldown_active()
        ask = self._store.get_screen_grant_settings(controller_id, default_cooldown=self.default_cooldown).ask_each_time
        if not ask and not cooldown:
            return GateDecision("not_required")
        if now < self._backoff_until:
            return GateDecision("refused", refusal="backoff")
        if self._last_window_at is not None and now - self._last_window_at < self.window_interval:
            return GateDecision("refused", refusal="backoff")  # one consent window per minute per PC
        return GateDecision("ask", cause="cooldown" if cooldown else "ask_mode")

    def note_window_opened(self) -> None:
        self._last_window_at = self._now()

    def note_outcome(self, controller_id: str, outcome: ConsentAction | Literal["timeout"]) -> None:
        now = self._now()
        if outcome == "allow":
            self._refusals = 0
            self._backoff_until = 0.0
            return
        if outcome == "block":
            self._blocks[controller_id] = now + self.block_seconds
            return
        # deny, timeout and "turn off": consecutive refusals escalate the no-window interval for EVERY controller
        self._refusals += 1
        self._backoff_until = now + self.backoff[min(self._refusals, len(self.backoff)) - 1]

    def blocked_until(self, controller_id: str) -> float | None:
        return self._blocks.get(controller_id)

    def summary(self) -> dict[str, object]:
        until, by = self._store.get_screen_cooldown()
        return {
            "cooldown_until": until if self.cooldown_active() else None,
            "cooldown_by": by if until else None,
            "consecutive_refusals": self._refusals,
            "backoff_remaining_seconds": max(0, int(self._backoff_until - self._now())),
            "blocked_controllers": sorted(cid for cid, t in self._blocks.items() if t > self._now()),
        }


ConsentOutcome = Literal["allow", "deny", "block", "turn_off", "timeout", "withdrawn"]


class ConsentBroker:
    """Opens the consent window and waits for the person at the PC (design §2.3)."""

    def __init__(self, ui: AgentUI, permission_ui: PermissionUiState, *, timeout_seconds: float | None = None) -> None:
        self._ui = ui
        self._permission_ui = permission_ui
        self.timeout_seconds = float(
            timeout_seconds if timeout_seconds is not None else load_registry().limits["screen_consent_timeout_seconds"]
        )
        self.asked = 0
        self._waiting: set[asyncio.Future[ConsentOutcome]] = set()

    def withdraw_all(self) -> int:
        """An emergency stop: every waiting window answers ``withdrawn`` (and is closed by :meth:`ask`)."""
        withdrawn = 0
        for future in list(self._waiting):
            if not future.done():
                future.set_result("withdrawn")
                withdrawn += 1
        return withdrawn

    async def ask(self, *, controller_id: str, display_name: str, cause: ConsentCause) -> ConsentOutcome:
        loop = asyncio.get_running_loop()
        done: asyncio.Future[ConsentOutcome] = loop.create_future()

        def respond(action: ConsentAction) -> None:
            def _set() -> None:
                if not done.done():
                    done.set_result(action)

            loop.call_soon_threadsafe(_set)

        request = ScreenConsentRequest(
            request_id=secrets.token_urlsafe(8),
            controller_id=controller_id,
            display_name=display_name,
            cause=cause,
            timeout_seconds=int(self.timeout_seconds),
            respond=respond,
        )
        token = self._permission_ui.open("consent_window")
        self.asked += 1
        self._waiting.add(done)
        try:
            self._ui.show_screen_consent(request)
            try:
                outcome = await asyncio.wait_for(done, self.timeout_seconds)
            except TimeoutError:
                with contextlib.suppress(Exception):
                    self._ui.close_screen_consent(request.request_id)
                return "timeout"
            except asyncio.CancelledError:
                # the start was abandoned (command timeout, revocation): the window must not outlive it
                with contextlib.suppress(Exception):
                    self._ui.close_screen_consent(request.request_id)
                raise
            if outcome == "withdrawn":
                with contextlib.suppress(Exception):
                    self._ui.close_screen_consent(request.request_id)
            return outcome
        finally:
            self._waiting.discard(done)
            self._permission_ui.close(token)


class GrantWindowModel:
    """Allow is enabled only after a 5 s hold (design §2.1: "a one-time cost on a rare action")."""

    def __init__(self, *, opened_at: float, hold_seconds: float = GRANT_HOLD_SECONDS) -> None:
        self.opened_at = opened_at
        self.hold_seconds = hold_seconds

    def allow_enabled(self, now: float) -> bool:
        return now - self.opened_at >= self.hold_seconds

    def hold_remaining(self, now: float) -> int:
        return max(0, int(self.hold_seconds - (now - self.opened_at) + 0.999))

    def decide(
        self, now: float, *, screen_audio: bool, ask_each_time: bool, cooldown_seconds: int
    ) -> ScreenGrantDecision:
        if not self.allow_enabled(now):
            raise ValueError("Allow is not enabled yet")
        if cooldown_seconds not in {c for c, _ in COOLDOWN_CHOICES}:
            raise ValueError("unknown cooldown choice")
        return ScreenGrantDecision(
            screen_audio=screen_audio, ask_each_time=ask_each_time, cooldown_seconds=cooldown_seconds
        )


class ConsentWindowModel:
    """Allow is enabled after 1 s; the window answers "timeout" when nobody acts within the limit."""

    def __init__(self, *, opened_at: float, timeout_seconds: float, hold_seconds: float = CONSENT_HOLD_SECONDS) -> None:
        self.opened_at = opened_at
        self.timeout_seconds = timeout_seconds
        self.hold_seconds = hold_seconds

    def allow_enabled(self, now: float) -> bool:
        return now - self.opened_at >= self.hold_seconds

    def remaining(self, now: float) -> int:
        return max(0, int(self.timeout_seconds - (now - self.opened_at) + 0.999))

    def expired(self, now: float) -> bool:
        return now - self.opened_at >= self.timeout_seconds


__all__ = [
    "BACKOFF_SECONDS",
    "BLOCK_SECONDS",
    "COOLDOWN_CHOICES",
    "ConsentBroker",
    "ConsentGate",
    "ConsentOutcome",
    "ConsentWindowModel",
    "GateDecision",
    "GrantWindowModel",
    "PermissionUiState",
    "PermissionWindowKind",
]
