"""OIDC login/callback, logout and the session document (ADR-0001 D3)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from dome_api.auth.deps import DB, Auth, Svc
from dome_api.auth.oidc import EmailNotVerifiedError
from dome_api.auth.sessions import clear_session_cookie, create_session, set_session_cookie
from dome_api.db.models import Account
from dome_api.errors import ApiError
from dome_api.logging import get_logger
from dome_api.plans import plan_for
from dome_api.security import events
from dome_api.security.tokens import ip_hash
from dome_api.settings import Settings
from dome_api.util import client_ip, rest_response, ts_required, utcnow

router = APIRouter(tags=["auth"])
log = get_logger("dome_api.routes.auth")
MAX_RETURN_TO = 1024
# Where a sign-in refused for an unconfirmed email lands: the public home page, which shows "Confirm your email
# first" for this reason (mobile-app/src/lib/signInProblem.ts). Never /app: the app shell sends a signed-out
# browser straight back to the provider, which remembers the person and would answer unverified again, in a loop.
EMAIL_UNVERIFIED_REDIRECT = "/?sign_in=email_unverified"
# The re-prompt marker (DECISIONS #119). After a refusal the provider still remembers the person (Auth0 keeps its own
# sign-in session), so the next sign-in would be answered "unverified" again at once, without the provider's login
# page, where "Forgot password?" and switching account are. The refusal sets this cookie; every GET /v1/auth/login
# that carries it asks the provider for that page (prompt=login). Login only reads it. The callback alone ends it,
# by the outcome and not by the tap: a sign-in that succeeds clears it, a refusal sets it again, and it runs out a
# day after the last refusal. Clearing it at login would use it up on a try that never comes back (the person leaves
# the provider's page to look for the email), and the try after that would land on the notice again. Its value is
# only ever "1" and any other value is ignored. It grants nothing and decides nothing else: not the callback's
# outcome, not sessions, accounts or the return_to rules.
REPROMPT_COOKIE = "dome_reprompt"
REPROMPT_VALUE = "1"
# Sent only to the auth routes: this router is mounted under /v1 (main.py), so /v1/auth/login, /v1/auth/callback and
# /v1/auth/logout (which ignores it).
REPROMPT_COOKIE_PATH = "/v1/auth"
# One day. The provider remembers the person for days, so a marker that ran out early would send someone who comes
# back later that day ("I'll look for the email tonight") straight to the notice once more before the login page
# shows. A marker that outlives its use costs one password entry at the provider (signing in clears it), nothing else.
REPROMPT_MAX_AGE = 24 * 60 * 60


def safe_return_to(value: str | None) -> str:
    """Only a relative path on our own origin is accepted; anything else becomes ``/``."""
    if not value or len(value) > MAX_RETURN_TO:
        return "/"
    if not value.startswith("/") or value.startswith("//") or "\\" in value or any(ord(c) < 32 for c in value):
        raise ApiError(400, "MALFORMED_MESSAGE", "return_to must be a relative path")
    return value


def reprompt_marked(request: Request) -> bool:
    """The browser carries the marker: exactly ``"1"``, nothing else counts."""
    return request.cookies.get(REPROMPT_COOKIE) == REPROMPT_VALUE


def set_reprompt_cookie(response: Response, settings: Settings) -> None:
    """HttpOnly, SameSite=Lax, and Secure by the session cookie's own rule (``set_session_cookie``)."""
    response.set_cookie(
        REPROMPT_COOKIE,
        REPROMPT_VALUE,
        max_age=REPROMPT_MAX_AGE,
        path=REPROMPT_COOKIE_PATH,
        secure=settings.cookie_secure,
        httponly=True,
        samesite="lax",
    )


def clear_reprompt_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        REPROMPT_COOKIE, path=REPROMPT_COOKIE_PATH, secure=settings.cookie_secure, httponly=True, samesite="lax"
    )


@router.get("/auth/login")
async def login(request: Request, db: DB, svc: Svc, return_to: str | None = None) -> Response:
    if not svc.login_limiter.allow(client_ip(request) or "?"):
        raise ApiError(429, "RATE_LIMITED")
    target = safe_return_to(return_to)
    # The marker is read here and never changed: this try may not reach the callback, and only the callback ends it.
    url = await svc.oidc.begin(db, target, prompt="login" if reprompt_marked(request) else None)
    return RedirectResponse(url, status_code=303, headers={"Cache-Control": "no-store"})


@router.get("/auth/callback")
async def callback(
    request: Request, db: DB, svc: Svc, code: str | None = None, state: str | None = None, error: str | None = None
) -> Response:
    if error is not None or code is None or state is None:
        log.info("oidc.callback_error", provider_error=bool(error))
        raise ApiError(400, "MALFORMED_MESSAGE", "Sign-in was cancelled or failed. Try again.")
    try:
        identity, return_to = await svc.oidc.complete(db, code, state)
    except EmailNotVerifiedError:
        # Nothing about an account is read or written and no session cookie is set, only the re-prompt marker;
        # returning (not raising) commits the deletion of the login attempt, so this callback URL cannot be replayed.
        refused = RedirectResponse(EMAIL_UNVERIFIED_REDIRECT, status_code=303, headers={"Cache-Control": "no-store"})
        set_reprompt_cookie(refused, svc.settings)
        return refused
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
    if reprompt_marked(request):
        # Signed in, so the marker is of no use any more. This is the one place that clears it.
        clear_reprompt_cookie(response, svc.settings)
    return response


@router.get("/session")
async def session_document(auth: Auth, db: DB, svc: Svc) -> Any:
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
        "entitlement_state": await svc.billing.entitlement_state(db, auth.account.id),
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
