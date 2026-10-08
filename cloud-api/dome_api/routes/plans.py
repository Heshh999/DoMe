"""Public plan catalogue (plans.json plus configured display prices)."""

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
        "billing_enabled": bool(svc.settings.stripe_secret_key.get_secret_value()),
    }
    return rest_response(svc.settings.validate_rest_responses, "plans_response", body)
