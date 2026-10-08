"""Command lifecycle rows (never params, results or titles)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from sqlalchemy import select

from dome_api.auth.deps import DB, Auth, Svc
from dome_api.db.models import Command
from dome_api.util import parse_uuid, rest_response, ts, ts_required

router = APIRouter(tags=["commands"])


@router.get("/commands")
async def list_commands(
    auth: Auth, db: DB, svc: Svc, pc_id: str | None = None, limit: int = Query(default=20, ge=1, le=100)
) -> Any:
    stmt = select(Command).where(Command.account_id == auth.account.id)
    if pc_id is not None:
        stmt = stmt.where(Command.pc_id == parse_uuid(pc_id))
    rows = (await db.execute(stmt.order_by(Command.created_at.desc()).limit(limit))).scalars()
    body = {
        "commands": [
            {
                "command_id": str(c.id),
                "pc_id": str(c.pc_id),
                "controller_id": str(c.controller_id),
                "action": c.action,
                "state": c.state,
                "error_code": c.error_code,
                "created_at": ts_required(c.created_at),
                "finished_at": ts(c.finished_at),
                "duration_ms": c.duration_ms,
            }
            for c in rows
        ]
    }
    return rest_response(svc.settings.validate_rest_responses, "commands_response", body)
