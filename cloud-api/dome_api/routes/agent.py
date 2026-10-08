"""Agent-facing REST: credential → access token, entitlement assertion, JWKS."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select

from dome_api.auth.deps import DB, Agent, AgentPublic, Svc
from dome_api.db.models import PC, Account, PCAccessToken, PCCredential
from dome_api.errors import ApiError
from dome_api.plans import plan_for
from dome_api.security import events
from dome_api.security.tokens import new_token, sha256
from dome_api.util import client_ip, rest_response, strict_body, utcnow

router = APIRouter(tags=["agent"])
wellknown = APIRouter()


@router.post("/agent/token")
async def token(request: Request, _: AgentPublic, db: DB, svc: Svc) -> Any:
    if not svc.agent_token_limiter.allow(client_ip(request) or "?"):
        raise ApiError(429, "RATE_LIMITED")
    body = await strict_body(request, "agent_token_request")
    now = utcnow()
    cred = await db.scalar(select(PCCredential).where(PCCredential.credential_hash == sha256(body["pc_credential"])))
    pc = await db.get(PC, cred.pc_id) if cred is not None and cred.revoked_at is None else None
    account = await db.get(Account, pc.account_id) if pc is not None and pc.deleted_at is None else None
    if cred is None or pc is None or account is None or account.deleted_at is not None:
        await events.record_now(
            svc.db,
            account_id=None,
            kind="agent_token_refused",
            severity="warning",
            actor="pc",
            detail={"reason": "unknown_or_revoked_credential"},
        )
        raise ApiError(
            401,
            "UNAUTHENTICATED",
            "PC credential invalid or revoked; link this PC again",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access = new_token()
    db.add(
        PCAccessToken(
            pc_id=pc.id,
            token_hash=sha256(access),
            created_at=now,
            expires_at=now + timedelta(seconds=svc.settings.pc_access_token_seconds),
        )
    )
    cred.last_used_at = now
    out = {
        "access_token": access,
        "expires_in": svc.settings.pc_access_token_seconds,
        "pc_id": str(pc.id),
        "account_id": str(pc.account_id),
    }
    return rest_response(svc.settings.validate_rest_responses, "agent_token_response", out)


@router.post("/agent/entitlement")
async def entitlement(agent: Agent, svc: Svc) -> Any:
    plan = plan_for(agent.account.plan)
    out = {
        "plan": plan.id,
        "assertion": svc.signer.assertion(agent.account.id, agent.pc.id, plan),
        "pc_enabled": bool(agent.pc.enabled),
    }
    return rest_response(svc.settings.validate_rest_responses, "agent_entitlement_response", out)


@wellknown.get("/.well-known/dome-jwks.json")
async def jwks(svc: Svc) -> Any:
    return JSONResponse(svc.signer.jwks(), headers={"Cache-Control": "public, max-age=300"})
