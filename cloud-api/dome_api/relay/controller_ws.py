"""``/ws/controller`` — the phone's socket (cookie session + exact Origin)."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from dome_protocol import (
    KeyRecord,
    ProtocolError,
    dumps_compact,
    loads_strict,
    parse_rfc3339,
    protocol_compatible,
    verify_and_parse_confirmation,
    verify_hello_proof,
)
from sqlalchemy import select
from starlette.websockets import WebSocket, WebSocketDisconnect

from dome_api.auth.sessions import COOKIE_NAME, resolve_session
from dome_api.db.models import PC, Controller, Grant
from dome_api.logging import get_logger
from dome_api.relay import frames
from dome_api.relay.manager import (
    CLOSE_FRAME_TOO_LARGE,
    CLOSE_PROTOCOL_ERROR,
    CLOSE_REVOKED,
    PC_STATE_1_1_FIELDS,
    ConnectionManager,
    ControllerConn,
)
from dome_api.relay.router import (
    RateLimiters,
    audited_event,
    reject_throttled,
    resolve_controller_record,
    route_command,
    route_input_batch,
)
from dome_api.relay.ws_http import deny_upgrade
from dome_api.security.origin import origin_allowed
from dome_api.state import Services
from dome_api.util import ts_required, utcnow

log = get_logger("dome_api.relay.controller")


async def controller_endpoint(ws: WebSocket, svc: Services, limiters: RateLimiters) -> None:
    mgr = svc.relay
    if not origin_allowed(ws.headers.get("origin"), svc.settings.allowed_origins):
        await deny_upgrade(ws, 403, "Origin not allowed")
        return
    async with svc.db() as db:
        async with db.begin():
            auth = await resolve_session(db, svc.settings, ws.cookies.get(COOKIE_NAME))
            if auth is None:
                await deny_upgrade(ws, 401, "Sign in to continue")
                return
            session_id, account_id = auth.session.id, auth.account.id
    if not mgr.reserve_slot():  # pre-hello sockets count toward the cap
        await deny_upgrade(ws, 503, "The relay is at its connection limit")
        return

    await ws.accept()
    conn: ControllerConn | None = None
    slot_reserved = True
    try:
        hello = await _receive_frame(ws, svc, wait_seconds=svc.settings.relay_hello_timeout_seconds)
        if (
            hello is None
            or hello.get("type") != "hello"
            or hello.get("component") != "controller"
            or "kid" not in hello
        ):
            await ws.send_text(
                dumps_compact(
                    frames.error_frame("MALFORMED_MESSAGE", "first frame must be a controller hello with kid")
                )
            )
            await ws.close(code=CLOSE_PROTOCOL_ERROR)
            return
        ours = (svc.registry.protocol_version,)
        if not any(protocol_compatible(v, ours) for v in hello["protocol_versions"]) or not protocol_compatible(
            hello["registry_version"], (svc.registry.registry_version,)
        ):
            await ws.send_text(
                dumps_compact(
                    frames.error_frame(
                        "PROTOCOL_INCOMPATIBLE", detail={"peer": hello["protocol_versions"], "supported": list(ours)}
                    )
                )
            )
            await ws.close(code=CLOSE_PROTOCOL_ERROR)
            return
        conn = ControllerConn(ws, session_id, account_id, hello["kid"])
        # Protocol 1.1 frames (input_batch, input_ack, input_session) only flow to and from peers that announced 1.1.
        conn.supports_input = protocol_compatible("1.1", tuple(hello["protocol_versions"]))
        proof = hello.get("proof")
        proof_error: ProtocolError | None = None
        async with svc.db() as db:
            async with db.begin():
                ctrl = await db.scalar(
                    select(Controller).where(
                        Controller.account_id == account_id, Controller.kid == conn.kid, Controller.revoked_at.is_(None)
                    )
                )
                if ctrl is not None and proof is not None:
                    # rules.controller_socket_identity: a kid is public to everyone signed in to the account, so the
                    # socket is bound to the paired controller only once the caller proves it holds the key.
                    record = KeyRecord(
                        controller_id=str(ctrl.id), account_id=str(account_id), jwk=dict(ctrl.public_jwk)
                    )
                    try:
                        verified = verify_hello_proof(
                            proof,
                            lambda kid: record if kid == conn.kid else None,
                            expected_kid=conn.kid,
                            expected_account_id=str(account_id),
                            registry=svc.registry,
                            schemas=svc.schemas,
                        )
                        if not mgr.accept_hello_nonce(verified.nonce, parse_rfc3339(verified.payload["expires_at"])):
                            raise ProtocolError("UNKNOWN_KEY", "hello proof replayed")
                    except ProtocolError as exc:
                        proof_error = exc
                    else:
                        conn.controller_id = ctrl.id
                        ctrl.last_seen_at = utcnow()
        if proof_error is not None:
            log.warning("controller.hello_proof_rejected", code=proof_error.code, kid_prefix=conn.kid[:8])
            await mgr.security_event(
                account_id=account_id,
                kind="controller_hello_proof_rejected",
                severity="warning",
                actor="controller",
                detail={"code": proof_error.code, "kid_prefix": conn.kid[:8]},
            )
            await ws.send_text(dumps_compact(frames.error_frame("UNKNOWN_KEY", "hello proof rejected")))
            await ws.close(code=CLOSE_REVOKED)
            return
        mgr.register_controller(conn)
        mgr.release_slot()  # the registered socket is counted from here on
        slot_reserved = False
        await conn.send(frames.hello_ack(conn.connection_id, controller_id=conn.controller_id))
        await _loop(ws, svc, mgr, limiters, conn)
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001
        log.exception("controller.socket_error")
        if conn is not None:
            await conn.close(CLOSE_PROTOCOL_ERROR)
    finally:
        if slot_reserved:
            mgr.release_slot()
        if conn is not None:
            conn.closed = True
            mgr.unregister_controller(conn)
            limiters.forget_connection(conn)


async def _receive_frame(ws: WebSocket, svc: Services, *, wait_seconds: float | None = None) -> dict[str, Any] | None:
    text = await (asyncio.wait_for(ws.receive_text(), wait_seconds) if wait_seconds is not None else ws.receive_text())
    if len(text.encode("utf-8", "surrogatepass")) > svc.settings.relay_max_frame_bytes:
        await ws.close(code=CLOSE_FRAME_TOO_LARGE)
        raise WebSocketDisconnect(CLOSE_FRAME_TOO_LARGE)
    try:
        frame = loads_strict(text, max_bytes=svc.settings.relay_max_frame_bytes, max_depth=12, require_object=True)
        svc.schemas.validate_frame("controller_to_relay", frame)
    except ProtocolError as exc:
        await ws.send_text(dumps_compact(frames.error_frame(exc.code, exc.message)))
        return None
    assert isinstance(frame, dict)
    return frame


async def _loop(
    ws: WebSocket, svc: Services, mgr: ConnectionManager, limiters: RateLimiters, conn: ControllerConn
) -> None:
    bad_frames = 0
    bucket_key = str(conn.connection_id)
    while not conn.closed:
        frame = await _receive_frame(ws, svc)
        conn.last_seen = utcnow()
        if frame is None:
            bad_frames += 1
            if bad_frames >= 5:
                await conn.close(CLOSE_PROTOCOL_ERROR)
                return
            continue
        kind = frame["type"]
        if kind == "input_batch":
            # The input stream has its own pre-database bucket (rules.input_sessions: 40 batches/s), far above the
            # command bucket. Refusals are RATE_LIMITED errors at most once per second per socket; ordinary
            # overshoot while dragging keeps the session, a sustained flood (refusals beyond 2x the budget for
            # about 5 s) closes the socket like a command flood does.
            if not limiters.input_frames.allow(bucket_key):
                conn.input_refused += 1
                if not limiters.input_refusals.allow(bucket_key):
                    await _close_throttled(mgr, conn, reason="input_flood", refused=conn.input_refused)
                    return
                if conn.input_notice_due(utcnow()):
                    await conn.send(
                        frames.error_frame(
                            "RATE_LIMITED", "Too many input batches; slow down", ref_pc_id=frame["pc_id"]
                        )
                    )
                continue
            await route_input_batch(svc, mgr, limiters, conn, frame)
            continue
        # Per-socket inbound budget, before any database work: a flood of well-formed frames is answered
        # from memory and, after ``burst`` refusals, the socket is closed (4000) with one security event.
        if not limiters.frames.allow(bucket_key):
            conn.throttle_violations += 1
            await reject_throttled(mgr, conn, frame)
            if conn.throttle_violations >= limiters.frames.burst:
                await _close_throttled(mgr, conn, reason="frame_flood", refused=conn.throttle_violations)
                return
            continue
        if kind == "ping":
            await conn.send({"type": "pong", **({"t": frame["t"]} if "t" in frame else {})})
        elif kind == "pong":
            pass
        elif kind == "hello":
            await conn.send(frames.error_frame("MALFORMED_MESSAGE", "hello already received"))
        elif kind == "subscribe":
            await _on_subscribe(svc, mgr, limiters, conn, frame)
        elif kind == "command":
            await route_command(svc, mgr, limiters, conn, frame)
        elif kind == "confirmation":
            await _on_confirmation(svc, mgr, conn, frame)
        elif kind == "cancel":
            await _on_cancel(mgr, conn, frame)


async def _close_throttled(mgr: ConnectionManager, conn: ControllerConn, *, reason: str, refused: int) -> None:
    """Close a flooding socket (4000) with exactly one ``controller_throttled`` security event."""
    log.info("controller.throttled_close", controller_id=str(conn.controller_id), reason=reason)
    await mgr.security_event(
        account_id=conn.account_id,
        kind="controller_throttled",
        severity="warning",
        actor="controller",
        subject_id=conn.controller_id,
        detail={"refused_frames": refused, "reason": reason},
    )
    await conn.close(CLOSE_PROTOCOL_ERROR)


async def _on_subscribe(
    svc: Services, mgr: ConnectionManager, limiters: RateLimiters, conn: ControllerConn, frame: dict[str, Any]
) -> None:
    requested = [uuid.UUID(p) for p in frame["pc_ids"]]
    accepted: set[uuid.UUID] = set()
    if conn.controller_id is None:
        for pc_id in requested:
            await conn.send(
                frames.error_frame("GRANT_MISSING", "This phone is not paired on this account", ref_pc_id=pc_id)
            )
        mgr.set_subscriptions(conn, set())
        return
    async with svc.db() as db:
        for pc_id in requested:
            pc = await db.get(PC, pc_id)
            grant = None
            if pc is not None and pc.deleted_at is None and pc.account_id == conn.account_id:
                grant = await db.scalar(
                    select(Grant.id).where(
                        Grant.controller_id == conn.controller_id,
                        Grant.pc_id == pc_id,
                        Grant.account_id == conn.account_id,
                        Grant.revoked_at.is_(None),
                    )
                )
            if grant is None:
                await conn.send(frames.error_frame("GRANT_MISSING", ref_pc_id=pc_id))
                await audited_event(
                    mgr, limiters, conn, kind="subscribe_refused", severity="warning", detail={"pc_id": str(pc_id)}
                )
            else:
                accepted.add(pc_id)
        mgr.set_subscriptions(conn, accepted)
        for pc_id in accepted:
            status = await mgr.pc_status_frame(pc_id, db)
            if status is not None:
                await conn.send(status)
            agent = mgr.agent_for(pc_id)
            if agent is not None and agent.state_frame is not None:
                cached = agent.state_frame  # cached, original `at`; a 1.0 socket gets it without the 1.1 fields
                if not conn.supports_input:
                    cached = frames.legacy_state_frame(cached, PC_STATE_1_1_FIELDS)
                await conn.send(cached, validate=False)


async def _on_confirmation(svc: Services, mgr: ConnectionManager, conn: ControllerConn, frame: dict[str, Any]) -> None:
    envelope = frame["envelope"]
    pc_id_str: str = frame["pc_id"]
    if envelope.get("kid") != conn.kid:
        await conn.send(
            frames.error_frame("UNKNOWN_KEY", "Envelope kid does not match this connection", ref_pc_id=pc_id_str)
        )
        await conn.close(CLOSE_REVOKED)
        return
    if conn.controller_id is None:
        await conn.send(
            frames.error_frame(
                "GRANT_MISSING", "This connection is not bound to a paired controller", ref_pc_id=pc_id_str
            )
        )
        return
    async with svc.db() as db:
        ctrl, record = await resolve_controller_record(db, conn)
    if ctrl is None or record is None or ctrl.revoked_at is not None:
        await conn.send(frames.error_frame("CONTROLLER_REVOKED", ref_pc_id=pc_id_str))
        await conn.close(CLOSE_REVOKED)
        return
    try:
        conf = verify_and_parse_confirmation(
            envelope, lambda kid: record if kid == conn.kid else None, registry=svc.registry, schemas=svc.schemas
        )
    except ProtocolError as exc:
        await conn.send(frames.error_frame(exc.code, exc.message, ref_pc_id=pc_id_str))
        await mgr.security_event(
            account_id=conn.account_id,
            kind="confirmation_rejected",
            severity="warning",
            actor="controller",
            subject_id=conn.controller_id,
            detail={"reason": exc.code},
        )
        return
    agent = mgr.agent_for(uuid.UUID(pc_id_str))
    inf = agent.inflight.get(uuid.UUID(conf.command_id)) if agent is not None else None
    if (
        agent is None
        or inf is None
        or inf.controller_id != conn.controller_id
        or conf.payload["target_pc_id"] != pc_id_str
    ):
        await conn.send(
            frames.error_frame(
                "CONFIRMATION_INVALID", "No pending confirmation for that command on this PC", ref_pc_id=pc_id_str
            )
        )
        return
    if not conf.approved:
        # the PC owns the challenge; it answers the command with CONFIRMATION_DECLINED
        pass
    await agent.send(
        {
            "type": "confirmation",
            "envelope": envelope,
            "relay": {"received_at": ts_required(utcnow()), "connection_id": str(conn.connection_id)},
        },
        validate=False,
    )


async def _on_cancel(mgr: ConnectionManager, conn: ControllerConn, frame: dict[str, Any]) -> None:
    if conn.controller_id is None:
        await conn.send(frames.error_frame("GRANT_MISSING", ref_pc_id=frame["pc_id"]))
        return
    agent = mgr.agent_for(uuid.UUID(frame["pc_id"]))
    inf = agent.inflight.get(uuid.UUID(frame["command_id"])) if agent is not None else None
    if agent is None or inf is None or inf.controller_id != conn.controller_id:
        log.info("cancel.ignored", controller_id=str(conn.controller_id))
        return
    await agent.send({"type": "cancel", "command_id": frame["command_id"], "controller_id": str(conn.controller_id)})
