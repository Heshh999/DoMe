"""The AI assistant's PWA-only endpoints (docs/design/assistant.md §1.2; docs/PROTOCOL.md §19). Session-authenticated
like every PWA route; the state-changing ones need ``X-DoMe-CSRF`` and the exact ``Origin`` (the ``Auth``
dependency). Bodies are strict-parsed and validated against rest.schema.json; errors never echo request content."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, Response

from dome_api.auth.deps import DB, Auth, Svc
from dome_api.util import rest_response, strict_body

router = APIRouter(tags=["assistant"])


@router.get("/assistant")
async def assistant_status(auth: Auth, db: DB, svc: Svc) -> Any:
    body = await svc.assistant.status_body(db, auth.account)
    return rest_response(svc.settings.validate_rest_responses, "assistant_status_response", body)


@router.post("/assistant/consent")
async def give_consent(request: Request, auth: Auth, db: DB, svc: Svc) -> Any:
    # the schema pins the consent text version and accept: true, so an outdated PWA's consent is a 400
    await strict_body(request, "assistant_consent_request")
    body = await svc.assistant.give_consent(db, auth.account)
    return rest_response(svc.settings.validate_rest_responses, "assistant_status_response", body)


@router.delete("/assistant/consent", status_code=204)
async def withdraw_consent(auth: Auth, db: DB, svc: Svc) -> Response:
    await svc.assistant.withdraw_consent(db, auth.account)
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


@router.post("/assistant/turn")
async def assistant_turn(request: Request, auth: Auth, db: DB, svc: Svc) -> Response:
    return await svc.assistant.turn(request, db, auth.account)
