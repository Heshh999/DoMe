"""PC ↔ account linking, device-authorization shaped (ADR-0001 D4, design "PC linking")."""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from dome_protocol import ProtocolError, kid_from_jwk
from fastapi import APIRouter, Request, Response
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from dome_api.auth.deps import DB, AgentPublic, Auth, Svc
from dome_api.db.models import PC, Account, DeviceLinkCode, PCCredential
from dome_api.errors import ApiError
from dome_api.plans import plan_for
from dome_api.security import events
from dome_api.security.tokens import ip_hash, new_token, new_user_code, normalize_user_code, sha256
from dome_api.util import client_ip, rest_response, strict_body, ts_required, utcnow

router = APIRouter(tags=["agent-link"])
POLL_INTERVAL_SECONDS = 5


async def _unique_user_code(db: Any) -> str:
    for _ in range(8):
        code = new_user_code()
        if await db.scalar(select(DeviceLinkCode.id).where(DeviceLinkCode.user_code == code)) is None:
            return code
    raise ApiError(503, "SERVICE_UNAVAILABLE")


@router.post("/agent-link/start")
async def start(request: Request, _: AgentPublic, db: DB, svc: Svc) -> Any:
    ip = client_ip(request)
    if not svc.link_start_limiter.allow(ip or "?"):
        raise ApiError(429, "RATE_LIMITED")
    body = await strict_body(request, "agent_link_start_request")
    try:
        kid = kid_from_jwk(body["pc_public_jwk"])
    except ProtocolError as exc:
        raise ApiError(400, "MALFORMED_MESSAGE", exc.message) from None
    now = utcnow()
    lifetime = int(svc.registry.limits["device_link_code_lifetime_seconds"])
    device_code = new_token()
    row = DeviceLinkCode(
        device_code_hash=sha256(device_code),
        user_code=await _unique_user_code(db),
        pc_public_jwk=body["pc_public_jwk"],
        kid=kid,
        agent_version=str(body["agent_version"])[:64],
        platform=body["platform"],
        pc_name_hint=(str(body["pc_name_hint"])[:64] if body.get("pc_name_hint") else None),
        state="pending",
        created_at=now,
        expires_at=now + timedelta(seconds=lifetime),
        requester_ip_hash=ip_hash(svc.settings.session_secret.get_secret_value(), ip),
    )
    db.add(row)
    await db.flush()
    out = {
        "device_code": device_code,
        "user_code": row.user_code,
        "verification_uri_complete": f"{svc.settings.public_origin}/link?user_code={row.user_code}",
        "expires_in": lifetime,
        "interval": POLL_INTERVAL_SECONDS,
    }
    return rest_response(svc.settings.validate_rest_responses, "agent_link_start_response", out)


async def _pending_code(db: Any, user_code: str, *, for_update: bool = False) -> DeviceLinkCode:
    normalized = normalize_user_code(user_code)
    if normalized is None:
        raise ApiError(404, "NOT_FOUND")
    stmt = select(DeviceLinkCode).where(DeviceLinkCode.user_code == normalized)
    if for_update:
        stmt = stmt.with_for_update()
    row = await db.scalar(stmt)
    if row is None:
        raise ApiError(404, "NOT_FOUND")
    if row.state != "pending":
        raise ApiError(410, "LINK_EXPIRED")
    if row.expires_at <= utcnow():
        row.state = "expired"
        raise ApiError(410, "LINK_EXPIRED")
    return row


@router.get("/agent-link/{user_code}")
async def preview(user_code: str, auth: Auth, db: DB, svc: Svc) -> Any:
    row = await _pending_code(db, user_code)
    out: dict[str, Any] = {
        "user_code": row.user_code,
        "agent_version": row.agent_version,
        "platform": row.platform,
        "kid": row.kid,
        "expires_at": ts_required(row.expires_at),
    }
    if row.pc_name_hint:
        out["pc_name_hint"] = row.pc_name_hint
    return rest_response(svc.settings.validate_rest_responses, "agent_link_preview_response", out)


async def count_enabled_pcs(db: Any, account_id: uuid.UUID, *, exclude: uuid.UUID | None = None) -> int:
    stmt = (
        select(func.count())
        .select_from(PC)
        .where(PC.account_id == account_id, PC.enabled.is_(True), PC.deleted_at.is_(None))
    )
    if exclude is not None:
        stmt = stmt.where(PC.id != exclude)
    return int(await db.scalar(stmt) or 0)


