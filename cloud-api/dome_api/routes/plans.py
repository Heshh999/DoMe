"""Public plan catalogue (plans.json, display prices, whether checkout exists and in which Stripe mode, and whether the
AI assistant is switched on, so the website never sells AI allowances as usable while it is off)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from dome_api.auth.deps import Svc
from dome_api.plans import catalog
from dome_api.util import rest_response

router = APIRouter(tags=["plans"])


@router.get("/plans")
async def plans(svc: Svc) -> Any:
    cat = catalog()
    body = {
        "plans": cat.public_plans(),
        "pricing": cat.pricing_defaults,
        "billing_enabled": svc.billing.enabled,
        "billing_mode": svc.billing.config.mode if svc.billing.enabled else "off",
        "ai_enabled": svc.assistant.configured,
    }
    return rest_response(svc.settings.validate_rest_responses, "plans_response", body)
