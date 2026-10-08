from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

from .errors import ProtocolError

# ASCII-only digits, anchored with \Z so a trailing newline is rejected (matches the TS/Ajv rule).
_RFC3339 = re.compile(r"\A([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})(?:\.([0-9]{1,3}))?Z\Z", re.ASCII)


def now_utc() -> datetime:
    return datetime.now(UTC)


def format_rfc3339(dt: datetime) -> str:
    if dt.tzinfo is None:
        raise ValueError("naive datetime")
    dt = dt.astimezone(UTC)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def parse_rfc3339(text: str) -> datetime:
    if not isinstance(text, str):
        raise ProtocolError("MALFORMED_MESSAGE", "timestamp must be a string")
    m = _RFC3339.match(text)
    if not m:
        raise ProtocolError("MALFORMED_MESSAGE", "timestamp must be RFC 3339 UTC (…Z)")
    y, mo, d, h, mi, s, frac = m.groups()
    ms = int((frac or "0").ljust(3, "0"))
    try:
        return datetime(int(y), int(mo), int(d), int(h), int(mi), int(s), ms * 1000, tzinfo=UTC)
    except ValueError as exc:
        raise ProtocolError("MALFORMED_MESSAGE", f"invalid timestamp: {exc}") from None


def check_command_window(
    issued_at: str,
    expires_at: str,
    *,
    now: datetime | None = None,
    max_lifetime_seconds: int = 300,
    max_skew_seconds: int = 5,
) -> None:
    """Reject expired, future-dated, or over-long lifetimes (commands and confirmations alike)."""
    now = now or now_utc()
    issued = parse_rfc3339(issued_at)
    expires = parse_rfc3339(expires_at)
    if expires <= issued:
        raise ProtocolError("MALFORMED_MESSAGE", "expires_at must be after issued_at")
    if expires - issued > timedelta(seconds=max_lifetime_seconds):
        raise ProtocolError("MALFORMED_MESSAGE", "lifetime too long")
    if issued > now + timedelta(seconds=max_skew_seconds):
        raise ProtocolError("CLOCK_SKEW", "issued in the future", retryable=True)
    if expires < now - timedelta(seconds=max_skew_seconds):
        raise ProtocolError("COMMAND_EXPIRED", "expired", retryable=True)
