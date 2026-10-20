"""Central plan definitions.

This is the ONLY module that reads ``shared/protocol/plans.json`` or knows plan names. Every
other module asks for a :class:`Plan` (by an account's ``plan`` column) and reads its attributes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from dome_protocol.registry import contract_dir

_FREE_PLAN_ID = "free"  # the one plan that exists without a subscription; referenced nowhere else
# The entitlement assertion's `plan` claim for every paid plan (schemas/entitlement.schema.json enum [free, pro]):
# Pro and Pro Plus share every PC-facing limit, so the agent needs no new claim value (docs/BILLING.md §2).
_ASSERTION_PAID_PLAN_ID = "pro"


@dataclass(frozen=True, slots=True)
class RateLimit:
    per_minute: int
    burst: int


@dataclass(frozen=True, slots=True)
class Window:
    """A rolling AI allowance (plans.json ai_window / ai_week): ``requests`` charged turns per window of ``length``."""

    length_hours: int
    requests: int


@dataclass(frozen=True, slots=True)
class Plan:
    id: str
    display_name: str
    max_enabled_pcs: int
    max_controllers: int
    custom_layouts: bool
    routines: bool
    routine_max_steps: int
    routine_max_seconds: int
    # AI allowances (rules.ai_allowance): Free's one-time taste, the 5-hour and the weekly window, whether top-ups
    # may be bought and spent; read by the assistant and billing only, never part of session_response or the assertion
    ai_taste_requests: int
    ai_window: Window
    ai_week: Window
    ai_topup_allowed: bool
    ai_transcription_minutes_per_period: int
    # the assistant's internal per-account backstop on the metered provider cost (US cents per weekly window) and
    # its per-account turn bucket
    ai_cost_cap_cents_per_week: int
    ai_turn_rate_limit: RateLimit
    manual_command_rate_limit: RateLimit
    coalescable_command_rate_limit: RateLimit
    # signed input_batch frames per controller (rules.input_sessions); identical for every plan
    input_rate_limit: RateLimit

    @property
    def is_default_plan(self) -> bool:
        """True for the plan every account has without a subscription (no entitlement assertion)."""
        return self.id == _FREE_PLAN_ID

    @property
    def issues_entitlement_assertion(self) -> bool:
        return not self.is_default_plan

    @property
    def entitlement_plan_id(self) -> str:
        """The assertion's ``plan`` claim: ``pro`` for every paid plan (the agent knows only free and pro)."""
        return self.id if self.is_default_plan else _ASSERTION_PAID_PLAN_ID

    @property
    def has_ai_allowance(self) -> bool:
        """The plan grants AI turns by itself (a taste or a window); availability is still DOME_AI_ENABLED."""
        return self.ai_taste_requests > 0 or self.ai_window.requests > 0

    def limits_shrink_from(self, other: Plan) -> bool:
        """True when moving from ``other`` to this plan lowers a device limit (a downgrade for selection purposes)."""
        return self.max_enabled_pcs < other.max_enabled_pcs or self.max_controllers < other.max_controllers

    def session_limits(self) -> dict[str, Any]:
        return {
            "max_enabled_pcs": self.max_enabled_pcs,
            "max_controllers": self.max_controllers,
            "routines": self.routines,
            "custom_layouts": self.custom_layouts,
        }

    def entitlement_limits(self) -> dict[str, Any]:
        return {
            "max_enabled_pcs": self.max_enabled_pcs,
            "max_controllers": self.max_controllers,
            "routines": self.routines,
            "routine_max_steps": self.routine_max_steps,
            "routine_max_seconds": self.routine_max_seconds,
            "custom_layouts": self.custom_layouts,
        }


