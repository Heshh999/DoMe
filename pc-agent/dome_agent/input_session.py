"""Manual touchpad/keyboard sessions (spec §10A-D/E, design brief §3.2, ``rules.input_sessions``).

One live :class:`InputSession` per PC. The agent issues the 22-character id and a lease of
``input_lease_seconds`` that every valid batch renews (an empty batch is a keepalive). A watchdog task
owned by this manager — independent of the relay socket — ends the session on lease expiry, Windows
session lock, a secure desktop or a local *Disable remote control*; the agent additionally ends it on
revocation, grant narrowing, takeover, ``input.session_stop``, relay disconnect / supersession and
agent shutdown. Ending always happens in this order: retire the id (delayed batches can no longer
press anything) → wait for the in-flight dispatch → release every button/key THIS session injected
(never the physical keyboard's) → delete the crash-recovery file → emit ``input_session{ended}``.

Batches (:func:`dome_protocol.verify_and_parse_input_batch` has already verified the signature,
schema, binding and window; the agent has checked the grant) are accepted only when the session id is
the live one owned by the sending controller, ``seq`` is strictly increasing, the batch is younger than
``input_age_budget_ms`` and the session's capabilities cover every event type. The age is judged
against a per-session estimate of the phone's clock offset (:class:`AgeEstimator`: windowed minimum of
receipt-minus-issued_at, seeded by the ``input.session_start`` command), so a phone clock a few seconds
behind or ahead of the PC neither kills the touchpad nor hides a relay stall; the relay's
``received_at`` stamp bounds the relay → agent leg the same way. Rejections drop the batch and are
reported as (rate-limited) ``error`` frames plus ``dropped_events`` in the next ack; they never end the
session. NOTE: the current relay does not forward agent ``error`` frames (they carry no session or
controller reference, CONTRACT_ISSUES.md #11), so in the integrated system the phone sees only the
``dropped_events`` count, ``input_session{suspended|ended}`` and ``pc_state``. Accepted batches are dispatched in order on one worker: adjacent
``pointer_move`` events are summed, nothing is merged across any other event, and if the dispatch lag
exceeds the age budget the backlog is discarded and the session is suspended (fresh start required).

Before each ``text``/``key``/``shortcut`` the dispatcher compares the foreground window identity with
the one captured when the session started or when the phone last clicked; a change BLOCKS keyboard
input of the session (``INPUT_TARGET_CHANGED`` once, every later keyboard event counted as dropped —
including batches the phone had already queued before it could know). The block clears only on a
user-directed click/press from this session (re-capturing the target), on a fresh
``input.session_start``, or for a batch issued at least ``target_change_grace_seconds`` after the change
(by then the phone has received the ``pc_state.foreground_app`` change and paused live typing, so new
typing is a deliberate continuation). Acks carry Windows acceptance only, at most four per second.

Content hygiene: text payloads never reach a log line, a security event, the journal, diagnostics,
state frames or the recovery file. Only event *types* and counts are logged.
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
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any, Literal

from dome_protocol import ProtocolError, format_rfc3339, load_registry, now_utc, parse_rfc3339
from dome_protocol.commands import VerifiedInputBatch

from .frames import error_frame, input_ack_frame, input_session_frame
from .logsetup import get_logger
from .platform.protocol import ForegroundApp, InputHoldError, PlatformSet

log = get_logger(__name__)

Sender = Callable[[dict[str, Any]], Awaitable[bool]]
SessionState = Literal["live", "suspended", "ended"]
EndReason = Literal[
    "stopped",
    "lease_expired",
    "takeover",
    "controller_revoked",
    "grant_removed",
    "pc_switch",
    "controller_disconnected",
    "session_locked",
    "secure_desktop",
    "remote_disabled",
    "agent_restart",
]
HOLDS_FILENAME = "input_holds.json"
POINTER_EVENTS = frozenset({"pointer_move", "pointer_button", "pointer_scroll"})
KEYBOARD_EVENTS = frozenset({"text", "key", "shortcut"})
RETIRED_IDS_KEPT = 64
ERROR_FRAME_INTERVAL = 1.0  # per code per session
CLOCK_WINDOW_SECONDS = 30.0  # > input_batch_lifetime + 2 x max_clock_skew: older batches fail the envelope window
TARGET_CHANGE_GRACE_SECONDS = 2.0
INFLIGHT_WAIT_SECONDS = 2.0


class AgeEstimator:
    """Age of a stamped message judged against the sender's own clock (finding: phone/PC skew).

    ``delta = agent_now - sender_stamp`` equals ``latency - offset`` (offset = sender clock minus agent
    clock). The windowed minimum of ``delta`` is ``min_latency - offset``, so ``delta - minimum`` is the
    extra delay of THIS message whatever the constant offset: a phone 2 s behind no longer makes every
    batch stale, a phone 2 s ahead no longer hides a stall. The baseline is clamped to what the envelope
    window can admit (``[-max_skew, lifetime + max_skew]``), and samples older than ``window_seconds``
    expire so a phone clock correction is followed within that window."""

    def __init__(self, *, lower: float, upper: float, window_seconds: float = CLOCK_WINDOW_SECONDS) -> None:
        self.lower = lower
        self.upper = upper
        self.window_seconds = window_seconds
        self._samples: deque[tuple[float, float]] = deque()  # (monotonic, delta), deltas increasing

    def observe(self, delta: float, now: float | None = None) -> None:
        t = time.monotonic() if now is None else now
        while self._samples and self._samples[0][0] < t - self.window_seconds:
            self._samples.popleft()
        while self._samples and self._samples[-1][1] >= delta:
            self._samples.pop()
        self._samples.append((t, delta))

    @property
    def baseline(self) -> float | None:
        if not self._samples:
            return None
        return max(self.lower, min(self.upper, self._samples[0][1]))

    def age(self, delta: float) -> float:
        """Extra delay of a message with this ``delta`` over the fastest recent one (never negative)."""
        base = self.baseline
        if base is None:
            return max(0.0, delta)
        return max(0.0, delta - base)


def new_session_id() -> str:
    """22 base64url characters (16 random bytes), matching ``^[A-Za-z0-9_-]{22}$``."""
    return secrets.token_urlsafe(16)


@dataclass(slots=True)
class InputSession:
    input_session_id: str
    controller_id: str
    kid: str
    pointer: bool
    keyboard: bool
    started_at: float  # monotonic
    lease_expires_at: float  # monotonic
    state: SessionState = "live"
    last_seq: int = 0
    accepted_events: int = 0
    dropped_events: int = 0
    held_buttons: set[str] = field(default_factory=set)
    held_keys: set[str] = field(default_factory=set)
    target: tuple[str, int] | None = None  # foreground identity the keyboard events are bound to
    keyboard_blocked: bool = False  # the target changed: keyboard events are dropped until re-capture
    blocked_at: float = 0.0  # monotonic time the block was set
    end_reason: str | None = None
    clock: AgeEstimator | None = None  # the owning phone's clock offset (issued_at vs our receipt)

    @property
    def live(self) -> bool:
        return self.state == "live"

    def lease_expires_text(self) -> str:
        remaining = max(0.0, self.lease_expires_at - time.monotonic())
        return format_rfc3339(now_utc() + timedelta(seconds=remaining))

    def pc_state(self) -> dict[str, Any]:
        return {
            "controller_id": self.controller_id,
            "pointer": self.pointer,
            "keyboard": self.keyboard,
            "lease_expires_at": self.lease_expires_text(),
        }


@dataclass(slots=True)
class _Pending:
    session_id: str
    seq: int
    events: list[dict[str, Any]]
    enqueued_at: float
    issued_mono: float = 0.0  # estimated monotonic time the phone issued the batch (enqueued_at - age)


@dataclass(slots=True)
class _DispatchOutcome:
    accepted: int = 0
    dropped: int = 0
    errors: list[tuple[str, str]] = field(default_factory=list)
    target_changed: bool = False


def coalesce_events(events: Iterable[dict[str, Any]], motion_max: int = 1 << 20) -> list[tuple[dict[str, Any], int]]:
    """Sum ADJACENT ``pointer_move`` events; never merge across a button, scroll, text, key or shortcut
    event; preserve order. Returns ``(event, number_of_original_events)`` pairs so acks count what the
    phone sent. A zero-motion result is kept (counted, nothing injected)."""
    out: list[tuple[dict[str, Any], int]] = []
    for ev in events:
        if ev.get("type") == "pointer_move" and out and out[-1][0].get("type") == "pointer_move":
            prev, n = out[-1]
            dx = max(-motion_max, min(motion_max, int(prev["dx"]) + int(ev["dx"])))
            dy = max(-motion_max, min(motion_max, int(prev["dy"]) + int(ev["dy"])))
            out[-1] = ({"type": "pointer_move", "dx": dx, "dy": dy}, n + 1)
            continue
        out.append((dict(ev), 1))
    return out


class InputSessionManager:
    def __init__(
        self,
        *,
        platform: PlatformSet,
        send: Sender,
        pc_id: Callable[[], str | None],
        state_dir: Path,
        session_locked: Callable[[], Awaitable[bool]],
        remote_enabled: Callable[[], bool],
        on_state_change: Callable[[], None],
        lease_seconds: float | None = None,
        age_budget_ms: int | None = None,
        ack_interval: float = 0.25,
        watchdog_interval: float = 0.1,
        lock_poll_seconds: float = 1.0,
        foreground_refresh_seconds: float = 2.0,
    ) -> None:
        limits = load_registry().limits
        self._platform = platform
        self._send = send
        self._pc_id = pc_id
        self._holds_path = state_dir / HOLDS_FILENAME
        self._session_locked = session_locked
        self._remote_enabled = remote_enabled
        self._on_state_change = on_state_change
        self.lease_seconds = float(lease_seconds if lease_seconds is not None else limits["input_lease_seconds"])
        self.age_budget_ms = int(age_budget_ms if age_budget_ms is not None else limits["input_age_budget_ms"])
        self.max_batch_events = int(limits["input_batch_max_events"])
        self.max_clock_skew = float(limits["max_clock_skew_seconds"])
        self.batch_lifetime = float(limits["input_batch_lifetime_seconds"])
        self.target_change_grace_seconds = TARGET_CHANGE_GRACE_SECONDS
        self.inflight_wait_seconds = INFLIGHT_WAIT_SECONDS
        self.ack_interval = ack_interval
        self.watchdog_interval = watchdog_interval
        self.lock_poll_seconds = lock_poll_seconds
        self.foreground_refresh_seconds = foreground_refresh_seconds
        self._current: InputSession | None = None
        self._retired: deque[str] = deque(maxlen=RETIRED_IDS_KEPT)
        self._queue: deque[_Pending] = deque()
        self._wake = asyncio.Event()
        self._worker: asyncio.Task[None] | None = None
        self._watchdog: asyncio.Task[None] | None = None
        self._inflight: asyncio.Future[Any] | None = None
        self._ack_task: asyncio.Task[None] | None = None
        self._last_ack_at = 0.0
        self._last_error_at: dict[str, float] = {}
        self._foreground: ForegroundApp | None = None
        self._foreground_at = 0.0
        self._restricted = False
        self._pending_restart_frame: dict[str, Any] | None = None
        self._relay_clock = self._new_estimator()
        self._relay_clock_conn: str | None = None
        self._end_lock = asyncio.Lock()
        self.acks_sent = 0
        self.sessions_started = 0

    # ----- lifecycle --------------------------------------------------------------------------------------
    def _new_estimator(self) -> AgeEstimator:
        return AgeEstimator(lower=-self.max_clock_skew, upper=self.batch_lifetime + self.max_clock_skew)

    def start(self) -> None:
        if self._worker is None or self._worker.done():
            self._worker = asyncio.get_running_loop().create_task(self._run(), name="dome-input-dispatch")

    async def shutdown(self) -> None:
        """Agent stopping: end the session (``agent_restart``) and release its holds now."""
        if self._current is not None:
            await self.end("agent_restart")
        for task in (self._worker, self._watchdog, self._ack_task):
            if task is not None and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        self._worker = None

    @property
    def current(self) -> InputSession | None:
        return self._current

    @property
    def live_session(self) -> InputSession | None:
        s = self._current
        return s if s is not None and s.live else None

    # ----- crash recovery ---------------------------------------------------------------------------------
    async def recover_after_restart(self) -> int:
        """Release buttons/keys a previous agent process still held when it died (``input_holds.json``,
        content-free). The ``input_session{ended, agent_restart}`` frame is queued until the relay is
        back (sent by :meth:`on_snapshot_applied`). Returns the number of releases."""
        data = self._read_holds()
        if data is None:
            return 0
        buttons = {b for b in data.get("held_buttons", []) if isinstance(b, str)}
        keys = {k for k in data.get("held_keys", []) if isinstance(k, str)}
        released = 0
        if buttons or keys:
            try:
                released = int(await asyncio.to_thread(self._platform.input.release, buttons, keys))
            except ProtocolError as exc:
                if exc.code == "PLATFORM_UNSUPPORTED":
                    log.warning("recovery file names held input but this platform cannot inject; cleared")
                else:
                    log.error("could not release input held by the previous agent process", code=exc.code)
                    return 0  # keep the file: the next start-up tries again
        self._delete_holds()
        session_id = data.get("input_session_id")
        controller_id = data.get("controller_id")
        pc_id = self._pc_id()
        if isinstance(session_id, str) and isinstance(controller_id, str) and pc_id is not None:
            self._pending_restart_frame = input_session_frame(
                pc_id, session_id, controller_id, event="ended", reason="agent_restart", holds_released=released
            )
        log.warning("input holds of a previous agent process released", released=released)
        return released

    async def on_snapshot_applied(self) -> None:
        """Relay is back and the first snapshot applied: deliver the queued restart notice, if any."""
        frame = self._pending_restart_frame
        if frame is not None and await self._send(frame):
            self._pending_restart_frame = None

    # ----- session lifecycle -----------------------------------------------------------------------------
    async def start_session(
        self,
        *,
        controller_id: str,
        kid: str,
        pointer: bool,
        keyboard: bool,
        takeover: bool,
        controller_issued_at: str | None = None,
    ) -> InputSession:
        if not pointer and not keyboard:
            raise ProtocolError("INPUT_NOT_PERMITTED", load_registry().errors["INPUT_NOT_PERMITTED"]["user_message"])
        if not self._remote_enabled():
            raise ProtocolError("PC_REMOTE_DISABLED", load_registry().errors["PC_REMOTE_DISABLED"]["user_message"])
        if await self._session_locked():
            raise ProtocolError("PC_SESSION_LOCKED", load_registry().errors["PC_SESSION_LOCKED"]["user_message"])
        existing = self._current
        if existing is not None and existing.state != "ended":
            if existing.controller_id != controller_id:
                if not takeover:
                    raise ProtocolError(
                        "INPUT_SESSION_OWNED", load_registry().errors["INPUT_SESSION_OWNED"]["user_message"]
                    )
                await self.end("takeover")  # releases the other phone's holds BEFORE the new session exists
            else:
                await self.end("stopped")  # same phone again: a restart (PC switch / page reload) replaces it
        now = time.monotonic()
        session = InputSession(
            input_session_id=new_session_id(),
            controller_id=controller_id,
            kid=kid,
            pointer=pointer,
            keyboard=keyboard,
            started_at=now,
            lease_expires_at=now + self.lease_seconds,
        )
        session.clock = self._new_estimator()
        if controller_issued_at is not None:
            # Seed the phone-clock baseline with the start command itself, so even the first batch after a
            # stall is judged against a real sample (a later, faster batch lowers the baseline anyway).
            with contextlib.suppress(ValueError, TypeError):
                session.clock.observe((now_utc() - parse_rfc3339(controller_issued_at)).total_seconds())
        fg = await self.refresh_foreground(force=True)
        session.target = fg.identity if fg is not None else None
        self._current = session
        self.sessions_started += 1
        self._last_ack_at = 0.0
        self._last_error_at.clear()
        self._watchdog = asyncio.get_running_loop().create_task(self._watch(session), name="dome-input-watchdog")
        log.info(
            "input session started",
            controller_id=controller_id,
            pointer=pointer,
            keyboard=keyboard,
            takeover=takeover,
            lease_seconds=self.lease_seconds,
        )
        await self._emit_session(session, "started", "started", 0)
        self._on_state_change()
        return session

    async def stop_session(self, *, controller_id: str, input_session_id: str) -> tuple[bool, int]:
        """``input.session_stop``: only the owner can stop its own live/suspended session. Idempotent."""
        session = self._current
        if (
            session is None
            or session.state == "ended"
            or session.input_session_id != input_session_id
            or session.controller_id != controller_id
        ):
            return False, 0
        released = await self.end("stopped")
        return True, released

    async def end(self, reason: EndReason) -> int:
        """End the current session (if any) for ``reason``; returns the number of holds released."""
        session = self._current
        if session is None or session.state == "ended":
            return 0
        return await self._end(session, reason)

    async def end_for_controller(self, controller_id: str, reason: EndReason) -> int:
        session = self._current
        if session is None or session.state == "ended" or session.controller_id != controller_id:
            return 0
        return await self._end(session, reason)

    async def apply_capabilities(self, controller_id: str, effective: Iterable[str]) -> None:
        """A snapshot or a local grant change narrowed/widened what the owner may do. Both input
        capabilities gone → ``grant_removed``; otherwise the session's flags follow the grant."""
        session = self._current
        if session is None or session.state == "ended" or session.controller_id != controller_id:
            return
        caps = set(effective)
        pointer, keyboard = "pointer" in caps, "keyboard" in caps
        if not pointer and not keyboard:
            await self._end(session, "grant_removed")
            return
        if (pointer, keyboard) != (session.pointer, session.keyboard):
            session.pointer, session.keyboard = pointer, keyboard
            if not pointer and session.held_buttons:
                await self._release(session)  # a drag cannot continue without the pointer capability
            log.info("input session capabilities changed", pointer=pointer, keyboard=keyboard)
            self._on_state_change()

    async def _end(self, session: InputSession, reason: EndReason) -> int:
        async with self._end_lock:
            if session.state == "ended":
                return 0
            # 1. retire the id so a delayed batch can never press anything again
            session.state = "ended"
            session.end_reason = reason
            self._retired.append(session.input_session_id)
            if self._current is session:
                self._current = None
            self._queue.clear()
            if not await self._await_inflight():
                self._release_when_done(session)
            # 2. release exactly what this session injected
            released = await self._release(session)
            # 3. tell the phone(s)
            for task in (self._watchdog, self._ack_task):
                if task is not None and not task.done() and task is not asyncio.current_task():
                    task.cancel()
            self._ack_task = None
            log.info(
                "input session ended",
                reason=reason,
                holds_released=released,
                accepted_events=session.accepted_events,
                dropped_events=session.dropped_events,
            )
            await self._emit_session(session, "ended", reason, released)
            self._on_state_change()
            return released

    async def _suspend(self, session: InputSession) -> None:
        """Backpressure: discard the backlog, release holds, keep the id answering INPUT_SUSPENDED."""
        if session.state != "live":
            return
        session.state = "suspended"
        self._queue.clear()
        if not await self._await_inflight():
            self._release_when_done(session)
        released = await self._release(session)
        log.warning("input session suspended: dispatch fell behind the age budget", holds_released=released)
        await self._emit_session(session, "suspended", "backpressure", released)
        self._on_state_change()

    async def _release(self, session: InputSession) -> int:
        buttons, keys = set(session.held_buttons), set(session.held_keys)
        if not buttons and not keys:
            self._sync_holds_file(session)
            return 0
        try:
            released = int(await asyncio.to_thread(self._platform.input.release, buttons, keys))
        except ProtocolError as exc:
            if exc.code == "PLATFORM_UNSUPPORTED":
                released = 0
            else:
                # The holds may be stuck: keep the recovery file so the next start-up retries the release.
                log.error("releasing held input failed", code=exc.code, buttons=len(buttons), keys=len(keys))
                return 0
        except Exception as exc:  # noqa: BLE001 - adapter bug must not keep the session alive
            log.error("input adapter raised on release", error=exc.__class__.__name__)
            return 0
        session.held_buttons.difference_update(buttons)
        session.held_keys.difference_update(keys)
        self._sync_holds_file(session)
        return released

    def _sync_holds_file(self, session: InputSession) -> None:
        """After releasing ``session``'s holds: the recovery file describes the CURRENT session only. A late
        release of an old session must not delete the file of a newer one that holds something."""
        current = self._current
        if current is not None and current is not session:
            self._persist_holds(current)
        elif session.held_buttons or session.held_keys:
            self._persist_holds(session)
        else:
            self._delete_holds()

    async def _await_inflight(self) -> bool:
        """Wait (bounded) for the running dispatch; False when it is still running after the wait."""
        fut = self._inflight
        if fut is None or fut.done():
            return True
        with contextlib.suppress(Exception):
            await asyncio.wait_for(asyncio.shield(fut), timeout=self.inflight_wait_seconds)
        return fut.done()

    def _release_when_done(self, session: InputSession) -> None:
        """The adapter thread outlived the bounded wait (SendInput blocking): whatever it still presses
        after our release must be released as soon as it returns, not left to the next restart."""
        fut = self._inflight
        if fut is None:
            return
        loop = asyncio.get_running_loop()

        def _done(_f: asyncio.Future[Any]) -> None:
            if session.held_buttons or session.held_keys:
                log.warning("releasing input pressed after the session ended (slow adapter call)")
                loop.create_task(self._release(session), name="dome-input-late-release")

        fut.add_done_callback(_done)

    # ----- batches ------------------------------------------------------------------------------------------
    async def handle_batch(
        self, vb: VerifiedInputBatch, *, relay_received_at: str | None = None, relay_connection_id: str | None = None
    ) -> bool:
        """Accept (enqueue) or drop one verified batch. Returns True when accepted.

        ``relay_received_at`` (the relay's stamp in ``relay_to_agent_input_batch.relay``) additionally
        bounds the relay → agent leg against the relay's own clock offset."""
        session = self._current
        sid = vb.input_session_id
        count = len(vb.payload["events"])
        if session is None or session.state == "ended":
            code = "INPUT_SESSION_EXPIRED" if sid in self._retired else "INPUT_SESSION_REQUIRED"
            await self._report(code, session_id=sid, controller_id=vb.key.controller_id)
            return False
        if sid != session.input_session_id or vb.key.controller_id != session.controller_id:
            code = "INPUT_SESSION_EXPIRED" if sid in self._retired else "INPUT_SESSION_REQUIRED"
            await self._report(code, session_id=sid, controller_id=vb.key.controller_id)
            return False
        if session.state == "suspended":
            session.dropped_events += count
            await self._report("INPUT_SUSPENDED", session=session)
            return False
        if vb.seq <= session.last_seq:
            session.dropped_events += count
            await self._report("INPUT_SEQUENCE_INVALID", session=session)
            self._schedule_ack(session)
            return False
        age = self._batch_age(session, vb, relay_received_at, relay_connection_id)
        if age * 1000.0 > self.age_budget_ms:
            session.dropped_events += count
            await self._report("INPUT_STALE", session=session)
            self._schedule_ack(session)
            return False
        needed = vb.required_capabilities
        if ("pointer" in needed and not session.pointer) or ("keyboard" in needed and not session.keyboard):
            session.dropped_events += count
            await self._report("INPUT_NOT_PERMITTED", session=session)
            self._schedule_ack(session)
            return False
        session.last_seq = vb.seq
        session.lease_expires_at = time.monotonic() + self.lease_seconds
        if count:
            now = time.monotonic()
            self._queue.append(_Pending(sid, vb.seq, list(vb.payload["events"]), now, now - age))
            self._wake.set()
        else:
            self._schedule_ack(session)  # keepalive: the phone still learns last_seq/holds at ≤ 4/s
        return True

    def _batch_age(
        self,
        session: InputSession,
        vb: VerifiedInputBatch,
        relay_received_at: str | None,
        relay_connection_id: str | None,
    ) -> float:
        """Seconds this batch spent in transit beyond the fastest recent batch of the same phone (phone
        clock offset removed), or beyond the fastest recent relay hop when that is larger."""
        now = now_utc()
        mono = time.monotonic()
        if session.clock is None:
            session.clock = self._new_estimator()
        delta = vb.age_seconds(now)
        session.clock.observe(delta, mono)
        age = session.clock.age(delta)
        if relay_received_at is not None:
            try:
                relay_delta = (now - parse_rfc3339(relay_received_at)).total_seconds()
            except (ValueError, TypeError):
                relay_delta = None
            if relay_delta is not None:
                if relay_connection_id != self._relay_clock_conn:
                    self._relay_clock = self._new_estimator()  # a new relay connection/instance: new clock
                    self._relay_clock_conn = relay_connection_id
                self._relay_clock.observe(relay_delta, mono)
                age = max(age, self._relay_clock.age(relay_delta))
        return age

    async def _run(self) -> None:
        while True:
            if not self._queue:
                self._wake.clear()
                await self._wake.wait()
                continue
            pending = self._queue.popleft()
            session = self._current
            if session is None or not session.live or session.input_session_id != pending.session_id:
                continue  # retired while queued: never injected
            lag_ms = (time.monotonic() - pending.enqueued_at) * 1000.0
            if lag_ms > self.age_budget_ms:
                session.dropped_events += len(pending.events) + sum(len(p.events) for p in self._queue)
                await self._suspend(session)
                continue
            try:
                fut = asyncio.get_running_loop().run_in_executor(None, self._dispatch_sync, session, pending)
                self._inflight = fut
                outcome = await fut
            except Exception as exc:  # noqa: BLE001 - never let the worker die
                log.error("input dispatch failed", error=exc.__class__.__name__)
                session.dropped_events += len(pending.events)
                continue
            finally:
                self._inflight = None
            session.accepted_events += outcome.accepted
            session.dropped_events += outcome.dropped
            if outcome.target_changed:
                await self.refresh_foreground(force=True)  # state frame: the phone sees the new window
            if any(code == "INPUT_RESTRICTED" for code, _m in outcome.errors):
                await self._probe_restricted()  # state frame: pc_state.input_restricted reaches the phone
            for code, message in outcome.errors:
                await self._report(code, message, session=session)
            if session.live:
                self._schedule_ack(session)

    def _dispatch_sync(self, session: InputSession, pending: _Pending) -> _DispatchOutcome:
        """Runs in a worker thread: inject in order, track holds, enforce the keyboard target rule."""
        adapter = self._platform.input
        out = _DispatchOutcome()
        coalesced = coalesce_events(pending.events)
        restricted: bool | None = None  # probed live, once per batch, at the first keyboard event
        for index, (ev, n) in enumerate(coalesced):
            if not session.live:  # retired meanwhile: stop immediately, nothing more is pressed
                out.dropped += n
                continue
            kind = ev["type"]
            try:
                if kind in KEYBOARD_EVENTS:
                    if session.keyboard_blocked:
                        if pending.issued_mono - session.blocked_at >= self.target_change_grace_seconds:
                            # Issued well after the change was announced in pc_state.foreground_app: the
                            # phone paused live typing and the customer deliberately continues.
                            session.keyboard_blocked = False
                            self._capture_target(session)
                        else:
                            out.dropped += n  # queued before the phone could know: never typed elsewhere
                            continue
                    if restricted is None:
                        restricted = self._restricted_now()
                    if restricted:
                        # An elevated window (or protected desktop) has the keyboard focus: Windows UIPI
                        # drops such input WITHOUT an error, so it must not be acked as accepted.
                        raise ProtocolError(
                            "INPUT_RESTRICTED",
                            load_registry().errors["INPUT_RESTRICTED"]["user_message"],
                        )
                    if not self._target_still_in_front(session):
                        session.keyboard_blocked = True
                        session.blocked_at = time.monotonic()
                        out.target_changed = True
                        out.errors.append(("INPUT_TARGET_CHANGED", ""))
                        out.dropped += n
                        continue
                if kind == "pointer_move":
                    if ev["dx"] or ev["dy"]:
                        adapter.move(int(ev["dx"]), int(ev["dy"]))
                elif kind == "pointer_scroll":
                    adapter.scroll(int(ev["dx"]), int(ev["dy"]))
                elif kind == "pointer_button":
                    button, action = str(ev["button"]), str(ev["action"])
                    adapter.button(button, action)
                    if action == "down":
                        session.held_buttons.add(button)
                        self._persist_holds(session)
                    elif button in session.held_buttons:
                        # up, and click/double_click (down+up) on a held button: Windows released it
                        session.held_buttons.discard(button)
                        self._persist_holds(session)
                    if action in ("down", "click", "double_click"):
                        # a user-directed click selects the target (and ends a target-change block)
                        self._capture_target(session)
                        session.keyboard_blocked = False
                elif kind == "text":
                    adapter.text(str(ev["text"]))
                elif kind == "key":
                    adapter.key(str(ev["key"]))
                elif kind == "shortcut":
                    name = str(ev["name"])
                    held = {"ctrl", name}  # the modifier and the shortcut's letter key, for crash recovery
                    session.held_keys.update(held)
                    self._persist_holds(session)
                    stuck: frozenset[str] = frozenset()
                    try:
                        adapter.shortcut(name)
                    except InputHoldError as exc:
                        stuck = exc.stuck_keys  # the adapter's own recovery release failed too
                        raise
                    finally:
                        session.held_keys.difference_update(held - stuck)
                        self._persist_holds(session)
                else:
                    raise ProtocolError("MALFORMED_MESSAGE", f"unknown input event type {kind}")
                out.accepted += n
            except ProtocolError as exc:
                # One failed event drops the rest of the batch: nothing runs out of order.
                out.dropped += sum(count for _ev, count in coalesced[index:])
                out.errors.append((exc.code, exc.message))
                break
        return out

    def _restricted_now(self) -> bool:
        try:
            return bool(self._platform.input.input_restricted())
        except Exception:  # noqa: BLE001 - a failed probe is not evidence of a restriction
            return False

    def _target_still_in_front(self, session: InputSession) -> bool:
        try:
            fg = self._platform.input.foreground()
        except Exception:  # noqa: BLE001 - unknown foreground: do not block typing on a probe failure
            return True
        current = fg.identity if fg is not None else None
        if session.target is None:
            session.target = current
            return True
        return current == session.target

    def _capture_target(self, session: InputSession) -> None:
        try:
            fg = self._platform.input.foreground()
        except Exception:  # noqa: BLE001
            return
        session.target = fg.identity if fg is not None else None

    # ----- watchdog ----------------------------------------------------------------------------------------
    async def _watch(self, session: InputSession) -> None:
        next_lock_check = 0.0
        next_fg = 0.0
        while True:
            await asyncio.sleep(self.watchdog_interval)
            if session.end_reason is not None:
                return
            now = time.monotonic()
            try:
                if now >= session.lease_expires_at:
                    await self._end(session, "lease_expired")
                    return
                if not self._remote_enabled():
                    await self._end(session, "remote_disabled")
                    return
                if now >= next_lock_check:
                    next_lock_check = now + self.lock_poll_seconds
                    if await self._session_locked():
                        await self._end(session, "session_locked")
                        return
                    # Ask the adapter WHICH restriction it is, live: only a secure desktop (lock screen,
                    # sign-in, UAC consent) ends the session. An elevated or unknown-integrity window on
                    # the normal desktop only restricts (pc_state.input_restricted, INPUT_RESTRICTED per
                    # batch); the cached foreground is never used for this decision.
                    if await self._probe_secure_desktop():
                        self._restricted = True
                        await self._end(session, "secure_desktop")
                        return
                    await self._probe_restricted()
                if now >= next_fg:
                    next_fg = now + self.foreground_refresh_seconds
                    await self.refresh_foreground(force=True)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("input watchdog check failed", error=exc.__class__.__name__)

    async def _probe_secure_desktop(self) -> bool:
        try:
            return bool(await asyncio.to_thread(self._platform.input.secure_desktop_active))
        except Exception:  # noqa: BLE001 - a failed probe is not evidence of a secure desktop
            return False

    async def _probe_restricted(self) -> bool:
        before = self._restricted
        try:
            self._restricted = bool(await asyncio.to_thread(self._platform.input.input_restricted))
        except Exception:  # noqa: BLE001
            self._restricted = False
        if self._restricted != before and self.live_session is not None:
            self._on_state_change()  # the phone shows "restricted" without waiting for the periodic frame
        return self._restricted

    # ----- foreground / state ----------------------------------------------------------------------------
    async def refresh_foreground(self, *, force: bool = False) -> ForegroundApp | None:
        """Refresh at most every ``foreground_refresh_seconds`` while a session is live, otherwise on request."""
        now = time.monotonic()
        if not force and self.live_session is not None and now - self._foreground_at < self.foreground_refresh_seconds:
            return self._foreground
        try:
            fg = await asyncio.to_thread(self._platform.input.foreground)
        except Exception as exc:  # noqa: BLE001 - PLATFORM_UNSUPPORTED or an API failure: unknown, not invented
            log.debug("foreground probe failed", error=exc.__class__.__name__)
            fg = None
        changed = (fg.identity if fg else None) != (self._foreground.identity if self._foreground else None)
        self._foreground = fg
        self._foreground_at = now
        if changed and self.live_session is not None:
            self._on_state_change()
        return fg

    async def state_fields(self) -> dict[str, Any]:
        """``pc_state.foreground_app`` / ``input_session`` / ``input_restricted``."""
        session = self.live_session
        if session is None:
            await self.refresh_foreground(force=True)
            await self._probe_restricted()
        else:
            await self.refresh_foreground()
        fg = self._foreground
        return {
            "foreground_app": fg.as_result() if fg is not None else None,
            "input_session": session.pc_state() if session is not None else None,
            "input_restricted": bool(self._restricted),
        }

    @property
    def foreground(self) -> ForegroundApp | None:
        return self._foreground

    def summary(self) -> dict[str, Any]:
        s = self._current
        return {
            "session": None
            if s is None
            else {
                "controller_id": s.controller_id,
                "state": s.state,
                "pointer": s.pointer,
                "keyboard": s.keyboard,
                "last_seq": s.last_seq,
                "accepted_events": s.accepted_events,
                "dropped_events": s.dropped_events,
                "held_buttons": sorted(s.held_buttons),
                "held_keys": sorted(s.held_keys),
                "lease_expires_at": s.lease_expires_text(),
            },
            "sessions_started": self.sessions_started,
            "acks_sent": self.acks_sent,
            "input_restricted": self._restricted,
            "foreground_process": self._foreground.process_name if self._foreground else None,
        }

    # ----- frames --------------------------------------------------------------------------------------------
    def _schedule_ack(self, session: InputSession) -> None:
        if self._ack_task is not None and not self._ack_task.done():
            return  # one is already due; it will carry the latest counts
        delay = max(0.0, self._last_ack_at + self.ack_interval - time.monotonic())
        self._ack_task = asyncio.get_running_loop().create_task(self._ack_later(session, delay), name="dome-input-ack")

    async def _ack_later(self, session: InputSession, delay: float) -> None:
        if delay > 0:
            await asyncio.sleep(delay)
        if session.state == "ended":
            return
        pc_id = self._pc_id()
        if pc_id is None:
            return
        self._last_ack_at = time.monotonic()
        frame = input_ack_frame(
            pc_id,
            session.input_session_id,
            last_seq=session.last_seq,
            accepted_events=session.accepted_events,
            dropped_events=session.dropped_events,
            held_buttons=sorted(session.held_buttons),
            held_keys=sorted(session.held_keys),
        )
        if await self._send(frame):
            self.acks_sent += 1

    async def _emit_session(self, session: InputSession, event: str, reason: str, holds_released: int) -> None:
        pc_id = self._pc_id()
        if pc_id is None:
            return
        await self._send(
            input_session_frame(
                pc_id,
                session.input_session_id,
                session.controller_id,
                event=event,
                reason=reason,
                holds_released=holds_released,
            )
        )

    async def _report(
        self,
        code: str,
        message: str | None = None,
        *,
        session: InputSession | None = None,
        session_id: str | None = None,
        controller_id: str | None = None,
    ) -> None:
        """Error frame for a dropped batch/event, at most one per code per second (counts go in the ack). It names
        the batch's session and controller so the relay can deliver it to that phone (rules.input_sessions)."""
        now = time.monotonic()
        if now - self._last_error_at.get(code, 0.0) < ERROR_FRAME_INTERVAL:
            return
        self._last_error_at[code] = now
        defaults = load_registry().errors.get(code, {})
        text = message or str(defaults.get("user_message", code))
        sid = session.input_session_id if session is not None else session_id
        cid = session.controller_id if session is not None else controller_id
        log.info("input batch dropped", code=code, session=sid is not None)
        await self._send(
            error_frame(
                (code, text, bool(defaults.get("retryable", False))), ref_input_session_id=sid, ref_controller_id=cid
            )
        )

    # ----- recovery file ---------------------------------------------------------------------------------------
    def _persist_holds(self, session: InputSession) -> None:
        """Tiny, content-free: session/controller ids and the held button/key NAMES only."""
        if not session.held_buttons and not session.held_keys:
            self._delete_holds()
            return
        data = {
            "input_session_id": session.input_session_id,
            "controller_id": session.controller_id,
            "held_buttons": sorted(session.held_buttons),
            "held_keys": sorted(session.held_keys),
        }
        try:
            self._holds_path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=".holds-", dir=str(self._holds_path.parent))
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh)
            os.replace(tmp, self._holds_path)
        except OSError as exc:
            log.warning("could not write the input recovery file", error=exc.__class__.__name__)

    def _read_holds(self) -> dict[str, Any] | None:
        try:
            raw = self._holds_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError:
            return None
        try:
            data = json.loads(raw)
        except ValueError:
            self._delete_holds()
            return None
        return data if isinstance(data, dict) else None

    def _delete_holds(self) -> None:
        with contextlib.suppress(OSError):
            self._holds_path.unlink()


__all__ = ["HOLDS_FILENAME", "InputSession", "InputSessionManager", "coalesce_events", "new_session_id"]
