"""FastAPI dependencies: database transaction, signed-in account (with CSRF + Origin enforcement
on state-changing requests) and PC bearer identity (with Origin-absent enforcement)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dome_api.auth.sessions import COOKIE_NAME, CSRF_HEADER, Authenticated, csrf_matches, resolve_session
from dome_api.db.models import PC, Account, PCAccessToken
from dome_api.errors import ApiError
from dome_api.security.origin import require_allowed_origin, require_origin_absent
from dome_api.security.tokens import sha256
from dome_api.state import Services
from dome_api.util import utcnow

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def services(request: Request) -> Services:
    svc: Services = request.app.state.services
    return svc


async def db_session(request: Request) -> AsyncIterator[AsyncSession]:
    """One transaction per request: committed when the handler returns, rolled back on any error.

    Handlers that must perform a side effect only *after* the data is durable (delivering a frame to
    a live socket) call ``await db.commit()`` themselves first; the final commit here is then a no-op.
    """
    svc = services(request)
    async with svc.db() as session:
        try:
            yield session
        except BaseException:
            await session.rollback()
            raise
        else:
            await session.commit()


# scope="function": the exit code (commit/rollback) runs BEFORE the response is sent, so a client that
# receives 2xx can rely on the data being durable (FastAPI >= 0.118 defaults to exiting after the response).
DB = Annotated[AsyncSession, Depends(db_session, scope="function")]
Svc = Annotated[Services, Depends(services)]


async def optional_account(request: Request, db: DB, svc: Svc) -> Authenticated | None:
    return await resolve_session(db, svc.settings, request.cookies.get(COOKIE_NAME))


async def current_account(request: Request, db: DB, svc: Svc) -> Authenticated:
    auth = await resolve_session(db, svc.settings, request.cookies.get(COOKIE_NAME))
    if auth is None:
        raise ApiError(401, "UNAUTHENTICATED")
    if request.method not in SAFE_METHODS:
        require_allowed_origin(request.headers, svc.settings.allowed_origins)
        if not csrf_matches(auth.session, request.headers.get(CSRF_HEADER)):
            raise ApiError(403, "FORBIDDEN", "Missing or invalid CSRF token")
    return auth


Auth = Annotated[Authenticated, Depends(current_account)]


@dataclass(slots=True)
class AgentIdentity:
    pc: PC
    account: Account
    token_id: uuid.UUID


async def agent_identity(request: Request, db: DB, svc: Svc) -> AgentIdentity:
    require_origin_absent(request.headers)
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not (32 <= len(token.strip()) <= 128):
        raise ApiError(401, "UNAUTHENTICATED", "PC access token required", headers={"WWW-Authenticate": "Bearer"})
    identity = await resolve_agent_token(db, token.strip())
    if identity is None:
        raise ApiError(
            401, "UNAUTHENTICATED", "PC access token invalid or expired", headers={"WWW-Authenticate": "Bearer"}
        )
    return identity


async def resolve_agent_token(db: AsyncSession, token: str) -> AgentIdentity | None:
    now = utcnow()
    row = await db.scalar(select(PCAccessToken).where(PCAccessToken.token_hash == sha256(token)))
    if row is None or row.revoked_at is not None or row.expires_at <= now:
        return None
    pc = await db.get(PC, row.pc_id)
    if pc is None or pc.deleted_at is not None:
        return None
    account = await db.get(Account, pc.account_id)
    if account is None or account.deleted_at is not None:
        return None
    return AgentIdentity(pc=pc, account=account, token_id=row.id)


Agent = Annotated[AgentIdentity, Depends(agent_identity)]


def agent_public_endpoint(request: Request) -> None:
    """Unauthenticated agent endpoints (link start/poll, token): still never callable from a page."""
    require_origin_absent(request.headers)


AgentPublic = Annotated[None, Depends(agent_public_endpoint)]
