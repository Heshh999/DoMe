"""Server-side web sessions.

The cookie carries a random 32-byte id; the table stores only ``HMAC-SHA256(session_secret, id)``.
Idle expiry slides on use (``DOME_SESSION_IDLE_DAYS``) under an absolute cap
(``DOME_SESSION_ABSOLUTE_DAYS``). Revocation is a timestamp so the row stays for the activity list.
"""

from __future__ import annotations

import hashlib
import hmac
import uuid
from dataclasses import dataclass
from datetime import timedelta

from fastapi import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dome_api.db.models import Account, Session
from dome_api.security.tokens import hmac_sha256, new_token
from dome_api.settings import Settings
from dome_api.util import utcnow

COOKIE_NAME = "dome_session"
CSRF_HEADER = "x-dome-csrf"
_TOUCH_INTERVAL = timedelta(minutes=5)


@dataclass(slots=True)
class Authenticated:
    session: Session
    account: Account


def session_token_hash(settings: Settings, token: str) -> bytes:
    return hmac_sha256(settings.session_secret.get_secret_value(), "session|" + token)


def user_agent_hash(ua: str | None) -> bytes | None:
    return hashlib.sha256(ua.encode("utf-8", "replace")).digest() if ua else None


async def create_session(
    db: AsyncSession, settings: Settings, account_id: uuid.UUID, user_agent: str | None
) -> tuple[Session, str]:
    token = new_token()
    now = utcnow()
    row = Session(
        account_id=account_id,
        token_hash=session_token_hash(settings, token),
        csrf_token=new_token(),
        created_at=now,
        last_seen_at=now,
        expires_at=now + timedelta(days=settings.session_idle_days),
        absolute_expires_at=now + timedelta(days=settings.session_absolute_days),
        user_agent_hash=user_agent_hash(user_agent),
    )
    db.add(row)
    await db.flush()
    return row, token


async def resolve_session(db: AsyncSession, settings: Settings, token: str | None) -> Authenticated | None:
    if not token or len(token) < 32 or len(token) > 128:
        return None
    now = utcnow()
    row = await db.scalar(select(Session).where(Session.token_hash == session_token_hash(settings, token)))
    if row is None or row.revoked_at is not None or row.expires_at <= now or row.absolute_expires_at <= now:
        return None
    account = await db.get(Account, row.account_id)
    if account is None or account.deleted_at is not None:
        return None
    if now - row.last_seen_at > _TOUCH_INTERVAL:
        row.last_seen_at = now
        row.expires_at = min(now + timedelta(days=settings.session_idle_days), row.absolute_expires_at)
    return Authenticated(session=row, account=account)


def csrf_matches(session: Session, header_value: str | None) -> bool:
    return bool(header_value) and hmac.compare_digest(session.csrf_token, header_value or "")


def set_session_cookie(response: Response, settings: Settings, token: str) -> None:
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=settings.session_idle_days * 86400,
        path="/",
        secure=settings.cookie_secure,
        httponly=True,
        samesite="lax",
    )


def clear_session_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(COOKIE_NAME, path="/", secure=settings.cookie_secure, httponly=True, samesite="lax")
