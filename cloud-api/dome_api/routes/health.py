"""Liveness/readiness. Reports the database round-trip, relay occupancy, the build commit, whether the client address
was resolved through the hosting proxy, and the Screen view switch and TURN figures (docs/design/screen-view.md §12:
``turn``, ``turn_month_gb`` reconciled and provisional, ``turn_reconciled_at``); no account data, no secret."""

from __future__ import annotations

import ipaddress
from typing import Any, Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from dome_api import __version__
from dome_api.auth.deps import Svc
from dome_api.security.proxy import single_header_ip

router = APIRouter(tags=["health"])

# The header Fly.io's proxy sets to the address it accepted the connection from (deploy/fly.toml).
_FLY_CLIENT_IP = b"fly-client-ip"

ForwardedClient = Literal["ok", "not_checked", "unresolved_private", "unresolved_other"]


def forwarded_client_check(request: Request) -> ForwardedClient:
    """Compare the client address every per-IP limit sees with the one the hosting proxy reports.

    ``ok``: they match. ``not_checked``: no single, valid ``Fly-Client-IP`` header (local runs, Fly's own
    health checks). ``unresolved_private``: the address the app resolved is not a public one (the proxy's
    own address): DOME_TRUSTED_PROXIES does not cover the proxy, so every client would share one address.
    ``unresolved_other``: a different public address. The header is used only for this self-check; a
    client that forges it changes nothing but its own answer here.
    """
    reported = single_header_ip(request.scope, _FLY_CLIENT_IP)
    if reported is None:
        return "not_checked"
    host = request.client.host if request.client else ""
    try:
        resolved = ipaddress.ip_address(host)
    except ValueError:
        return "unresolved_other"
    if isinstance(resolved, ipaddress.IPv6Address) and resolved.ipv4_mapped is not None:
        resolved = resolved.ipv4_mapped
    if resolved == reported:
        return "ok"
    return "unresolved_private" if not resolved.is_global else "unresolved_other"


@router.get("/healthz")
async def healthz(request: Request, svc: Svc) -> Any:
    db_ok = True
    try:
        async with svc.engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001
        db_ok = False
    body = {
        "status": "ok" if db_ok else "degraded",
        "version": __version__,
        # The git commit the image was built from (DOME_BUILD_COMMIT), so a deploy can prove what runs.
        "commit": svc.settings.build_commit or None,
        "database": "ok" if db_ok else "unavailable",
        "forwarded_client": forwarded_client_check(request),
        "relay": {
            "agents": len(svc.relay.agents),
            "controllers": len(svc.relay.controllers),
            "max_connections": svc.settings.relay_max_connections,
        },
    }
    if db_ok:
        try:
            body.update(await svc.screen.health())
        except Exception:  # noqa: BLE001 - the TURN figures never take the health check down
            body.update({"screen_view": "on" if svc.screen.config.enabled else "off", "turn": "error"})
    return JSONResponse(body, status_code=200 if db_ok else 503, headers={"Cache-Control": "no-store"})
