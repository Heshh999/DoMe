"""Command routing (design: Relay → "Command routing", in this exact order). Every rejection
after the frame itself parsed is a ``result{origin:'relay', state:'failed', error}`` so each
command_id a controller sends ends with exactly one result (``rules.terminal_result``)."""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta
from typing import Any

from dome_protocol import (
    KeyRecord,
    ProtocolError,
    Registry,
    VerifiedCommand,
    loads_strict,
    parse_rfc3339,
    verify_and_parse_command,
    verify_and_parse_input_batch,
)
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from dome_api.db.models import PC, Account, Command, Controller, Grant
from dome_api.logging import get_logger
from dome_api.plans import Plan, catalog, plan_for
from dome_api.relay import frames
from dome_api.relay.manager import CLOSE_REVOKED, AgentConn, ConnectionManager, ControllerConn, InFlight
from dome_api.security.ratelimit import SlidingWindowLimiter, TokenBucketLimiter
from dome_api.settings import Settings
from dome_api.state import Services
from dome_api.util import ts_required, utcnow

log = get_logger("dome_api.relay.router")
_UUID_RE = re.compile(r"\A[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
TERMINAL_STATES = ("succeeded", "failed", "expired", "canceled", "outcome_unknown")
IN_FLIGHT_STATES = ("created", "accepted", "executing", "awaiting_confirmation")


class Rejection(Exception):  # noqa: N818 - routing control flow, always converted to a result frame
    def __init__(
        self, code: str, message: str | None = None, *, close: bool = False, detail: dict[str, Any] | None = None
    ) -> None:
        super().__init__(code)
        self.code = code
        self.message = message
        self.close = close
        self.detail = detail


class RateLimiters:
    """Relay-side limiters.

    * per-plan token buckets keyed by controller id (plans.json manual/coalescable command limits),
      applied at routing step 7 after the command verified;
    * ``frames``: one token bucket per controller *socket*, applied to every inbound frame before any
      database work, so an authenticated flood is cut at the socket;
    * ``events``: a cap on security-event rows a single socket may write per minute, so rejections
      cannot grow ``security_events`` without bound;
    * ``input_frames``: the pre-database bucket for ``input_batch`` frames per socket (the ordinary frame
      bucket is sized for commands and would starve a touchpad); sustained rate from
      ``version.json → limits.input_batches_per_second``, burst = the largest plan burst;
    * per-plan ``input_rate_limit`` buckets keyed by controller id (``rules.input_sessions``), applied once
      the batch verified;
    * ``input_rejections``: at most one ``input_rejected`` security event per minute per controller.
    """

    def __init__(self, settings: Settings, registry: Registry | None = None) -> None:
        self._manual: dict[str, TokenBucketLimiter] = {}
        self._coalescable: dict[str, TokenBucketLimiter] = {}
        self._input: dict[str, TokenBucketLimiter] = {}
        self.frames = TokenBucketLimiter(
            settings.relay_controller_frames_per_minute, settings.relay_controller_frame_burst
        )
        self.events = SlidingWindowLimiter(settings.relay_security_events_per_connection_per_minute, 60)
        per_second = int((registry.limits if registry is not None else {}).get("input_batches_per_second", 40))
        burst = max(p.input_rate_limit.burst for p in catalog().plans.values())
        self.input_frames = TokenBucketLimiter(per_second * 60, burst)
        self.input_rejections = SlidingWindowLimiter(1, 60)

    def forget_connection(self, conn: ControllerConn) -> None:
        self.frames.forget(str(conn.connection_id))
        self.input_frames.forget(str(conn.connection_id))

    def allow(self, plan: Plan, controller_id: uuid.UUID, *, coalescable: bool) -> bool:
        table = self._coalescable if coalescable else self._manual
        limiter = table.get(plan.id)
        if limiter is None:
            rl = plan.coalescable_command_rate_limit if coalescable else plan.manual_command_rate_limit
            limiter = table[plan.id] = TokenBucketLimiter(rl.per_minute, rl.burst)
        return limiter.allow(str(controller_id))

    def allow_input(self, plan: Plan, controller_id: uuid.UUID) -> bool:
        limiter = self._input.get(plan.id)
        if limiter is None:
            rl = plan.input_rate_limit
            limiter = self._input[plan.id] = TokenBucketLimiter(rl.per_minute, rl.burst)
        return limiter.allow(str(controller_id))


async def audited_event(
    mgr: ConnectionManager,
    limiters: RateLimiters,
    conn: ControllerConn,
    *,
    kind: str,
    severity: str,
    detail: dict[str, Any] | None,
) -> bool:
    """Write a controller-attributed security event unless this socket exhausted its per-minute budget.
    The first suppressed event is replaced by one ``relay_events_throttled`` row so the flood itself stays
    visible in the account's security log; everything after that is only logged. Returns whether a row
    was written for ``kind``."""
    if limiters.events.allow(str(conn.connection_id)):
        await mgr.security_event(
            account_id=conn.account_id,
            kind=kind,
            severity=severity,
            actor="controller",
            subject_id=conn.controller_id,
            detail=detail,
        )
        return True
    conn.events_suppressed += 1
    if conn.events_suppressed == 1:
        await mgr.security_event(
            account_id=conn.account_id,
            kind="relay_events_throttled",
            severity="warning",
            actor="controller",
            subject_id=conn.controller_id,
            detail={"first_suppressed": kind, "per_minute": limiters.events.limit},
        )
    log.info("security_event.suppressed", kind=kind, controller_id=str(conn.controller_id), n=conn.events_suppressed)
    return False


def untrusted_command_id(envelope: Any) -> str | None:
    """Best-effort correlation id for a rejection reply when the signature could not be verified.
    The value is used for nothing but addressing the ``result`` frame."""
    try:
        payload = envelope.get("payload") if isinstance(envelope, dict) else None
        if not isinstance(payload, str):
            return None
        parsed = loads_strict(payload, require_object=True)
        cid = parsed.get("command_id")
        return cid if isinstance(cid, str) and _UUID_RE.match(cid) else None
    except Exception:  # noqa: BLE001
        return None


async def resolve_controller_record(
    db: AsyncSession, conn: ControllerConn
) -> tuple[Controller | None, KeyRecord | None]:
    if conn.controller_id is None:
        return None, None
    ctrl = await db.get(Controller, conn.controller_id)
    if ctrl is None or ctrl.account_id != conn.account_id or ctrl.kid != conn.kid:
        return None, None
    jwk = {str(k): str(v) for k, v in ctrl.public_jwk.items()}
    return ctrl, KeyRecord(controller_id=str(ctrl.id), account_id=str(ctrl.account_id), jwk=jwk)


def compute_deadline(cmd: VerifiedCommand, received_at: datetime, registry_limits: dict[str, int]) -> datetime:
    """``rules.in_flight``: max(expires_at, received_at + timeout) [+ challenge lifetime] + 10 s."""
    expires: datetime = parse_rfc3339(cmd.payload["expires_at"])
    base = max(expires, received_at + timedelta(milliseconds=cmd.spec.timeout_ms))
    if cmd.spec.requires_confirmation:
        base += timedelta(seconds=registry_limits["confirmation_challenge_lifetime_seconds"])
    return base + timedelta(seconds=10)


async def route_command(
    svc: Services, mgr: ConnectionManager, limiters: RateLimiters, conn: ControllerConn, frame: dict[str, Any]
) -> None:
    """Steps 2-10 of the design's routing order (step 1, the frame schema, and the per-socket frame
    bucket happen in ``controller_ws._loop`` before this is called)."""
    pc_id_str: str = frame["pc_id"]
    envelope = frame["envelope"]
    received_at = utcnow()
    command_id: str | None = None
    try:
        # 2. socket binding
        if envelope.get("kid") != conn.kid:
            command_id = untrusted_command_id(envelope)
            raise Rejection("UNKNOWN_KEY", "Envelope kid does not match this connection", close=True)
        if conn.controller_id is None:
            command_id = untrusted_command_id(envelope)
            raise Rejection("GRANT_MISSING", "This connection is not bound to a paired controller")
        async with svc.db() as db:
            ctrl, record = await resolve_controller_record(db, conn)
            if ctrl is None or record is None:
                command_id = untrusted_command_id(envelope)
                raise Rejection("UNKNOWN_KEY", close=True)
            if ctrl.revoked_at is not None:
                command_id = untrusted_command_id(envelope)
                raise Rejection("CONTROLLER_REVOKED", close=True)
            account = await db.get(Account, conn.account_id)
            plan = plan_for(account.plan if account else None)
            enabled_ids = await mgr.plan_enabled_controller_ids(db, conn.account_id, plan)
            # 3. signature, strict parse, schema, controller/account binding, window, registry
            try:
                cmd = verify_and_parse_command(
                    envelope,
                    lambda kid: record if kid == conn.kid else None,
                    registry=svc.registry,
                    schemas=svc.schemas,
                )
            except ProtocolError as exc:
                command_id = untrusted_command_id(envelope)
                raise Rejection(exc.code, exc.message, detail=exc.detail or None) from None
            command_id = cmd.command_id
            if ctrl.id not in enabled_ids:
                raise Rejection("CONTROLLER_PLAN_DISABLED")
            # 4. frame/payload PC agreement
            if cmd.target_pc_id != pc_id_str:
                raise Rejection("TARGET_PC_MISMATCH")
            pc_id = uuid.UUID(pc_id_str)
            # 5. PC ownership and state
            pc = await db.get(PC, pc_id)
            if pc is None or pc.deleted_at is not None or pc.account_id != conn.account_id:
                raise Rejection("ACCOUNT_MISMATCH", "No such PC on this account")
            if not pc.enabled:
                raise Rejection("PC_PLAN_DISABLED")
            # 6. grant covers the capability
            grant = await db.scalar(
                select(Grant).where(
                    Grant.controller_id == ctrl.id,
                    Grant.pc_id == pc.id,
                    Grant.account_id == conn.account_id,
                    Grant.revoked_at.is_(None),
                )
            )
            # ``satisfied_by``: the action's capability or any alternate (input.session_start runs on a
            # keyboard-only grant as well as a pointer-only one).
            if grant is None or not cmd.spec.satisfied_by(grant.capabilities):
                raise Rejection("GRANT_MISSING")
            # duplicate / reuse handling (rules.duplicate_command) — before anything that consumes budget.
            # Scoped to the account: a command id that lives in another tenant is invisible here and only
            # surfaces as a primary-key conflict at insert time (same COMMAND_ID_REUSED answer, no oracle).
            existing = await db.scalar(
                select(Command).where(Command.id == uuid.UUID(cmd.command_id), Command.account_id == conn.account_id)
            )
            if existing is not None:
                if existing.digest != cmd.digest or existing.controller_id != ctrl.id or existing.pc_id != pc.id:
                    raise Rejection("COMMAND_ID_REUSED")
                await _answer_duplicate(conn, mgr.agent_for(pc.id), existing)
                return
            # 7. rate limit
            if not limiters.allow(plan, ctrl.id, coalescable=cmd.spec.coalesce is not None):
                raise Rejection("RATE_LIMITED")
            # 8. agent online (never queued)
            agent = mgr.agent_for(pc.id)
            if agent is None or not agent.snapshot_sent:
                raise Rejection("PC_OFFLINE")
            # 9. queue depth
            if len(agent.inflight) >= svc.settings.relay_per_pc_queue_depth:
                raise Rejection("QUEUE_FULL")
            # 10. persist lifecycle row and forward
            deadline = compute_deadline(cmd, received_at, svc.registry.limits)
            inf = InFlight(
                command_id=uuid.UUID(cmd.command_id),
                pc_id=pc.id,
                account_id=conn.account_id,
                controller_id=ctrl.id,
                controller_conn_id=conn.connection_id,
                action=cmd.spec.name,
                received_at=received_at,
                deadline=deadline,
                timeout_ms=cmd.spec.timeout_ms,
                requires_confirmation=cmd.spec.requires_confirmation,
            )
            db.add(
                Command(
                    id=inf.command_id,
                    account_id=conn.account_id,
                    controller_id=ctrl.id,
                    pc_id=pc.id,
                    action=cmd.spec.name,
                    digest=cmd.digest,
                    state="created",
                    created_at=received_at,
                    deadline_at=deadline,
                )
            )
            if cmd.spec.capability == "power" and cmd.spec.requires_confirmation:
                pc.last_power_request = {"action": cmd.spec.name, "at": ts_required(received_at)}
            try:
                await db.commit()
            except IntegrityError:
                await db.rollback()
                raise Rejection("COMMAND_ID_REUSED") from None
            agent.inflight[inf.command_id] = inf
            forwarded = {
                "type": "command",
                "envelope": envelope,
                "relay": {"received_at": ts_required(received_at), "connection_id": str(conn.connection_id)},
            }
            if not await agent.send(forwarded, validate=False):
                agent.inflight.pop(inf.command_id, None)
                failed = frames.relay_result(
                    inf.command_id, "failed", error=frames.error_object("PC_OFFLINE"), started_at=received_at
                )
                await mgr.finish_command(inf, "failed", "PC_OFFLINE", failed)
                return
            log.info("command.forwarded", action=cmd.spec.name, pc_id=pc_id_str, controller_id=str(ctrl.id))
    except Rejection as rej:
        await _reject(mgr, limiters, conn, pc_id_str, command_id, rej, received_at)


async def route_input_batch(
    svc: Services, mgr: ConnectionManager, limiters: RateLimiters, conn: ControllerConn, frame: dict[str, Any]
) -> None:
    """``rules.input_sessions`` (2), relay half: verify the controller's signature with the socket's key
    record, bind the batch to this socket's controller and account, require a live grant covering every
    event type, the PC online and past its first snapshot, the per-controller input budget, then forward the
    envelope verbatim. No ``commands`` row is written and nothing about the content is logged. Rejections are
    ``error`` frames with ``ref_pc_id`` and never close the socket, except a kid mismatch (UNKNOWN_KEY, 4003)."""
    pc_id_str: str = frame["pc_id"]
    envelope = frame["envelope"]
    received_at = utcnow()
    try:
        if envelope.get("kid") != conn.kid:
            raise Rejection("UNKNOWN_KEY", "Envelope kid does not match this connection", close=True)
        if not conn.supports_input:
            raise Rejection("PROTOCOL_INCOMPATIBLE", "Announce protocol 1.1 in hello to send input")
        if conn.controller_id is None:
            raise Rejection("GRANT_MISSING", "This connection is not bound to a paired controller")
        async with svc.db() as db:
            ctrl, record = await resolve_controller_record(db, conn)
            if ctrl is None or record is None:
                raise Rejection("UNKNOWN_KEY", close=True)
            if ctrl.revoked_at is not None:
                raise Rejection("CONTROLLER_REVOKED")
            try:
                batch = verify_and_parse_input_batch(
                    envelope,
                    lambda kid: record if kid == conn.kid else None,
                    registry=svc.registry,
                    schemas=svc.schemas,
                )
                required = batch.required_capabilities
            except ProtocolError as exc:
                # the shared window check speaks in command terms; for a stream the honest code is INPUT_STALE
                code = "INPUT_STALE" if exc.code == "COMMAND_EXPIRED" else exc.code
                raise Rejection(code, exc.message, detail=exc.detail or None) from None
            if batch.target_pc_id != pc_id_str:
                raise Rejection("TARGET_PC_MISMATCH")
            # Defence in depth against replay on this socket: the agent's seq check is authoritative
            # (rules.input_sessions), but a batch this socket already forwarded for the same session is dropped here.
            last = conn.input_seq.get(batch.input_session_id)
            if last is not None and batch.seq <= last:
                raise Rejection("INPUT_SEQUENCE_INVALID")
            pc_id = uuid.UUID(pc_id_str)
            account = await db.get(Account, conn.account_id)
            plan = plan_for(account.plan if account else None)
            if ctrl.id not in await mgr.plan_enabled_controller_ids(db, conn.account_id, plan):
                raise Rejection("CONTROLLER_PLAN_DISABLED")
            pc = await db.get(PC, pc_id)
            if pc is None or pc.deleted_at is not None or pc.account_id != conn.account_id:
                raise Rejection("ACCOUNT_MISMATCH", "No such PC on this account")
            if not pc.enabled:
                raise Rejection("PC_PLAN_DISABLED")
            grant = await db.scalar(
                select(Grant).where(
                    Grant.controller_id == ctrl.id,
                    Grant.pc_id == pc.id,
                    Grant.account_id == conn.account_id,
                    Grant.revoked_at.is_(None),
                )
            )
            if grant is None:
                raise Rejection("GRANT_MISSING")
            held = set(grant.capabilities)
            if not ({"pointer", "keyboard"} & held) or not required <= held:
                raise Rejection("INPUT_NOT_PERMITTED", detail={"missing": sorted(required - held)})
        if not limiters.allow_input(plan, ctrl.id):
            raise Rejection("RATE_LIMITED")
        agent = mgr.agent_for(pc_id)
        if agent is None:
            raise Rejection("PC_OFFLINE")
        if not agent.snapshot_sent:
            raise Rejection("PC_RECONNECTING")
        if not agent.supports_input:
            raise Rejection("PROTOCOL_INCOMPATIBLE", "The PC's DoMe agent needs an update for touchpad and keyboard")
        forwarded = {
            "type": "input_batch",
            "envelope": envelope,
            "relay": {"received_at": ts_required(received_at), "connection_id": str(conn.connection_id)},
        }
        if not await agent.send(forwarded, validate=False):
            raise Rejection("PC_OFFLINE")
        conn.remember_input_seq(batch.input_session_id, batch.seq)
    except Rejection as rej:
        await _reject_input(mgr, limiters, conn, pc_id_str, rej)


async def _reject_input(
    mgr: ConnectionManager, limiters: RateLimiters, conn: ControllerConn, pc_id: str, rej: Rejection
) -> None:
    error = frames.error_object(rej.code, rej.message, detail=rej.detail)
    await conn.send(
        {"type": "error", "error": error, "ref_pc_id": pc_id}
        if _UUID_RE.match(pc_id)
        else {"type": "error", "error": error}
    )
    log.info("input_batch.rejected", code=rej.code, controller_id=str(conn.controller_id), pc_id=pc_id)
    # Bursty by nature (a phone keeps sending while a grant is gone): one event per minute per controller on
    # top of the per-socket cap, so the security log shows the fact without a row per batch.
    key = str(conn.controller_id or conn.connection_id)
    if limiters.input_rejections.allow(key):
        await audited_event(
            mgr,
            limiters,
            conn,
            kind="input_rejected",
            severity="notice" if rej.code in ("PC_OFFLINE", "PC_RECONNECTING", "RATE_LIMITED") else "warning",
            detail={"reason": rej.code, "pc_id": pc_id},
        )
    if rej.close:
        await conn.close(CLOSE_REVOKED)


async def reject_throttled(mgr: ConnectionManager, conn: ControllerConn, frame: dict[str, Any]) -> None:
    """Answer a frame the per-socket bucket refused without touching the database: a ``command`` still
    ends with a ``result`` when its id is readable (DECISIONS #7), anything else gets an ``error``."""
    pc_id = frame.get("pc_id")
    ref = pc_id if isinstance(pc_id, str) and _UUID_RE.match(pc_id) else None
    if frame.get("type") == "command":
        cid = untrusted_command_id(frame.get("envelope"))
        if cid is not None:
            await conn.send(
                frames.relay_result(cid, "failed", error=frames.error_object("RATE_LIMITED"), started_at=utcnow())
            )
            return
    await conn.send(frames.error_frame("RATE_LIMITED", "Too many frames on this connection", ref_pc_id=ref))


async def _answer_duplicate(conn: ControllerConn, agent: AgentConn | None, existing: Command) -> None:
    """Identical re-submission: re-emit the known terminal result, or the current ack while running."""
    inf = agent.inflight.get(existing.id) if agent is not None else None
    if inf is not None:
        if inf.state in ("accepted", "executing", "awaiting_confirmation"):
            await conn.send(
                {"type": "ack", "command_id": str(existing.id), "state": inf.state, "at": ts_required(utcnow())}
            )
        return  # still 'created': the original ack/result will arrive on its own
    state = existing.state if existing.state in TERMINAL_STATES else "failed"
    frame = frames.relay_result(existing.id, state)
    if existing.error_code:
        frame["error"] = frames.error_object(existing.error_code)
    elif state == "failed":
        frame["error"] = frames.error_object("OUTCOME_UNKNOWN")
    frame["duration_ms"] = int(existing.duration_ms or 0)
    frame["warning"] = "Duplicate of a command this relay already completed; the original outcome is shown."
    await conn.send(frame)


async def _reject(
    mgr: ConnectionManager,
    limiters: RateLimiters,
    conn: ControllerConn,
    pc_id: str,
    command_id: str | None,
    rej: Rejection,
    received_at: datetime,
) -> None:
    error = frames.error_object(rej.code, rej.message, detail=rej.detail)
    if command_id is not None:
        await conn.send(frames.relay_result(command_id, "failed", error=error, started_at=received_at))
    else:
        await conn.send(frames.error_frame(rej.code, rej.message, ref_pc_id=pc_id if _UUID_RE.match(pc_id) else None))
    log.info("command.rejected", code=rej.code, controller_id=str(conn.controller_id), pc_id=pc_id)
    await audited_event(
        mgr,
        limiters,
        conn,
        kind="command_rejected",
        severity="notice" if rej.code in ("PC_OFFLINE", "QUEUE_FULL", "RATE_LIMITED", "COMMAND_EXPIRED") else "warning",
        detail={"reason": rej.code, "pc_id": pc_id},
    )
    if rej.close:
        await conn.close(CLOSE_REVOKED)


__all__ = [
    "RateLimiters",
    "Rejection",
    "audited_event",
    "compute_deadline",
    "reject_throttled",
    "resolve_controller_record",
    "route_command",
    "route_input_batch",
    "IN_FLIGHT_STATES",
]
