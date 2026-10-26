"""Screen view sessions (design §7, §4.2 step 5, §6.3, §10; ``rules.screen_sessions``).

One live :class:`ScreenSession` per PC, copying :class:`~dome_agent.input_session.InputSessionManager`'s shape: a
22-character id, a watchdog task independent of the relay socket, a fixed end order. The phone's signed
``screen.session_start`` reaches :meth:`ScreenSessionManager.start_session` only after the authorizer verified it
against the LOCAL grant; here the checks run in the design's order: remote control on → the Windows session unlocked
and not on the secure desktop → ``relay.screen`` present and valid (the first allowance ticket) → the strict SDP check
→ ownership (``SCREEN_SESSION_OWNED`` unless ``takeover``) → consent (ask mode, the PC-wide post-Stop cooldown, the
back-off and the 1-hour block) → the monitors → the old session ends (``takeover`` / ``replaced``) → a "Screen view
on" bar on EVERY monitor, confirmed (fail-closed: ``SCREEN_CAPTURE_UNAVAILABLE{detail: indicator}``) → only then the
worker is spawned and its send-only answer signed with the PC's identity key and sent in a ``screen_answer`` frame,
never in the result.

While live: the phone's ``hb`` on the data channel (forwarded by the worker) renews the 8 s viewer lease; the agent
ASKS for allowance tickets (``screen_usage`` about 20 s before ``granted_until``, at most twice a minute) and ends
the session when a ticket passes unanswered (``allowance_exhausted`` / ``controller_offline`` / ``plan_disabled`` /
``allowance_unconfirmed``: the dead-man switch); a lock ends it, a secure desktop pauses it (ended after 120 s), a
missing bar pauses it (ended after 10 s), 4 h ends it, no DTLS within 20 s is ``connection_failed``. Pause causes are
a set: capture resumes only once the last one (a secure desktop, a missing bar) has gone, and each cause is timed on
its own. Every stop works offline. End order: retire the id → ``stop`` to the worker, wait ≤ 2 s, terminate → close
the bars only once the worker has exited → ``screen_session{ended}`` → notification and history row → (the relay
revokes the phone's TURN credential when it sees ``ended``). The first step is synchronous (:meth:`_retire`); an end
the worker asks for (``bye``, ``disconnected``, a late ``error``, its exit) runs on a task of its own, never on the
supervisor's reader task that delivered the event (the stop waits for that reader).

Content hygiene (``rules.screen_content``): SDP exists in memory and on the private pipe only; frames, results, the
history and the recovery file carry ids, geometry, flags, reasons and seconds.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import secrets
import tempfile
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from dome_protocol import ProtocolError, format_rfc3339, load_registry, now_utc, parse_rfc3339

from ..frames import screen_answer_frame, screen_session_frame, screen_usage_frame
from ..logsetup import get_logger
from ..platform.protocol import Monitor, PlatformSet
from ..ui import AgentUI, ScreenSessionView
from . import NOT_INCLUDED_MESSAGE, SCREEN_VIEW_NOT_INCLUDED, media_stack_available
from .consent import ConsentBroker, ConsentGate, PermissionUiState
from .history import ScreenHistory
from .indicator import IndicatorController
from .sdp_check import check_offer
from .signing import sign_screen_answer
from .supervisor import WorkerLauncher, WorkerSupervisor

if TYPE_CHECKING:
    from ..identity import Identity
    from ..store import Store

log = get_logger(__name__)

Sender = Callable[[dict[str, Any]], Awaitable[bool]]
SessionState = Literal["starting", "live", "ended"]
EndReason = Literal[
    "stopped",
    "stopped_on_pc",
    "replaced",
    "viewer_gone",
    "takeover",
    "controller_revoked",
    "controller_offline",
    "grant_removed",
    "plan_disabled",
    "remote_disabled",
    "session_locked",
    "secure_desktop",
    "allowance_exhausted",
    "allowance_unconfirmed",
    "max_duration",
    "capture_failed",
    "connection_failed",
    "agent_restart",
]
PauseReason = Literal["secure_desktop", "indicator"]
RECOVERY_FILENAME = "screen_session.json"
RETIRED_IDS_KEPT = 64
TICKETS_KEPT = 32
TICKET_MAX_AGE_SECONDS = 120.0
USAGE_MIN_INTERVAL_SECONDS = 30.0  # at most twice per minute per session
INDICATOR_GRACE_SECONDS = 10.0
WORKER_START_TIMEOUT_SECONDS = 15.0
# The start must answer inside the command's own timeout (actions.json screen.session_start timeout_ms, 45 s): a
# consent wait (30 s) plus the bars (3 s) plus the worker would otherwise overrun it, and the executor would cancel
# the handler with a generic "did not complete". The worker's answer wait is cut to what is left of the budget.
START_BUDGET_MARGIN_SECONDS = 3.0
TICK_TIMEOUT_SECONDS = 10.0  # the worker exits after this long without an agent tick (design §7)
NORMALISED_MAX = 65535
BITRATE_KBPS = {"direct": 1500, "relayed": 1000, "data_saver": 500}  # design §5.3
STOP_HINT = "Stop: tray > Stop Screen view, or Ctrl+Alt+Shift+S"


def new_session_id() -> str:
    """22 base64url characters (16 random bytes), matching ``^[A-Za-z0-9_-]{22}$``."""
    return secrets.token_urlsafe(16)


def map_to_pixels(monitor: Monitor, x: int, y: int) -> tuple[int, int]:
    """``pointer_move_to`` (0..65535 over the streamed monitor) → physical virtual-desktop pixels (design §8.2):
    ``px = left + round(x · (width − 1) / 65535)``. Pure; unit-tested on negative origins and mixed DPI layouts."""
    px = monitor.left + round(max(0, min(NORMALISED_MAX, int(x))) * (monitor.width - 1) / NORMALISED_MAX)
    py = monitor.top + round(max(0, min(NORMALISED_MAX, int(y))) * (monitor.height - 1) / NORMALISED_MAX)
    return px, py


def valid_ticket(ticket: Any) -> bool:
    """``relay.screen`` as the relay attaches it to a forwarded ``screen.session_start``."""
    if not isinstance(ticket, dict) or not isinstance(ticket.get("unlimited"), bool):
        return False
    until = ticket.get("granted_until")
    if until is None:
        return bool(ticket["unlimited"])
    if not isinstance(until, str):
        return False
    try:
        parse_rfc3339(until)
    except (ValueError, TypeError):
        return False
    return isinstance(ticket.get("stun_urls"), list)


def _duration_text(seconds: int) -> str:
    minutes, rest = divmod(max(0, int(seconds)), 60)
    return f"{minutes} min {rest} s" if minutes else f"{rest} s"


@dataclass(slots=True)
class ScreenSession:
    screen_session_id: str
    controller_id: str
    kid: str
    display_name: str
    command_id: str
    monitor: Monitor
    surfaces: tuple[Monitor, ...]
    consent: str  # not_required | granted
    started_at: float  # monotonic, when the worker was asked to start
    started_wall: str
    state: SessionState = "starting"
    gen: int = 1
    gens: deque[tuple[int, Monitor]] = field(default_factory=lambda: deque(maxlen=4))
    encoder: str | None = None
    connected: bool = False
    first_frame: bool = False
    path: str | None = None
    relayed: bool = False
    relayed_bytes: int = 0
    lease_expires_at: float = 0.0
    live_since: float | None = None
    accumulated: float = 0.0
    # Active pause causes and when each began (design §7): capture runs only while this is empty; the secure-desktop
    # limit and the missing-bar grace are timed per cause.
    pauses: dict[str, float] = field(default_factory=dict)
    indicator_missing_since: float | None = None
    # A rebuild or a new reserved band was asked of the worker and its new `geometry` has not arrived yet: taps are
    # refused SCREEN_GEOMETRY_CHANGED meanwhile (the phone re-aims on the new picture).
    geometry_pending: bool = False
    geometry_pending_since: float = 0.0
    reserved: list[dict[str, int]] = field(default_factory=list)  # the bars' band over the streamed monitor
    started_emitted: bool = False
    end_reason: str | None = None
    # allowance tickets (rules.screen_sessions (4))
    unlimited: bool = False
    ticket_deadline: float = 0.0  # monotonic
    final: bool = False
    final_reason: str | None = None
    resets_at: str | None = None
    last_usage_at: float | None = None
    answer_future: asyncio.Future[dict[str, Any]] | None = None
    # Until the start path has processed the worker's answer it owns the clean-up: worker events and exits only feed
    # the future (or `start_error`), so a start that fails is answered once and its bars are closed once.
    start_complete: bool = False
    start_error: ProtocolError | None = None
    # The rest of the end order (worker, bars, frame, history) has been claimed by `_end` or `_abort_start`.
    closed: bool = False

    @property
    def live(self) -> bool:
        return self.state != "ended"

    @property
    def paused_reason(self) -> str | None:
        """One active pause cause for status lines (a secure desktop first), or None while capturing."""
        if not self.pauses:
            return None
        return "secure_desktop" if "secure_desktop" in self.pauses else next(iter(self.pauses))

    def live_seconds(self, now: float | None = None) -> int:
        now = time.monotonic() if now is None else now
        total = self.accumulated + ((now - self.live_since) if self.live_since is not None else 0.0)
        return int(total)

    def monitor_for_gen(self, gen: int) -> Monitor | None:
        for known, monitor in self.gens:
            if known == gen:
                return monitor
        return None

    def view(self) -> ScreenSessionView:
        return ScreenSessionView(phone_name=self.display_name, since=self.started_wall, audio=False)


class ScreenSessionManager:
    def __init__(
        self,
        *,
        platform: PlatformSet,
        store: Store,
        identity: Callable[[], Identity],
        send: Sender,
        ui: AgentUI,
        launcher: WorkerLauncher,
        permission_ui: PermissionUiState,
        state_dir: Path,
        session_locked: Callable[[], Awaitable[bool]],
        remote_enabled: Callable[[], bool],
        snapshot_received: Callable[[], bool],
        on_state_change: Callable[[], None],
        on_turn_off: Callable[[str], Awaitable[None]],
        security_event: Callable[[str, dict[str, Any]], None],
        lease_seconds: float | None = None,
        connect_timeout_seconds: float | None = None,
        max_seconds: float | None = None,
        renew_before_seconds: float | None = None,
        chunk_seconds: float | None = None,
        secure_pause_max_seconds: float | None = None,
        consent_timeout_seconds: float | None = None,
        indicator_grace_seconds: float = INDICATOR_GRACE_SECONDS,
        watchdog_interval: float = 0.1,
        lock_poll_seconds: float = 1.0,
        indicator_poll_seconds: float = 1.0,
        worker_start_timeout: float = WORKER_START_TIMEOUT_SECONDS,
        start_budget_seconds: float | None = None,
        tick_interval: float = 3.0,
        tick_timeout_seconds: float = TICK_TIMEOUT_SECONDS,
        stop_grace: float = 2.0,
        source: dict[str, Any] | None = None,
        media_available: Callable[[], bool] = media_stack_available,
    ) -> None:
        limits = load_registry().limits
        self._media_available = media_available
        self._platform = platform
        self._store = store
        self._identity = identity
        self._send = send
        self._ui = ui
        self._permission_ui = permission_ui
        self._recovery_path = state_dir / RECOVERY_FILENAME
        self._session_locked = session_locked
        self._remote_enabled = remote_enabled
        self._snapshot_received = snapshot_received
        self._on_state_change = on_state_change
        self._on_turn_off = on_turn_off
        self._security_event = security_event
        self.lease_seconds = float(lease_seconds if lease_seconds is not None else limits["screen_lease_seconds"])
        self.connect_timeout = float(
            connect_timeout_seconds if connect_timeout_seconds is not None else limits["screen_connect_timeout_seconds"]
        )
        self.max_seconds = float(max_seconds if max_seconds is not None else limits["screen_session_max_seconds"])
        self.renew_before = float(
            renew_before_seconds
            if renew_before_seconds is not None
            else limits["screen_allowance_renew_before_seconds"]
        )
        self.chunk_seconds = float(
            chunk_seconds if chunk_seconds is not None else limits["screen_allowance_chunk_seconds"]
        )
        self.secure_pause_max = float(
            secure_pause_max_seconds
            if secure_pause_max_seconds is not None
            else limits["screen_secure_desktop_pause_max_seconds"]
        )
        self.heartbeat_seconds = int(limits["screen_heartbeat_seconds"])
        self.indicator_grace = indicator_grace_seconds
        self.watchdog_interval = watchdog_interval
        self.lock_poll_seconds = lock_poll_seconds
        self.indicator_poll_seconds = indicator_poll_seconds
        self.worker_start_timeout = worker_start_timeout
        self.start_budget = float(
            start_budget_seconds
            if start_budget_seconds is not None
            else load_registry().get("screen.session_start").timeout_ms / 1000 - START_BUDGET_MARGIN_SECONDS
        )
        self.tick_timeout_seconds = tick_timeout_seconds
        self.usage_min_interval = USAGE_MIN_INTERVAL_SECONDS
        self.source = dict(source) if source is not None else {"kind": "fake" if platform.name == "fake" else "ddagrab"}
        self.gate = ConsentGate(store)
        self.broker = ConsentBroker(ui, permission_ui, timeout_seconds=consent_timeout_seconds)
        self.indicator = IndicatorController(ui, permission_ui)
        self.history = ScreenHistory(store)
        self.supervisor = WorkerSupervisor(
            launcher, tick_interval=tick_interval, stop_grace=stop_grace, alive=self.indicator.ui_alive
        )
        self._current: ScreenSession | None = None
        self._retired: deque[str] = deque(maxlen=RETIRED_IDS_KEPT)
        self._tickets: dict[str, tuple[float, dict[str, Any]]] = {}
        self._watchdog: asyncio.Task[None] | None = None
        self._end_lock = asyncio.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._pending_restart_frame: dict[str, Any] | None = None
        self._display_changed = False
        self._start_lock = asyncio.Lock()
        self._end_tasks: set[asyncio.Task[None]] = set()  # ends scheduled off the supervisor's tasks (strong refs)
        self.sessions_started = 0

    # ----- lifecycle --------------------------------------------------------------------------------------
    def start(self) -> None:
        self._loop = asyncio.get_running_loop()

    async def shutdown(self) -> None:
        """Agent stopping: end the session (``agent_restart``); the next start records it in the history. An end
        already scheduled (a worker event) is awaited, so its frame and history row are not lost."""
        if self._current is not None:
            await self.end("agent_restart")
        await self.drain_ends()

    async def drain_ends(self) -> None:
        """Wait for every end scheduled by :meth:`_end_soon`."""
        while self._end_tasks:
            await asyncio.gather(*list(self._end_tasks), return_exceptions=True)

    @property
    def current(self) -> ScreenSession | None:
        return self._current

    @property
    def media_available(self) -> bool:
        """This build contains the media stack (aiortc + PyAV); False: Screen view is not in this version."""
        try:
            return bool(self._media_available())
        except Exception:  # noqa: BLE001
            return False

    @property
    def live_session(self) -> ScreenSession | None:
        s = self._current
        return s if s is not None and s.live else None

    # ----- crash recovery ----------------------------------------------------------------------------------
    async def recover_after_restart(self) -> bool:
        """A previous agent process died with a session: the Job object killed its worker; record ``agent_restart``
        in the history and queue ``screen_session{ended, agent_restart}`` for the relay (sent after the snapshot)."""
        data = self._read_recovery()
        if data is None:
            return False
        self._delete_recovery()
        sid, cid = data.get("screen_session_id"), data.get("controller_id")
        pc_id = self._identity().pc_id
        if isinstance(sid, str) and isinstance(cid, str) and pc_id is not None:
            self._pending_restart_frame = screen_session_frame(
                pc_id, sid, cid, event="ended", reason="agent_restart", live_seconds=0
            )
            self.history.record_session(
                controller_id=cid,
                display_name=str(data.get("display_name", "a phone"))[:64],
                started_at=str(data.get("started_at", format_rfc3339(now_utc()))),
                live_seconds=0,
                reason="agent_restart",
            )
        log.warning("a Screen view session of a previous agent process was recorded as ended (agent_restart)")
        return True

    async def on_snapshot_applied(self) -> None:
        """Relay back and the snapshot applied first (a revocation has ended the session by now): deliver the queued
        restart notice; tickets resume on the next watchdog pass."""
        frame = self._pending_restart_frame
        if frame is not None and await self._send(frame):
            self._pending_restart_frame = None

    def on_display_change(self) -> None:
        """``WM_DISPLAYCHANGE`` (or any monitor change): the watchdog re-checks the bars on its next pass."""
        self._display_changed = True

    # ----- tickets attached to forwarded starts ----------------------------------------------------------------
    def remember_ticket(self, command_id: str, relay_screen: Any) -> None:
        """``relay.screen`` from the forwarded command, kept until the handler runs (never journaled)."""
        now = time.monotonic()
        for cid, (at, _ticket) in list(self._tickets.items()):
            if now - at > TICKET_MAX_AGE_SECONDS:
                del self._tickets[cid]
        if len(self._tickets) >= TICKETS_KEPT:
            oldest = min(self._tickets, key=lambda c: self._tickets[c][0])
            del self._tickets[oldest]
        self._tickets[command_id] = (now, dict(relay_screen) if isinstance(relay_screen, dict) else {})

    def take_ticket(self, command_id: str) -> dict[str, Any] | None:
        entry = self._tickets.pop(command_id, None)
        return entry[1] if entry is not None else None

    # ----- start ----------------------------------------------------------------------------------------------------
    async def start_session(
        self,
        *,
        controller_id: str,
        kid: str,
        display_name: str,
        command_id: str,
        offer_sdp: str,
        surface: str | int,
        takeover: bool,
    ) -> ScreenSession:
        deadline = time.monotonic() + self.start_budget  # the command's own timeout, less a margin
        async with self._start_lock:
            return await self._start_session(
                controller_id=controller_id,
                kid=kid,
                display_name=display_name,
                command_id=command_id,
                offer_sdp=offer_sdp,
                surface=surface,
                takeover=takeover,
                deadline=deadline,
            )

    async def _start_session(
        self,
        *,
        controller_id: str,
        kid: str,
        display_name: str,
        command_id: str,
        offer_sdp: str,
        surface: str | int,
        takeover: bool,
        deadline: float,
    ) -> ScreenSession:
        registry = load_registry()
        if not self.media_available:
            # Honest and specific (never "your display isn't supported"), before any bar or window
            raise registry.make_error("ACTION_UNAVAILABLE", NOT_INCLUDED_MESSAGE, reason=SCREEN_VIEW_NOT_INCLUDED)
        await self._recheck_start_conditions(registry)
        ticket = self.take_ticket(command_id)
        if not valid_ticket(ticket):
            raise registry.make_error(
                "ACTION_UNAVAILABLE", "The DoMe service attached no Screen view allowance to this start. Try again."
            )
        assert ticket is not None
        summary = check_offer(offer_sdp)
        existing = self.live_session
        if existing is not None and existing.controller_id != controller_id and not takeover:
            raise registry.make_error("SCREEN_SESSION_OWNED")
        consent = await self._consent(controller_id, display_name)
        monitors = await self._monitors()
        monitor = self._pick_surface(monitors, surface)
        # The consent window can stay open for 30 s: Disable remote control, a lock or a UAC prompt meanwhile refuses
        # the start exactly as at entry (design §7 "refuses new starts"), before any bar or worker exists.
        await self._recheck_start_conditions(registry)
        existing = self.live_session
        if existing is not None and existing.controller_id != controller_id and not takeover:
            raise registry.make_error("SCREEN_SESSION_OWNED")
        if existing is not None:
            await self._end(existing, "takeover" if existing.controller_id != controller_id else "replaced")
        # An end a worker event scheduled (a `bye` a moment ago) must finish first: it stops that worker and closes
        # the bars, which must not happen to this session's.
        await self.drain_ends()
        now = time.monotonic()
        session = ScreenSession(
            screen_session_id=new_session_id(),
            controller_id=controller_id,
            kid=kid,
            display_name=display_name,
            command_id=command_id,
            monitor=monitor,
            surfaces=tuple(monitors),
            consent=consent,
            started_at=now,
            started_wall=format_rfc3339(now_utc()),
        )
        session.gens.append((1, monitor))
        self._apply_ticket(session, ticket)
        self._current = session
        self._update_status()
        try:
            return await self._bring_up(session, offer_sdp, ticket, summary.h264, summary.vp8, deadline, takeover)
        except asyncio.CancelledError:
            # The executor gave up on the command (its timeout) or the phone was revoked mid-start: no bar, no worker
            # and no half-started session may stay behind.
            await self._abort_start(session)
            raise

    async def _recheck_start_conditions(self, registry: Any) -> None:
        if not self._remote_enabled():
            raise registry.make_error("PC_REMOTE_DISABLED")
        if await self._session_locked():
            raise registry.make_error("PC_SESSION_LOCKED")
        if await self._probe_secure_desktop():
            raise registry.make_error(
                "PC_SESSION_LOCKED", "Windows is asking for permission on the PC. Someone at the PC must answer first."
            )

    async def _bring_up(
        self,
        session: ScreenSession,
        offer_sdp: str,
        ticket: dict[str, Any],
        h264: bool,
        vp8: bool,
        deadline: float,
        takeover: bool,
    ) -> ScreenSession:
        """Bars, then the worker, then the signed answer (the start's second half; the caller aborts on cancel)."""
        registry = load_registry()
        controller_id, display_name, command_id = session.controller_id, session.display_name, session.command_id
        monitor, monitors = session.monitor, list(session.surfaces)
        consent = session.consent
        try:
            await self.indicator.arm(
                viewer_name=display_name,
                audio=False,
                monitors=monitors,
                on_stop=self._stop_from_ui,
                on_turn_off=self._turn_off_from_ui,
            )
        except ProtocolError:
            session.state = "ended"
            session.closed = True
            if self._current is session:
                self._current = None
            self._update_status()
            raise
        if session.state == "ended" or not self._remote_enabled():
            # an emergency stop retired the session (or remote control went off) while the bars were being confirmed:
            # no worker is spawned
            reason = session.end_reason if session.state == "ended" else "remote_disabled"
            await self._abort_start(session)
            raise _start_interrupted(reason)
        loop = asyncio.get_running_loop()
        session.answer_future = loop.create_future()
        session.answer_future.add_done_callback(_mark_retrieved)

        async def on_event(ev: dict[str, Any]) -> None:
            await self._on_worker_event(session, ev)

        async def on_exit(code: int | None) -> None:
            await self._on_worker_exit(session, code)

        try:
            await self.supervisor.start(
                self._start_message(session, offer_sdp, ticket, h264, vp8),
                on_event=on_event,
                on_exit=on_exit,
            )
            wait = min(self.worker_start_timeout, max(0.5, deadline - time.monotonic()))
            answer = await asyncio.wait_for(asyncio.shield(session.answer_future), wait)
        except ProtocolError as exc:
            await self._abort_start(session)
            raise exc from None
        except TimeoutError:
            await self._abort_start(session)
            raise registry.make_error("SCREEN_CAPTURE_UNAVAILABLE", detail="driver") from None
        except Exception as exc:  # noqa: BLE001 - a launcher failure is a PC that cannot share its screen
            log.error("screen worker could not be started", error=exc.__class__.__name__)
            await self._abort_start(session)
            raise registry.make_error("SCREEN_CAPTURE_UNAVAILABLE", detail="driver") from None
        if _ended(session):
            # an emergency stop (remote disabled, revocation, lock) ran while the worker was answering
            await self._abort_start(session)
            raise _start_interrupted(session.end_reason)
        if session.start_error is not None or not self.supervisor.running:
            # the worker answered and then failed or exited before the start was complete
            error = session.start_error or registry.make_error("SCREEN_CAPTURE_UNAVAILABLE", detail="driver")
            await self._abort_start(session)
            raise error
        session.start_complete = True  # from here on worker events and exits end the session themselves
        session.encoder = str(answer.get("encoder")) if answer.get("encoder") in ("h264", "vp8") else None
        geometry = answer.get("geometry")
        if isinstance(geometry, dict) and isinstance(geometry.get("gen"), int):
            session.gen = int(geometry["gen"])
            session.gens.clear()
            session.gens.append((session.gen, monitor))
        identity = self._identity()
        account_id, pc_id = identity.account_id, identity.pc_id
        assert account_id is not None and pc_id is not None
        envelope = sign_screen_answer(
            identity,
            account_id=account_id,
            pc_id=pc_id,
            controller_id=controller_id,
            command_id=command_id,
            screen_session_id=session.screen_session_id,
            offer_sdp=offer_sdp,
            answer_sdp=str(answer["answer_sdp"]),
        )
        if not await self._send(screen_answer_frame(envelope, controller_id)):
            log.warning("screen answer could not be sent (relay offline); the phone will time out")
        self._write_recovery(session)
        self.sessions_started += 1
        self._watchdog = loop.create_task(self._watch(session), name="dome-screen-watchdog")
        log.info(
            "screen session starting",
            controller_id=controller_id,
            surface_index=monitor.index,
            encoder=session.encoder,
            consent=consent,
            takeover=takeover,
        )
        self._on_state_change()
        self._update_status()
        return session

    async def _abort_start(self, session: ScreenSession) -> None:
        """A start that failed after the bars were up: worker gone first, then the bars (design §7 order). Nothing
        is emitted or recorded: a start that never completed is answered by its result alone. When an emergency stop
        retired the session meanwhile its end may have run before the worker or the bars existed, so the worker and
        the bars are released here regardless (both idempotent; the start lock keeps any newer session out)."""
        if session.state != "ended":
            session.state = "ended"
            session.end_reason = "capture_failed"
            session.closed = True
            self._retired.append(session.screen_session_id)
        await self.supervisor.stop()
        self.indicator.disarm()
        if self._current is session:
            self._current = None
        self._update_status()

    async def _consent(self, controller_id: str, display_name: str) -> str:
        decision = self.gate.decide(controller_id)
        if decision.kind == "not_required":
            return "not_required"
        if decision.kind == "refused":
            assert decision.refusal is not None
            self._record_refusal(controller_id, display_name, decision.refusal)
            raise load_registry().make_error("SCREEN_CONSENT_DECLINED")
        assert decision.cause is not None
        self.gate.note_window_opened()
        outcome = await self.broker.ask(controller_id=controller_id, display_name=display_name, cause=decision.cause)
        if outcome == "withdrawn":
            # Disable remote control (or another emergency stop) closed the window: nobody refused, nothing counts
            await self._recheck_start_conditions(load_registry())
            raise load_registry().make_error("PC_REMOTE_DISABLED")
        self.gate.note_outcome(controller_id, outcome)
        if outcome == "allow":
            return "granted"
        if outcome == "turn_off":
            with contextlib.suppress(Exception):
                await self._on_turn_off(controller_id)
        reason = {"deny": "denied", "timeout": "timeout", "block": "blocked", "turn_off": "denied"}[outcome]
        self._record_refusal(controller_id, display_name, reason)
        raise load_registry().make_error(
            "SCREEN_CONSENT_TIMEOUT" if outcome == "timeout" else "SCREEN_CONSENT_DECLINED"
        )

    def _record_refusal(self, controller_id: str, display_name: str, reason: str) -> None:
        self.history.record_refusal(controller_id=controller_id, display_name=display_name, reason=reason)
        self._security_event("screen_consent_declined", {"reason": reason})

    async def _monitors(self) -> list[Monitor]:
        try:
            monitors = await asyncio.to_thread(self._platform.screen.monitors)
        except ProtocolError as exc:
            if exc.code == "SCREEN_CAPTURE_UNAVAILABLE":
                raise
            raise load_registry().make_error("SCREEN_CAPTURE_UNAVAILABLE", detail="unsupported_display") from None
        except Exception as exc:  # noqa: BLE001
            log.warning("monitor enumeration failed", error=exc.__class__.__name__)
            raise load_registry().make_error("SCREEN_CAPTURE_UNAVAILABLE", detail="unsupported_display") from None
        if not monitors:
            raise load_registry().make_error("SCREEN_CAPTURE_UNAVAILABLE", detail="unsupported_display")
        return monitors

    @staticmethod
    def _pick_surface(monitors: list[Monitor], surface: str | int) -> Monitor:
        if surface == "primary":
            return next((m for m in monitors if m.primary), monitors[0])
        for m in monitors:
            if m.index == surface:
                return m
        raise load_registry().make_error("INVALID_PARAMETERS", "This PC has no monitor with that surface index.")

    def _apply_ticket(self, session: ScreenSession, ticket: dict[str, Any]) -> None:
        now = time.monotonic()
        session.unlimited = bool(ticket["unlimited"])
        until = ticket.get("granted_until")
        if isinstance(until, str):
            session.ticket_deadline = now + max(0.0, (parse_rfc3339(until) - now_utc()).total_seconds())
        else:
            session.ticket_deadline = now + self.chunk_seconds  # unlimited plans still get the dead-man latency
        session.final = False
        session.final_reason = None

    def _start_message(
        self, session: ScreenSession, offer_sdp: str, ticket: dict[str, Any], h264: bool, vp8: bool
    ) -> dict[str, Any]:
        monitor = session.monitor
        source = dict(self.source)
        source.update(_source_geometry(monitor))
        source.setdefault("fps", 30)
        session.reserved = self.indicator.reserved_for(monitor)
        preference = [c for c, offered in (("h264", h264), ("vp8", vp8)) if offered]
        message: dict[str, Any] = {
            "op": "start",
            "offer_sdp": offer_sdp,
            "stun_urls": [u for u in ticket.get("stun_urls", []) if isinstance(u, str)],
            "source": source,
            "reserved": session.reserved,
            "lease_seconds": self.lease_seconds,
            "heartbeat_seconds": self.heartbeat_seconds,
            "tick_timeout_seconds": self.tick_timeout_seconds,
            "connect_timeout_seconds": self.connect_timeout,
            "encoder_preference": preference,
            "bitrate_kbps": dict(BITRATE_KBPS),
            "audio": False,
        }
        if isinstance(ticket.get("relayed_max_kbps"), int):
            message["relayed_max_kbps"] = ticket["relayed_max_kbps"]
        return message

    # ----- worker events -----------------------------------------------------------------------------------------
    async def _on_worker_event(self, session: ScreenSession, ev: dict[str, Any]) -> None:
        if session.state == "ended":
            return  # retired: late data-channel messages and late worker events are dropped
        kind = ev.get("ev")
        now = time.monotonic()
        if kind == "answer":
            if session.answer_future is not None and not session.answer_future.done():
                session.answer_future.set_result(ev)
        elif kind == "error":
            code = str(ev.get("code", "SCREEN_CAPTURE_UNAVAILABLE"))
            key = "reason" if code == "SCREEN_OFFER_INVALID" else "detail"  # the contract's detail key per error
            detail = {key: ev["detail"]} if isinstance(ev.get("detail"), str) else {}
            if session.answer_future is not None and not session.answer_future.done():
                session.answer_future.set_exception(load_registry().make_error(code, **detail))
                return
            if not session.start_complete:
                session.start_error = load_registry().make_error(code, **detail)  # the start path answers and aborts
                return
            # detail and message say why (e.g. Desktop Duplication's and Windows.Graphics.Capture's own reasons, which
            # the worker sanitised): the diagnostics bundle carries this line
            message = ev.get("message")
            log.warning(
                "screen worker reported an error",
                code=code,
                detail=ev.get("detail") if isinstance(ev.get("detail"), str) else None,
                message=" ".join(message.split())[:200] if isinstance(message, str) else None,
            )
            self._end_soon(session, "connection_failed" if code == "SCREEN_CONNECTION_FAILED" else "capture_failed")
        elif kind == "connected":
            session.connected = True
            session.path = "relayed" if ev.get("path") == "relayed" else "direct"
            session.relayed = session.path == "relayed"
            session.lease_expires_at = now + self.lease_seconds
        elif kind == "first_frame":
            if not session.first_frame:
                session.first_frame = True
                session.state = "live"
                session.live_since = now if not session.pauses else None
                await self._emit(session, "started", "started", path=session.path or "direct")
                session.started_emitted = True
                self._ui_notify("Screen view", f"{session.display_name} is viewing this screen. {STOP_HINT}")
                self._on_state_change()
                self._update_status()
        elif kind == "hb":
            session.lease_expires_at = now + self.lease_seconds
        elif kind == "bye":
            self._end_soon(session, "stopped")
        elif kind == "geometry":
            gen = ev.get("gen")
            if isinstance(gen, int) and gen >= 1:
                session.gen = gen
                session.gens.append((gen, session.monitor))
                session.geometry_pending = False  # taps aim at the new picture from here on
        elif kind == "state":
            reason = ev.get("reason")
            if ev.get("state") == "paused" and reason in ("secure_desktop", "indicator"):
                await self._pause(session, reason, tell_worker=False)
            elif ev.get("state") == "sending" and session.pauses and ev.get("reason") is None:
                pass  # the worker resumed on its own order; the agent's own pause causes still rule
        elif kind == "stats":
            if isinstance(ev.get("relayed_bytes"), int):
                session.relayed_bytes = int(ev["relayed_bytes"])
        elif kind == "disconnected":
            self._end_soon(session, "viewer_gone" if session.connected else "connection_failed")
        elif kind in ("keyframe", "audio", "exiting"):
            log.debug("screen worker event", kind=kind)
        else:
            log.debug("unknown screen worker event dropped", kind=str(kind)[:32])

    async def _on_worker_exit(self, session: ScreenSession, code: int | None) -> None:
        if session.answer_future is not None and not session.answer_future.done():
            session.answer_future.set_exception(
                load_registry().make_error("SCREEN_CAPTURE_UNAVAILABLE", detail="driver")
            )
            return
        if not session.start_complete:
            return  # the start path sees `supervisor.running` false and aborts: one answer, one clean-up
        if session.state != "ended":
            log.warning("screen worker exited while the session was live", code=code)
            self._end_soon(session, "capture_failed")

    # ----- stops ---------------------------------------------------------------------------------------------------
    async def stop_session(self, *, controller_id: str, screen_session_id: str) -> tuple[bool, int]:
        """``screen.session_stop``: only the owner can stop its own session. Idempotent."""
        session = self._current
        if (
            session is None
            or session.state == "ended"
            or session.screen_session_id != screen_session_id
            or session.controller_id != controller_id
        ):
            return False, 0
        live = session.live_seconds()
        await self._end(session, "stopped")
        return True, live

    async def end(self, reason: EndReason) -> bool:
        session = self._current
        if session is None or session.state == "ended":
            return False
        await self._end(session, reason)
        return True

    def end_soon(self, reason: EndReason) -> Awaitable[None]:
        """An emergency stop (*Disable remote control*): before this returns the session is retired (the id is
        dropped, ``pointer_move_to`` is refused, the start path cannot complete) and a consent window that is still
        waiting is withdrawn; the rest of the end order (the worker's ``stop``, the bars, the ``ended`` frame) runs
        on its own task, which the caller awaits AFTER releasing held input (design §7: the screen has ended before
        ``set_remote_enabled`` returns, and a held key is not kept down while the worker exits)."""
        self.broker.withdraw_all()
        session = self._current
        if session is None or session.state == "ended":
            return _done()
        task = self._end_soon(session, reason)
        return task if task is not None else _done()

    async def end_for_controller(self, controller_id: str, reason: EndReason) -> bool:
        session = self._current
        if session is None or session.state == "ended" or session.controller_id != controller_id:
            return False
        await self._end(session, reason)
        return True

    async def apply_capabilities(self, controller_id: str, effective: Any) -> None:
        """A snapshot or a local grant change: ``screen`` gone → ``grant_removed`` (design §6.2)."""
        session = self._current
        if session is None or session.state == "ended" or session.controller_id != controller_id:
            return
        if "screen" not in set(effective):
            await self._end(session, "grant_removed")

    async def stop_on_pc(self, source: str = "tray") -> bool:
        """Bar Stop, tray "Stop Screen view" or the hotkey: ends ``stopped_on_pc`` and starts the PC-wide cooldown."""
        session = self._current
        if session is None or session.state == "ended":
            return False
        self._note_stop_on_pc(session, source)
        await self._end(session, "stopped_on_pc")
        return True

    async def turn_off_on_pc(self, controller_id: str, source: str) -> bool:
        """The bar's "Stop and turn off Screen view for this phone", or the tray's "Turn off Screen view" while that
        phone is watching: the strongest "don't watch me" signal at the desk, so the PC-wide cooldown starts exactly as
        for Stop (design §6.3: the Safari tab and the home-screen app are two controllers) before ``screen`` and
        ``screen_audio`` are removed and the session ends ``grant_removed``. The cooldown length is read BEFORE the
        grant narrows, so the stopped phone's own setting counts. True: a live session of that phone was stopped."""
        session = self._current
        watching = session is not None and session.state != "ended" and session.controller_id == controller_id
        if watching:
            assert session is not None
            self._note_stop_on_pc(session, source, turned_off=True)
        with contextlib.suppress(Exception):
            await self._on_turn_off(controller_id)
        if watching:
            await self.end_for_controller(controller_id, "grant_removed")
        return watching

    def _note_stop_on_pc(self, session: ScreenSession, source: str, *, turned_off: bool = False) -> None:
        screen_grants = [g.controller_id for g in self._store.list_grants() if "screen" in g.capabilities]
        if session.controller_id not in screen_grants:
            screen_grants.append(session.controller_id)
        length = self.gate.note_stop_on_pc(session.controller_id, screen_grants)
        log.info("Screen view stopped at the PC", source=source, cooldown_seconds=length, turned_off=turned_off)
        detail: dict[str, Any] = {"source": source, "cooldown_seconds": length}
        if turned_off:
            detail["turned_off"] = True
        self._security_event("screen_stopped_on_pc", detail)

    def _stop_from_ui(self, source: str) -> None:
        """Called from the UI thread (bar Stop, hotkey)."""
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        loop.call_soon_threadsafe(lambda: loop.create_task(self.stop_on_pc(source), name="dome-screen-stop-ui"))

    def _turn_off_from_ui(self) -> None:
        """ "Stop and turn off Screen view for this phone" (the bar's menu, from the UI thread): the cooldown starts,
        then screen / screen_audio go through grant_update and the session ends ``grant_removed``."""
        loop = self._loop
        session = self._current
        if loop is None or loop.is_closed() or session is None:
            return
        cid = session.controller_id
        loop.call_soon_threadsafe(
            lambda: loop.create_task(self.turn_off_on_pc(cid, "bar"), name="dome-screen-turn-off")
        )

    def _retire(self, session: ScreenSession, reason: str) -> bool:
        """Step 1 of the end order, synchronous: retire the id. Late data-channel messages, worker events and
        ``pointer_move_to`` are dropped from here on and the session no longer counts as current. False when it was
        already retired (the first reason stands)."""
        if session.state == "ended":
            return False
        session.state = "ended"
        session.end_reason = reason
        live = session.live_seconds()
        session.accumulated = float(live)
        session.live_since = None
        self._retired.append(session.screen_session_id)
        if self._current is session:
            self._current = None
        return True

    def _end_soon(self, session: ScreenSession, reason: str) -> asyncio.Task[None] | None:
        """Retire now and run the rest of the end order on a task of its own. Used for ends a worker event or exit
        asks for: those arrive on the supervisor's reader / exit-watcher task, and the end's ``supervisor.stop()``
        waits for exactly those tasks, so ending inline would make the two wait on each other."""
        if not self._retire(session, reason) and session.closed:
            return None
        loop = self._loop or asyncio.get_running_loop()
        task = loop.create_task(self._end(session, reason), name="dome-screen-end")
        self._end_tasks.add(task)
        task.add_done_callback(self._end_tasks.discard)
        return task

    async def _end(self, session: ScreenSession, reason: str) -> None:
        self._retire(session, reason)  # no-op when an emergency stop or a worker event already retired it
        async with self._end_lock:
            if session.closed:
                return
            session.closed = True
            reason = session.end_reason or reason
            live = session.live_seconds()
            if (
                self._watchdog is not None
                and not self._watchdog.done()
                and self._watchdog is not asyncio.current_task()
            ):
                self._watchdog.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._watchdog
            # 2. the worker: stop, wait at most 2 s, terminate
            await self.supervisor.stop()
            # 3. the bars close only once the worker has exited
            self.indicator.disarm()
            self._delete_recovery()
            # 4. the relay, then the person at the PC and the local history
            log.info(
                "screen session ended",
                reason=reason,
                live_seconds=live,
                path=session.path,
                connected=session.connected,
                relayed_bytes=session.relayed_bytes,
            )
            await self._emit(session, "ended", reason, live_seconds=live)
            if session.started_emitted:
                self._ui_notify("Screen view ended", f"Screen view ended ({_duration_text(live)})")
            self.history.record_session(
                controller_id=session.controller_id,
                display_name=session.display_name,
                started_at=session.started_wall,
                live_seconds=live,
                reason=reason,
            )
            self._on_state_change()
            self._update_status()

    # ----- pause / resume -----------------------------------------------------------------------------------------
    async def _pause(self, session: ScreenSession, reason: PauseReason, *, tell_worker: bool = True) -> None:
        """Add a pause cause. The worker is paused when the first cause appears; every cause gets its own
        ``paused`` frame (the relay discounts only a ``secure_desktop`` pair it saw, and the phone keeps the set)."""
        if reason in session.pauses or session.state == "ended":
            return
        now = time.monotonic()
        first = not session.pauses
        session.pauses[reason] = now
        if first:
            if session.live_since is not None:
                session.accumulated += now - session.live_since
                session.live_since = None
            if tell_worker:
                await self.supervisor.send({"op": "pause", "reason": reason})
        if session.started_emitted:
            await self._emit(session, "paused", reason)
        log.info("screen capture paused", reason=reason, causes=sorted(session.pauses))

    async def _resume(self, session: ScreenSession, reason: PauseReason) -> None:
        """Remove a pause cause; capture resumes only once no cause is left (a UAC prompt answered while a bar is
        still missing keeps the picture paused, and the other way round)."""
        if reason not in session.pauses or session.state == "ended":
            return
        del session.pauses[reason]
        if not session.pauses:
            if session.first_frame and session.connected:
                session.live_since = time.monotonic()
            await self.supervisor.send({"op": "resume"})
        if session.started_emitted:
            await self._emit(session, "resumed", reason, path=session.path)
        log.info("screen capture resumed" if not session.pauses else "a pause cause cleared", reason=reason)

    # ----- allowance tickets ---------------------------------------------------------------------------------------
    async def handle_allowance(self, frame: dict[str, Any]) -> None:
        """``screen_allowance`` from the relay (rules.screen_sessions (4))."""
        session = self._current
        if session is None or session.state == "ended" or frame.get("screen_session_id") != session.screen_session_id:
            log.debug("screen_allowance for an unknown or ended session dropped")
            return
        session.unlimited = bool(frame.get("unlimited", False))
        session.final = bool(frame.get("final", False))
        reason = frame.get("reason")
        session.final_reason = reason if isinstance(reason, str) else None
        resets = frame.get("resets_at")
        session.resets_at = resets if isinstance(resets, str) else None
        until = frame.get("granted_until")
        now = time.monotonic()
        if isinstance(until, str):
            session.ticket_deadline = now + max(0.0, (parse_rfc3339(until) - now_utc()).total_seconds())
        elif session.final:
            await self._end(session, self._ticket_end_reason(session))
            return
        else:
            session.ticket_deadline = now + self.chunk_seconds
        await self.supervisor.send(
            {
                "op": "allowance",
                "unlimited": session.unlimited,
                "remaining_seconds": None,  # the ticket says when to ask again, not what is left in the window
                "resets_at": session.resets_at,
            }
        )

    @staticmethod
    def _ticket_end_reason(session: ScreenSession) -> str:
        if session.final_reason == "controller_offline":
            return "controller_offline"
        if session.final_reason == "plan_disabled":
            return "plan_disabled"
        if session.final:
            return "allowance_exhausted"
        return "allowance_unconfirmed"

    async def _request_ticket(self, session: ScreenSession) -> None:
        pc_id = self._identity().pc_id
        if pc_id is None:
            return
        session.last_usage_at = time.monotonic()
        await self._send(
            screen_usage_frame(
                pc_id,
                session.screen_session_id,
                live_seconds=session.live_seconds(),
                relayed=session.relayed,
                relayed_bytes=session.relayed_bytes,
            )
        )

    # ----- the watchdog -----------------------------------------------------------------------------------------
    async def _watch(self, session: ScreenSession) -> None:
        next_lock = 0.0
        next_indicator = 0.0
        while True:
            await asyncio.sleep(self.watchdog_interval)
            if session.state == "ended":
                return
            now = time.monotonic()
            try:
                if not self._remote_enabled():
                    await self._end(session, "remote_disabled")
                    return
                if not session.connected and now - session.started_at >= self.connect_timeout:
                    await self._end(session, "connection_failed")
                    return
                if session.connected and now >= session.lease_expires_at:
                    await self._end(session, "viewer_gone")
                    return
                if now - session.started_at >= self.max_seconds:
                    await self._end(session, "max_duration")
                    return
                if now >= session.ticket_deadline:
                    await self._end(session, self._ticket_end_reason(session))
                    return
                if (
                    not session.final
                    and session.ticket_deadline - now <= self.renew_before
                    and (session.last_usage_at is None or now - session.last_usage_at >= self.usage_min_interval)
                    and self._snapshot_received()
                ):
                    await self._request_ticket(session)
                if now >= next_lock:
                    next_lock = now + self.lock_poll_seconds
                    if await self._session_locked():
                        await self._end(session, "session_locked")
                        return
                    secure = await self._probe_secure_desktop()
                    if secure:
                        await self._pause(session, "secure_desktop")  # whatever else is already pausing capture
                    else:
                        await self._resume(session, "secure_desktop")
                    since = session.pauses.get("secure_desktop")
                    if since is not None and now - since >= self.secure_pause_max:
                        await self._end(session, "secure_desktop")
                        return
                if now >= next_indicator or self._display_changed:
                    next_indicator = now + self.indicator_poll_seconds
                    self._display_changed = False
                    if await self._check_indicator(session, now):
                        return
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("screen watchdog check failed", error=exc.__class__.__name__)

    async def _check_indicator(self, session: ScreenSession, now: float) -> bool:
        """Every monitor must carry a bar (design §7, fail-closed). The monitors are enumerated afresh on every
        check, so a monitor plugged in since the bars were made (it has none), an unplugged one or a resolution change
        counts as a missing bar just like a bar window that vanished: capture pauses (``indicator``), the bars are
        recreated for the new set, the worker is told the streamed monitor's new geometry (a new ``gen``), and capture
        resumes once every bar is confirmed again; after the grace the session ends ``capture_failed``, and at once if
        the streamed monitor itself is gone. True = ended."""
        if session.geometry_pending and now - session.geometry_pending_since >= self.indicator_grace:
            log.warning("the worker never announced the rebuilt picture; Screen view ends")
            await self._end(session, "capture_failed")  # a rebuild that never finished: nothing to tap on
            return True
        monitors = await self._monitors_or_none()
        if monitors is not None and self.indicator.present() and tuple(monitors) == self.indicator.monitors:
            if session.indicator_missing_since is not None:
                session.indicator_missing_since = None
                await self._resume(session, "indicator")
            return False
        if session.indicator_missing_since is None:
            session.indicator_missing_since = now
            log.info("a monitor has no Screen view bar; capture paused until every monitor has one")
        await self._pause(session, "indicator")  # added whatever else is already pausing capture (a UAC prompt)
        if now - session.indicator_missing_since >= self.indicator_grace:
            await self._end(session, "capture_failed")
            return True
        if monitors is None:
            return False  # nothing to rebuild the bars for yet: try again on the next pass
        streamed = match_monitor(session.monitor, monitors)
        if streamed is None:
            log.warning("the monitor being viewed is gone; Screen view ends")
            await self._end(session, "capture_failed")
            return True
        try:
            await self.indicator.arm(
                viewer_name=session.display_name,
                audio=False,
                monitors=monitors,
                on_stop=self._stop_from_ui,
                on_turn_off=self._turn_off_from_ui,
            )
        except ProtocolError:
            return False
        await self._announce_geometry(session, streamed, monitors)
        return False

    async def _monitors_or_none(self) -> list[Monitor] | None:
        """A fresh enumeration for the live checks; None when Windows cannot list them right now (a mode change in
        progress), which the caller treats as a missing bar (fail closed)."""
        try:
            return await self._monitors()
        except ProtocolError:
            return None

    async def _announce_geometry(self, session: ScreenSession, streamed: Monitor, monitors: list[Monitor]) -> None:
        """After the bars were recreated: the streamed monitor moved or changed size → the worker rebuilds its capture
        at the new geometry; only the bars' band changed → the worker announces it. Either way a new ``gen`` follows
        and taps wait for it (``geometry_pending``)."""
        session.surfaces = tuple(monitors)
        reserved = self.indicator.reserved_for(streamed)
        old = session.monitor
        moved = (old.left, old.top, old.width, old.height, old.rotation) != (
            streamed.left,
            streamed.top,
            streamed.width,
            streamed.height,
            streamed.rotation,
        )
        if not moved and old.index == streamed.index and reserved == session.reserved:
            return
        session.monitor = streamed
        session.reserved = reserved
        session.geometry_pending = True
        session.geometry_pending_since = time.monotonic()
        if moved:
            log.info(
                "the viewed monitor changed geometry; the worker rebuilds its capture", surface_index=streamed.index
            )
            await self.supervisor.send({"op": "rebuild", "source": _source_geometry(streamed), "reserved": reserved})
        else:
            await self.supervisor.send({"op": "reserved", "surface_index": streamed.index, "reserved": reserved})

    async def _probe_secure_desktop(self) -> bool:
        try:
            return bool(await asyncio.to_thread(self._platform.input.secure_desktop_active))
        except Exception:  # noqa: BLE001 - a failed probe is not evidence of a secure desktop
            return False

    # ----- input gate (pointer_move_to) ---------------------------------------------------------------------------
    def screen_target(self, controller_id: str, gen: int) -> tuple[Monitor | None, str | None]:
        """The monitor a ``pointer_move_to`` at generation ``gen`` maps onto, or the error code that drops the batch:
        ``SCREEN_SESSION_REQUIRED`` (no live session owned by this controller), ``SCREEN_GEOMETRY_CHANGED`` (``gen`` is
        not the CURRENT generation, ``rules.input_sessions`` (2a), or a new one is being announced). A tap the phone
        aimed at an older picture is never mapped onto a rectangle that may have moved since (CONTRACT_ISSUES.md)."""
        session = self._current
        if (
            session is None
            or session.state == "ended"
            or session.controller_id != controller_id
            or not session.connected
        ):
            return None, "SCREEN_SESSION_REQUIRED"
        if gen != session.gen or session.geometry_pending:
            return None, "SCREEN_GEOMETRY_CHANGED"
        monitor = session.monitor_for_gen(gen)
        if monitor is None:
            return None, "SCREEN_GEOMETRY_CHANGED"
        return monitor, None

    async def notify_restricted(self, restricted: bool, reason: str | None) -> None:
        """Input is refused on the PC (elevated window, secure desktop, lock, permission UI): tell the phone."""
        if self.live_session is None:
            return
        message: dict[str, Any] = {"op": "restricted", "restricted": bool(restricted)}
        if reason in ("elevated", "secure_desktop", "locked", "permission_ui"):
            message["reason"] = reason
        await self.supervisor.send(message)

    # ----- status ---------------------------------------------------------------------------------------------------
    def status_view(self) -> ScreenSessionView | None:
        session = self._current
        return session.view() if session is not None and session.live else None

    def summary(self) -> dict[str, Any]:
        s = self._current
        return {
            "session": None
            if s is None
            else {
                "controller_id": s.controller_id,
                "state": s.state,
                "connected": s.connected,
                "path": s.path,
                "encoder": s.encoder,
                "gen": s.gen,
                "surface_index": s.monitor.index,
                "paused_reason": s.paused_reason,
                "paused_causes": sorted(s.pauses),
                "live_seconds": s.live_seconds(),
                "consent": s.consent,
                "unlimited": s.unlimited,
                "started_at": s.started_wall,
            },
            "sessions_started": self.sessions_started,
            "included": self.media_available,
            "consent": self.gate.summary(),
            "history": self.history.recent(),
        }

    # ----- plumbing --------------------------------------------------------------------------------------------------
    async def _emit(
        self,
        session: ScreenSession,
        event: str,
        reason: str,
        *,
        live_seconds: int | None = None,
        path: str | None = None,
    ) -> None:
        pc_id = self._identity().pc_id
        if pc_id is None:
            return
        await self._send(
            screen_session_frame(
                pc_id,
                session.screen_session_id,
                session.controller_id,
                event=event,
                reason=reason,
                live_seconds=session.live_seconds() if live_seconds is None else live_seconds,
                path=path,
            )
        )

    def _ui_notify(self, title: str, message: str) -> None:
        try:
            self._ui.notify(title, message)
        except Exception:  # noqa: BLE001 - UI must never break the session
            log.debug("screen notification failed")

    def _update_status(self) -> None:
        self._on_state_change()

    def _write_recovery(self, session: ScreenSession) -> None:
        data = {
            "screen_session_id": session.screen_session_id,
            "controller_id": session.controller_id,
            "display_name": session.display_name,
            "started_at": session.started_wall,
        }
        try:
            self._recovery_path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=".screen-", dir=str(self._recovery_path.parent))
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh)
            os.replace(tmp, self._recovery_path)
        except OSError as exc:
            log.warning("could not write the screen recovery file", error=exc.__class__.__name__)

    def _read_recovery(self) -> dict[str, Any] | None:
        try:
            raw = self._recovery_path.read_text(encoding="utf-8")
        except OSError:
            return None
        try:
            data = json.loads(raw)
        except ValueError:
            self._delete_recovery()
            return None
        return data if isinstance(data, dict) else None

    def _delete_recovery(self) -> None:
        with contextlib.suppress(OSError):
            self._recovery_path.unlink()


