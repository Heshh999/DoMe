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
    return str(format_rfc3339(dt))


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
        raise ApiError(
            400, code if code in ("MALFORMED_MESSAGE", "PAYLOAD_TOO_LARGE") else "MALFORMED_MESSAGE"
        ) from None
    assert isinstance(parsed, dict)
    return parsed


def client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def rest_response(
    settings_validate: bool,
    body_name: str,
    body: dict[str, Any],
    *,
    status: int = 200,
    headers: dict[str, str] | None = None,
) -> Any:
    """Build a JSON response for a body that is one of ``rest.schema.json#/$defs``.

    Outside production (``Settings.validate_rest_responses``) the body is validated first; a contract
    violation becomes an ``INTERNAL`` 500 instead of a non-conforming 200.
    """
    from fastapi.responses import JSONResponse

    if settings_validate:
        try:
            load_schemas().validate_rest(body_name, body)
        except Exception as exc:  # ProtocolError: our own body does not match the contract
            raise ApiError(
                500, "INTERNAL", f"response does not match rest.schema.json#/$defs/{body_name}: {exc}"
            ) from None
    return JSONResponse(body, status_code=status, headers={"Cache-Control": "no-store", **(headers or {})})


def b64url_to_sha256(value: str) -> bytes:
    """Decode a 43-char base64url SHA-256 handle (already pattern-checked by the REST schema)."""
    from dome_protocol.keys import b64url_decode

    return bytes(b64url_decode(value, expected_len=32))
