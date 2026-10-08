"""Revoking one controller's grant on one PC."""

from __future__ import annotations

from fastapi import APIRouter, Response
from sqlalchemy import select

from dome_api.auth.deps import DB, Auth, Svc
from dome_api.db.models import Grant
from dome_api.errors import ApiError
from dome_api.security import events
from dome_api.util import parse_uuid, utcnow

router = APIRouter(tags=["grants"])


@router.delete("/grants/{grant_id}", status_code=204)
async def revoke_grant(grant_id: str, auth: Auth, db: DB, svc: Svc) -> Response:
    gid = parse_uuid(grant_id)
    grant = await db.scalar(select(Grant).where(Grant.id == gid, Grant.account_id == auth.account.id).with_for_update())
    if grant is None:
        raise ApiError(404, "NOT_FOUND")
    if grant.revoked_at is None:
        grant.revoked_at = utcnow()
        events.record(
            db,
            account_id=auth.account.id,
            kind="grant_revoked",
            severity="notice",
            actor="account",
            subject_id=grant.controller_id,
            detail={"pc_id": str(grant.pc_id), "reason": "account"},
        )
    await db.commit()
    await svc.relay.apply_controller_revocation(
        grant.controller_id, reason="grant_revoked", pc_ids=[grant.pc_id], revoked_pc_id=grant.pc_id
    )
    return Response(status_code=204, headers={"Cache-Control": "no-store"})