def match_monitor(old: Monitor, monitors: list[Monitor]) -> Monitor | None:
    """The streamed monitor in a fresh enumeration: the primary stays the primary (its origin is 0,0 whatever its
    size); another monitor is recognised by its top-left corner. None: it was unplugged."""
    if old.primary:
        return next((m for m in monitors if m.primary), None)
    return next((m for m in monitors if (m.left, m.top) == (old.left, old.top) and not m.primary), None)


def _source_geometry(monitor: Monitor) -> dict[str, Any]:
    """What the worker needs to capture one monitor (its rectangle in physical pixels, its rotation, and whether it
    is the primary: the WGC fallback can only ask for the primary by that name)."""
    return {
        "surface_index": monitor.index,
        "primary": monitor.primary,
        "left": monitor.left,
        "top": monitor.top,
        "width": monitor.width,
        "height": monitor.height,
        "rotation": monitor.rotation,
    }


def parse_wall(text: str) -> datetime:
    return parse_rfc3339(text)


def _ended(session: ScreenSession) -> bool:
    """``session.state == "ended"`` read after an await (another task may have retired it meanwhile)."""
    return session.state == "ended"


async def _nothing() -> None:
    return None


def _done() -> Awaitable[None]:
    return _nothing()


def _mark_retrieved(future: asyncio.Future[Any]) -> None:
    """The answer future may fail after its awaiter gave up (the start timed out): reading the exception keeps
    asyncio from logging "Future exception was never retrieved"."""
    if not future.cancelled():
        future.exception()


def _start_interrupted(reason: str | None) -> ProtocolError:
    """The error a start gets when an emergency stop ended the session while the worker was still answering."""
    code = {
        "remote_disabled": "PC_REMOTE_DISABLED",
        "session_locked": "PC_SESSION_LOCKED",
        "controller_revoked": "CONTROLLER_REVOKED",
        "grant_removed": "GRANT_MISSING",
        "plan_disabled": "PC_PLAN_DISABLED",
    }.get(reason or "", "SCREEN_CAPTURE_UNAVAILABLE")
    registry = load_registry()
    if code == "SCREEN_CAPTURE_UNAVAILABLE":
        return registry.make_error(code, detail="driver")
    err: ProtocolError = registry.make_error(code)
    return err


__all__ = [
    "BITRATE_KBPS",
    "RECOVERY_FILENAME",
    "EndReason",
    "ScreenSession",
    "ScreenSessionManager",
    "map_to_pixels",
    "match_monitor",
    "new_session_id",
    "valid_ticket",
]
