"""Pairing (ADR-0001 D5, version.json rules ``pairing_secret`` / ``pairing_offline``).

The PC generated the code and shows it; the backend only ever stores ``SHA-256`` handles. A claim is
accepted only for an open session of the *same account*; everything else is ``PAIRING_CODE_INVALID``
so a wrong code, another account's code and an expired code are indistinguishable to the caller.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from dome_protocol import ProtocolError, kid_from_jwk
from fastapi import APIRouter, Request
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from dome_api.auth.deps import DB, Agent, Auth, Svc
from dome_api.db.models import PC, Account, Controller, PairingSession
from dome_api.errors import ApiError
from dome_api.plans import plan_for
from dome_api.security import events
from dome_api.security.tokens import ip_hash
from dome_api.util import b64url_to_sha256, client_ip, parse_uuid, rest_response, strict_body, ts_required, utcnow

router = APIRouter(tags=["pairing"])


@router.post("/pairing/start")
async def start(request: Request, agent: Agent, db: DB, svc: Svc) -> Any:
    body = await strict_body(request, "pairing_start_request")
    code_hash = b64url_to_sha256(body["code_hash"])
    now = utcnow()
    # One open session per PC: a new start expires the previous one (and any claim waiting on it).
    for old in (
        await db.execute(
            select(PairingSession).where(
                PairingSession.pc_id == agent.pc.id, PairingSession.state.in_(("open", "claimed"))
            )
        )
    ).scalars():
        old.state = "expired"
        old.decided_at = now
    ps = PairingSession(
        account_id=agent.account.id,
        pc_id=agent.pc.id,
        code_hash=code_hash,
        state="open",
        attempts=0,
        created_at=now,
        expires_at=now + timedelta(seconds=int(svc.registry.limits["pairing_code_lifetime_seconds"])),
    )
    db.add(ps)
    try:
        await db.flush()
    except IntegrityError:
        # the same handle is open for another PC — practically impossible with 100-bit codes
        raise ApiError(409, "PAIRING_CODE_INVALID", "Generate a new pairing code on the PC") from None
    events.record(
        db, account_id=agent.account.id, kind="pairing_started", severity="info", actor="pc", subject_id=agent.pc.id
    )
    out = {"pairing_id": str(ps.id), "expires_at": ts_required(ps.expires_at)}
    return rest_response(svc.settings.validate_rest_responses, "pairing_start_response", out)


async def _claim_failed(svc: Svc, db: DB, account_id: Any, ip: str | None, reason: str) -> ApiError:
    # The request transaction may hold the account row FOR UPDATE; the event row's FK check would wait on it.
    await db.rollback()
    await events.record_now(
        svc.db,
        account_id=account_id,
        kind="pairing_failed",
        severity="warning",
        actor="account",
        detail={"reason": reason},
        ip_hash=ip_hash(svc.settings.session_secret.get_secret_value(), ip),
    )
    return ApiError(400, "PAIRING_CODE_INVALID")


@router.post("/pairing/claim", status_code=202)
async def claim(request: Request, auth: Auth, db: DB, svc: Svc) -> Any:
    ip = client_ip(request)
    if not svc.pairing_claim_account_limiter.allow(str(auth.account.id)) or not svc.pairing_claim_ip_limiter.allow(
        ip or "?"
    ):
        await events.record_now(
            svc.db, account_id=auth.account.id, kind="pairing_rate_limited", severity="warning", actor="account"
        )
        raise ApiError(429, "RATE_LIMITED")
    body = await strict_body(request, "pairing_claim_request")
    try:
        kid = kid_from_jwk(body["public_jwk"])
    except ProtocolError:
        raise await _claim_failed(svc, db, auth.account.id, ip, "bad_jwk") from None
    now = utcnow()
    # Lock the account row: controller limit enforcement must be transactional.
    account = await db.scalar(select(Account).where(Account.id == auth.account.id).with_for_update())
    assert account is not None
    ps = await db.scalar(
        select(PairingSession)
        .join(PC, PC.id == PairingSession.pc_id)
        .where(
            PairingSession.code_hash == b64url_to_sha256(body["code_hash"]),
            PairingSession.account_id == account.id,
            PairingSession.state == "open",
            PairingSession.expires_at > now,
            PC.deleted_at.is_(None),
        )
        .with_for_update(of=PairingSession)
    )
    if ps is None:
        raise await _claim_failed(svc, db, auth.account.id, ip, "no_open_session")
    plan = plan_for(account.plan)
    known = await db.scalar(
        select(Controller).where(
            Controller.account_id == account.id, Controller.kid == kid, Controller.revoked_at.is_(None)
        )
    )
    if known is None:
        live = int(
            await db.scalar(
                select(func.count())
                .select_from(Controller)
                .where(Controller.account_id == account.id, Controller.revoked_at.is_(None))
            )
            or 0
        )
        if live >= plan.max_controllers:
            events.record(
                db,
                account_id=account.id,
                kind="controller_limit_reached",
                severity="notice",
                actor="account",
                detail={"limit": plan.max_controllers},
            )
            await db.commit()
            raise ApiError(403, "DEVICE_LIMIT_REACHED")
    ps.state = "claimed"
    ps.controller_kid = kid
    ps.controller_public_jwk = body["public_jwk"]
    ps.controller_display_name = str(body["display_name"])[:64]
    ps.requested_capabilities = sorted(set(body["requested_capabilities"]))
    ps.attempts = ps.attempts + 1
    events.record(
        db, account_id=account.id, kind="pairing_claimed", severity="info", actor="account", subject_id=ps.pc_id
    )
    pc = await db.get(PC, ps.pc_id)
    assert pc is not None
    await db.commit()  # durable before the PC can answer with pairing_decision
    online = await svc.relay.deliver_pairing_request(ps)
    return rest_response(
        svc.settings.validate_rest_responses, "pairing_status_response", _status_body(ps, pc, online), status=202
    )


def _status_body(ps: PairingSession, pc: PC, online: bool) -> dict[str, Any]:
    out: dict[str, Any] = {
        "pairing_id": str(ps.id),
        "state": ps.state,
        "pc_id": str(ps.pc_id),
        "pc_name": pc.name,
        "pc_online": online,
        "expires_at": ts_required(ps.expires_at),
    }
    if ps.state == "approved":
        if ps.controller_id is not None:
            out["controller_id"] = str(ps.controller_id)
        if ps.grant_id is not None:
            out["grant_id"] = str(ps.grant_id)
    return out


@router.get("/pairing/{pairing_id}")
async def status(pairing_id: str, auth: Auth, db: DB, svc: Svc) -> Any:
    pid = parse_uuid(pairing_id)
    ps = await db.scalar(
        select(PairingSession).where(PairingSession.id == pid, PairingSession.account_id == auth.account.id)
    )
    if ps is None or ps.state == "open":
        raise ApiError(404, "NOT_FOUND")
    if ps.state == "claimed" and ps.expires_at <= utcnow():
        ps.state = "expired"
        ps.decided_at = utcnow()
    pc = await db.get(PC, ps.pc_id)
    if pc is None:
        raise ApiError(404, "NOT_FOUND")
    body = _status_body(ps, pc, svc.relay.is_online(pc.id))
    if ps.state == "approved" and ps.grant_id is not None:
        from dome_api.db.models import Grant

        grant = await db.get(Grant, ps.grant_id)
        if grant is not None:
            body["granted_capabilities"] = sorted(set(grant.capabilities))
    return rest_response(svc.settings.validate_rest_responses, "pairing_status_response", body)
