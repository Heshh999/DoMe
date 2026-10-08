"""Small helpers shared by routes and the relay."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from dome_protocol import format_rfc3339, load_schemas, loads_strict
from fastapi import Request

from dome_api.errors import ApiError

MAX_REST_BODY_BYTES = 16384


def utcnow() -> datetime:
    return datetime.now(UTC)


def ts(dt: datetime | None) -> str | None:
    return None if dt is None else format_rfc3339(dt)


def ts_required(dt: datetime) -> str:
    return format_rfc3339(dt)


def plus(seconds: float) -> datetime:
    return utcnow() + timedelta(seconds=seconds)


def parse_uuid(value: str) -> uuid.UUID:
    """Accept only canonical lower-case hyphenated UUIDs (the contract's ``uuid`` pattern)."""
    if len(value) != 36 or value != value.lower():
        raise ApiError(404, "NOT_FOUND")
    try:
        parsed = uuid.UUID(value)
    except ValueError:
        raise ApiError(404, "NOT_FOUND") from None
    if str(parsed) != value:
        raise ApiError(404, "NOT_FOUND")
    return parsed


async def strict_body(request: Request, rest_def: str) -> dict[str, Any]:
    """Read the raw body, parse it with the strict parser and validate it against
    ``rest.schema.json#/$defs/<rest_def>``. Nothing from the body is used before this passes."""
    ctype = request.headers.get("content-type", "")
    if not ctype.split(";")[0].strip().lower() == "application/json":
        raise ApiError(400, "MALFORMED_MESSAGE", "Content-Type must be application/json")
    raw = await request.body()
    if len(raw) > MAX_REST_BODY_BYTES:
        raise ApiError(413, "PAYLOAD_TOO_LARGE")
    try:
        parsed = loads_strict(raw, max_bytes=MAX_REST_BODY_BYTES, require_object=True)
        load_schemas().validate_rest(rest_def, parsed)
    except Exception as exc:  # ProtocolError carries the code; anything else is malformed
        code = getattr(exc, "code", "MALFORMED_MESSAGE")
        raise ApiError(400, code if code in ("MALFORMED_MESSAGE", "PAYLOAD_TOO_LARGE") else "MALFORMED_MESSAGE") from None
    assert isinstance(parsed, dict)
    return parsed


def client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None