@dataclass(frozen=True, slots=True)
class PlanCatalog:
    raw: dict[str, Any]
    plans: dict[str, Plan]

    @property
    def default_plan(self) -> Plan:
        return self.plans[_FREE_PLAN_ID]

    def get(self, plan_id: str | None) -> Plan:
        """Resolve an account's stored plan id; unknown/empty ids fall back to the default plan."""
        if plan_id and plan_id in self.plans:
            return self.plans[plan_id]
        return self.default_plan

    @property
    def pricing_defaults(self) -> dict[str, Any]:
        """rest.schema.json#/$defs/pricing: display amounts for GET /v1/plans and the Billing page."""
        p = self.raw["pricing_defaults"]
        return {
            "currency": p["currency"],
            "plans": {
                pid: {"monthly_cents": int(v["monthly_cents"]), "annual_cents": int(v["annual_cents"])}
                for pid, v in p["plans"].items()
            },
            "topup": dict(self.topup),
        }

    @property
    def topup(self) -> dict[str, int]:
        t = self.raw["pricing_defaults"]["topup"]
        return {
            "requests": int(t["requests"]),
            "price_cents": int(t["price_cents"]),
            "max_balance_requests": int(t["max_balance_requests"]),
        }

    @property
    def paid_plan_ids(self) -> tuple[str, ...]:
        return tuple(pid for pid in self.plans if pid != _FREE_PLAN_ID)

    @property
    def downgrade_policy(self) -> dict[str, Any]:
        return {k: v for k, v in self.raw["downgrade_policy"].items() if not k.startswith("$")}

    @property
    def selection_window_days(self) -> int:
        return int(self.raw["downgrade_policy"]["selection_window_days"])

    @property
    def past_due_grace_days(self) -> int:
        return int(self.raw["billing_grace"]["past_due_grace_days"])

    @property
    def entitlement_lifetime_seconds(self) -> int:
        return int(self.raw["entitlement_assertion"]["lifetime_seconds"])

    @property
    def entitlement_algorithm(self) -> str:
        return str(self.raw["entitlement_assertion"]["algorithm"])

    def public_plans(self) -> dict[str, Any]:
        """plans.json ``plans`` object without ``$comment`` members (for GET /v1/plans)."""
        return {
            pid: {k: v for k, v in body.items() if not k.startswith("$")} for pid, body in self.raw["plans"].items()
        }


def _rate(obj: dict[str, Any]) -> RateLimit:
    return RateLimit(per_minute=int(obj["per_minute"]), burst=int(obj["burst"]))


def _rate_per_second(obj: dict[str, Any]) -> RateLimit:
    """plans.json expresses the input budget per second; the limiter works per minute."""
    return RateLimit(per_minute=int(obj["batches_per_second"]) * 60, burst=int(obj["burst"]))


@lru_cache(maxsize=1)
def catalog() -> PlanCatalog:
    with (contract_dir() / "plans.json").open("r", encoding="utf-8") as fh:
        raw = json.load(fh)
    plans: dict[str, Plan] = {}
    for pid, body in raw["plans"].items():
        plans[pid] = Plan(
            id=pid,
            display_name=body["display_name"],
            max_enabled_pcs=int(body["max_enabled_pcs"]),
            max_controllers=int(body["max_controllers"]),
            custom_layouts=bool(body["custom_layouts"]),
            routines=bool(body["routines"]),
            routine_max_steps=int(body["routine_max_steps"]),
            routine_max_seconds=int(body["routine_max_seconds"]),
            ai_taste_requests=int(body["ai_taste_requests"]),
            ai_window=Window(length_hours=int(body["ai_window"]["hours"]), requests=int(body["ai_window"]["requests"])),
            ai_week=Window(length_hours=int(body["ai_week"]["days"]) * 24, requests=int(body["ai_week"]["requests"])),
            ai_topup_allowed=bool(body["ai_topup_allowed"]),
            ai_transcription_minutes_per_period=int(body["ai_transcription_minutes_per_period"]),
            ai_cost_cap_cents_per_week=int(body["ai_cost_cap_cents_per_week"]),
            ai_turn_rate_limit=_rate(body["ai_turn_rate_limit"]),
            manual_command_rate_limit=_rate(body["manual_command_rate_limit"]),
            coalescable_command_rate_limit=_rate(body["coalescable_command_rate_limit"]),
            input_rate_limit=_rate_per_second(body["input_rate_limit"]),
        )
    if _FREE_PLAN_ID not in plans:
        raise RuntimeError("plans.json must define the default plan")
    return PlanCatalog(raw=raw, plans=plans)


def plan_for(plan_id: str | None) -> Plan:
    return catalog().get(plan_id)
