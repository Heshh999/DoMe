"""The relay's Screen view service (docs/design/screen-view.md §12): what the routing code calls for ICE servers, the
start hook, the signaling and lifecycle frames, the allowance tickets and the socket-close paths, with the ledger
(``metering``) and the TURN bookkeeping (``turn``) underneath.

Rules that hold everywhere here: the relay never reads, stores or logs SDP, an address or a credential (frames are
forwarded verbatim and log lines carry ids, reasons and seconds); the ledger is charged on the relay's clock and never
on the agent's figures; a phone's TURN credential is revoked on every refusal and end path; a controller that is signed
out, revoked or absent for 60 s loses its session within one ticket (``controller_offline``). Everything a test needs
to vary (the switch, the override, the clock) lives in the replaceable :class:`ScreenConfig`.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from dome_protocol import Registry
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dome_api.db.engine import transaction
from dome_api.db.models import PC, Account, Controller, Grant, ScreenReservation
from dome_api.logging import get_logger
from dome_api.plans import Plan, plan_for
from dome_api.screen import metering
from dome_api.screen.metering import AllowanceExhaustedError, Limits, Ticket
from dome_api.screen.turn import DATA_SAVER_KBPS, TurnService
from dome_api.security import events
from dome_api.settings import Settings
from dome_api.util import ts, ts_required, utcnow

if TYPE_CHECKING:
    from dome_api.relay.manager import AgentConn, ConnectionManager, InFlight

log = get_logger("dome_api.screen")

SCREEN_SESSION_ID_RE = re.compile(r"\A[A-Za-z0-9_-]{22}\Z")
# how long the relay remembers a forwarded start without a result or a `started` frame: the controller's command
# lifetime plus the action's timeout
PENDING_START_SECONDS = 60 + 45
CONSENT_REFUSALS = {"SCREEN_CONSENT_DECLINED": "denied", "SCREEN_CONSENT_TIMEOUT": "timeout"}
CONSENT_REASONS = frozenset({"denied", "timeout", "backoff", "blocked"})
# relay-side end reasons that are not in the frame enum (the ledger's end_reason column, 32 chars)
START_FAILED = "start_failed"


@dataclass(frozen=True, slots=True)
class ScreenConfig:
    enabled: bool  # DOME_SCREEN_VIEW_ENABLED: the operator switch (starts and ICE requests only)
    stun_urls: list[str]
    free_seconds_override: int  # development/staging only; 0 = plans.json
    charge_from: str
    chunk_seconds: int
    connect_timeout_seconds: int
    pause_max_seconds: int
    start_lifetime_seconds: int
    consent_timeout_seconds: int = 30
    owner_grace_seconds: int = 60  # rules.screen_sessions (4): no bound socket for this long = controller_offline
    usage_per_minute: int = 2  # screen_usage frames accepted per session per minute (design §12)
    sweep_interval_seconds: float = 5.0
    clock: Callable[[], datetime] = utcnow

    @classmethod
    def from_settings(cls, settings: Settings, registry: Registry) -> ScreenConfig:
        limits = registry.limits
        return cls(
            enabled=settings.screen_view_enabled,
            stun_urls=settings.stun_url_list,
            free_seconds_override=settings.screen_free_seconds_override,
            charge_from=settings.screen_charge_from,
            chunk_seconds=int(limits["screen_allowance_chunk_seconds"]),
            connect_timeout_seconds=int(limits["screen_connect_timeout_seconds"]),
            pause_max_seconds=int(limits["screen_secure_desktop_pause_max_seconds"]),
            start_lifetime_seconds=int(limits["screen_start_command_lifetime_seconds"]),
            consent_timeout_seconds=int(limits["screen_consent_timeout_seconds"]),
            sweep_interval_seconds=settings.relay_sweep_interval_seconds,
        )


@dataclass(frozen=True, slots=True)
class IceAnswer:
    """The body of ``screen_ice_servers`` minus ``type``/``pc_id``."""

    ice_servers: list[dict[str, Any]]
    expires_at: datetime | None
    allowance: Ticket
    turn: str

    def frame(self, pc_id: uuid.UUID) -> dict[str, Any]:
        return {
            "type": "screen_ice_servers",
            "pc_id": str(pc_id),
            "ice_servers": self.ice_servers,
            "expires_at": ts(self.expires_at),
            "allowance": self.allowance.allowance_state(),
            "turn": self.turn,
        }


def duration_bucket(seconds: int) -> str:
    """Only a coarse bucket goes into the one structured line per session end (rules.screen_content)."""
    for limit, name in ((30, "lt_30s"), (300, "lt_5m"), (1800, "lt_30m"), (3600, "lt_1h")):
        if seconds < limit:
            return name
    return "ge_1h"


class ScreenService:
    def __init__(
        self,
        *,
        config: ScreenConfig,
        turn: TurnService,
        db: async_sessionmaker[AsyncSession],
        relay: ConnectionManager,
    ) -> None:
        self.config = config
        self.turn = turn
        self.db = db
        self.relay = relay
        self._sweeper: asyncio.Task[None] | None = None
        self._reconciler: asyncio.Task[None] | None = None
        self._health_cache: tuple[datetime, dict[str, Any]] | None = None

    # ----- plan limits --------------------------------------------------------------------------------------------
    def limits_for(self, plan: Plan) -> Limits:
        seconds = plan.screen_view_seconds_per_window
        if seconds is not None and self.config.free_seconds_override > 0:
            seconds = self.config.free_seconds_override
        return Limits(
            seconds_per_window=seconds,
            window_hours=plan.screen_view_window_hours,
            chunk_seconds=self.config.chunk_seconds,
            connect_timeout_seconds=self.config.connect_timeout_seconds,
            pause_max_seconds=self.config.pause_max_seconds,
            charge_from=self.config.charge_from,
            consent_timeout_seconds=self.config.consent_timeout_seconds,
        )

    async def _limits_for_account(self, db: AsyncSession, account_id: uuid.UUID) -> Limits:
        account = await db.get(Account, account_id)
        return self.limits_for(plan_for(account.plan if account else None))

    def now(self) -> datetime:
        return self.config.clock()

    # ----- ICE servers (controller -> relay screen_ice_request) ------------------------------------------------------
    async def ice_servers(
        self, db: AsyncSession, *, account_id: uuid.UUID, controller_id: uuid.UUID, plan: Plan, now: datetime
    ) -> IceAnswer:
        """After the router's own checks (socket version, switch, binding, grant, PC online, rate limits): the current
        allowance (refused when nothing is left) and the phone's TURN credential, STUN first."""
        limits = self.limits_for(plan)
        allowance = await metering.allowance(db, account_id, now, limits)
        if not allowance.unlimited and (allowance.remaining_seconds or 0) <= 0:
            raise AllowanceExhaustedError(allowance.resets_at)
        outcome = await self.turn.mint(
            db,
            account_id=account_id,
            controller_id=controller_id,
            free_plan=not plan.screen_view_unlimited,
            remaining_seconds=None if allowance.unlimited else allowance.remaining_seconds,
            now=now,
        )
        servers: list[dict[str, Any]] = [{"urls": list(self.config.stun_urls)}]
        if outcome.ice_server is not None:
            servers.append(outcome.ice_server)
        log.info("screen.ice_servers", controller_id=str(controller_id), turn=outcome.turn, reason=outcome.reason)
        return IceAnswer(ice_servers=servers, expires_at=outcome.expires_at, allowance=allowance, turn=outcome.turn)

    # ----- the start hook (route_command, screen.session_start) ------------------------------------------------------
    async def reserve_start(
        self,
        db: AsyncSession,
        *,
        account_id: uuid.UUID,
        pc_id: uuid.UUID,
        controller_id: uuid.UUID,
        command_id: uuid.UUID,
        plan: Plan,
        now: datetime,
    ) -> tuple[uuid.UUID, dict[str, Any]]:
        """Reserve the first chunk in the caller's transaction and build ``relay.screen`` for the forwarded command.
        Raises :class:`AllowanceExhaustedError`; the router turns it into the relay-origin failed result."""
        limits = self.limits_for(plan)
        try:
            reservation, ticket = await metering.reserve_start(
                db,
                account_id=account_id,
                pc_id=pc_id,
                controller_id=controller_id,
                command_id=command_id,
                now=now,
                limits=limits,
            )
        except AllowanceExhaustedError as exc:
            # the refusal's own transaction rolls back with the command row; the event is written by the router
            raise exc
        block: dict[str, Any] = {
            "granted_until": ts(ticket.granted_until),
            "unlimited": ticket.unlimited,
            "stun_urls": list(self.config.stun_urls),
        }
        if await self.turn.data_saver(db, now, free_plan=not plan.screen_view_unlimited):
            # the monthly guard's first step (design §13): relayed Free sessions get Data saver past the soft cap;
            # recorded with the start it applies to (design §12: turn_cap_reached{cap: soft}), in the same transaction
            block["relayed_max_kbps"] = DATA_SAVER_KBPS
            events.record(
                db,
                account_id=account_id,
                kind="turn_cap_reached",
                severity="notice",
                actor="system",
                subject_id=controller_id,
                detail={"cap": "soft", "relayed_max_kbps": DATA_SAVER_KBPS},
            )
        return reservation.id, block

    async def on_start_forwarded(
        self,
        agent: AgentConn,
        *,
        command_id: uuid.UUID,
        controller_id: uuid.UUID,
        controller_conn_id: uuid.UUID,
        account_id: uuid.UUID,
        reservation_id: uuid.UUID,
        now: datetime,
    ) -> None:
        from dome_api.relay.manager import PendingScreenStart

        agent.screen_pending_starts[command_id] = PendingScreenStart(
            controller_id=controller_id,
            controller_conn_id=controller_conn_id,
            account_id=account_id,
            reservation_id=reservation_id,
            forwarded_at=now,
        )
        async with transaction(self.db) as db:
            await self.turn.bind_start(db, controller_id, command_id)

    async def on_start_lost(self, reservation_id: uuid.UUID, controller_id: uuid.UUID, account_id: uuid.UUID) -> None:
        """The forward failed on the socket (the PC vanished between the check and the write): nothing ran. Only that
        start's credential is revoked (design §13), never one the phone minted since for its next start."""
        now = self.now()
        async with transaction(self.db) as db:
            limits = await self._limits_for_account(db, account_id)
            settled, _ = await metering.settle(db, reservation_id, now, limits, reason=START_FAILED)
            if settled is not None:
                await self.turn.revoke_for_start(
                    db, controller_id, settled.command_id, settled.forwarded_at, "start_refused", now
                )

    async def on_start_refused(self, controller_id: uuid.UUID, code: str) -> None:
        """Any relay-side refusal of a screen.session_start revokes the controller's credential (design §13)."""
        now = self.now()
        async with transaction(self.db) as db:
            await self.turn.revoke_controller(db, controller_id, "start_refused", now)
        log.info("screen.start_refused", controller_id=str(controller_id), code=code)

    async def allowance_exhausted_event(
        self, account_id: uuid.UUID, controller_id: uuid.UUID, pc_id: uuid.UUID, resets_at: datetime | None
    ) -> None:
        await self.relay.security_event(
            account_id=account_id,
            kind="screen_allowance_exhausted",
            severity="notice",
            actor="controller",
            subject_id=controller_id,
            detail={"pc_id": str(pc_id), "resets_at": ts(resets_at)},
        )

    async def on_start_result(self, agent: AgentConn, inf: InFlight, frame: dict[str, Any]) -> None:
        """The agent answered a forwarded screen.session_start. Success binds the session id to the reservation (the
        owner is learnt early, so the answer and the first ticket route); any other outcome settles the reservation
        (a start that never became a session is forgiven under the zero-charge rule) and revokes the credential."""
        pending = agent.screen_pending_starts.get(inf.command_id)
        if pending is None:
            return
        now = self.now()
        state = frame.get("state")
        if state == "succeeded":
            result = frame.get("result")
            sid = result.get("screen_session_id") if isinstance(result, dict) else None
            if isinstance(sid, str) and SCREEN_SESSION_ID_RE.match(sid):
                async with transaction(self.db) as db:
                    await metering.bind_session(db, pending.reservation_id, sid)
                    await self.turn.bind_session(db, pending.controller_id, sid)
                agent.remember_screen_session(sid, pending.controller_id)
            return
        error = frame.get("error") if isinstance(frame.get("error"), dict) else {}
        code = str(error.get("code", "")) if isinstance(error, dict) else ""
        agent.screen_pending_starts.pop(inf.command_id, None)
        async with transaction(self.db) as db:
            limits = await self._limits_for_account(db, pending.account_id)
            if code == "SCREEN_CONNECTION_FAILED":
                reason = "connection_failed"
            elif code in CONSENT_REFUSALS:
                reason = metering.CONSENT_REFUSED  # nothing was shown: the wait for the PC's owner is forgiven
            else:
                reason = START_FAILED
            await metering.settle(db, pending.reservation_id, now, limits, reason=reason)
            # only this start's credential: after a late failure the phone may already hold a newer one, live
            await self.turn.revoke_for_start(
                db, pending.controller_id, inf.command_id, pending.forwarded_at, "start_refused", now
            )
        if code in CONSENT_REFUSALS:
            detail = error.get("detail") if isinstance(error, dict) else None
            why = detail.get("reason") if isinstance(detail, dict) else None
            await self.relay.security_event(
                account_id=pending.account_id,
                kind="screen_consent_declined",
                severity="notice",
                actor="pc",
                subject_id=agent.pc_id,
                detail={"reason": why if why in CONSENT_REASONS else CONSENT_REFUSALS[code]},
            )
        log.info("screen.start_failed", pc_id=str(agent.pc_id), code=code or state)

    # ----- signaling: the PC-signed answer -------------------------------------------------------------------------
    async def on_screen_answer(self, agent: AgentConn, frame: dict[str, Any]) -> None:
        """Deliver the PC-signed answer to the issuing controller's 1.4 sockets only (rules.screen_signaling (5)).
        The envelope is forwarded verbatim and never read, logged or stored here."""
        controller_id = uuid.UUID(frame["ref_controller_id"])
        async with self.db() as db:
            ok = await self._controller_holds_screen(db, agent, controller_id)
        if ok is not None:
            log.warning("screen_answer.refused", pc_id=str(agent.pc_id), reason=ok)
            await self.relay.agent_rejection_event(agent, {"frame": "screen_answer", "reason": ok})
            return
        preferred = next(
            (p.controller_conn_id for p in agent.screen_pending_starts.values() if p.controller_id == controller_id),
            None,
        )
        out = {"type": "screen_answer", "pc_id": str(agent.pc_id), "envelope": frame["envelope"]}
        written = await self.relay.send_screen_frame_to_controller(controller_id, out, preferred_conn_id=preferred)
        log.info("screen.answer_routed", pc_id=str(agent.pc_id), controller_id=str(controller_id), sockets=len(written))

    async def _controller_holds_screen(
        self, db: AsyncSession, agent: AgentConn, controller_id: uuid.UUID
    ) -> str | None:
        """None when the controller is live on this PC's account with a live grant holding ``screen``; else why not."""
        ctrl = await db.get(Controller, controller_id)
        if ctrl is None or ctrl.account_id != agent.account_id:
            return "controller_not_in_account"
        if ctrl.revoked_at is not None:
            return "controller_revoked"
        grant = await db.scalar(
            select(Grant).where(
                Grant.controller_id == controller_id,
                Grant.pc_id == agent.pc_id,
                Grant.account_id == agent.account_id,
                Grant.revoked_at.is_(None),
            )
        )
        if grant is None or "screen" not in set(grant.capabilities):
            return "no_screen_grant"
        return None

    # ----- lifecycle frames -----------------------------------------------------------------------------------------
    async def _reservation_for(
        self, db: AsyncSession, agent: AgentConn, screen_session_id: str, controller_id: uuid.UUID | None
    ) -> ScreenReservation | None:
        """The live reservation behind a session: by its id, else the controller's pending start on this PC (the
        agent sent `started` before, or instead of, the result), else, when the relay lost that memory (the PC's
        socket dropped or the relay restarted between the forward and the result: the consent prompt and DTLS fit in
        that gap), the newest reservation of this PC that has no session yet (the controller's, when the frame names
        one), which is then bound."""
        r = ScreenReservation
        row = await db.scalar(
            select(r).where(r.screen_session_id == screen_session_id, r.state.in_(("reserved", "live")))
        )
        if row is not None:
            return row if row.pc_id == agent.pc_id else None
        for cid, pending in list(agent.screen_pending_starts.items()):
            if controller_id is None or pending.controller_id == controller_id:
                if await metering.bind_session(db, pending.reservation_id, screen_session_id):
                    await self.turn.bind_session(db, pending.controller_id, screen_session_id)
                    agent.screen_pending_starts.pop(cid, None)
                    bound: ScreenReservation | None = await db.scalar(select(r).where(r.id == pending.reservation_id))
                    return bound
        recent = self.now() - timedelta(seconds=PENDING_START_SECONDS)  # only a start the relay could still answer
        orphan_stmt = (
            select(r)
            .where(
                r.pc_id == agent.pc_id,
                r.screen_session_id.is_(None),
                r.state == "reserved",
                r.forwarded_at >= recent,
            )
            .order_by(r.forwarded_at.desc())
            .limit(1)
        )
        if controller_id is not None:
            orphan_stmt = orphan_stmt.where(r.controller_id == controller_id)
        orphan = await db.scalar(orphan_stmt)
        if orphan is None or not await metering.bind_session(db, orphan.id, screen_session_id):
            return None
        await self.turn.bind_session(db, orphan.controller_id, screen_session_id)
        log.info("screen.reservation_rebound", pc_id=str(agent.pc_id))
        rebound: ScreenReservation | None = await db.scalar(
            select(r).where(r.id == orphan.id).execution_options(populate_existing=True)
        )
        return rebound

    async def on_screen_session(self, agent: AgentConn, frame: dict[str, Any]) -> None:
        """``screen_session{started|paused|resumed|ended}``: settle the ledger on the relay's clock, remember or forget
        the owner, write the security events, deliver to the owner's 1.4 sockets and the PC's 1.4 subscribers."""
        controller_id = uuid.UUID(frame["controller_id"])
        sid: str = frame["screen_session_id"]
        event: str = frame["event"]
        reason: str = frame["reason"]
        now = self.now()
        known = agent.screen_sessions.get(sid)
        charged = 0
        async with transaction(self.db) as db:
            if known != controller_id:
                why = await self._controller_holds_screen(db, agent, controller_id)
                if why is not None and not (event == "ended" and why != "controller_not_in_account"):
                    # an `ended` for a just-revoked controller still reaches its sockets and the subscribers
                    log.warning("screen_session.refused", pc_id=str(agent.pc_id), reason=why)
                    await self.relay.agent_rejection_event(agent, {"frame": "screen_session", "reason": why})
                    return
            reservation = await self._reservation_for(db, agent, sid, controller_id)
            limits = await self._limits_for_account(db, agent.account_id)
            if reservation is None:
                # not reserved through this relay: the frame still travels, but no ticket will ever follow
                log.info("screen_session.unreserved", pc_id=str(agent.pc_id), frame_event=event)
            elif event == "started":
                await metering.mark_started(db, reservation.id, now, limits)
                for cid, pending in list(agent.screen_pending_starts.items()):
                    if pending.reservation_id == reservation.id:
                        agent.screen_pending_starts.pop(cid, None)
            elif event == "paused" and reason == "secure_desktop":
                await metering.mark_paused(db, reservation.id, now)
            elif event == "resumed" and reason == "secure_desktop":
                await metering.mark_resumed(db, reservation.id, now)
            elif event == "ended":
                _, charged = await metering.settle(db, reservation.id, now, limits, reason=reason)
                await self.turn.revoke_session(db, sid, "session_ended", now)
        if event == "started":
            agent.remember_screen_session(sid, controller_id)
            await self.relay.security_event(
                account_id=agent.account_id,
                kind="screen_session_started",
                severity="notice",
                actor="pc",
                subject_id=agent.pc_id,
                detail={"controller_id": str(controller_id), "path": frame.get("path")},
            )
        elif event == "ended":
            agent.forget_screen_session(sid)
            await self.relay.security_event(
                account_id=agent.account_id,
                kind="screen_session_ended",
                severity="notice",
                actor="pc",
                subject_id=agent.pc_id,
                detail={"controller_id": str(controller_id), "reason": reason, "charged_seconds": charged},
            )
            log.info(
                "screen.session_ended",
                pc_id=str(agent.pc_id),
                reason=reason,
                path=frame.get("path"),
                duration=duration_bucket(charged),
            )
        written = await self.relay.send_screen_frame_to_controller(controller_id, frame)
        await self.relay.broadcast_to_subscribers(agent.pc_id, frame, validate=False, screen_frame=True, skip=written)

    # ----- allowance tickets ----------------------------------------------------------------------------------------
    def _final(
        self, sid: str, unlimited: bool, reason: str | None, resets_at: datetime | None = None
    ) -> dict[str, Any]:
        frame: dict[str, Any] = {
            "type": "screen_allowance",
            "screen_session_id": sid,
            "granted_until": None,
            "unlimited": unlimited,
            "final": True,
        }
        if reason is not None:
            frame["reason"] = reason
        if resets_at is not None:
            frame["resets_at"] = ts_required(resets_at)
        return frame

    async def on_screen_usage(self, agent: AgentConn, frame: dict[str, Any], *, budget_ok: bool) -> None:
        """``screen_usage`` -> ``screen_allowance``: the next 60 s chunk, or ``final`` with the honest reason. The
        agent's figures in the frame are read by nobody here (rules.screen_metering (3))."""
        sid: str = frame["screen_session_id"]
        now = self.now()
        if not budget_ok:
            log.info("screen_usage.over_budget", pc_id=str(agent.pc_id))
            await self.relay.agent_rejection_event(agent, {"frame": "screen_usage", "reason": "budget"})
            return
        out: dict[str, Any] | None
        async with transaction(self.db) as db:
            reservation = await self._reservation_for(db, agent, sid, None)
            if reservation is None:
                r = ScreenReservation
                settled = await db.scalar(select(r.id).where(r.screen_session_id == sid, r.pc_id == agent.pc_id))
                # A session this relay settled already gets `final` (no more minutes). One it never reserved gets no
                # answer at all: the agent ends at its ticket's deadline with the honest `allowance_unconfirmed`,
                # never with "Free time is used up" (not known, and not true on an unlimited plan).
                log.info("screen_usage.unknown_session", pc_id=str(agent.pc_id), settled=settled is not None)
                out = self._final(sid, False, None) if settled is not None else None
            else:
                account = await db.get(Account, reservation.account_id)
                plan = plan_for(account.plan if account else None)
                limits = self.limits_for(plan)
                controller_id = reservation.controller_id
                agent.remember_screen_session(sid, controller_id)  # after a relay restart the owner is re-learnt here
                if reservation.final_reason is not None:
                    out = self._final(sid, limits.unlimited, reservation.final_reason)
                elif not self.relay.controller_present(controller_id, now, self.config.owner_grace_seconds):
                    reservation.final_reason = "controller_offline"
                    await self.turn.revoke_session(db, sid, "controller_offline", now)
                    out = self._final(sid, limits.unlimited, "controller_offline")
                elif not await self._plan_allows(db, reservation, plan):
                    reservation.final_reason = "plan_disabled"
                    await self.turn.revoke_session(db, sid, "plan_change", now)
                    out = self._final(sid, limits.unlimited, "plan_disabled")
                else:
                    renewed, ticket = await metering.renew(db, reservation.id, now, limits)
                    if renewed is None or ticket is None:
                        resets = None
                        if renewed is not None:
                            window = await metering.open_window(db, renewed.account_id, now)
                            resets = window.window_end if window is not None else None
                        out = self._final(sid, limits.unlimited, "allowance_exhausted", resets)
                    else:
                        out = {
                            "type": "screen_allowance",
                            "screen_session_id": sid,
                            "granted_until": ts(ticket.granted_until),
                            "unlimited": ticket.unlimited,
                            "final": False,
                        }
        if out is None:
            return
        if out["final"]:
            agent.screen_final_sent.add(sid)
            if out.get("reason") == "allowance_exhausted" and reservation is not None:
                await self.allowance_exhausted_event(
                    reservation.account_id, reservation.controller_id, agent.pc_id, None
                )
        await agent.send(out)

    async def _plan_allows(self, db: AsyncSession, reservation: ScreenReservation, plan: Plan) -> bool:
        pc = await db.get(PC, reservation.pc_id)
        if pc is None or pc.deleted_at is not None or not pc.enabled:
            return False
        enabled = await self.relay.plan_enabled_controller_ids(db, reservation.account_id, plan)
        return reservation.controller_id in enabled

    # ----- the socket-close paths -----------------------------------------------------------------------------------
    async def controller_gone(self, controller_id: uuid.UUID, reason: str) -> None:
        """The controller was revoked, signed out or lost its last bound socket for the grace period: every session
        it owns is told ``final`` with ``controller_offline`` at once (if a ticket is outstanding) and its TURN
        credential is revoked (design §7 "Relay socket lost (phone)", §13)."""
        now = self.now()
        sent = 0
        async with transaction(self.db) as db:
            await self.turn.revoke_controller(db, controller_id, reason[:32], now)
            r = ScreenReservation
            rows = (
                await db.scalars(
                    select(r)
                    .where(
                        r.controller_id == controller_id, r.state.in_(("reserved", "live")), r.final_reason.is_(None)
                    )
                    .with_for_update()
                )
            ).all()
            for row in rows:
                row.final_reason = "controller_offline"
            targets = [(row.pc_id, row.screen_session_id, row.unlimited) for row in rows]
        for pc_id, sid, unlimited in targets:
            agent = self.relay.agent_for(pc_id)
            if agent is None or sid is None or sid in agent.screen_final_sent:
                continue
            agent.screen_final_sent.add(sid)
            if await agent.send(self._final(sid, unlimited, "controller_offline")):
                sent += 1
        if sent or targets:
            log.info("screen.controller_gone", controller_id=str(controller_id), reason=reason, sessions=len(targets))

    async def grant_narrowed(self, controller_id: uuid.UUID, pc_id: uuid.UUID, removed: list[str]) -> None:
        """``grant_update`` took ``screen`` away: the PC ends the session itself (grant_removed); the relay revokes the
        phone's TURN credential (design §13)."""
        if "screen" not in removed:
            return
        now = self.now()
        async with transaction(self.db) as db:
            await self.turn.revoke_controller(db, controller_id, "grant_narrowed", now)

    async def account_plan_changed(self, account_id: uuid.UUID) -> None:
        now = self.now()
        async with transaction(self.db) as db:
            await self.turn.revoke_account(db, account_id, "plan_change", now)

    # ----- the sweeper ----------------------------------------------------------------------------------------------
    async def sweep(self, now: datetime | None = None) -> dict[str, int]:
        """Every few seconds: charge stale reservations in full, revoke credentials that got no start, drop forgotten
        pending starts, and end the sessions of owners absent for the grace period."""
        now = now or self.now()
        counts = {"swept": 0, "turn_revoked": 0, "offline": 0}
        async with transaction(self.db) as db:
            swept = await metering.sweep(db, now, lambda account_id: self._limits_for_account(db, account_id))
            for row in swept:
                if row.screen_session_id is not None:
                    await self.turn.revoke_session(db, row.screen_session_id, "swept", now)
                else:
                    # a start that never became a session: only its own credential (the phone may have a newer one)
                    await self.turn.revoke_for_start(
                        db, row.controller_id, row.command_id, row.forwarded_at, "swept", now
                    )
            counts["swept"] = len(swept)
            counts["turn_revoked"] = await self.turn.sweep(db, now)
        for row in swept:
            agent = self.relay.agent_for(row.pc_id)
            if agent is not None:
                if row.screen_session_id is not None:
                    agent.forget_screen_session(row.screen_session_id)
                agent.screen_pending_starts.pop(row.command_id, None)
            await self.relay.security_event(
                account_id=row.account_id,
                kind="screen_session_ended",
                severity="notice",
                actor="system",
                subject_id=row.pc_id,
                detail={
                    "controller_id": str(row.controller_id),
                    "reason": "swept",
                    "charged_seconds": row.charged_seconds,
                },
            )
        for agent in list(self.relay.agents.values()):
            for cid, pending in list(agent.screen_pending_starts.items()):
                if (now - pending.forwarded_at).total_seconds() > PENDING_START_SECONDS:
                    agent.screen_pending_starts.pop(cid, None)
            for sid, controller_id in list(agent.screen_sessions.items()):
                if sid in agent.screen_final_sent:
                    continue
                if not self.relay.controller_present(controller_id, now, self.config.owner_grace_seconds):
                    await self.controller_gone(controller_id, "controller_offline")
                    counts["offline"] += 1
        return counts

    async def _sweep_loop(self) -> None:
        while True:
            await asyncio.sleep(self.config.sweep_interval_seconds)
            try:
                await self.sweep()
            except Exception:  # noqa: BLE001 - keep sweeping
                log.exception("screen.sweep_failed")

    async def _reconcile_loop(self) -> None:
        while True:
            try:
                await self.reconcile()
            except Exception:  # noqa: BLE001 - keep reconciling
                log.exception("screen.reconcile_failed")
            await asyncio.sleep(self.turn.config.reconcile_interval_seconds)

    async def reconcile(self, now: datetime | None = None) -> int:
        now = now or self.now()
        async with transaction(self.db) as db:
            written = await self.turn.reconcile(db, now)
            await self.turn.forget_expired_usage(db, now)
        self._health_cache = None
        return written

    async def start(self) -> None:
        self._sweeper = asyncio.create_task(self._sweep_loop(), name="screen-sweeper")
        self._reconciler = asyncio.create_task(self._reconcile_loop(), name="turn-reconciler")

    async def stop(self) -> None:
        for task in (self._sweeper, self._reconciler):
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        self._sweeper = self._reconciler = None

    async def aclose(self) -> None:
        await self.turn.provider.aclose()

    # ----- health ---------------------------------------------------------------------------------------------------
    async def health(self, now: datetime | None = None) -> dict[str, Any]:
        """``/healthz`` fields: the switch, the TURN state and the month's relayed GB exactly as the monthly guard
        counts it (``turn_month_gb``; ``turn_month_basis`` says whether the provider's reconciled figures or the
        provisional bound carry it), the whole month's provisional bound and the latest reconciliation pass. Cached
        for a minute; no secret, no account data."""
        now = now or self.now()
        if self._health_cache is not None and (now - self._health_cache[0]) < timedelta(seconds=60):
            return dict(self._health_cache[1])
        async with self.db() as db:
            summary = await self.turn.month_summary(db, now)
        gb = 1_000_000_000
        body: dict[str, Any] = {
            "screen_view": "on" if self.config.enabled else "off",
            "turn": self.turn.status(now),
            "turn_month_gb": round(summary.guard_bytes / gb, 3),
            "turn_month_basis": summary.basis,
            "turn_month_gb_provisional": round(summary.provisional_bytes / gb, 3),
            "turn_reconciled_at": ts(summary.reconciled_at),
        }
        self._health_cache = (now, body)
        return dict(body)


__all__ = ["IceAnswer", "PENDING_START_SECONDS", "ScreenConfig", "ScreenService", "duration_bucket"]
