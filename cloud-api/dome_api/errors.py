"""REST error responses. Every error body is ``rest.schema.json#/$defs/error_body``.

Codes come from ``shared/protocol/errors.json`` wherever one fits. The handful of purely HTTP-level
conditions the contract does not name (``UNAUTHENTICATED``, ``FORBIDDEN``, ``NOT_FOUND``,
``METHOD_NOT_ALLOWED``, ``LINK_DENIED``, ``LINK_EXPIRED``, ``SERVICE_UNAVAILABLE``) are listed in
``CONTRACT_ISSUES.md`` and only ever appear in REST error bodies, never in relay frames.
"""

from __future__ import annotations

from typing import Any

from dome_protocol import ProtocolError, load_registry
from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

API_ONLY_CODES: dict[str, tuple[str, bool]] = {
    "UNAUTHENTICATED": ("Sign in to continue.", False),
    "FORBIDDEN": ("This request was not allowed.", False),
    "NOT_FOUND": ("Not found.", False),
    "METHOD_NOT_ALLOWED": ("Method not allowed.", False),
    "LINK_DENIED": ("Linking this PC was declined.", False),
    "LINK_EXPIRED": ("The link code expired or was already used. Start again on the PC.", False),
    "SERVICE_UNAVAILABLE": ("DoMe is temporarily unavailable. Try again shortly.", True),
}


def error_defaults(code: str) -> tuple[str, bool]:
    if code in API_ONLY_CODES:
        return API_ONLY_CODES[code]
    reg = load_registry()
    entry = reg.errors.get(code)
    if entry is None:
        entry = reg.errors["INTERNAL"]
    return str(entry["user_message"]), bool(entry["retryable"])


class ApiError(Exception):
    def __init__(
        self,
        status: int,
        code: str,
        message: str | None = None,
        *,
        retryable: bool | None = None,
        detail: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        default_message, default_retryable = error_defaults(code)
        self.status = status
        self.code = code
        self.message = (message or default_message)[:512]
        self.retryable = default_retryable if retryable is None else retryable
        self.detail = detail
        self.headers = headers or {}
        super().__init__(f"{status} {code}: {self.message}")

    def body(self) -> dict[str, Any]:
        err: dict[str, Any] = {"code": self.code, "message": self.message, "retryable": self.retryable}
        if self.detail:
            err["detail"] = self.detail
        return {"error": err}

    @classmethod
    def from_protocol(cls, exc: ProtocolError, status: int = 400) -> ApiError:
        return cls(status, exc.code, exc.message, retryable=exc.retryable, detail=exc.detail or None)


def _response(err: ApiError) -> JSONResponse:
    return JSONResponse(err.body(), status_code=err.status, headers={"Cache-Control": "no-store", **err.headers})


async def api_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ApiError)
    return _response(exc)


async def protocol_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ProtocolError)
    return _response(ApiError.from_protocol(exc))


async def http_exception_handler(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    code = {401: "UNAUTHENTICATED", 403: "FORBIDDEN", 404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED", 429: "RATE_LIMITED"}.get(
        exc.status_code, "MALFORMED_MESSAGE" if exc.status_code < 500 else "INTERNAL"
    )
    message = exc.detail if isinstance(exc.detail, str) and code in ("MALFORMED_MESSAGE",) else None
    headers = dict(exc.headers or {})
    return _response(ApiError(exc.status_code, code, message, headers=headers))


async def validation_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    return _response(ApiError(400, "MALFORMED_MESSAGE", "The request was not understood."))


async def unhandled_error_handler(_request: Request, _exc: Exception) -> JSONResponse:
    return _response(ApiError(500, "INTERNAL"))