@router.post("/agent-link/{user_code}/approve")
async def approve(user_code: str, request: Request, auth: Auth, db: DB, svc: Svc) -> Any:
    body = await strict_body(request, "agent_link_approve_request")
    now = utcnow()
    # ONE transaction: lock the account row so concurrent approvals cannot exceed max_enabled_pcs.
    account = await db.scalar(select(Account).where(Account.id == auth.account.id).with_for_update())
    assert account is not None
    row = await _pending_code(db, user_code, for_update=True)
    plan = plan_for(account.plan)
    existing = await db.scalar(select(PC).where(PC.kid == row.kid).with_for_update())
    if existing is not None and existing.account_id != account.id:
        raise ApiError(409, "FORBIDDEN", "This PC key is already linked to a different account. Unlink it there first.")
    enabled_count = await count_enabled_pcs(db, account.id, exclude=existing.id if existing else None)
    enabled = enabled_count < plan.max_enabled_pcs
    if existing is None:
        pc = PC(
            account_id=account.id,
            name=body["pc_name"],
            public_jwk=row.pc_public_jwk,
            kid=row.kid,
            enabled=enabled,
            platform=row.platform,
            agent_version=row.agent_version,
            created_at=now,
        )
        db.add(pc)
        await db.flush()
    else:
        # Re-link of a known PC key (reinstall / credential lost): reuse the row, rotate credentials.
        pc = existing
        pc.name = body["pc_name"]
        pc.deleted_at = None
        pc.enabled = enabled
        pc.platform = row.platform
        pc.agent_version = row.agent_version
        for cred in (
            await db.execute(select(PCCredential).where(PCCredential.pc_id == pc.id, PCCredential.revoked_at.is_(None)))
        ).scalars():
            cred.revoked_at = now
    row.state = "approved"
    row.account_id = account.id
    row.pc_id = pc.id
    row.pc_name = pc.name
    events.record(
        db,
        account_id=account.id,
        kind="pc_linked",
        severity="notice",
        actor="account",
        subject_id=pc.id,
        detail={"enabled": enabled, "platform": pc.platform, "relink": existing is not None},
    )
    out: dict[str, Any] = {"pc_id": str(pc.id), "enabled": enabled}
    if not enabled:
        out["reason"] = "DEVICE_LIMIT_REACHED"
    await db.commit()
    if existing is not None:
        await svc.relay.disconnect_agent(pc.id, revoked_reason="credential_rotated")
    return rest_response(svc.settings.validate_rest_responses, "agent_link_approve_response", out)


@router.post("/agent-link/{user_code}/deny", status_code=204)
async def deny(user_code: str, auth: Auth, db: DB, svc: Svc) -> Response:
    row = await _pending_code(db, user_code, for_update=True)
    row.state = "denied"
    row.account_id = auth.account.id
    events.record(
        db, account_id=auth.account.id, kind="pc_link_denied", severity="notice", actor="account", subject_id=row.id
    )
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


@router.post("/agent-link/poll")
async def poll(request: Request, _: AgentPublic, db: DB, svc: Svc) -> Any:
    body = await strict_body(request, "agent_link_poll_request")
    now = utcnow()
    row = await db.scalar(
        select(DeviceLinkCode).where(DeviceLinkCode.device_code_hash == sha256(body["device_code"])).with_for_update()
    )
    if row is None or row.state in ("consumed", "expired"):
        raise ApiError(410, "LINK_EXPIRED")
    if row.expires_at <= now and row.state == "pending":
        row.state = "expired"
        raise ApiError(410, "LINK_EXPIRED")
    if row.state == "denied":
        raise ApiError(410, "LINK_DENIED")
    if row.state == "pending":
        status = "authorization_pending"
        if row.last_polled_at is not None and (now - row.last_polled_at).total_seconds() < POLL_INTERVAL_SECONDS - 1:
            status = "slow_down"
        row.last_polled_at = now
        return rest_response(
            svc.settings.validate_rest_responses, "agent_link_poll_pending", {"status": status}, status=428
        )
    # approved → hand out the PC credential exactly once
    assert row.state == "approved" and row.pc_id is not None and row.account_id is not None
    pc = await db.get(PC, row.pc_id)
    if pc is None or pc.deleted_at is not None:
        row.state = "expired"
        raise ApiError(410, "LINK_EXPIRED")
    credential = new_token()
    db.add(PCCredential(pc_id=pc.id, credential_hash=sha256(credential), created_at=now))
    row.state = "consumed"
    row.consumed_at = now
    try:
        await db.flush()
    except IntegrityError:
        raise ApiError(503, "SERVICE_UNAVAILABLE") from None
    events.record(
        db, account_id=pc.account_id, kind="pc_credential_issued", severity="info", actor="pc", subject_id=pc.id
    )
    out = {
        "pc_id": str(pc.id),
        "account_id": str(pc.account_id),
        "pc_credential": credential,
        "relay_url": svc.settings.effective_relay_url,
        "api_url": svc.settings.effective_api_url,
        "pc_name": pc.name,
        "enabled": bool(pc.enabled),
    }
    return rest_response(svc.settings.validate_rest_responses, "agent_link_poll_response", out)
