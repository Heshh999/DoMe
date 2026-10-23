"""A stand-in for the Stripe REST reads the Deploy button makes (dome_deploy.StripeChecker), with Stripe-shaped state.

Shapes are those of Stripe's API version 2026-09-30.endive, the one cloud-api's stripe SDK pins and dome_deploy sends:
the OpenAPI spec of that version (github.com/stripe/openapi, spec3.json: ``account``, ``price`` with an expanded
``product`` or ``deleted_product``, the ``webhook_endpoint`` and ``billing_portal.configuration`` lists) and the answers
of stripe-mock v0.206.0, which the same requests were run against. As at Stripe:

* the key travels in ``Authorization: Bearer``; a revoked key gets 401 "Invalid API Key provided: sk_test_****wxyz"
  (Stripe masks all but the last four characters itself; ``echo_key`` quotes the whole header instead, as stripe-mock
  does, to prove the deploy hides it anyway);
* a restricted key without a permission gets 403 for that resource (``forbidden``);
* a test key sees only Test mode objects and a live key only Live mode ones; an unknown price is 404
  ``resource_missing`` "No such price: '<id>'", with Stripe's "; a similar object exists in test mode, but a live mode
  key was used to make this request." when the other mode has it;
* a query parameter an endpoint does not take, or a malformed one, is 400, as stripe-mock refuses them.

The default state is the founder's intended setup: the sandbox "domeremote sandbox" with the five prices of
conftest.STRIPE_PRICES at the amounts of shared/protocol/plans.json, an enabled webhook endpoint for each environment with
the thirteen events of docs/LAUNCH_SETUP.md section 11A, and a saved customer portal, identically in Test and Live mode.
Answers are injected through ``fail``, keyed by path prefix (``"/v1/prices"``), with an HttpResult or an HttpFailure.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, unquote, urlsplit

from dome_deploy import HttpFailure, HttpResult

API = "https://api.stripe.com"
API_VERSION = "2026-09-30.endive"
ACCOUNT_ID = "acct_1FakeSandbox0001"
PLANS = Path(__file__).resolve().parents[3] / "shared" / "protocol" / "plans.json"
EVENTS = (
    "checkout.session.completed",
    "checkout.session.async_payment_succeeded",
    "checkout.session.expired",
    "customer.subscription.created",
    "customer.subscription.updated",
    "customer.subscription.deleted",
    "invoice.paid",
    "invoice.payment_failed",
    "invoice.payment_action_required",
    "charge.refunded",
    "charge.dispute.created",
    "charge.dispute.updated",
    "charge.dispute.closed",
)
HOOKS = ("https://staging.domeremote.com/v1/billing/webhook", "https://domeremote.com/v1/billing/webhook")
# variable -> (product id, recurring interval or "" for one-time, where plans.json keeps the amount)
TERMS = {
    "DOME_STRIPE_PRICE_PRO_MONTHLY": ("prod_FakePro", "month", ("plans", "pro", "monthly_cents")),
    "DOME_STRIPE_PRICE_PRO_ANNUAL": ("prod_FakePro", "year", ("plans", "pro", "annual_cents")),
    "DOME_STRIPE_PRICE_PRO_PLUS_MONTHLY": ("prod_FakeProPlus", "month", ("plans", "pro_plus", "monthly_cents")),
    "DOME_STRIPE_PRICE_PRO_PLUS_ANNUAL": ("prod_FakeProPlus", "year", ("plans", "pro_plus", "annual_cents")),
    "DOME_STRIPE_PRICE_TOPUP": ("prod_FakeTopUp", "", ("topup", "price_cents")),
}
# path prefix -> the query parameters that endpoint takes (spec3.json, GET operations)
PARAMETERS = {
    "/v1/account": {"expand[]"},
    "/v1/prices/": {"expand[]"},
    "/v1/webhook_endpoints": {"ending_before", "expand[]", "limit", "starting_after"},
    "/v1/billing_portal/configurations": {
        "active",
        "ending_before",
        "expand[]",
        "is_default",
        "limit",
        "starting_after",
    },
}
RESOURCES = {
    "/v1/account": "account",
    "/v1/prices/": "prices",
    "/v1/webhook_endpoints": "webhook_endpoints",
    "/v1/billing_portal/configurations": "billing_portal",
}


def _answer(status: int, body: Any) -> HttpResult:
    headers = {"content-type": "application/json", "request-id": "req_Fake0123456789"}
    return HttpResult(status, headers, json.dumps(body).encode())


def _error(status: int, message: str, *, code: str | None = None, param: str | None = None) -> HttpResult:
    error: dict[str, Any] = {"type": "invalid_request_error", "message": message}
    if code:
        error["code"] = code
    if param:
        error["param"] = param
    return _answer(status, {"error": error})


def _masked(key: str) -> str:
    return key[:8] + "*" * max(len(key) - 12, 0) + key[-4:]


class FakeStripe:
    def __init__(self, price_ids: Mapping[str, str]) -> None:
        self.price_ids = dict(price_ids)
        self.account: dict[str, Any] = {
            "id": ACCOUNT_ID,
            "object": "account",
            "business_profile": {"name": None, "url": None},
            "charges_enabled": True,
            "details_submitted": True,
            "settings": {"dashboard": {"display_name": "domeremote sandbox", "timezone": "Etc/UTC"}},
            "type": "standard",
        }
        self.modes = {"test": self._objects(live=False), "live": self._objects(live=True)}
        self.revoked: set[str] = set()
        self.forbidden: dict[str, set[str]] = {}  # key -> resources answered with 403
        self.fail: dict[str, HttpResult | HttpFailure] = {}
        self.echo_key = False
        self.requests: list[tuple[str, dict[str, str]]] = []

    # ----- state ------------------------------------------------------------------------------------
    def _objects(self, *, live: bool) -> dict[str, Any]:
        pricing = json.loads(PLANS.read_text(encoding="utf-8"))["pricing_defaults"]
        products = {
            pid: {"id": pid, "object": "product", "active": True, "name": name, "livemode": live}
            for pid, name in (
                ("prod_FakePro", "DoMe Pro"),
                ("prod_FakeProPlus", "DoMe Pro Plus"),
                ("prod_FakeTopUp", "DoMe AI top-up"),
            )
        }
        prices: dict[str, dict[str, Any]] = {}
        for variable, price_id in self.price_ids.items():
            product, interval, where = TERMS[variable]
            cents: Any = pricing
            for part in where:
                cents = cents[part]
            prices[price_id] = {
                "id": price_id,
                "object": "price",
                "active": True,
                "billing_scheme": "per_unit",
                "currency": "usd",
                "custom_unit_amount": None,
                "livemode": live,
                "product": product,
                "recurring": {"interval": interval, "interval_count": 1, "meter": None, "usage_type": "licensed"}
                if interval
                else None,
                "tax_behavior": "inclusive",
                "type": "recurring" if interval else "one_time",
                "unit_amount": cents,
                "unit_amount_decimal": str(cents),
            }
        endpoints = [
            {
                "id": f"we_Fake{i}",
                "object": "webhook_endpoint",
                "api_version": None,
                "enabled_events": list(EVENTS),
                "livemode": live,
                "status": "enabled",
                "url": url,
            }
            for i, url in enumerate(HOOKS)
        ]
        subscription = [p for v, p in self.price_ids.items() if TERMS[v][1]]
        portal = [
            {
                "id": "bpc_Fake0001",
                "object": "billing_portal.configuration",
                "active": True,
                "is_default": True,
                "livemode": live,
                "features": {
                    "customer_update": {"allowed_updates": ["email"], "enabled": True},
                    "invoice_history": {"enabled": True},
                    "payment_method_update": {"enabled": True, "payment_method_configuration": None},
                    "subscription_cancel": {
                        "cancellation_reason": {"enabled": False, "options": []},
                        "enabled": True,
                        "mode": "at_period_end",
                        "proration_behavior": "none",
                    },
                    "subscription_update": {
                        "default_allowed_updates": ["price"],
                        "enabled": True,
                        "products": [
                            {"product": "prod_FakePro", "prices": subscription[:2]},
                            {"product": "prod_FakeProPlus", "prices": subscription[2:]},
                        ],
                        "proration_behavior": "create_prorations",
                    },
                },
            }
        ]
        return {"products": products, "prices": prices, "endpoints": endpoints, "portal": portal}

    def price(self, variable: str, mode: str = "test") -> dict[str, Any]:
        """The price object a variable names, to change in place."""
        price: dict[str, Any] = self.modes[mode]["prices"][self.price_ids[variable]]
        return price

    def product(self, product_id: str, mode: str = "test") -> dict[str, Any]:
        product: dict[str, Any] = self.modes[mode]["products"][product_id]
        return product

    def endpoints(self, mode: str = "test") -> list[dict[str, Any]]:
        endpoints: list[dict[str, Any]] = self.modes[mode]["endpoints"]
        return endpoints

    def endpoint(self, url: str, mode: str = "test") -> dict[str, Any]:
        return next(e for e in self.endpoints(mode) if e["url"] == url)

    def portal(self, mode: str = "test") -> list[dict[str, Any]]:
        portal: list[dict[str, Any]] = self.modes[mode]["portal"]
        return portal

    def portal_features(self, mode: str = "test") -> dict[str, Any]:
        features: dict[str, Any] = self.portal(mode)[0]["features"]
        return features

    def paths(self) -> list[str]:
        return [url.removeprefix(API) for url, _ in self.requests]

    # ----- answering ----------------------------------------------------------------------------------
    def __call__(self, url: str, headers: Mapping[str, str]) -> HttpResult:
        self.requests.append((url, dict(headers)))
        parts = urlsplit(url)
        path = parts.path
        for prefix, forced in self.fail.items():
            if path.startswith(prefix):
                if isinstance(forced, HttpFailure):
                    raise forced
                return forced
        authorization = headers.get("Authorization", "")
        key = authorization.removeprefix("Bearer ") if authorization.startswith("Bearer ") else ""
        if not key.startswith(("sk_", "rk_")) or key in self.revoked:
            shown = f"Authorization was '{authorization}'" if self.echo_key else _masked(key)
            return _error(401, f"Invalid API Key provided: {shown}")
        if headers.get("Stripe-Version") != API_VERSION:
            return _error(400, "This stand-in answers only for Stripe-Version " + API_VERSION)
        prefix = next((p for p in PARAMETERS if path == p or (p.endswith("/") and path.startswith(p))), "")
        if not prefix:
            return _error(404, f"Unrecognized request URL (GET: {path}).")
        query = parse_qsl(parts.query, keep_blank_values=True)
        for name, value in query:
            if name not in PARAMETERS[prefix]:
                return _error(400, f"Received unknown parameter: {name}", param=name)
            if name == "limit" and not (value.isdigit() and 1 <= int(value) <= 100):
                return _error(400, "Invalid integer: " + value, param=name)
            if name in ("is_default", "active") and value not in ("true", "false"):
                return _error(400, "Invalid boolean: " + value, param=name)
        if RESOURCES[prefix] in self.forbidden.get(key, set()):
            return _error(
                403,
                f"The provided key '{_masked(key)}' does not have the required permissions for this endpoint on "
                f"account '{ACCOUNT_ID}'.",
            )
        mode = "live" if key.startswith(("sk_live_", "rk_live_")) else "test"
        objects = self.modes[mode]
        if prefix == "/v1/account":
            return _answer(200, self.account)
        if prefix == "/v1/prices/":
            return self._price(unquote(path.removeprefix(prefix)), mode, [v for n, v in query if n == "expand[]"])
        if prefix == "/v1/webhook_endpoints":
            data = copy.deepcopy(objects["endpoints"])
            return _answer(200, {"object": "list", "data": data, "has_more": False, "url": path})
        wanted = dict(query).get("is_default")
        data = [c for c in objects["portal"] if wanted is None or c["is_default"] == (wanted == "true")]
        return _answer(200, {"object": "list", "data": copy.deepcopy(data), "has_more": False, "url": path})

    def _price(self, price_id: str, mode: str, expand: list[str]) -> HttpResult:
        if any(e != "product" for e in expand):
            return _error(400, "This property cannot be expanded.", param="expand")
        found = self.modes[mode]["prices"].get(price_id)
        if found is None:
            message = f"No such price: '{price_id}'"
            other = "live" if mode == "test" else "test"
            if price_id in self.modes[other]["prices"]:
                message += (
                    f"; a similar object exists in {other} mode, but a {mode} mode key was used to make this request."
                )
            return _error(404, message, code="resource_missing", param="id")
        price = copy.deepcopy(found)
        if expand:
            product = self.modes[mode]["products"].get(price["product"])
            price["product"] = (
                copy.deepcopy(product) if product else {"id": price["product"], "object": "product", "deleted": True}
            )
        return _answer(200, price)
