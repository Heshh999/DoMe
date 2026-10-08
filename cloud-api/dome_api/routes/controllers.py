"""Controller installations: inventory, rename, revoke."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Request, Response
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from dome_api.auth.deps import DB, Auth, Svc
from dome_api.db.models import Controller, Grant
from dome_api.errors import ApiError
from dome_api.plans import plan_for
from dome_api.security import events
from dome_api.util import parse_uuid, rest_response, strict_body, ts, ts_required, utcnow

router = APIRouter(tags=["controllers"])


def controller_body(c: Controller, enabled_ids: set[uuid.UUID]) -> dict[str, Any]:
    status = "revoked" if c.revoked_at is not None else ("active" if c.id in enabled_ids else "plan_disabled")
    return {
        "id": str(c.id),
        "kid": c.kid,
        "display_name": c.display_name,
        "status": status,
        "created_at": ts_required(c.created_at),
        "last_seen": ts(c.last_seen_at),
    }


@router.get("/controllers")
async def list_controllers(auth: Auth, db: DB, svc: Svc) -> Any:
    rows = (
        (
            await db.execute(
                select(Controller).where(Controller.account_id == auth.account.id).order_by(Controller.created_at)
            )
        )
        .scalars()
        .all()
    )
    enabled_ids = await svc.relay.plan_enabled_controller_ids(db, auth.account.id, plan_for(auth.account.plan))
    return rest_response(
        svc.settings.validate_rest_responses,
        "controllers_response",
        {"controllers": [controller_body(c, enabled_ids) for c in rows]},
    )


async def owned_controller(db: AsyncSession, account_id: uuid.UUID, controller_id: str) -> Controller:
    cid = parse_uuid(controller_id)
    ctrl = await db.scalar(
        select(Controller).where(Controller.id == cid, Controller.account_id == account_id).with_for_update()
    )
    if ctrl is None:
        raise ApiError(404, "NOT_FOUND")
    return ctrl


@router.patch("/controllers/{controller_id}")
async def rename(controller_id: str, request: Request, auth: Auth, db: DB, svc: Svc) -> Any:
    body = await strict_body(request, "controller_patch_request")
    ctrl = await owned_controller(db, auth.account.id, controller_id)
    ctrl.display_name = body["display_name"]
    pc_ids = list(
        (
            await db.execute(select(Grant.pc_id).where(Grant.controller_id == ctrl.id, Grant.revoked_at.is_(None)))
        ).scalars()
    )
    enabled_ids = await svc.relay.plan_enabled_controller_ids(db, auth.account.id, plan_for(auth.account.plan))
    await db.commit()
    for pc_id in pc_ids:  # the snapshot carries display_name
        await svc.relay.push_grants_snapshot(pc_id)
    return rest_response(svc.settings.validate_rest_responses, "controller", controller_body(ctrl, enabled_ids))


@router.delete("/controllers/{controller_id}", status_code=204)
async def revoke(controller_id: str, auth: Auth, db: DB, svc: Svc) -> Response:
    ctrl = await owned_controller(db, auth.account.id, controller_id)
    now = utcnow()
    pc_ids = list(
        (
            await db.execute(select(Grant.pc_id).where(Grant.controller_id == ctrl.id, Grant.revoked_at.is_(None)))
        ).scalars()
    )
    if ctrl.revoked_at is None:
        ctrl.revoked_at = now
        await db.execute(
            update(Grant).where(Grant.controller_id == ctrl.id, Grant.revoked_at.is_(None)).values(revoked_at=now)
        )
        events.record(
            db,
            account_id=auth.account.id,
            kind="controller_revoked",
            severity="notice",
            actor="account",
            subject_id=ctrl.id,
            detail={"pcs": len(pc_ids)},
        )
    await db.commit()
    await svc.relay.apply_controller_revocation(ctrl.id, reason="controller_revoked", pc_ids=pc_ids)
    return Response(status_code=204, headers={"Cache-Control": "no-store"})
