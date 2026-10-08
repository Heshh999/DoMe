"""OIDC login/callback, logout and the session document (ADR-0001 D3)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from dome_api.auth.deps import DB, Auth, Svc
from dome_api.auth.sessions import clear_session_cookie, create_session, set_session_cookie
from dome_api.db.models import Account
from dome_api.errors import ApiError
from dome_api.logging import get_logger
from dome_api.plans import catalog, plan_for
from dome_api.security import events
from dome_api.security.tokens import ip_hash
from dome_api.util import client_ip, rest_response, ts_required, utcnow

router = APIRouter(tags=["auth"])
log = get_logger("dome_api.routes.auth")
MAX_RETURN_TO = 1024


def safe_return_to(value: str | None) -> str:
    """Only a relative path on our own origin is accepted; anything else becomes ``/``."""
    if not value or len(value) > MAX_RETURN_TO:
        return "/"
    if not value.startswith("/") or value.startswith("//") or "\\" in value or any(ord(c) < 32 for c in value):
        raise ApiError(400, "MALFORMED_MESSAGE", "return_to must be a relative path")
    return value


@router.get("/auth/login")
async def login(request: Request, db: DB, svc: Svc, return_to: str | None = None) -> Response:
    if not svc.login_limiter.allow(client_ip(request) or "?"):
        raise ApiError(429, "RATE_LIMITED")
    target = safe_return_to(return_to)
    url = await svc.oidc.begin(db, target)
    return RedirectResponse(url, status_code=303, headers={"Cache-Control": "no-store"})


@router.get("/auth/callback")
async def callback(
    request: Request, db: DB, svc: Svc, code: str | None = None, state: str | None = None, error: str | None = None
) -> Response:
    if error is not None or code is None or state is None:
        log.info("oidc.callback_error", provider_error=bool(error))
        raise ApiError(400, "MALFORMED_MESSAGE", "Sign-in was cancelled or failed. Try again.")
    identity, return_to = await svc.oidc.complete(db, code, state)
    now = utcnow()
    account = await db.scalar(
        select(Account).where(Account.issuer == identity.issuer, Account.subject == identity.subject)
    )
    created = False
    if account is None:
        account = Account(
            issuer=identity.issuer,
            subject=identity.subject,
            email=identity.email,
            display_name=identity.display_name,
            created_at=now,
        )
        db.add(account)
        await db.flush()
        created = True
    elif account.deleted_at is not None:
        raise ApiError(403, "FORBIDDEN", "This account was deleted.")
    else:
        account.email = identity.email or account.email
        account.display_name = identity.display_name or account.display_name
    session, token = await create_session(db, svc.settings, account.id, request.headers.get("user-agent"))
    iph = ip_hash(svc.settings.session_secret.get_secret_value(), client_ip(request))
    events.record(
        db,
        account_id=account.id,
        kind="account_created" if created else "login",
        severity="info",
        actor="account",
        subject_id=session.id,
        ip_hash=iph,
    )
    response = RedirectResponse(return_to, status_code=303, headers={"Cache-Control": "no-store"})
    set_session_cookie(response, svc.settings, token)
    return response


@router.get("/session")
async def session_document(auth: Auth, svc: Svc) -> Any:
    plan = plan_for(auth.account.plan)
    body = {
        "account": {
            "id": str(auth.account.id),
            "email": auth.account.email,
            "display_name": auth.account.display_name,
            "created_at": ts_required(auth.account.created_at),
        },
        "csrf_token": auth.session.csrf_token,
        "plan": plan.id,
        "entitlement_state": catalog().entitlement_state_for(plan),
        "limits": plan.session_limits(),
        "protocol_version": svc.registry.protocol_version,
    }
    return rest_response(svc.settings.validate_rest_responses, "session_response", body)


@router.post("/auth/logout", status_code=204)
async def logout(auth: Auth, db: DB, svc: Svc) -> Response:
    auth.session.revoked_at = utcnow()
    events.record(
        db, account_id=auth.account.id, kind="logout", severity="info", actor="account", subject_id=auth.session.id
    )
    await db.commit()
    await svc.relay.close_session_sockets(auth.session.id)
    response = Response(status_code=204, headers={"Cache-Control": "no-store"})
    clear_session_cookie(response, svc.settings)
    return response
