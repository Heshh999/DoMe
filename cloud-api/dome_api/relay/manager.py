"""Bounded in-process connection manager (ADR-0001 D1: one relay process, PostgreSQL only).

Holds one agent socket per PC, any number of controller sockets, their subscriptions, the per-PC
cached ``state`` frame and the in-flight command table. All mutation happens on the event loop; the
only awaits inside critical sections are socket sends guarded by a per-connection lock.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from dome_protocol import dumps_compact
from dome_protocol.keys import b64url_encode
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.websockets import WebSocket, WebSocketState

from dome_api.db.models import PC, Command, Controller, Grant, PairingSession
from dome_api.entitlements import EntitlementSigner
from dome_api.logging import get_logger
from dome_api.plans import Plan, plan_for
from dome_api.relay import frames
from dome_api.security import events
from dome_api.security.ratelimit import SlidingWindowLimiter
from dome_api.settings import Settings
from dome_api.util import ts_required, utcnow

log = get_logger("dome_api.relay")

CLOSE_FRAME_TOO_LARGE = 1009
CLOSE_PROTOCOL_ERROR = 4000
CLOSE_SUPERSEDED = 4001
CLOSE_REVOKED = 4003
CLOSE_AUTH_REQUIRED = 4008
MAX_AGENT_INPUT_SESSIONS = 4
# pc_state fields added in protocol 1.1; a 1.0 peer rejects unknown fields, so its copy of a state frame omits them
PC_STATE_1_1_FIELDS = ("foreground_app", "input_session", "input_restricted")


@dataclass(slots=True)
class InFlight:
    command_id: uuid.UUID
    pc_id: uuid.UUID
    account_id: uuid.UUID
    controller_id: uuid.UUID
    controller_conn_id: uuid.UUID
    action: str
    received_at: datetime
    deadline: datetime
    timeout_ms: int
    requires_confirmation: bool
    executing_seen: bool = False
    state: str = "created"


class _Conn:
    __slots__ = ("ws", "connection_id", "account_id", "_send_lock", "closed", "last_seen")

    def __init__(self, ws: WebSocket, account_id: uuid.UUID) -> None:
        self.ws = ws
        self.connection_id = uuid.uuid4()
        self.account_id = account_id
        self._send_lock = asyncio.Lock()
        self.closed = False
        self.last_seen = utcnow()

    async def send_text(self, text: str) -> bool:
        if self.closed:
            return False
        async with self._send_lock:
            if self.closed or self.ws.client_state != WebSocketState.CONNECTED:
                return False
            try:
                await self.ws.send_text(text)
                return True
            except Exception:  # noqa: BLE001 - peer vanished; the receive loop will clean up
                self.closed = True
                return False

    async def close(self, code: int) -> None:
        if self.closed:
            return
        self.closed = True
        with contextlib.suppress(Exception):  # peer may already be gone
            await self.ws.close(code=code)


class AgentConn(_Conn):
    __slots__ = (
        "pc_id",
        "state_frame",
        "inflight",
        "pending_power",
        "snapshot_sent",
        "last_seen_written",
        "remote_enabled_reported",
        "supports_input",
        "input_sessions",
        "events_suppressed",
    )

    def __init__(self, ws: WebSocket, pc_id: uuid.UUID, account_id: uuid.UUID) -> None:
        super().__init__(ws, account_id)
        self.pc_id = pc_id
        self.state_frame: dict[str, Any] | None = None
        self.inflight: dict[uuid.UUID, InFlight] = {}
        self.pending_power: tuple[uuid.UUID, datetime] | None = None
        self.snapshot_sent = False
        self.last_seen_written: datetime | None = None
        self.remote_enabled_reported: bool | None = None
        # the agent announced protocol 1.1 (rules.controller_socket_identity: 1.0 peers never see input frames)
        self.supports_input = False
        # live manual-input sessions as the AGENT reported them (input_session frames): id -> owning controller.
        # Bounded: the latest session per controller, at most MAX_AGENT_INPUT_SESSIONS entries (one live session
        # per PC is the rule; the slack covers a takeover whose `ended` frame is still in flight).
        self.input_sessions: dict[str, uuid.UUID] = {}
        self.events_suppressed = 0  # agent-attributed rejection rows not written because the per-PC cap was hit

    def remember_input_session(self, input_session_id: str, controller_id: uuid.UUID) -> None:
        for sid, owner in list(self.input_sessions.items()):
            if owner == controller_id and sid != input_session_id:
                del self.input_sessions[sid]  # a controller has at most one live session on a PC
        self.input_sessions.pop(input_session_id, None)
        self.input_sessions[input_session_id] = controller_id  # newest last
        while len(self.input_sessions) > MAX_AGENT_INPUT_SESSIONS:
            del self.input_sessions[next(iter(self.input_sessions))]

    async def send(self, frame: dict[str, Any], *, validate: bool = True) -> bool:
        if validate:
            frames.validate_outbound("relay_to_agent", frame)
        return await self.send_text(dumps_compact(frame))


class ControllerConn(_Conn):
    __slots__ = (
        "session_id",
        "kid",
        "controller_id",
        "subs",
        "throttle_violations",
        "events_suppressed",
        "supports_input",
        "input_throttle_notified",
        "input_seq",
        "input_refused",
    )

    def __init__(self, ws: WebSocket, session_id: uuid.UUID, account_id: uuid.UUID, kid: str) -> None:
        super().__init__(ws, account_id)
        self.session_id = session_id
        self.kid = kid
        self.controller_id: uuid.UUID | None = None
        self.subs: set[uuid.UUID] = set()
        self.throttle_violations = 0  # inbound frames refused by the per-socket bucket
        self.events_suppressed = 0  # security-event rows not written because the per-socket cap was hit
        self.supports_input = False  # hello announced protocol 1.1
        self.input_throttle_notified: datetime | None = None  # last RATE_LIMITED error sent for input_batch floods
        self.input_seq: dict[str, int] = {}  # input_session_id -> highest seq forwarded on this socket (bounded)
        self.input_refused = 0  # input_batch frames refused by the per-socket input bucket (separate from commands)

    def input_notice_due(self, now: datetime) -> bool:
        """At most one RATE_LIMITED error frame (and log line) per second for refused input batches."""
        if self.input_throttle_notified is not None and (now - self.input_throttle_notified).total_seconds() < 1:
            return False
        self.input_throttle_notified = now
        return True

    def remember_input_seq(self, input_session_id: str, seq: int) -> None:
        if input_session_id not in self.input_seq and len(self.input_seq) >= 8:
            del self.input_seq[next(iter(self.input_seq))]  # oldest session first; ids are fresh per session
        self.input_seq[input_session_id] = seq

    async def send(self, frame: dict[str, Any], *, validate: bool = True) -> bool:
        if validate:
            frames.validate_outbound("relay_to_controller", frame)
        return await self.send_text(dumps_compact(frame))


@dataclass(slots=True)
class SnapshotController:
    controller_id: uuid.UUID
    kid: str
    capabilities: list[str]
    display_name: str
    status: str


@dataclass
class ConnectionManager:
    settings: Settings
    db: async_sessionmaker[AsyncSession]
    signer: EntitlementSigner
    agents: dict[uuid.UUID, AgentConn] = field(default_factory=dict)
    controllers: dict[uuid.UUID, ControllerConn] = field(default_factory=dict)
    subs: dict[uuid.UUID, set[uuid.UUID]] = field(default_factory=dict)
    # sockets accepted but not yet past ``hello`` (they hold a slot so a hello-less flood cannot exceed the cap)
    pending_handshakes: int = 0
    # hello-proof nonces still inside their validity window (rules.controller_socket_identity: single use)
    hello_nonces: dict[str, datetime] = field(default_factory=dict)
    # cap on agent-attributed rejection rows (relay_frame_rejected) per PC per minute, as `audited_event` caps
    # controller sockets; a buggy or compromised agent cannot grow security_events at wire speed this way
    agent_events: SlidingWindowLimiter = field(init=False)

    def __post_init__(self) -> None:
        self.agent_events = SlidingWindowLimiter(self.settings.relay_security_events_per_connection_per_minute, 60)

    # ----- hello proof ---------------------------------------------------------------------------
    def accept_hello_nonce(self, nonce: str, expires_at: datetime) -> bool:
        """Record a verified hello-proof nonce; False when it was already used while still valid."""
        now = utcnow()
        for seen, until in list(self.hello_nonces.items()):
            if until < now:
                del self.hello_nonces[seen]
        if nonce in self.hello_nonces:
            return False
        self.hello_nonces[nonce] = expires_at + timedelta(seconds=30)
        return True

    # ----- capacity ------------------------------------------------------------------------------
    def connection_count(self) -> int:
        return len(self.agents) + len(self.controllers) + self.pending_handshakes

    def has_capacity(self) -> bool:
        return self.connection_count() < self.settings.relay_max_connections

    def reserve_slot(self) -> bool:
        """Claim a connection slot for a socket about to be accepted; ``False`` when the relay is full.
        The caller releases it with :meth:`release_slot` once the socket is registered or gone."""
        if not self.has_capacity():
            return False
        self.pending_handshakes += 1
        return True

    def release_slot(self) -> None:
        self.pending_handshakes = max(0, self.pending_handshakes - 1)

    # ----- agents --------------------------------------------------------------------------------
    def agent_for(self, pc_id: uuid.UUID) -> AgentConn | None:
        conn = self.agents.get(pc_id)
        return conn if conn is not None and not conn.closed else None

    def is_online(self, pc_id: uuid.UUID) -> bool:
        return self.agent_for(pc_id) is not None

    async def register_agent(self, conn: AgentConn) -> None:
        old = self.agents.get(conn.pc_id)
        self.agents[conn.pc_id] = conn
        if old is not None and old is not conn:
            log.info("agent.superseded", pc_id=str(conn.pc_id))
            await old.close(CLOSE_SUPERSEDED)
            await self._fail_inflight(old, reason="superseded")

    async def unregister_agent(self, conn: AgentConn) -> None:
        current = self.agents.get(conn.pc_id)
        if current is conn:
            del self.agents[conn.pc_id]
        await self._fail_inflight(conn, reason="disconnect")
        if current is conn:
            await self.touch_pc_last_seen(conn.pc_id, conn.last_seen, force=True)
            await self.broadcast_pc_status(conn.pc_id)

    async def _fail_inflight(self, conn: AgentConn, *, reason: str) -> None:
        pending = list(conn.inflight.values())
        conn.inflight.clear()
        for inf in pending:
            await self._terminate_unknown(inf)
        if pending:
            log.info("agent.inflight_unknown", pc_id=str(conn.pc_id), count=len(pending), reason=reason)

    async def _terminate_unknown(self, inf: InFlight) -> None:
        """``rules.terminal_result``: the command was written to the PC's socket, so nothing the relay sees can
        prove it did not run — whether or not an ack arrived, the honest terminal state is outcome_unknown.
        The agent's journaled result may correct it once (``rules.late_results``)."""
        frame = frames.relay_result(
            inf.command_id, "outcome_unknown", error=frames.error_object("OUTCOME_UNKNOWN"), started_at=inf.received_at
        )
        await self.finish_command(inf, "outcome_unknown", "OUTCOME_UNKNOWN", frame)

    async def disconnect_agent(self, pc_id: uuid.UUID, *, revoked_reason: str | None) -> None:
        """Close the PC's socket; with ``revoked_reason`` the agent is told its cloud access is gone."""
        conn = self.agents.get(pc_id)
        if conn is None:
            return
        if revoked_reason:
            await conn.send({"type": "revoked", "reason": revoked_reason})
        await conn.close(CLOSE_REVOKED)
        await self.unregister_agent(conn)

    # ----- controllers ---------------------------------------------------------------------------
    def register_controller(self, conn: ControllerConn) -> None:
        self.controllers[conn.connection_id] = conn

    def unregister_controller(self, conn: ControllerConn) -> None:
        self.controllers.pop(conn.connection_id, None)
        for pc_id in list(conn.subs):
            self._unsubscribe(conn, pc_id)

    def _unsubscribe(self, conn: ControllerConn, pc_id: uuid.UUID) -> None:
        conn.subs.discard(pc_id)
        s = self.subs.get(pc_id)
        if s is not None:
            s.discard(conn.connection_id)
            if not s:
                del self.subs[pc_id]

    def set_subscriptions(self, conn: ControllerConn, pc_ids: set[uuid.UUID]) -> None:
        for pc_id in list(conn.subs):
            if pc_id not in pc_ids:
                self._unsubscribe(conn, pc_id)
        for pc_id in pc_ids:
            conn.subs.add(pc_id)
            self.subs.setdefault(pc_id, set()).add(conn.connection_id)

    def controller_conns(self, controller_id: uuid.UUID) -> list[ControllerConn]:
        return [c for c in self.controllers.values() if c.controller_id == controller_id and not c.closed]

    def session_conns(self, session_id: uuid.UUID) -> list[ControllerConn]:
        return [c for c in self.controllers.values() if c.session_id == session_id and not c.closed]

    async def revoke_controller_sockets(
        self, controller_id: uuid.UUID, reason: str, *, pc_id: uuid.UUID | None = None
    ) -> int:
        """Send ``revoked`` to every socket bound to the controller and close them (4003)."""
        n = 0
        for conn in self.controller_conns(controller_id):
            frame: dict[str, Any] = {"type": "revoked", "reason": reason}
            if pc_id is not None:
                frame["pc_id"] = str(pc_id)
            await conn.send(frame)
            await conn.close(CLOSE_REVOKED)
            self.unregister_controller(conn)
            n += 1
        return n

    async def apply_controller_revocation(
        self, controller_id: uuid.UUID, *, reason: str, pc_ids: list[uuid.UUID], revoked_pc_id: uuid.UUID | None = None
    ) -> None:
        """Everything the live relay does after a grant/controller revocation was committed:
        best-effort ``cancel`` to the PC for the controller's in-flight commands (their results still
        terminate them honestly), ``revoked`` + close for the controller's sockets, fresh snapshots."""
        for pc_id in pc_ids:
            agent = self.agent_for(pc_id)
            if agent is None:
                continue
            for inf in [i for i in agent.inflight.values() if i.controller_id == controller_id]:
                await agent.send(
                    {"type": "cancel", "command_id": str(inf.command_id), "controller_id": str(controller_id)}
                )
        await self.revoke_controller_sockets(controller_id, reason, pc_id=revoked_pc_id)
        for pc_id in pc_ids:
            await self.push_grants_snapshot(pc_id)

    async def close_session_sockets(self, session_id: uuid.UUID) -> int:
        n = 0
        for conn in self.session_conns(session_id):
            await conn.send({"type": "revoked", "reason": "session_ended"})
            await conn.close(CLOSE_REVOKED)
            self.unregister_controller(conn)
            n += 1
        return n

    async def send_to_controller(
        self,
        controller_id: uuid.UUID,
        preferred_conn_id: uuid.UUID | None,
        frame: dict[str, Any],
        *,
        validate: bool = True,
    ) -> bool:
        """Deliver to the originating socket when it is still open, else to every socket bound to the controller."""
        if preferred_conn_id is not None:
            conn = self.controllers.get(preferred_conn_id)
            if conn is not None and not conn.closed and conn.controller_id == controller_id:
                return await conn.send(frame, validate=validate)
        sent = False
        for conn in self.controller_conns(controller_id):
            sent = await conn.send(frame, validate=validate) or sent
        return sent

    async def broadcast_to_subscribers(
        self,
        pc_id: uuid.UUID,
        frame: dict[str, Any],
        *,
        validate: bool = True,
        input_frame: bool = False,
        legacy_frame: dict[str, Any] | None = None,
        skip: set[uuid.UUID] | frozenset[uuid.UUID] = frozenset(),
    ) -> None:
        """``input_frame`` marks protocol 1.1 frames, which 1.0 sockets never receive; ``legacy_frame`` is the
        copy 1.0 sockets get instead of ``frame`` (a state frame without the 1.1 pc_state fields); ``skip`` lists
        connection ids that already received the frame directly."""
        for conn_id in list(self.subs.get(pc_id, ())):
            if conn_id in skip:
                continue
            conn = self.controllers.get(conn_id)
            if conn is None:
                continue
            if conn.supports_input:
                await conn.send(frame, validate=validate)
            elif not input_frame:
                await conn.send(legacy_frame if legacy_frame is not None else frame, validate=validate)

    async def send_input_frame_to_controller(self, controller_id: uuid.UUID, frame: dict[str, Any]) -> set[uuid.UUID]:
        """Deliver an input_ack / input_session frame to every protocol-1.1 socket bound to the controller.
        Returns the connection ids written, so a subscriber broadcast can skip them."""
        written: set[uuid.UUID] = set()
        for conn in self.controller_conns(controller_id):
            if conn.supports_input and await conn.send(frame, validate=False):
                written.add(conn.connection_id)
        return written

    # ----- PC status -----------------------------------------------------------------------------
    async def pc_status_frame(self, pc_id: uuid.UUID, db: AsyncSession | None = None) -> dict[str, Any] | None:
        conn = self.agent_for(pc_id)
        if db is None:
            async with self.db() as session:
                pc = await session.get(PC, pc_id)
        else:
            pc = await db.get(PC, pc_id)
        if pc is None or pc.deleted_at is not None:
            return None
        last_seen = conn.last_seen if conn else pc.last_seen_at
        return frames.pc_status(
            pc_id,
            connection="online" if conn else "offline",
            last_seen=last_seen,
            last_power_request=pc.last_power_request,
            enabled=pc.enabled,
        )

    async def broadcast_pc_status(self, pc_id: uuid.UUID) -> None:
        if not self.subs.get(pc_id):
            return
        frame = await self.pc_status_frame(pc_id)
        if frame is not None:
            await self.broadcast_to_subscribers(pc_id, frame)

    async def announce_pc_unlinked(self, pc_id: uuid.UUID, last_seen: datetime | None) -> int:
        """After ``DELETE /v1/pcs/{id}``: every subscribed phone gets a final
        ``pc_status{connection:'offline', enabled:false}`` and its subscription to the PC is dropped, so no
        live view keeps showing an unlinked PC as online. Returns the number of sockets informed."""
        conn_ids = list(self.subs.get(pc_id, ()))
        if not conn_ids:
            return 0
        frame = frames.pc_status(
            pc_id, connection="offline", last_seen=last_seen, last_power_request=None, enabled=False
        )
        n = 0
        for conn_id in conn_ids:
            conn = self.controllers.get(conn_id)
            if conn is not None:
                if await conn.send(frame):
                    n += 1
                self._unsubscribe(conn, pc_id)
        self.subs.pop(pc_id, None)
        return n

    async def touch_pc_last_seen(self, pc_id: uuid.UUID, at: datetime, *, force: bool = False) -> None:
        conn = self.agents.get(pc_id)
        if conn is not None and not force:
            # throttle writes: the in-memory value is authoritative while online
            if conn.last_seen_written is not None and (at - conn.last_seen_written).total_seconds() < 30:
                return
        async with self.db() as db:
            async with db.begin():
                await db.execute(update(PC).where(PC.id == pc_id).values(last_seen_at=at))
        if conn is not None:
            conn.last_seen_written = at

    # ----- grants snapshot -----------------------------------------------------------------------
    async def snapshot_controllers(self, db: AsyncSession, pc: PC, plan: Plan) -> list[SnapshotController]:
        rows = (
            await db.execute(
                select(Grant, Controller)
                .join(Controller, Controller.id == Grant.controller_id)
                .where(
                    Grant.pc_id == pc.id,
                    Grant.account_id == pc.account_id,
                    Grant.revoked_at.is_(None),
                    Controller.revoked_at.is_(None),
                )
            )
        ).all()
        enabled_ids = await self.plan_enabled_controller_ids(db, pc.account_id, plan)
        out: list[SnapshotController] = []
        for grant, ctrl in rows:
            out.append(
                SnapshotController(
                    controller_id=ctrl.id,
                    kid=ctrl.kid,
                    capabilities=sorted(set(grant.capabilities)),
                    display_name=ctrl.display_name[:64],
                    status="active" if ctrl.id in enabled_ids else "plan_disabled",
                )
            )
        return out

    async def plan_enabled_controller_ids(self, db: AsyncSession, account_id: uuid.UUID, plan: Plan) -> set[uuid.UUID]:
        """The controllers that count as enabled under the plan: the most recently seen
        ``max_controllers`` live controllers (plans.json downgrade_policy.default_selection)."""
        rows = (
            await db.execute(
                select(Controller.id)
                .where(Controller.account_id == account_id, Controller.revoked_at.is_(None))
                .order_by(Controller.last_seen_at.desc().nulls_last(), Controller.created_at.asc())
                .limit(plan.max_controllers)
            )
        ).scalars()
        return set(rows)

    async def build_snapshot(self, db: AsyncSession, pc: PC) -> dict[str, Any]:
        from dome_api.db.models import Account

        account = await db.get(Account, pc.account_id)
        plan = plan_for(account.plan if account else None)
        controllers = await self.snapshot_controllers(db, pc, plan)
        frame: dict[str, Any] = {
            "type": "grants_snapshot",
            "pc_id": str(pc.id),
            "account_id": str(pc.account_id),
            "snapshot_id": str(uuid.uuid4()),
            "pc_enabled": bool(pc.enabled and pc.deleted_at is None),
            "controllers": [
                {
                    "controller_id": str(c.controller_id),
                    "kid": c.kid,
                    "capabilities": c.capabilities,
                    "display_name": c.display_name,
                    "status": c.status,
                }
                for c in controllers
            ],
        }
        assertion = self.signer.assertion(pc.account_id, pc.id, plan)
        if assertion is not None:
            frame["entitlement_assertion"] = assertion
        return frame

    async def push_grants_snapshot(self, pc_id: uuid.UUID) -> bool:
        conn = self.agent_for(pc_id)
        if conn is None:
            return False
        async with self.db() as db:
            pc = await db.get(PC, pc_id)
            if pc is None:
                return False
            frame = await self.build_snapshot(db, pc)
        ok = await conn.send(frame)
        conn.snapshot_sent = conn.snapshot_sent or ok
        return ok

    # ----- pairing requests ----------------------------------------------------------------------
    @staticmethod
    def pairing_request_frame(ps: PairingSession) -> dict[str, Any]:
        return {
            "type": "pairing_request",
            "pairing_id": str(ps.id),
            "code_hash": b64url_encode(ps.code_hash),
            "controller_display_name": (ps.controller_display_name or "")[:64],
            "public_jwk": ps.controller_public_jwk,
            "kid": ps.controller_kid,
            "requested_capabilities": sorted(set(ps.requested_capabilities or [])),
            "expires_at": ts_required(ps.expires_at),
        }

    async def deliver_pairing_request(self, ps: PairingSession) -> bool:
        conn = self.agent_for(ps.pc_id)
        if conn is None:
            return False
        return await conn.send(self.pairing_request_frame(ps))

    async def redeliver_pending_pairing_requests(self, conn: AgentConn) -> int:
        async with self.db() as db:
            rows = (
                (
                    await db.execute(
                        select(PairingSession)
                        .where(
                            PairingSession.pc_id == conn.pc_id,
                            PairingSession.state == "claimed",
                            PairingSession.expires_at > utcnow(),
                        )
                        .order_by(PairingSession.created_at)
                    )
                )
                .scalars()
                .all()
            )
        n = 0
        for ps in rows:
            if await conn.send(self.pairing_request_frame(ps)):
                n += 1
        return n

    # ----- commands table ------------------------------------------------------------------------
    async def finish_command(self, inf: InFlight, state: str, error_code: str | None, frame: dict[str, Any]) -> None:
        """Persist a relay-originated terminal state and deliver the result frame."""
        now = utcnow()
        async with self.db() as db:
            async with db.begin():
                await db.execute(
                    update(Command)
                    .where(
                        Command.id == inf.command_id,
                        Command.state.in_(("created", "accepted", "executing", "awaiting_confirmation")),
                    )
                    .values(
                        state=state,
                        error_code=error_code,
                        finished_at=now,
                        duration_ms=int((now - inf.received_at).total_seconds() * 1000),
                    )
                )
        inf.state = state
        await self.send_to_controller(inf.controller_id, inf.controller_conn_id, frame)

    async def sweep_deadlines(self) -> int:
        """Apply ``rules.in_flight``: expire or mark unknown every command past its hard deadline."""
        now = utcnow()
        swept = 0
        for conn in list(self.agents.values()):
            for inf in list(conn.inflight.values()):
                deadline = inf.deadline
                pp = conn.pending_power
                if pp is not None and pp[0] == inf.command_id:
                    deadline = max(deadline, pp[1] + timedelta(milliseconds=inf.timeout_ms))
                if now < deadline:
                    continue
                conn.inflight.pop(inf.command_id, None)
                await self._terminate_unknown(inf)
                swept += 1
        return swept

    async def startup_sweep(self) -> int:
        """Rows left in flight by a previous process can never complete through this relay. A row exists only
        once routing reached the forwarding step, so the previous process may have written the command to the
        PC: the honest terminal state is outcome_unknown (``rules.terminal_result``)."""
        now = utcnow()
        async with self.db() as db:
            async with db.begin():
                r1 = await db.execute(
                    update(Command)
                    .where(Command.state.in_(("created", "accepted", "executing", "awaiting_confirmation")))
                    .values(state="outcome_unknown", error_code="OUTCOME_UNKNOWN", finished_at=now)
                )
        return int(getattr(r1, "rowcount", 0) or 0)

    # ----- security events -----------------------------------------------------------------------
    async def agent_rejection_event(self, conn: AgentConn, detail: dict[str, Any]) -> bool:
        """Write a ``relay_frame_rejected`` row for a frame the PC sent, unless the PC exhausted its per-minute
        budget; the first suppressed row becomes one ``relay_events_throttled`` row. Returns whether written."""
        if self.agent_events.allow(str(conn.pc_id)):
            await self.security_event(
                account_id=conn.account_id,
                kind="relay_frame_rejected",
                severity="warning",
                actor="pc",
                subject_id=conn.pc_id,
                detail=detail,
            )
            return True
        conn.events_suppressed += 1
        if conn.events_suppressed == 1:
            await self.security_event(
                account_id=conn.account_id,
                kind="relay_events_throttled",
                severity="warning",
                actor="pc",
                subject_id=conn.pc_id,
                detail={"first_suppressed": "relay_frame_rejected", "per_minute": self.agent_events.limit},
            )
        log.info("security_event.suppressed", kind="relay_frame_rejected", pc_id=str(conn.pc_id))
        return False

    async def security_event(
        self,
        *,
        account_id: uuid.UUID | None,
        kind: str,
        severity: str = "notice",
        actor: str = "system",
        subject_id: uuid.UUID | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        try:
            await events.record_now(
                self.db,
                account_id=account_id,
                kind=kind,
                severity=severity,
                actor=actor,
                subject_id=subject_id,
                detail=detail,
            )
        except Exception:  # noqa: BLE001 - never let auditing break routing
            log.exception("security_event.failed", kind=kind)
