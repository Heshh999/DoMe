"""Account self-service: security activity, web-session inventory/revocation, the Billing page's status and the
downgrade device selection (docs/BILLING.md §8)."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Query, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dome_api.auth.deps import DB, Auth, Svc
from dome_api.db.models import PC, Account, Controller, SecurityEvent, Session
from dome_api.errors import ApiError
from dome_api.plans import Plan, catalog, plan_for
from dome_api.security import events
from dome_api.util import parse_uuid, rest_response, strict_body, ts, ts_required, utcnow

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


# ----- billing status and the downgrade selection (docs/BILLING.md §8) ------------------------------------------------


@router.get("/account/billing")
async def billing_status(auth: Auth, db: DB, svc: Svc) -> Any:
    body = await svc.billing.status_body(db, auth.account)
    return rest_response(svc.settings.validate_rest_responses, "billing_status_response", body)


async def selection_body(
    db: AsyncSession, account: Account, plan: Plan, enabled_ids: set[uuid.UUID], now: datetime
) -> dict[str, Any]:
    """rest.schema.json#/$defs/plan_selection_response"""
    deadline = (
        account.plan_changed_at + timedelta(days=catalog().selection_window_days) if account.plan_changed_at else None
    )
    pcs = (
        await db.execute(
            select(PC).where(PC.account_id == account.id, PC.deleted_at.is_(None)).order_by(PC.created_at).limit(64)
        )
    ).scalars()
    controllers = (
        await db.execute(
            select(Controller)
            .where(Controller.account_id == account.id, Controller.revoked_at.is_(None))
            .order_by(Controller.created_at)
            .limit(64)
        )
    ).scalars()
    return {
        "open": deadline is not None and now < deadline,
        "deadline": ts(deadline),
        "plan": plan.id,
        "limits": {"max_enabled_pcs": plan.max_enabled_pcs, "max_controllers": plan.max_controllers},
        "pcs": [
            {"id": str(pc.id), "name": pc.name, "selected": bool(pc.enabled), "last_seen": ts(pc.last_seen_at)}
            for pc in pcs
        ],
        "controllers": [
            {
                "id": str(c.id),
                "display_name": c.display_name,
                "selected": c.id in enabled_ids,
                "last_seen": ts(c.last_seen_at),
            }
            for c in controllers
        ],
    }


@router.get("/account/plan-selection")
async def get_plan_selection(auth: Auth, db: DB, svc: Svc) -> Any:
    plan = plan_for(auth.account.plan)
    enabled_ids = await svc.relay.plan_enabled_controller_ids(db, auth.account.id, plan)
    body = await selection_body(db, auth.account, plan, enabled_ids, svc.billing.config.clock())
    return rest_response(svc.settings.validate_rest_responses, "plan_selection_response", body)


@router.put("/account/plan-selection")
async def put_plan_selection(request: Request, auth: Auth, db: DB, svc: Svc) -> Any:
    body = await strict_body(request, "plan_selection_request")
    account = await db.scalar(select(Account).where(Account.id == auth.account.id).with_for_update())
    assert account is not None
    now = svc.billing.config.clock()  # the billing clock, so tests can move past the window
    window = timedelta(days=catalog().selection_window_days)
    if account.plan_changed_at is None or now >= account.plan_changed_at + window:
        raise ApiError(409, "PLAN_SELECTION_CLOSED")
    plan = plan_for(account.plan)
    pc_ids = {uuid.UUID(str(v)) for v in body["pc_ids"]}
    controller_ids = {uuid.UUID(str(v)) for v in body["controller_ids"]}
    if len(pc_ids) > plan.max_enabled_pcs or len(controller_ids) > plan.max_controllers:
        raise ApiError(400, "PLAN_SELECTION_INVALID")
    pcs = list(
        (
            await db.execute(select(PC).where(PC.account_id == account.id, PC.deleted_at.is_(None)).with_for_update())
        ).scalars()
    )
    controllers = list(
        (
            await db.execute(
                select(Controller)
                .where(Controller.account_id == account.id, Controller.revoked_at.is_(None))
                .with_for_update()
            )
        ).scalars()
    )
    if not pc_ids <= {pc.id for pc in pcs} or not controller_ids <= {c.id for c in controllers}:
        raise ApiError(400, "PLAN_SELECTION_INVALID")
    for pc in pcs:
        selected = pc.id in pc_ids
        if selected and not pc.enabled:
            pc.enabled, pc.plan_disabled_at = True, None
        elif not selected and pc.enabled:
            pc.enabled, pc.plan_disabled_at = False, now
    for ctrl in controllers:
        ctrl.plan_selected_at = now if ctrl.id in controller_ids else None
    events.record(
        db,
        account_id=account.id,
        kind="plan_selection_applied",
        severity="notice",
        actor="account",
        detail={"pcs": len(pc_ids), "controllers": len(controller_ids)},
    )
    await db.commit()
    await svc.relay.push_account_snapshots(account.id)
    for pc in pcs:
        await svc.relay.broadcast_pc_status(pc.id)
    enabled_ids = await svc.relay.plan_enabled_controller_ids(db, account.id, plan)
    body_out = await selection_body(db, account, plan, enabled_ids, now)
    return rest_response(svc.settings.validate_rest_responses, "plan_selection_response", body_out)


__all__ = ["router", "ts"]
