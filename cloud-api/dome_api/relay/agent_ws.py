"""``/ws/agent`` — the PC's outbound socket."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from dome_protocol import ProtocolError, dumps_compact, loads_strict, parse_rfc3339, protocol_compatible
from dome_protocol.keys import kid_from_jwk
from sqlalchemy import select, update
from starlette.websockets import WebSocket, WebSocketDisconnect

from dome_api.auth.deps import resolve_agent_token
from dome_api.db.models import PC, Command, Controller, Grant, PairingSession
from dome_api.logging import get_logger
from dome_api.relay import frames
from dome_api.relay.manager import (
    CLOSE_FRAME_TOO_LARGE,
    CLOSE_PROTOCOL_ERROR,
    CLOSE_REVOKED,
    AgentConn,
    ConnectionManager,
)
from dome_api.relay.ws_http import deny_upgrade
from dome_api.security import events
from dome_api.state import Services
from dome_api.util import utcnow

log = get_logger("dome_api.relay.agent")
TERMINAL = ("succeeded", "failed", "expired", "canceled", "outcome_unknown")


async def agent_endpoint(ws: WebSocket, svc: Services) -> None:
    mgr = svc.relay
    headers = ws.headers
    if "origin" in headers:
        await deny_upgrade(ws, 403, "Browser origins may not open the agent socket")
        return
    scheme, _, token = headers.get("authorization", "").partition(" ")
    if (
        scheme.lower() != "bearer"
        or not (32 <= len(token.strip()) <= 128)
        or ws.query_params.get("access_token") is not None
    ):
        await deny_upgrade(ws, 401, "PC access token required in the Authorization header")
        return
    async with svc.db() as db:
        async with db.begin():
            identity = await resolve_agent_token(db, token.strip())
            if identity is None:
                await deny_upgrade(ws, 401, "PC access token invalid or expired")
                return
            pc_id, account_id = identity.pc.id, identity.account.id
    if not mgr.has_capacity():
        await deny_upgrade(ws, 503, "The relay is at its connection limit")
        return

    await ws.accept()
    conn = AgentConn(ws, pc_id, account_id)
    try:
        hello = await _receive_frame(ws, svc, "agent_to_relay", wait_seconds=svc.settings.relay_hello_timeout_seconds)
        if hello is None or hello.get("type") != "hello" or hello.get("component") != "agent":
            await conn.send(frames.error_frame("MALFORMED_MESSAGE", "first frame must be an agent hello"))
            await conn.close(CLOSE_PROTOCOL_ERROR)
            return
        if not _versions_ok(svc, hello):
            await conn.send(
                frames.error_frame(
                    "PROTOCOL_INCOMPATIBLE",
                    detail={
                        "peer": hello["protocol_versions"],
                        "supported": [svc.registry.protocol_version],
                        "registry": svc.registry.registry_version,
                    },
                )
            )
            await conn.close(CLOSE_PROTOCOL_ERROR)
            return
        await conn.send(frames.hello_ack(conn.connection_id, pc_id=pc_id))
        await mgr.register_agent(conn)
        async with svc.db() as db:
            async with db.begin():
                pc = await db.get(PC, pc_id)
                if pc is None or pc.deleted_at is not None:
                    await conn.send({"type": "revoked", "reason": "pc_unlinked"})
                    await conn.close(CLOSE_REVOKED)
                    return
                pc.agent_version = str(hello.get("component_version", ""))[:64]
                pc.last_seen_at = utcnow()
                snapshot = await mgr.build_snapshot(db, pc)
        if not await conn.send(snapshot):
            return
        conn.snapshot_sent = True
        await mgr.broadcast_pc_status(pc_id)
        await mgr.security_event(
            account_id=account_id,
            kind="agent_connected",
            severity="info",
            actor="pc",
            subject_id=pc_id,
            detail={"agent_version": pc.agent_version},
        )
        delivered = await mgr.redeliver_pending_pairing_requests(conn)
        if delivered:
            log.info("pairing.redelivered", pc_id=str(pc_id), count=delivered)
        await _loop(ws, svc, mgr, conn)
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001
        log.exception("agent.socket_error", pc_id=str(pc_id))
        await conn.close(CLOSE_PROTOCOL_ERROR)
    finally:
        conn.closed = True
        was_current = mgr.agents.get(pc_id) is conn
        await mgr.unregister_agent(conn)
        if was_current:
            await mgr.security_event(
                account_id=account_id, kind="agent_disconnected", severity="info", actor="pc", subject_id=pc_id
            )


def _versions_ok(svc: Services, hello: dict[str, Any]) -> bool:
    ours = (svc.registry.protocol_version,)
    proto_ok = any(protocol_compatible(v, ours) for v in hello["protocol_versions"])
    reg_ok = protocol_compatible(hello["registry_version"], (svc.registry.registry_version,))
    return proto_ok and reg_ok


async def _receive_frame(
    ws: WebSocket, svc: Services, direction: str, *, wait_seconds: float | None = None
) -> dict[str, Any] | None:
    """Receive one text frame; enforce the size limit and the schema. Returns None on protocol error
    after sending an error frame (the caller decides whether to close)."""
    if wait_seconds is not None:
        text = await asyncio.wait_for(ws.receive_text(), wait_seconds)
    else:
        text = await ws.receive_text()
    if len(text.encode("utf-8", "surrogatepass")) > svc.settings.relay_max_frame_bytes:
        await ws.close(code=CLOSE_FRAME_TOO_LARGE)
        raise WebSocketDisconnect(CLOSE_FRAME_TOO_LARGE)
    try:
        frame = loads_strict(text, max_bytes=svc.settings.relay_max_frame_bytes, max_depth=12, require_object=True)
        svc.schemas.validate_frame(direction, frame)
    except ProtocolError as exc:
        await ws.send_text(dumps_compact(frames.error_frame(exc.code, exc.message)))
        return None
    assert isinstance(frame, dict)
    return frame


async def _loop(ws: WebSocket, svc: Services, mgr: ConnectionManager, conn: AgentConn) -> None:
    bad_frames = 0
    while True:
        frame = await _receive_frame(ws, svc, "agent_to_relay")
        conn.last_seen = utcnow()
        if frame is None:
            bad_frames += 1
            if bad_frames >= 5:
                await conn.close(CLOSE_PROTOCOL_ERROR)
                return
            continue
        kind = frame["type"]
        if kind == "ping":
            await conn.send({"type": "pong", **({"t": frame["t"]} if "t" in frame else {})})
        elif kind == "pong":
            pass
        elif kind == "hello":
            await conn.send(frames.error_frame("MALFORMED_MESSAGE", "hello already received"))
        elif kind == "ack":
            await _on_ack(mgr, conn, frame)
        elif kind == "confirmation_required":
            await _on_confirmation_required(svc, mgr, conn, frame)
        elif kind == "result":
            await _on_result(mgr, conn, frame)
        elif kind == "state":
            await _on_state(mgr, conn, frame)
        elif kind == "pairing_decision":
            await _on_pairing_decision(svc, mgr, conn, frame)
        elif kind == "revoke_controller":
            await _on_revoke_controller(mgr, conn, frame)
        elif kind == "error":
            log.warning("agent.error_frame", pc_id=str(conn.pc_id), code=frame["error"].get("code"))
        await mgr.touch_pc_last_seen(conn.pc_id, conn.last_seen)


async def _on_ack(mgr: ConnectionManager, conn: AgentConn, frame: dict[str, Any]) -> None:
    cid = uuid.UUID(frame["command_id"])
    inf = conn.inflight.get(cid)
    if inf is None:
        log.info("ack.unknown_command", pc_id=str(conn.pc_id))
        return
    state = frame["state"]
    inf.state = state
    if state == "executing":
        inf.executing_seen = True
    async with mgr.db() as db:
        async with db.begin():
            await db.execute(
                update(Command)
                .where(
                    Command.id == cid, Command.state.in_(("created", "accepted", "executing", "awaiting_confirmation"))
                )
                .values(state=state, acked_at=utcnow())
            )
    await mgr.send_to_controller(inf.controller_id, inf.controller_conn_id, frame, validate=False)


async def _on_confirmation_required(
    svc: Services, mgr: ConnectionManager, conn: AgentConn, frame: dict[str, Any]
) -> None:
    cid = uuid.UUID(frame["command_id"])
    inf = conn.inflight.get(cid)
    if inf is None:
        log.info("confirmation_required.unknown_command", pc_id=str(conn.pc_id))
        return
    try:
        challenge = svc.schemas.validate_challenge_text(
            frame["challenge_text"], max_bytes=svc.registry.limits["max_challenge_text_bytes"]
        )
        if (
            challenge["command_id"] != str(cid)
            or challenge["pc_id"] != str(conn.pc_id)
            or challenge["controller_id"] != str(inf.controller_id)
        ):
            raise ProtocolError("MALFORMED_MESSAGE", "challenge does not bind to this command")
    except ProtocolError as exc:
        log.warning("confirmation_required.invalid", pc_id=str(conn.pc_id), code=exc.code)
        await mgr.security_event(
            account_id=conn.account_id,
            kind="relay_frame_rejected",
            severity="warning",
            actor="pc",
            subject_id=conn.pc_id,
            detail={"frame": "confirmation_required", "reason": exc.code},
        )
        conn.inflight.pop(cid, None)
        await conn.send({"type": "cancel", "command_id": str(cid), "controller_id": str(inf.controller_id)})
        result = frames.relay_result(
            cid,
            "failed",
            error=frames.error_object("MALFORMED_MESSAGE", "The PC sent an invalid confirmation challenge."),
            started_at=inf.received_at,
        )
        await mgr.finish_command(inf, "failed", "MALFORMED_MESSAGE", result)
        return
    inf.state = "awaiting_confirmation"
    async with mgr.db() as db:
        async with db.begin():
            await db.execute(
                update(Command)
                .where(Command.id == cid, Command.state.in_(("created", "accepted", "executing")))
                .values(state="awaiting_confirmation", acked_at=utcnow())
            )
    # forward the ORIGINAL frame (challenge_text untouched)
    await mgr.send_to_controller(inf.controller_id, inf.controller_conn_id, frame, validate=False)


async def _on_result(mgr: ConnectionManager, conn: AgentConn, frame: dict[str, Any]) -> None:
    cid = uuid.UUID(frame["command_id"])
    now = utcnow()
    state = frame["state"]
    error_code = frame.get("error", {}).get("code") if isinstance(frame.get("error"), dict) else None
    inf = conn.inflight.pop(cid, None)
    if inf is not None:
        inf.state = state
        async with mgr.db() as db:
            async with db.begin():
                await db.execute(
                    update(Command)
                    .where(Command.id == cid)
                    .values(state=state, error_code=error_code, finished_at=now, duration_ms=int(frame["duration_ms"]))
                )
        await mgr.send_to_controller(inf.controller_id, inf.controller_conn_id, frame, validate=False)
        return
    # Late result (rules.late_results): correct an outcome_unknown exactly once; drop anything else.
    async with mgr.db() as db:
        async with db.begin():
            row = await db.get(Command, cid)
            if row is None or row.pc_id != conn.pc_id or row.state != "outcome_unknown" or row.corrected_at is not None:
                log.info("result.post_terminal_dropped", pc_id=str(conn.pc_id), known=row is not None)
                return
            row.state = state
            row.error_code = error_code
            row.corrected_at = now
            row.duration_ms = int(frame["duration_ms"])
            controller_id = row.controller_id
    log.info("result.late_correction", pc_id=str(conn.pc_id))
    await mgr.send_to_controller(controller_id, None, frame, validate=False)


async def _on_state(mgr: ConnectionManager, conn: AgentConn, frame: dict[str, Any]) -> None:
    if frame["pc_id"] != str(conn.pc_id):
        await conn.send(frames.error_frame("TARGET_PC_MISMATCH", "state frame names another PC"))
        return
    state = frame["state"]
    conn.state_frame = frame
    pp = state.get("pending_power_action")
    if isinstance(pp, dict):
        try:
            conn.pending_power = (uuid.UUID(pp["command_id"]), parse_rfc3339(pp["fires_at"]))
        except (ProtocolError, ValueError):
            conn.pending_power = None
    else:
        conn.pending_power = None
    remote_enabled = bool(state["remote_enabled"])
    if conn.remote_enabled_reported != remote_enabled:
        conn.remote_enabled_reported = remote_enabled
        async with mgr.db() as db:
            async with db.begin():
                await db.execute(update(PC).where(PC.id == conn.pc_id).values(remote_enabled_reported=remote_enabled))
    await mgr.broadcast_to_subscribers(conn.pc_id, frame, validate=False)


async def _on_pairing_decision(svc: Services, mgr: ConnectionManager, conn: AgentConn, frame: dict[str, Any]) -> None:
    pairing_id = uuid.UUID(frame["pairing_id"])
    decision = frame["decision"]
    now = utcnow()
    outcome: str | None = None
    async with svc.db() as db:
        async with db.begin():
            ps = await db.scalar(
                select(PairingSession)
                .where(PairingSession.id == pairing_id, PairingSession.pc_id == conn.pc_id)
                .with_for_update()
            )
            if ps is None or ps.state != "claimed":
                log.info("pairing_decision.ignored", pc_id=str(conn.pc_id), reason="not_claimed")
                return
            if ps.expires_at <= now:
                ps.state = "expired"
                ps.decided_at = now
                outcome = "expired"
            elif decision == "decline":
                ps.state = "declined"
                ps.decided_at = now
                outcome = "declined"
            else:
                if (
                    frame["kid"] != ps.controller_kid
                    or ps.controller_public_jwk is None
                    or kid_from_jwk(ps.controller_public_jwk) != frame["kid"]
                ):
                    log.warning("pairing_decision.kid_mismatch", pc_id=str(conn.pc_id))
                    ps.state = "declined"
                    ps.decided_at = now
                    outcome = "kid_mismatch"
                else:
                    granted = sorted(set(frame["granted_capabilities"]) & set(ps.requested_capabilities or []))
                    ctrl = await db.scalar(
                        select(Controller)
                        .where(Controller.account_id == ps.account_id, Controller.kid == ps.controller_kid)
                        .with_for_update()
                    )
                    if ctrl is None:
                        ctrl = Controller(
                            account_id=ps.account_id,
                            kid=ps.controller_kid,
                            public_jwk=ps.controller_public_jwk,
                            display_name=(ps.controller_display_name or "Phone")[:64],
                            created_at=now,
                        )
                        db.add(ctrl)
                        await db.flush()
                    else:
                        ctrl.revoked_at = None
                        ctrl.public_jwk = ps.controller_public_jwk
                        ctrl.display_name = (ps.controller_display_name or ctrl.display_name)[:64]
                    grant = await db.scalar(
                        select(Grant)
                        .where(Grant.controller_id == ctrl.id, Grant.pc_id == ps.pc_id, Grant.revoked_at.is_(None))
                        .with_for_update()
                    )
                    if grant is None:
                        grant = Grant(
                            account_id=ps.account_id,
                            controller_id=ctrl.id,
                            pc_id=ps.pc_id,
                            capabilities=granted,
                            created_at=now,
                        )
                        db.add(grant)
                        await db.flush()
                    else:
                        grant.capabilities = granted
                    ps.state = "approved"
                    ps.decided_at = now
                    ps.controller_id = ctrl.id
                    ps.grant_id = grant.id
                    outcome = "approved"
                    events.record(
                        db,
                        account_id=ps.account_id,
                        kind="controller_paired",
                        severity="notice",
                        actor="pc",
                        subject_id=ctrl.id,
                        detail={"pc_id": str(ps.pc_id), "capabilities": granted},
                    )
            if outcome in ("declined", "kid_mismatch", "expired"):
                events.record(
                    db,
                    account_id=ps.account_id,
                    kind="pairing_declined",
                    severity="notice",
                    actor="pc",
                    subject_id=ps.pc_id,
                    detail={"reason": outcome},
                )
    if outcome == "approved":
        await mgr.push_grants_snapshot(conn.pc_id)


async def _on_revoke_controller(mgr: ConnectionManager, conn: AgentConn, frame: dict[str, Any]) -> None:
    controller_id = uuid.UUID(frame["controller_id"])
    now = utcnow()
    async with mgr.db() as db:
        async with db.begin():
            ctrl = await db.get(Controller, controller_id)
            if ctrl is None or ctrl.account_id != conn.account_id or ctrl.kid != frame["kid"]:
                log.info("revoke_controller.ignored", pc_id=str(conn.pc_id))
                return
            res = await db.execute(
                update(Grant)
                .where(Grant.controller_id == controller_id, Grant.pc_id == conn.pc_id, Grant.revoked_at.is_(None))
                .values(revoked_at=now)
            )
            events.record(
                db,
                account_id=conn.account_id,
                kind="grant_revoked",
                severity="notice",
                actor="pc",
                subject_id=controller_id,
                detail={
                    "pc_id": str(conn.pc_id),
                    "reason": frame["reason"],
                    "grants": int(getattr(res, "rowcount", 0) or 0),
                },
            )
    await mgr.apply_controller_revocation(
        controller_id, reason="grant_revoked", pc_ids=[conn.pc_id], revoked_pc_id=conn.pc_id
    )
