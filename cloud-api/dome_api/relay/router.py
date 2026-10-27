"""Command routing (design: Relay → "Command routing", in this exact order). Every rejection
after the frame itself parsed is a ``result{origin:'relay', state:'failed', error}`` so each
command_id a controller sends ends with exactly one result (``rules.terminal_result``)."""

from __future__ import annotations

import re
import time
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
    protocol_compatible,
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
from dome_api.relay.manager import (
    CLOSE_REVOKED,
    AgentConn,
    ConnectionManager,
    ControllerConn,
    InFlight,
    InputAuthorization,
)
from dome_api.screen.metering import AllowanceExhaustedError
from dome_api.security.ratelimit import SlidingWindowLimiter, TokenBucketLimiter
from dome_api.settings import Settings
from dome_api.state import Services
from dome_api.util import ts, ts_required, utcnow

log = get_logger("dome_api.relay.router")
_UUID_RE = re.compile(r"\A[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
TERMINAL_STATES = ("succeeded", "failed", "expired", "canceled", "outcome_unknown")
IN_FLIGHT_STATES = ("created", "accepted", "executing", "awaiting_confirmation")
SCREEN_PROTOCOL_VERSION = "1.4"  # the MINOR that introduced Screen view (rules.controller_socket_identity)
SCREEN_START_ACTION = "screen.session_start"


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
    * ``input_refusals``: per socket, a leaky budget for ``input_frames`` refusals (2x the input rate sustained,
      capacity 5 s of that); a socket that empties it is flooding far beyond anything a touchpad sends and is
      closed (4000) with one ``controller_throttled`` event, as the command path does after ``burst`` refusals;
    * ``input_rejections``: at most one ``input_rejected`` security event per minute per controller;
    * Screen view (rules.screen_metering (5)): per-plan ``screen_ice_rate_limit`` buckets keyed by controller id plus
      the per-account hourly window for ``screen_ice_request``, per-plan ``screen_start_rate_limit`` buckets for
      ``screen.session_start`` (on top of the manual command bucket), and ``screen_usage``: two ``screen_usage``
      frames per minute per session (design §12).
    """

    def __init__(self, settings: Settings, registry: Registry | None = None) -> None:
        self._manual: dict[str, TokenBucketLimiter] = {}
        self._coalescable: dict[str, TokenBucketLimiter] = {}
        self._input: dict[str, TokenBucketLimiter] = {}
        self._screen_ice: dict[str, TokenBucketLimiter] = {}
        self._screen_ice_account: dict[str, SlidingWindowLimiter] = {}
        self._screen_start: dict[str, TokenBucketLimiter] = {}
        self.screen_usage = SlidingWindowLimiter(2, 60)
        self.frames = TokenBucketLimiter(
            settings.relay_controller_frames_per_minute, settings.relay_controller_frame_burst
        )
        self.events = SlidingWindowLimiter(settings.relay_security_events_per_connection_per_minute, 60)
        limits = registry.limits if registry is not None else {}
        per_second = int(limits.get("input_batches_per_second", 40))
        burst = max(p.input_rate_limit.burst for p in catalog().plans.values())
        # the agent's age budget for a batch at dispatch (version.json): an input_batch that waited this long for the
        # socket loop while a newer one arrived behind it is dropped unrouted (controller_ws, DECISIONS #112)
        self.input_age_budget = int(limits.get("input_age_budget_ms", 1000)) / 1000.0
        self.input_frames = TokenBucketLimiter(per_second * 60, burst)
        self.input_flood_factor = 2
        self.input_flood_seconds = 5
        self.input_refusals = TokenBucketLimiter(
            per_second * self.input_flood_factor * 60, per_second * self.input_flood_factor * self.input_flood_seconds
        )
        self.input_rejections = SlidingWindowLimiter(1, 60)

    def forget_connection(self, conn: ControllerConn) -> None:
        self.frames.forget(str(conn.connection_id))
        self.input_frames.forget(str(conn.connection_id))
        self.input_refusals.forget(str(conn.connection_id))

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

    def allow_screen_ice(self, plan: Plan, controller_id: uuid.UUID, account_id: uuid.UUID) -> bool:
        """``screen_ice_request``: per controller (token bucket) and per account (hourly window); both must have room,
        and a refusal consumes nothing from the other."""
        bucket = self._screen_ice.get(plan.id)
        if bucket is None:
            rl = plan.screen_ice_rate_limit
            bucket = self._screen_ice[plan.id] = TokenBucketLimiter(rl.per_minute, rl.burst)
        hourly = self._screen_ice_account.get(plan.id)
        if hourly is None:
            hourly = self._screen_ice_account[plan.id] = SlidingWindowLimiter(plan.screen_ice_account_per_hour, 3600)
        if hourly.exhausted(str(account_id)):
            return False
        if not bucket.allow(str(controller_id)):
            return False
        hourly.hit(str(account_id))
        return True

    def allow_screen_start(self, plan: Plan, controller_id: uuid.UUID) -> bool:
        limiter = self._screen_start.get(plan.id)
        if limiter is None:
            rl = plan.screen_start_rate_limit
            limiter = self._screen_start[plan.id] = TokenBucketLimiter(rl.per_minute, rl.burst)
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


async def load_input_authorization(
    db: AsyncSession, mgr: ConnectionManager, conn: ControllerConn, pc_id: uuid.UUID, loaded_at: float
) -> InputAuthorization:
    """Read what ``route_input_batch`` checks in the database, in the order it checks it, stopping at the first
    refusal like the per-batch reads did: the socket's controller row, the plan and the enabled set, the PC, the
    live grant on it."""
    ctrl, record = await resolve_controller_record(db, conn)
    if ctrl is None or record is None:
        return InputAuthorization(loaded_at, None)
    if ctrl.revoked_at is not None:
        return InputAuthorization(loaded_at, record, controller_revoked=True)
    account = await db.get(Account, conn.account_id)
    plan = plan_for(account.plan if account else None)
    if ctrl.id not in await mgr.plan_enabled_controller_ids(db, conn.account_id, plan):
        return InputAuthorization(loaded_at, record, plan=plan)
    pc = await db.get(PC, pc_id)
    if pc is None or pc.deleted_at is not None or pc.account_id != conn.account_id:
        return InputAuthorization(loaded_at, record, plan=plan, plan_enabled=True, pc_refusal="ACCOUNT_MISMATCH")
    if not pc.enabled:
        return InputAuthorization(loaded_at, record, plan=plan, plan_enabled=True, pc_refusal="PC_PLAN_DISABLED")
    capabilities = await db.scalar(
        select(Grant.capabilities).where(
            Grant.controller_id == ctrl.id,
            Grant.pc_id == pc.id,
            Grant.account_id == conn.account_id,
            Grant.revoked_at.is_(None),
        )
    )
    return InputAuthorization(
        loaded_at,
        record,
        plan=plan,
        plan_enabled=True,
        capabilities=frozenset(capabilities) if capabilities is not None else None,
    )


async def input_authorization(
    svc: Services, mgr: ConnectionManager, conn: ControllerConn, pc_id: uuid.UUID
) -> InputAuthorization:
    """The socket's cached checks for the PC while younger than ``mgr.input_auth_ttl``, else a fresh read (DECISIONS
    #110). A touchpad sends up to 40 batches/s; reading five rows for each one made the relay's per-socket queue the
    bottleneck. Changes made through this relay drop the cached copy at once; the TTL bounds the rest."""
    now = time.monotonic()
    cached = conn.cached_input_auth(pc_id, now, mgr.input_auth_ttl)
    if cached is not None:
        conn.input_timing.auth_hits += 1
        return cached
    conn.input_timing.auth_misses += 1
    epoch = conn.input_auth_epoch
    async with svc.db() as db:
        auth = await load_input_authorization(db, mgr, conn, pc_id, now)
    conn.remember_input_auth(pc_id, auth, epoch)
    return auth


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
    # a refused screen.session_start also revokes the phone's TURN credential (design §13): remembered once parsed
    screen_start_by: uuid.UUID | None = None
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
            if cmd.spec.name == SCREEN_START_ACTION:
                screen_start_by = ctrl.id
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
            # 8a. Screen view (rules.screen_signaling (3)): the agent must have announced 1.4 for any screen.* action
            # (the shipped 1.2 agent cannot answer for itself); a start also passes the operator switch, its own
            # per-controller budget and the ledger, whose first chunk is reserved in this same transaction
            screen_relay: dict[str, Any] | None = None
            screen_reservation_id: uuid.UUID | None = None
            if cmd.spec.capability == "screen" and not agent.supports_screen:
                raise Rejection(
                    "PROTOCOL_INCOMPATIBLE",
                    "The PC's DoMe agent needs an update for Screen view",
                    detail={"peer": "agent", "required": SCREEN_PROTOCOL_VERSION},
                )
            if cmd.spec.name == SCREEN_START_ACTION:
                if not svc.screen.config.enabled:
                    raise Rejection("SCREEN_VIEW_DISABLED")
                if not limiters.allow_screen_start(plan, ctrl.id):
                    raise Rejection("RATE_LIMITED")
                ctrl_id, screen_pc_id = ctrl.id, pc.id  # a rollback expires the ORM rows; keep the ids
                try:
                    screen_reservation_id, screen_relay = await svc.screen.reserve_start(
                        db,
                        account_id=conn.account_id,
                        pc_id=screen_pc_id,
                        controller_id=ctrl_id,
                        command_id=uuid.UUID(cmd.command_id),
                        plan=plan,
                        now=svc.screen.now(),  # the ledger's clock (the relay's own; replaceable in tests)
                    )
                except AllowanceExhaustedError as exc:
                    await db.rollback()
                    await svc.screen.allowance_exhausted_event(conn.account_id, ctrl_id, screen_pc_id, exc.resets_at)
                    raise Rejection("SCREEN_ALLOWANCE_EXHAUSTED", detail={"resets_at": ts(exc.resets_at)}) from None
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
            forwarded: dict[str, Any] = {
                "type": "command",
                "envelope": envelope,
                "relay": {"received_at": ts_required(received_at), "connection_id": str(conn.connection_id)},
            }
            if screen_relay is not None:
                forwarded["relay"]["screen"] = screen_relay
            if not await agent.send(forwarded, validate=False):
                agent.inflight.pop(inf.command_id, None)
                failed = frames.relay_result(
                    inf.command_id, "failed", error=frames.error_object("PC_OFFLINE"), started_at=received_at
                )
                await mgr.finish_command(inf, "failed", "PC_OFFLINE", failed)
                if screen_reservation_id is not None:
                    await svc.screen.on_start_lost(screen_reservation_id, ctrl.id, conn.account_id)
                return
            if screen_reservation_id is not None:
                await svc.screen.on_start_forwarded(
                    agent,
                    command_id=inf.command_id,
                    controller_id=ctrl.id,
                    controller_conn_id=conn.connection_id,
                    account_id=conn.account_id,
                    reservation_id=screen_reservation_id,
                    now=svc.screen.now(),
                )
            log.info("command.forwarded", action=cmd.spec.name, pc_id=pc_id_str, controller_id=str(ctrl.id))
    except Rejection as rej:
        await _reject(mgr, limiters, conn, pc_id_str, command_id, rej, received_at)
        if screen_start_by is not None:
            await svc.screen.on_start_refused(screen_start_by, rej.code)


async def route_input_batch(
    svc: Services,
    mgr: ConnectionManager,
    limiters: RateLimiters,
    conn: ControllerConn,
    frame: dict[str, Any],
    *,
    received_at: datetime | None = None,
) -> None:
    """``rules.input_sessions`` (2), relay half: verify the controller's signature with the socket's key
    record, bind the batch to this socket's controller and account, require a live grant covering every
    event type, the PC online and past its first snapshot, the per-controller input budget, then forward the
    envelope verbatim. No ``commands`` row is written and nothing about the content is logged. Rejections are
    ``error`` frames with ``ref_pc_id`` and never close the socket, except a kid mismatch (UNKNOWN_KEY, 4003).
    The database rows behind the checks come from ``input_authorization`` (cached per socket and PC, DECISIONS #110);
    the signature, binding, window and sequence are checked on every batch. ``received_at`` is the moment the frame
    arrived on the socket (the loop's stamp, DECISIONS #112); it becomes ``relay.received_at`` for the agent, so the
    time the batch waited for its turn here counts toward the relay → agent leg. The time spent in each stage goes to
    the socket's ``relay.input_timing`` window (DECISIONS #113)."""
    pc_id_str: str = frame["pc_id"]
    envelope = frame["envelope"]
    if received_at is None:
        received_at = utcnow()
    timing = conn.input_timing
    try:
        if envelope.get("kid") != conn.kid:
            raise Rejection("UNKNOWN_KEY", "Envelope kid does not match this connection", close=True)
        if not conn.supports_input:
            raise Rejection("PROTOCOL_INCOMPATIBLE", "Announce protocol 1.1 in hello to send input")
        controller_id = conn.controller_id
        if controller_id is None:
            raise Rejection("GRANT_MISSING", "This connection is not bound to a paired controller")
        pc_id = uuid.UUID(pc_id_str)  # the frame schema already holds pc_id to a lowercase UUID
        started = time.monotonic()
        auth = await input_authorization(svc, mgr, conn, pc_id)
        timing.auth.add(time.monotonic() - started)
        record = auth.record
        if record is None:
            raise Rejection("UNKNOWN_KEY", close=True)
        if auth.controller_revoked:
            raise Rejection("CONTROLLER_REVOKED")
        started = time.monotonic()
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
        finally:
            timing.verify.add(time.monotonic() - started)
        if batch.target_pc_id != pc_id_str:
            raise Rejection("TARGET_PC_MISMATCH")
        # Defence in depth against replay on this socket: the agent's seq check is authoritative
        # (rules.input_sessions), but a batch this socket already forwarded for the same session is dropped here.
        last = conn.input_seq.get(batch.input_session_id)
        if last is not None and batch.seq <= last:
            raise Rejection("INPUT_SEQUENCE_INVALID")
        plan = auth.plan
        if plan is None or not auth.plan_enabled:
            raise Rejection("CONTROLLER_PLAN_DISABLED")
        if auth.pc_refusal == "ACCOUNT_MISMATCH":
            raise Rejection("ACCOUNT_MISMATCH", "No such PC on this account")
        if auth.pc_refusal is not None:
            raise Rejection(auth.pc_refusal)
        held = auth.capabilities
        if held is None:
            raise Rejection("GRANT_MISSING")
        if not ({"pointer", "keyboard"} & held) or not required <= held:
            raise Rejection("INPUT_NOT_PERMITTED", detail={"missing": sorted(required - held)})
        if not limiters.allow_input(plan, controller_id):
            raise Rejection("RATE_LIMITED")
        agent = mgr.agent_for(pc_id)
        if agent is None:
            raise Rejection("PC_OFFLINE")
        if not agent.snapshot_sent:
            raise Rejection("PC_RECONNECTING")
        if not agent.supports_input:
            raise Rejection("PROTOCOL_INCOMPATIBLE", "The PC's DoMe agent needs an update for touchpad and keyboard")
        # the stamp gate (rules.command_version_stamping, 1.4): a batch stamped with a MINOR the agent did not announce
        # (a 1.4 tap with pointer_move_to to a 1.2 agent) is refused here, because the shipped agent would answer
        # MALFORMED_MESSAGE for it instead of PROTOCOL_INCOMPATIBLE (PROTOCOL.md §20.6)
        stamp = str(batch.payload["protocol_version"])
        if not protocol_compatible(stamp, agent.protocol_versions):
            raise Rejection(
                "PROTOCOL_INCOMPATIBLE",
                "The PC's DoMe agent needs an update for Screen view",
                detail={"peer": "agent", "required": batch.required_protocol_version},
            )
        forwarded = {
            "type": "input_batch",
            "envelope": envelope,
            "relay": {"received_at": ts_required(received_at), "connection_id": str(conn.connection_id)},
        }
        started = time.monotonic()
        sent = await agent.send(forwarded, validate=False)
        timing.forward.add(time.monotonic() - started)
        if not sent:
            raise Rejection("PC_OFFLINE")
        conn.remember_input_seq(batch.input_session_id, batch.seq)
        timing.forwarded += 1
    except Rejection as rej:
        timing.refuse(rej.code)
        await _reject_input(mgr, limiters, conn, pc_id_str, rej)


async def drop_stale_input(conn: ControllerConn, pc_id: str) -> None:
    """A batch the socket loop dropped without routing it: it waited the agent's whole age budget for its turn while
    a newer batch arrived behind it, so the PC would have discarded it as stale (DECISIONS #112). The phone hears
    INPUT_STALE at most once per second, as it does from the agent; nothing about the batch was checked, so no
    security event is written."""
    if not conn.input_stale_notice_due(utcnow()):
        return
    await conn.send(
        frames.error_frame(
            "INPUT_STALE", "Dropped by the relay: the batch waited longer than the input age budget", ref_pc_id=pc_id
        )
    )
    log.info("input_batch.dropped_stale", controller_id=str(conn.controller_id), pc_id=pc_id)


async def route_screen_ice_request(
    svc: Services, mgr: ConnectionManager, limiters: RateLimiters, conn: ControllerConn, frame: dict[str, Any]
) -> None:
    """``rules.screen_signaling`` (1): the socket announced 1.4 and is bound; the operator switch; a live grant with
    ``screen`` for this controller on this PC; the PC online and past its first snapshot; the rate limits; the
    allowance; then the phone's TURN credential (one outstanding per controller) and ``screen_ice_servers``. Refusals
    are ``error`` frames with ``ref_pc_id`` (never a result: no command exists yet) and never close the socket."""
    pc_id_str: str = frame["pc_id"]
    now = svc.screen.now()
    try:
        if not conn.supports_screen:
            raise Rejection(
                "PROTOCOL_INCOMPATIBLE",
                "Announce protocol 1.4 in hello to use Screen view",
                detail={"peer": "controller", "required": SCREEN_PROTOCOL_VERSION},
            )
        if not svc.screen.config.enabled:
            raise Rejection("SCREEN_VIEW_DISABLED")
        if conn.controller_id is None:
            raise Rejection("GRANT_MISSING", "This connection is not bound to a paired controller")
        pc_id = uuid.UUID(pc_id_str)
        async with svc.db() as db:
            ctrl, record = await resolve_controller_record(db, conn)
            if ctrl is None or record is None or ctrl.revoked_at is not None:
                raise Rejection("CONTROLLER_REVOKED")
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
            if grant is None or "screen" not in set(grant.capabilities):
                raise Rejection("GRANT_MISSING", "Allow Screen view for this phone on the PC first")
            agent = mgr.agent_for(pc.id)
            if agent is None:
                raise Rejection("PC_OFFLINE")
            if not agent.snapshot_sent:
                raise Rejection("PC_RECONNECTING")
            if not agent.supports_screen:
                raise Rejection(
                    "PROTOCOL_INCOMPATIBLE",
                    "The PC's DoMe agent needs an update for Screen view",
                    detail={"peer": "agent", "required": SCREEN_PROTOCOL_VERSION},
                )
            if not limiters.allow_screen_ice(plan, ctrl.id, conn.account_id):
                raise Rejection("RATE_LIMITED")
            ctrl_id, pc_uuid = ctrl.id, pc.id  # a rollback expires the ORM rows; keep the ids
            try:
                answer = await svc.screen.ice_servers(
                    db, account_id=conn.account_id, controller_id=ctrl_id, plan=plan, now=now
                )
                await db.commit()  # the TURN row, the mint counter and any cap event
            except AllowanceExhaustedError as exc:
                await db.rollback()
                await svc.screen.allowance_exhausted_event(conn.account_id, ctrl_id, pc_uuid, exc.resets_at)
                raise Rejection("SCREEN_ALLOWANCE_EXHAUSTED", detail={"resets_at": ts(exc.resets_at)}) from None
        await conn.send(answer.frame(pc_uuid))
    except Rejection as rej:
        error = frames.error_object(rej.code, rej.message, detail=rej.detail)
        await conn.send(
            {"type": "error", "error": error, "ref_pc_id": pc_id_str}
            if _UUID_RE.match(pc_id_str)
            else {"type": "error", "error": error}
        )
        log.info("screen_ice_request.rejected", code=rej.code, controller_id=str(conn.controller_id), pc_id=pc_id_str)
        await audited_event(
            mgr,
            limiters,
            conn,
            kind="screen_ice_rejected",
            severity="notice" if rej.code in ("PC_OFFLINE", "PC_RECONNECTING", "RATE_LIMITED") else "warning",
            detail={"reason": rej.code, "pc_id": pc_id_str},
        )
        if rej.close:
            await conn.close(CLOSE_REVOKED)


async def _reject_input(
    mgr: ConnectionManager, limiters: RateLimiters, conn: ControllerConn, pc_id: str, rej: Rejection
) -> None:
    if rej.code == "RATE_LIMITED" and not conn.input_notice_due(utcnow()):
        # the per-controller budget refused another batch within the same second (e.g. a second socket of the
        # same phone): no frame, no log line, no event — the first refusal of the second already said so
        return
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
    "drop_stale_input",
    "input_authorization",
    "load_input_authorization",
    "reject_throttled",
    "resolve_controller_record",
    "route_command",
    "route_input_batch",
    "route_screen_ice_request",
    "IN_FLIGHT_STATES",
    "SCREEN_PROTOCOL_VERSION",
]
