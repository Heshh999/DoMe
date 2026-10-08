"""Account self-service: security activity and web-session inventory/revocation."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Query, Response
from sqlalchemy import select

from dome_api.auth.deps import DB, Auth, Svc
from dome_api.db.models import SecurityEvent, Session
from dome_api.errors import ApiError
from dome_api.security import events
from dome_api.util import parse_uuid, rest_response, ts, ts_required, utcnow

router = APIRouter(tags=["account"])


@router.get("/account/security-events")
async def security_events(auth: Auth, db: DB, svc: Svc, limit: int = Query(default=50, ge=1, le=200)) -> Any:
    rows = (
        await db.execute(
            select(SecurityEvent)
            .where(SecurityEvent.account_id == auth.account.id)
            .order_by(SecurityEvent.id.desc())
            .limit(limit)
        )
    ).scalars()
    body = {
        "events": [
            {
                "id": int(e.id),
                "kind": e.kind,
                "severity": e.severity,
                "actor": e.actor,
                "subject_id": str(e.subject_id) if e.subject_id else None,
                "detail": e.detail or {},
                "created_at": ts_required(e.created_at),
            }
            for e in rows
        ]
    }
    return rest_response(svc.settings.validate_rest_responses, "security_events_response", body)


@router.get("/account/sessions")
async def list_sessions(auth: Auth, db: DB, svc: Svc) -> Any:
    now = utcnow()
    rows = (
        await db.execute(
            select(Session)
            .where(Session.account_id == auth.account.id, Session.revoked_at.is_(None), Session.expires_at > now)
            .order_by(Session.last_seen_at.desc())
        )
    ).scalars()
    # No rest.schema.json definition exists for this body yet (see CONTRACT_ISSUES.md); shape proposed there.
    body = {
        "sessions": [
            {
                "id": str(s.id),
                "created_at": ts_required(s.created_at),
                "last_seen_at": ts_required(s.last_seen_at),
                "expires_at": ts_required(s.expires_at),
                "current": s.id == auth.session.id,
            }
            for s in rows
        ]
    }
    return rest_response(False, "sessions_response", body)


@router.delete("/account/sessions/{session_id}", status_code=204)
async def revoke_session(session_id: str, auth: Auth, db: DB, svc: Svc) -> Response:
    sid: uuid.UUID = parse_uuid(session_id)
    row = await db.scalar(select(Session).where(Session.id == sid, Session.account_id == auth.account.id))
    if row is None:
        raise ApiError(404, "NOT_FOUND")
    if row.revoked_at is None:
        row.revoked_at = utcnow()
        events.record(
            db,
            account_id=auth.account.id,
            kind="session_revoked",
            severity="notice",
            actor="account",
            subject_id=row.id,
            detail={"current": row.id == auth.session.id},
        )
    await db.commit()
    await svc.relay.close_session_sockets(row.id)
    response = Response(status_code=204, headers={"Cache-Control": "no-store"})
    if row.id == auth.session.id:
        from dome_api.auth.sessions import clear_session_cookie

        clear_session_cookie(response, svc.settings)
    return response


__all__ = ["router", "ts"]
