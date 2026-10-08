"""PC inventory: list, rename/enable, unlink, per-PC grants."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Request, Response
from sqlalchemy import select, update

from dome_api.auth.deps import DB, Auth, Svc
from dome_api.db.models import PC, Account, Grant, PairingSession, PCAccessToken, PCCredential
from dome_api.errors import ApiError
from dome_api.plans import plan_for
from dome_api.relay.manager import ConnectionManager
from dome_api.routes.agent_link import count_enabled_pcs
from dome_api.security import events
from dome_api.util import parse_uuid, rest_response, strict_body, ts, ts_required, utcnow

router = APIRouter(tags=["pcs"])


def pc_body(pc: PC, relay: ConnectionManager) -> dict[str, Any]:
    conn = relay.agent_for(pc.id)
    out: dict[str, Any] = {
        "id": str(pc.id),
        "name": pc.name,
        "enabled": bool(pc.enabled),
        "connection": "online" if conn is not None else "offline",
        "last_seen": ts(conn.last_seen if conn is not None else pc.last_seen_at),
        "created_at": ts_required(pc.created_at),
        "platform": pc.platform,
        "agent_version": pc.agent_version or "",
        "remote_enabled_reported": bool(pc.remote_enabled_reported),
        "last_power_request": pc.last_power_request,
    }
    return out


async def owned_pc(db: Any, account_id: uuid.UUID, pc_id: str, *, for_update: bool = False) -> PC:
    pid = parse_uuid(pc_id)
    stmt = select(PC).where(PC.id == pid, PC.account_id == account_id, PC.deleted_at.is_(None))
    if for_update:
        stmt = stmt.with_for_update()
    pc = await db.scalar(stmt)
    if pc is None:
        raise ApiError(404, "NOT_FOUND")
    return pc


@router.get("/pcs")
async def list_pcs(auth: Auth, db: DB, svc: Svc) -> Any:
    rows = (
        await db.execute(
            select(PC).where(PC.account_id == auth.account.id, PC.deleted_at.is_(None)).order_by(PC.created_at)
        )
    ).scalars()
    return rest_response(
        svc.settings.validate_rest_responses, "pcs_response", {"pcs": [pc_body(pc, svc.relay) for pc in rows]}
    )


@router.patch("/pcs/{pc_id}")
async def patch_pc(pc_id: str, request: Request, auth: Auth, db: DB, svc: Svc) -> Any:
    body = await strict_body(request, "pc_patch_request")
    account = await db.scalar(select(Account).where(Account.id == auth.account.id).with_for_update())
    assert account is not None
    pc = await owned_pc(db, account.id, pc_id, for_update=True)
    changed_enabled = False
    if "name" in body:
        pc.name = body["name"]
    if "enabled" in body and bool(body["enabled"]) != pc.enabled:
        if body["enabled"]:
            plan = plan_for(account.plan)
            if await count_enabled_pcs(db, account.id, exclude=pc.id) >= plan.max_enabled_pcs:
                raise ApiError(403, "DEVICE_LIMIT_REACHED")
        pc.enabled = bool(body["enabled"])
        changed_enabled = True
        events.record(
            db,
            account_id=account.id,
            kind="pc_enabled" if pc.enabled else "pc_disabled",
            severity="notice",
            actor="account",
            subject_id=pc.id,
        )
    await db.commit()
    if changed_enabled:
        await svc.relay.push_grants_snapshot(pc.id)
        await svc.relay.broadcast_pc_status(pc.id)
    return rest_response(svc.settings.validate_rest_responses, "pc", pc_body(pc, svc.relay))


@router.delete("/pcs/{pc_id}", status_code=204)
async def unlink_pc(pc_id: str, auth: Auth, db: DB, svc: Svc) -> Response:
    pc = await owned_pc(db, auth.account.id, pc_id, for_update=True)
    now = utcnow()
    await db.execute(
        update(PCCredential)
        .where(PCCredential.pc_id == pc.id, PCCredential.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    await db.execute(
        update(PCAccessToken)
        .where(PCAccessToken.pc_id == pc.id, PCAccessToken.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    await db.execute(update(Grant).where(Grant.pc_id == pc.id, Grant.revoked_at.is_(None)).values(revoked_at=now))
    await db.execute(
        update(PairingSession)
        .where(PairingSession.pc_id == pc.id, PairingSession.state.in_(("open", "claimed")))
        .values(state="expired", decided_at=now)
    )
    pc.deleted_at = now
    pc.enabled = False
    events.record(
        db, account_id=auth.account.id, kind="pc_unlinked", severity="notice", actor="account", subject_id=pc.id
    )
    await db.commit()
    await svc.relay.disconnect_agent(pc.id, revoked_reason="pc_unlinked")
    await svc.relay.broadcast_pc_status(pc.id)
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


def grant_body(g: Grant) -> dict[str, Any]:
    return {
        "id": str(g.id),
        "controller_id": str(g.controller_id),
        "pc_id": str(g.pc_id),
        "capabilities": sorted(set(g.capabilities)),
        "created_at": ts_required(g.created_at),
    }


@router.get("/pcs/{pc_id}/grants")
async def pc_grants(pc_id: str, auth: Auth, db: DB, svc: Svc) -> Any:
    pc = await owned_pc(db, auth.account.id, pc_id)
    rows = (
        await db.execute(
            select(Grant)
            .where(Grant.pc_id == pc.id, Grant.account_id == auth.account.id, Grant.revoked_at.is_(None))
            .order_by(Grant.created_at)
        )
    ).scalars()
    return rest_response(
        svc.settings.validate_rest_responses, "grants_response", {"grants": [grant_body(g) for g in rows]}
    )
