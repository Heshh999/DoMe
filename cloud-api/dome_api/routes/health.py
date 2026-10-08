"""Liveness/readiness. Reports the database round-trip and relay occupancy; no account data."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from dome_api import __version__
from dome_api.auth.deps import Svc

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz(svc: Svc) -> Any:
    db_ok = True
    try:
        async with svc.engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001
        db_ok = False
    body = {
        "status": "ok" if db_ok else "degraded",
        "version": __version__,
        "database": "ok" if db_ok else "unavailable",
        "relay": {
            "agents": len(svc.relay.agents),
            "controllers": len(svc.relay.controllers),
            "max_connections": svc.settings.relay_max_connections,
        },
    }
    return JSONResponse(body, status_code=200 if db_ok else 503, headers={"Cache-Control": "no-store"})
