"""Billing's PWA endpoints and the provider webhook (docs/BILLING.md §5–§6). The three page creations are
session-authenticated with ``X-DoMe-CSRF`` and the exact ``Origin`` (the ``Auth`` dependency) and strict-parse their
bodies; the webhook has no session, refuses any ``Origin`` header (a browser page can never call it), caps the raw
body while it streams (declared or chunked) before parsing and verifies the provider's signature over those exact
bytes. Nothing a return URL carries is ever read by the server."""

from __future__ import annotations

from typing import Any

from dome_protocol import loads_strict
from fastapi import APIRouter, Request

from dome_api.auth.deps import DB, Auth, Svc
from dome_api.errors import ApiError
from dome_api.security.origin import require_origin_absent
from dome_api.util import rest_response, strict_body

router = APIRouter(tags=["billing"])
WEBHOOK_MAX_BYTES = 65536


async def empty_json_object(request: Request) -> None:
    """The portal and top-up requests carry ``{}`` (a JSON body, so the CSRF/Origin rules of a state change apply
    and no accidental GET can create a page); anything else is malformed."""
    ctype = request.headers.get("content-type", "")
    if ctype.split(";")[0].strip().lower() != "application/json":
        raise ApiError(400, "MALFORMED_MESSAGE", "Content-Type must be application/json")
    raw = await request.body()
    try:
        parsed = loads_strict(raw, max_bytes=64, require_object=True)
    except Exception:  # noqa: BLE001 - ProtocolError or a size error: malformed either way
        raise ApiError(400, "MALFORMED_MESSAGE") from None
    if parsed != {}:
        raise ApiError(400, "MALFORMED_MESSAGE")


@router.post("/billing/checkout")
async def checkout(request: Request, auth: Auth, db: DB, svc: Svc) -> Any:
    body = await strict_body(request, "billing_checkout_request")
    out = await svc.billing.checkout(db, auth.account, str(body["plan"]), str(body["interval"]))
    return rest_response(svc.settings.validate_rest_responses, "billing_checkout_response", out)


@router.post("/billing/portal")
async def portal(request: Request, auth: Auth, db: DB, svc: Svc) -> Any:
    await empty_json_object(request)
    out = await svc.billing.portal(db, auth.account)
    return rest_response(svc.settings.validate_rest_responses, "billing_portal_response", out)


@router.post("/billing/topup")
async def topup(request: Request, auth: Auth, db: DB, svc: Svc) -> Any:
    await empty_json_object(request)
    out = await svc.billing.topup(db, auth.account)
    return rest_response(svc.settings.validate_rest_responses, "billing_checkout_response", out)


@router.post("/billing/webhook")
async def webhook(request: Request, svc: Svc) -> Any:
    require_origin_absent(request.headers)
    if not svc.billing.enabled:
        raise ApiError(503, "BILLING_NOT_AVAILABLE")
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > WEBHOOK_MAX_BYTES:
        raise ApiError(413, "PAYLOAD_TOO_LARGE")
    # counted while it streams: a chunked body declares no length, and nothing past the cap is ever buffered
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > WEBHOOK_MAX_BYTES:
            raise ApiError(413, "PAYLOAD_TOO_LARGE")
        chunks.append(chunk)
    outcome = await svc.billing.webhook(b"".join(chunks), request.headers.get("stripe-signature"))
    return rest_response(svc.settings.validate_rest_responses, "billing_webhook_response", outcome.body())
