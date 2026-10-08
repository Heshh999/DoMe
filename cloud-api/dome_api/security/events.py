"""Security event writer. ``detail`` is redacted with the logging redaction rules before storage, so
secrets, pairing material, challenge text and titles never reach the table even by mistake."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dome_api.db.models import SecurityEvent
from dome_api.logging import get_logger, redact

log = get_logger("dome_api.security")


def _row(
    *,
    account_id: uuid.UUID | None,
    kind: str,
    severity: str,
    actor: str,
    subject_id: uuid.UUID | None,
    detail: dict[str, Any] | None,
    ip_hash: bytes | None,
) -> SecurityEvent:
    clean = redact(detail or {})
    assert isinstance(clean, dict)
    log.info("security_event", kind=kind, severity=severity, actor=actor, account_id=str(account_id) if account_id else None)
    return SecurityEvent(
        account_id=account_id,
        kind=kind,
        severity=severity,
        actor=actor,
        subject_id=subject_id,
        detail=clean,
        ip_hash=ip_hash,
    )


def record(
    db: AsyncSession,
    *,
    account_id: uuid.UUID | None,
    kind: str,
    severity: str = "info",
    actor: str = "account",
    subject_id: uuid.UUID | None = None,
    detail: dict[str, Any] | None = None,
    ip_hash: bytes | None = None,
) -> None:
    """Add an event to the caller's transaction (committed together with the change it describes)."""
    db.add(_row(account_id=account_id, kind=kind, severity=severity, actor=actor, subject_id=subject_id, detail=detail, ip_hash=ip_hash))


async def record_now(
    factory: async_sessionmaker[AsyncSession],
    *,
    account_id: uuid.UUID | None,
    kind: str,
    severity: str = "notice",
    actor: str = "system",
    subject_id: uuid.UUID | None = None,
    detail: dict[str, Any] | None = None,
    ip_hash: bytes | None = None,
) -> None:
    """Write an event in its own transaction (for failure paths whose main transaction rolls back)."""
    async with factory() as db:
        async with db.begin():
            db.add(_row(account_id=account_id, kind=kind, severity=severity, actor=actor, subject_id=subject_id, detail=detail, ip_hash=ip_hash))
